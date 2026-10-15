#!/usr/bin/env python3
"""Create color-led landscape posters with isolated image API providers."""

from __future__ import annotations

import argparse
import base64
import binascii
import colorsys
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import struct
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zlib


SKILL_NAME = "color-scene-poster"
DEFAULTS = {
    "final_model": "gpt-image-2",
    "thirdparty_final_model": "gpt-image-2",
}
PROVIDERS = {
    "official": {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "thirdparty": {"base_url": "https://image.aicodeme.xyz/v1", "key_env": "AICODEME_API_KEY"},
}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODEL = re.compile(r"^[a-zA-Z0-9._-]+$")
PNG = b"\x89PNG\r\n\x1a\n"
MAX_REFERENCE_BYTES = 20 * 1024 * 1024


class PosterError(Exception):
    pass


def settings_path() -> Path:
    override = os.environ.get("COLOR_SCENE_POSTER_CONFIG")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / SKILL_NAME / "settings.json"


def read_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return DEFAULTS.copy()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise PosterError(f"无法读取设置：{path}: {exc}") from exc
    if not isinstance(stored, dict):
        raise PosterError("设置文件必须是 JSON 对象")
    stored.pop("preview_model", None)
    stored.pop("thirdparty_preview_model", None)
    for key in DEFAULTS:
        if key in stored and (not isinstance(stored[key], str) or not MODEL.fullmatch(stored[key])):
            raise PosterError(f"设置中的 {key} 无效")
    if stored.get("provider", "openai") != "openai":
        raise PosterError("旧版 provider 设置无效；每次运行请使用 --provider 选择服务")
    stored.pop("provider", None)
    return {**DEFAULTS, **stored}


def save_settings(values: dict) -> Path:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**read_settings(), **values}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def normalize_color(value: str) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise PosterError(f"颜色必须是 #RRGGBB：{value!r}")
    return value.upper()


def palette(main: str, accents: list[str]) -> dict:
    main = normalize_color(main)
    accents = [normalize_color(c) for c in accents]
    if len(accents) > 2 or len(set([main, *accents])) != len([main, *accents]):
        raise PosterError("颜色总数为 1–3 种，且不能重复")
    rgb = tuple(int(main[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(*rgb)

    def tone(lightness: float, saturation: float) -> str:
        channels = colorsys.hls_to_rgb(h, min(1, max(0, lightness)), min(1, max(0, saturation)))
        return "#" + "".join(f"{round(c * 255):02X}" for c in channels)

    return {
        "main": main,
        "light": tone(l + (1 - l) * 0.62, s * 0.55),
        "midtone": tone(l + (1 - l) * 0.24, s * 0.85),
        "shadow": tone(l * 0.38, min(1, s * 0.9 + 0.08)),
        "accents": accents,
    }


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PosterError(f"无法读取方案：{exc}") from exc
    if not isinstance(plan, dict) or plan.get("version") != 2:
        raise PosterError("方案必须是 version: 2 的单场景 JSON 对象；旧方案请将一个场景写入 scene")
    accents = plan.get("accents", [])
    if not isinstance(accents, list):
        raise PosterError("accents 必须是数组")
    colors = palette(plan.get("main_color"), accents)
    scene = plan.get("scene")
    if not isinstance(scene, dict) or "scenes" in plan:
        raise PosterError("方案只接受一个 scene 对象")
    scene_id = scene.get("id")
    if not isinstance(scene_id, str) or not ID.fullmatch(scene_id):
        raise PosterError("场景 id 必须是小写短横线标识")
    for key in ("title", "description", "composition", "palette_use"):
        value = scene.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 1200:
            raise PosterError(f"场景 {scene_id} 的 {key} 必须是 1–1200 字符的文本")
    refs = plan.get("references", [])
    if not isinstance(refs, list) or len(refs) > 3:
        raise PosterError("references 最多 3 张")
    resolved = []
    for item in refs:
        if not isinstance(item, str):
            raise PosterError("references 中的路径必须是文本")
        ref = (path.parent / item).resolve()
        if not ref.is_file() or ref.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise PosterError(f"参考图不可读或格式不支持：{item}")
        if ref.stat().st_size > MAX_REFERENCE_BYTES:
            raise PosterError(f"参考图超过 20 MB：{item}")
        resolved.append(ref)
    brief = plan.get("brief", "")
    if not isinstance(brief, str) or len(brief) > 2000:
        raise PosterError("brief 必须是 2000 字符以内的文本")
    return {**plan, "_palette": colors, "_resolved_refs": resolved}


def make_prompt(plan: dict, scene: dict) -> str:
    p = plan["_palette"]
    accents = ", ".join(p["accents"]) if p["accents"] else "derive only restrained harmonizing accents"
    refs = (
        "Attached images guide shape simplification, color masses, negative space and optional print texture. "
        "Use the new scene and composition below; reference subjects and settings are not required. "
        if plan["_resolved_refs"] else ""
    )
    return (
        "Create ONE finished square landscape illustration, never a collage or design sheet. "
        + refs
        + "Visual language: modern retro travel-poster illustration without typography; "
        "large clean flat color masses, simplified organic forms, clear spatial relationships, "
        "controlled painterly edges and high legibility at thumbnail scale. "
        "Let the supplied palette and scene-specific brief determine the setting, subjects, weather, "
        "action, viewpoint and composition. Integrate chosen elements into one coherent scenic moment. "
        "Use only the details needed for the visual focus and sense of space. "
        "Keep generous negative space; avoid photorealism, 3D, gradients as the main structure, "
        "heavy outlines, ornamental borders, captions, logos, lettering and watermarks. "
        f"Dominant color {p['main']}; user accents: {accents}. "
        f"Supporting tonal suggestions: light {p['light']}, midtone {p['midtone']}, shadow {p['shadow']}. "
        "Adapt supporting tones to the scene and color placement below, preserving the supplied colors. "
        "The dominant color must be unmistakable across large image regions, not merely a tiny accent. "
        f"Overall brief: {plan.get('brief') or 'a cohesive scenic moment inspired by the supplied colors'}. "
        f"Scene: {scene['description']}. Composition: {scene['composition']}. "
        f"Color placement: {scene['palette_use']}. "
        "Render a beautiful, complete landscape with intentional shapes and balanced negative space. "
        "Fine paper grain or local print speckling is optional and subordinate to the clean shapes."
    )


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or not data.startswith(PNG) or data[12:16] != b"IHDR":
        raise PosterError("接口没有返回有效 PNG 图像")
    return struct.unpack(">II", data[16:24])


def validate_png(data: bytes) -> tuple[int, int]:
    """Check chunks and bounded pixel-stream decompression using the standard library."""
    dimensions = png_size(data)
    offset = len(PNG)
    pixels = bytearray()
    header = None
    palette_entries = 0
    ended_pixels = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise PosterError("PNG 数据不完整")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise PosterError("PNG 数据不完整")
        expected_crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(data[offset + 4:end - 4]) != expected_crc:
            raise PosterError("PNG 校验失败")
        content = data[offset + 8:end - 4]
        if kind == b"IHDR":
            if offset != len(PNG) or length != 13:
                raise PosterError("PNG 图像头无效")
            header = struct.unpack(">IIBBBBB", content)
        elif kind == b"PLTE":
            if pixels or not length or length % 3 or length > 768:
                raise PosterError("PNG 调色板无效")
            palette_entries = length // 3
        elif kind == b"IDAT":
            if ended_pixels:
                raise PosterError("PNG 像素数据顺序无效")
            pixels.extend(content)
        elif pixels:
            ended_pixels = True
        if kind == b"IEND":
            if length != 0 or not pixels or end != len(data) or header is None:
                raise PosterError("PNG 图像结束标记无效")
            width, height, depth, color, compression, filtering, interlace = header
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (not 0 < width <= 8192 or not 0 < height <= 8192 or depth not in depths.get(color, ())
                    or compression != 0 or filtering != 0 or interlace not in (0, 1)
                    or (color == 3 and not 0 < palette_entries <= 2 ** depth)):
                raise PosterError("PNG 像素格式无效")
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            passes = [(0, 0, 1, 1)] if not interlace else [
                (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
                (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
            rows = []
            for x, y, dx, dy in passes:
                columns = max(0, (width - x + dx - 1) // dx)
                count = max(0, (height - y + dy - 1) // dy)
                if columns and count:
                    rows.extend([1 + (columns * channels * depth + 7) // 8] * count)
            expected = sum(rows)
            if expected > 128 * 1024 * 1024:
                raise PosterError("PNG 像素数据过大")
            try:
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(pixels, expected + 1)
            except zlib.error as exc:
                raise PosterError("PNG 像素数据无法解压") from exc
            if len(decoded) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise PosterError("PNG 像素数据长度无效")
            row_offset = 0
            for row_size in rows:
                if decoded[row_offset] > 4:
                    raise PosterError("PNG 扫描行过滤类型无效")
                row_offset += row_size
            return dimensions
        offset = end
    raise PosterError("PNG 数据不完整")


def multipart_payload(fields: dict[str, str], refs: list[Path], provider: str = "official") -> tuple[bytes, str]:
    boundary = "poster-" + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    image_field = "image" if provider == "thirdparty" else "image[]"
    for index, ref in enumerate(refs):
        mime = {".png": "image/png", ".webp": "image/webp"}.get(ref.suffix.lower(), "image/jpeg")
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{image_field}\"; filename=\"reference-{index}{ref.suffix.lower()}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
        body.extend(ref.read_bytes())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_open(request: urllib.request.Request, timeout: int):
    """Never forward a bearer token to a redirect target."""
    # Some standalone macOS Python installs have no OpenSSL CA bundle even
    # though the system CA file is present. Keep certificate verification on.
    system_ca = Path("/etc/ssl/cert.pem")
    if (sys.platform == "darwin" and not os.environ.get("SSL_CERT_FILE")
            and ssl.get_default_verify_paths().cafile is None and system_ca.is_file()):
        context = ssl.create_default_context(cafile=str(system_ca))
    else:
        context = ssl.create_default_context()
    return urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=context)).open(
        request, timeout=timeout)


def provider_key(provider: str) -> str:
    if provider not in PROVIDERS:
        raise PosterError(f"未知服务：{provider}")
    key = os.environ.get(PROVIDERS[provider]["key_env"], "")
    if not key:
        raise PosterError(f"缺少 {PROVIDERS[provider]['key_env']}；先检查对应服务的凭据状态")
    return key


def probe_models(provider: str, models: list[str]) -> dict:
    """A free model-list precheck. It cannot prove an image call will succeed."""
    key = provider_key(provider)
    endpoint = PROVIDERS[provider]["base_url"] + "/models"
    request = urllib.request.Request(endpoint, headers={
        "Authorization": "Bearer " + key,
        "User-Agent": SKILL_NAME,
    })
    try:
        with safe_open(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"provider": provider, "status": "unavailable", "reason": f"HTTP {exc.code}", "models": models}
        raise PosterError(f"{provider} 模型检查返回 HTTP {exc.code}；无法判定可用性，暂停") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PosterError(f"{provider} 模型检查未收到接口响应（{type(exc).__name__}）；暂停，不切换服务") from exc
    try:
        payload = json.loads(raw)
        items = payload["data"]
        available = {item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)}
    except (ValueError, KeyError, TypeError) as exc:
        raise PosterError(f"{provider} 模型列表格式不符；暂停，不切换服务") from exc
    missing = sorted(set(models) - available)
    return {"provider": provider, "status": "unavailable" if missing else "available",
            "reason": "model_missing" if missing else "models_listed", "models": models, "missing": missing,
            "note": "模型列表只做非计费预检，实际图像能力仍需生成时验证"}


def request_image(prompt: str, model: str, size: str, refs: list[Path], provider: str = "official") -> tuple[bytes, str | None]:
    key = provider_key(provider)
    fields = {"model": model, "prompt": prompt, "size": size, "output_format": "png",
              "quality": "medium" if provider == "thirdparty" else
                         ("high" if size == "2048x2048" else "low"), "n": "1"}
    if provider == "thirdparty":
        fields["response_format"] = "b64_json"
    if refs:
        payload, content_type = multipart_payload(fields, refs, provider=provider)
        endpoint = PROVIDERS[provider]["base_url"] + "/images/edits"
    else:
        payload = json.dumps({**fields, "n": 1}).encode("utf-8")
        content_type = "application/json"
        endpoint = PROVIDERS[provider]["base_url"] + "/images/generations"
    req = urllib.request.Request(endpoint, data=payload, headers={
        "Authorization": "Bearer " + key,
        "Content-Type": content_type,
        "User-Agent": "color-scene-poster/0.2.0",
    })
    try:
        with safe_open(req, timeout=300) as response:
            raw = response.read()
            request_id = response.headers.get("x-request-id")
    except urllib.error.HTTPError as exc:
        request_id = exc.headers.get("x-request-id") if exc.headers else None
        raise PosterError(f"图像接口返回 HTTP {exc.code}；请求 ID：{request_id or '未提供'}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PosterError(f"{provider} 图像接口未收到响应：{type(exc).__name__}；暂停，不切换服务") from exc
    try:
        parsed = json.loads(raw)
        image = base64.b64decode(parsed["data"][0]["b64_json"], validate=True)
    except (ValueError, binascii.Error, KeyError, IndexError, TypeError) as exc:
        raise PosterError("图像接口响应中没有可解码的图片") from exc
    actual = png_size(image)
    expected = tuple(int(v) for v in size.split("x"))
    if actual != expected:
        raise PosterError(f"图像接口返回 {actual[0]}×{actual[1]}，预期 {size}；没有保存不符尺寸的图片")
    validate_png(image)
    return image, request_id


def write_new(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError as exc:
        raise PosterError(f"输出已存在，未覆盖：{path}") from exc
    except OSError as exc:
        raise PosterError(f"输出保存失败，已保留此前结果，请先检查磁盘和权限：{path}") from exc


@contextmanager
def output_lock(out: Path, scene_id: str):
    """Advisory locks release on process exit; keep the inode to avoid lock-file races."""
    stream = None
    locked = False
    try:
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=out) as probe:
            probe.write(b"0")
            probe.flush()
            os.fsync(probe.fileno())
        stream = (out / f".poster-{scene_id}.lock").open("a+b")
        if os.name == "nt":
            import msvcrt
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
    except OSError as exc:
        if stream:
            stream.close()
        message = "同一输出任务正在运行，请等待完成后再试" if stream else "输出目录不可用，请检查路径、权限和磁盘空间"
        raise PosterError(message) from exc
    try:
        yield
    finally:
        if locked and stream:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()


def export_upscaled(source: Path, record: Path) -> None:
    upscaled = source.with_name(source.stem + "-upscaled-3508.png")
    upscaled_record = source.with_name(source.stem + "-upscaled-3508.json")
    if upscaled.exists() and upscaled_record.exists():
        print(f"已存在，跳过放大导出：{upscaled}")
        return
    if upscaled.exists() or upscaled_record.exists():
        raise PosterError(f"放大导出结果不完整，请先人工检查：{upscaled}")
    try:
        from PIL import Image
    except ImportError as exc:
        raise PosterError("3508 放大导出需要 Pillow：python3 -m pip install Pillow；2048 原图已保存") from exc
    with Image.open(source) as original:
        large = original.resize((3508, 3508), Image.Resampling.LANCZOS)
        large.save(upscaled, format="PNG")
    result = {
        "version": 1, "derived_from": source.name, "size": "3508x3508",
        "method": "Lanczos interpolation", "native_detail": False,
        "source_record": record.name,
    }
    write_new(upscaled_record, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"已保存放大导出（非原生细节）：{upscaled}")


def render(plan_path: Path, out: Path, *, upscale: bool = False, provider: str = "official") -> None:
    plan = load_plan(plan_path)
    settings = read_settings()
    with output_lock(out, plan["scene"]["id"]):
        _render_locked(plan, out, settings, upscale=upscale, provider=provider)


def _render_locked(plan: dict, out: Path, settings: dict, *, upscale: bool, provider: str) -> None:
    scene = plan["scene"]
    if provider not in PROVIDERS:
        raise PosterError(f"未知服务：{provider}")
    model_key = ("thirdparty_" if provider == "thirdparty" else "") + "final_model"
    model = settings[model_key]
    size = "2048x2048"
    reference_hashes = [hashlib.sha256(ref.read_bytes()).hexdigest() for ref in plan["_resolved_refs"]]
    stem = "poster-" + scene["id"]
    target = out / f"{stem}.png"
    metadata = out / f"{stem}.json"
    prompt = make_prompt(plan, scene)
    expected = {
        "scene_id": scene["id"], "model": model, "size": size,
        "provider": provider, "base_url": PROVIDERS[provider]["base_url"],
        "quality": "medium" if provider == "thirdparty" else "high",
        "main_color": plan["_palette"]["main"], "palette": plan["_palette"],
        "references": plan.get("references", []), "reference_hashes": reference_hashes,
        "prompt": prompt,
    }
    if target.exists() and metadata.exists():
        try:
            recorded = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PosterError(f"已有结果记录不可读：{metadata}") from exc
        if any(recorded.get(key) != value for key, value in expected.items()):
            raise PosterError(f"方案、模型或参考图已改变，请更换场景 id 或指定新的 --output-dir，保留旧结果：{stem}")
        try:
            if validate_png(target.read_bytes()) != (2048, 2048):
                raise PosterError("已有图片尺寸不符")
        except (OSError, PosterError) as exc:
            raise PosterError(f"已有图片损坏、不可读或尺寸不符，请先人工检查：{target}") from exc
        print(f"已存在，跳过：{target}")
        if upscale:
            export_upscaled(target, metadata)
        return
    if target.exists() or metadata.exists():
        raise PosterError(f"已有不完整结果，请先人工检查：{stem}")
    print(f"生成 {scene['id']}：{provider} / {model} / {size}", flush=True)
    image, request_id = request_image(prompt, model, size, plan["_resolved_refs"], provider=provider)
    result = {
        "version": 1,
        **expected,
        "request_id": request_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_new(target, image)
    write_new(metadata, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"已保存：{target}")
    if upscale:
        export_upscaled(target, metadata)


def main() -> int:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更新版本", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="颜色场景海报生成器")
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config", help="查看或修改非敏感模型设置")
    config.add_argument("action", choices=["show", "set"])
    config.add_argument("--final-model")
    config.add_argument("--thirdparty-final-model")
    probe = commands.add_parser("probe", help="通过模型列表做不计费的可用性预检")
    probe.add_argument("provider", choices=PROVIDERS)
    pal = commands.add_parser("palette", help="从 1–3 种输入颜色推导色板")
    pal.add_argument("main_color")
    pal.add_argument("--accent", action="append", default=[])
    validate = commands.add_parser("validate", help="检查单场景方案")
    validate.add_argument("plan", type=Path)
    cmd = commands.add_parser("generate", help="直接生成一张 2048×2048 成品")
    cmd.add_argument("plan", type=Path)
    cmd.add_argument("--output-dir", type=Path, help="输出目录，默认当前工作目录下的 output/")
    cmd.add_argument("--provider", choices=PROVIDERS, default="official")
    cmd.add_argument("--upscale-3508", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "config":
            if args.action == "show":
                print(json.dumps({"path": str(settings_path()), **read_settings(),
                                  "official_base_url": PROVIDERS["official"]["base_url"],
                                  "thirdparty_base_url": PROVIDERS["thirdparty"]["base_url"]},
                                 ensure_ascii=False, indent=2))
            else:
                values = {key: getattr(args, key) for key in
                          ("final_model", "thirdparty_final_model")
                          if getattr(args, key)}
                if not values or any(not MODEL.fullmatch(value) for value in values.values()):
                    raise PosterError("请提供合法的 --final-model 或 --thirdparty-final-model")
                print(f"已保存非敏感设置：{save_settings(values)}")
        elif args.command == "probe":
            settings = read_settings()
            prefix = "thirdparty_" if args.provider == "thirdparty" else ""
            models = [settings[prefix + "final_model"]]
            print(json.dumps(probe_models(args.provider, models), ensure_ascii=False))
        elif args.command == "palette":
            print(json.dumps(palette(args.main_color, args.accent), ensure_ascii=False, indent=2))
        elif args.command == "validate":
            plan = load_plan(args.plan)
            print(json.dumps({"status": "valid", "scene": plan["scene"]["id"],
                              "palette": plan["_palette"], "references": len(plan["_resolved_refs"])}, ensure_ascii=False))
        else:
            render(args.plan, args.output_dir or Path.cwd() / "output",
                   upscale=args.upscale_3508, provider=args.provider)
        return 0
    except PosterError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
