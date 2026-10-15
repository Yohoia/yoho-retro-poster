import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import zlib


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("poster", ROOT / "scripts" / "poster.py")
poster = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poster)


def fake_png(width, height):
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    row = b"\0" + b"\x80\x40\x20" * width
    pixels = zlib.compress(row * height)
    return (poster.PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", pixels) + chunk(b"IEND", b""))


def sample_plan(refs=None):
    return {
        "version": 2, "main_color": "#D98278", "accents": [], "brief": "quiet landscape",
        "references": refs or [],
        "scene": {"id": "quiet-meadow", "title": "Quiet Meadow", "description": "Quiet hillside",
                  "composition": "Low horizon", "palette_use": "Main color in hillside"},
    }


class FakeResponse:
    headers = {"x-request-id": "req-fake"}

    def __init__(self, image):
        self.image = image

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({"data": [{"b64_json": base64.b64encode(self.image).decode()}]}).encode()


class ModelResponse:
    def __init__(self, ids):
        self.ids = ids
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return json.dumps({"data": [{"id": name} for name in self.ids]}).encode()


class PosterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "settings.json"
        self.config_patch = mock.patch.dict(os.environ, {"COLOR_SCENE_POSTER_CONFIG": str(self.config),
                                                "OPENAI_API_KEY": "fake-secret-never-log"})
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def write_plan(self, refs=None):
        path = self.root / "plan.json"
        path.write_text(json.dumps(sample_plan(refs)), encoding="utf-8")
        return path

    def test_palette_and_plan_constraints(self):
        colors = poster.palette("#d98278", ["#f4c7a5"])
        self.assertEqual(colors["main"], "#D98278")
        self.assertEqual(colors["accents"], ["#F4C7A5"])
        with self.assertRaises(poster.PosterError):
            poster.palette("red", [])
        with self.assertRaises(poster.PosterError):
            poster.palette("#D98278", ["#D98278"])
        plan = poster.load_plan(self.write_plan())
        self.assertEqual(plan["scene"]["id"], "quiet-meadow")

    def test_generate_one_2048_image_resume_and_no_key_in_outputs(self):
        plan = self.write_plan()
        calls = []

        def urlopen(request, timeout):
            calls.append(request)
            size = json.loads(request.data)["size"]
            width, height = map(int, size.split("x"))
            return FakeResponse(fake_png(width, height))

        with mock.patch.object(poster, "safe_open", side_effect=urlopen):
            poster.render(plan, self.root / "out", upscale=False)
            poster.render(plan, self.root / "out", upscale=False)
            poster.render(plan, self.root / "out", upscale=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0].data)["size"], "2048x2048")
        self.assertEqual(json.loads(calls[0].data)["n"], 1)
        self.assertEqual(len(list((self.root / "out").glob("*.png"))), 1)
        self.assertFalse(list((self.root / "out").glob("preview-*")))
        self.assertEqual(poster.png_size((self.root / "out" / "poster-quiet-meadow.png").read_bytes()), (2048, 2048))
        record = (self.root / "out" / "poster-quiet-meadow.json").read_text()
        self.assertNotIn("fake-secret-never-log", record)
        self.assertIn("gpt-image-2", record)
        self.assertEqual(json.loads(calls[0].data)["model"], "gpt-image-2")
        changed = sample_plan()
        changed["scene"]["description"] = "A different landscape"
        plan.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(poster.PosterError, "新建任务目录"):
            poster.render(plan, self.root / "out", upscale=False)

    def test_cli_generate_calls_api_once_without_preview(self):
        plan = self.write_plan()
        with mock.patch.object(poster.sys, "argv", ["poster.py", "generate", str(plan)]), \
             mock.patch.object(poster, "safe_open", return_value=FakeResponse(fake_png(2048, 2048))) as opened, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(poster.main(), 0)
            self.assertEqual(poster.main(), 0)
        opened.assert_called_once()
        self.assertEqual(json.loads(opened.call_args.args[0].data)["size"], "2048x2048")
        self.assertEqual(len(list((self.root / "output").glob("*.png"))), 1)

    def test_resume_rejects_corrupt_or_wrong_size_images_without_request_or_overwrite(self):
        plan = self.write_plan()
        out = self.root / "out"
        with mock.patch.object(poster, "safe_open", return_value=FakeResponse(fake_png(2048, 2048))):
            poster.render(plan, out)
        target = out / "poster-quiet-meadow.png"
        original = target.read_bytes()
        damaged = bytearray(original)
        damaged[-1] ^= 1
        for invalid in (b"not a PNG", original[:24], bytes(damaged), fake_png(1024, 1024)):
            target.write_bytes(invalid)
            with self.subTest(size=len(invalid)), mock.patch.object(poster, "request_image") as request:
                with self.assertRaisesRegex(poster.PosterError, "已有图片.*人工检查"):
                    poster.render(plan, out)
                request.assert_not_called()
                self.assertEqual(target.read_bytes(), invalid)
        target.write_bytes(original)
        with mock.patch.object(poster, "request_image") as request:
            poster.render(plan, out)
            request.assert_not_called()

    def test_plan_rejects_multi_scene_and_old_schema_before_request(self):
        path = self.write_plan()
        for invalid in [dict(sample_plan(), scenes=[sample_plan()["scene"]]),
                        dict(sample_plan(), scene=[sample_plan()["scene"]]),
                        dict(sample_plan(), version=1)]:
            path.write_text(json.dumps(invalid))
            with mock.patch.object(poster, "safe_open") as opened:
                with self.assertRaises(poster.PosterError):
                    poster.render(path, self.root / "out", upscale=False)
                opened.assert_not_called()

    def test_wrong_size_stops_after_one_request_without_saving(self):
        plan = self.write_plan()
        with mock.patch.object(poster, "safe_open", return_value=FakeResponse(fake_png(1254, 1254))) as opened:
            with self.assertRaisesRegex(poster.PosterError, "1254×1254.*2048x2048"):
                poster.render(plan, self.root / "out", upscale=False)
        opened.assert_called_once()
        self.assertFalse(list((self.root / "out").glob("*.png")))
        self.assertFalse(list((self.root / "out").glob("*.json")))

    def test_unusable_output_stops_cli_before_any_image_request(self):
        plan = self.write_plan()
        blocked = self.root / "not-a-directory"
        blocked.write_text("keep this file")
        with mock.patch.object(poster.sys, "argv", ["poster.py", "generate", str(plan), "--output-dir", str(blocked)]), \
             mock.patch.object(poster, "request_image") as request, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as error:
            self.assertEqual(poster.main(), 2)
        request.assert_not_called()
        self.assertIn("输出目录不可用", error.getvalue())
        self.assertEqual(blocked.read_text(), "keep this file")

    def test_other_process_blocks_duplicate_request_and_exit_releases_lock(self):
        plan = self.write_plan()
        out = self.root / "out"
        script = (
            "import importlib.util, pathlib, sys\n"
            f"spec=importlib.util.spec_from_file_location('poster', {str(ROOT / 'scripts/poster.py')!r})\n"
            "p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)\n"
            f"with p.output_lock(pathlib.Path({str(out)!r}), 'quiet-meadow'):\n"
            " print('locked', flush=True)\n sys.stdin.readline()\n"
        )
        child = subprocess.Popen([sys.executable, "-u", "-c", script], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with mock.patch.object(poster, "request_image") as request:
                with self.assertRaisesRegex(poster.PosterError, "正在运行"):
                    poster.render(plan, out)
                request.assert_not_called()
        finally:
            child.terminate()
            child.communicate(timeout=5)
        with mock.patch.object(poster, "request_image", return_value=(fake_png(2048, 2048), None)) as request:
            poster.render(plan, out)
            poster.render(plan, out)
            request.assert_called_once()

    def test_png_with_valid_crcs_but_invalid_pixels_is_not_saved_or_resumed(self):
        def chunk(kind, content):
            return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
        header = chunk(b"IHDR", struct.pack(">IIBBBBB", 2048, 2048, 8, 2, 0, 0, 0))
        plan = self.write_plan()
        bad_streams = [b"not-a-zlib-stream", zlib.compress(b"too few scanlines"),
                       zlib.compress((b"\x05" + b"\0" * (2048 * 3)) * 2048)]
        for index, pixels in enumerate(bad_streams):
            damaged = poster.PNG + header + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
            with self.subTest(index=index), mock.patch.object(poster, "safe_open", return_value=FakeResponse(damaged)):
                with self.assertRaisesRegex(poster.PosterError, "PNG"):
                    poster.render(plan, self.root / f"invalid-{index}")
                self.assertFalse(list((self.root / f"invalid-{index}").glob("*.png")))
        out = self.root / "out"
        with mock.patch.object(poster, "request_image", return_value=(fake_png(2048, 2048), None)):
            poster.render(plan, out)
        (out / "poster-quiet-meadow.png").write_bytes(damaged)
        with mock.patch.object(poster, "request_image") as request:
            with self.assertRaisesRegex(poster.PosterError, "已有图片.*人工检查"):
                poster.render(plan, out)
            request.assert_not_called()

    def test_probe_uses_only_final_model_with_legacy_preview_settings(self):
        self.config.write_text(json.dumps({"final_model": "gpt-image-2", "preview_model": "unavailable-preview"}))
        with mock.patch.object(poster.sys, "argv", ["poster.py", "probe", "official"]), \
             mock.patch.object(poster, "probe_models", return_value={"status": "available"}) as probe, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(poster.main(), 0)
        probe.assert_called_once_with("official", ["gpt-image-2"])

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is optional")
    def test_upscale_existing_final_without_second_api_call(self):
        plan = self.write_plan()
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return FakeResponse(fake_png(2048, 2048))

        with mock.patch.object(poster, "safe_open", side_effect=urlopen):
            poster.render(plan, self.root / "out", upscale=False)
            poster.render(plan, self.root / "out", upscale=True)
        self.assertEqual(len(requests), 1)
        large = self.root / "out" / "poster-quiet-meadow-upscaled-3508.png"
        self.assertEqual(poster.png_size(large.read_bytes()), (3508, 3508))
        meta = json.loads(large.with_suffix(".json").read_text())
        self.assertFalse(meta["native_detail"])

    def test_reference_images_use_edits_and_no_filename_in_multipart(self):
        ref = self.root / 'odd"name.jpg'
        ref.write_bytes(b"example-jpeg-bytes")
        plan = poster.load_plan(self.write_plan([ref.name]))
        prompt = poster.make_prompt(plan, plan["scene"])
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return FakeResponse(fake_png(2048, 2048))

        with mock.patch.object(poster, "safe_open", side_effect=urlopen):
            poster.request_image(prompt, "gpt-image-2", "2048x2048", plan["_resolved_refs"])
        self.assertTrue(requests[0].full_url.endswith("/images/edits"))
        self.assertIn(b'name="image[]"', requests[0].data)
        self.assertNotIn(ref.name.encode(), requests[0].data)

    def test_settings_merge_and_no_secret_field(self):
        self.config.write_text('{"other_setting": "retain"}', encoding="utf-8")
        poster.save_settings({"final_model": "gpt-image-2.5-flare"})
        stored = json.loads(self.config.read_text())
        self.assertEqual(stored["other_setting"], "retain")
        self.assertEqual(stored["final_model"], "gpt-image-2.5-flare")
        self.assertNotIn("OPENAI_API_KEY", self.config.read_text())

    def test_model_probe_and_no_response_pause(self):
        with mock.patch.object(poster, "safe_open", return_value=ModelResponse(["gpt-image-2"])) as opened:
            result = poster.probe_models("official", ["gpt-image-2"])
        self.assertEqual(result["status"], "available")
        self.assertEqual(opened.call_args.args[0].full_url, "https://api.openai.com/v1/models")
        with mock.patch.object(poster, "safe_open", return_value=ModelResponse([])):
            self.assertEqual(poster.probe_models("official", ["gpt-image-2"])["status"], "unavailable")
        with mock.patch.object(poster, "safe_open", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(poster.PosterError, "未收到接口响应.*暂停"):
                poster.probe_models("official", ["gpt-image-2"])

    def test_thirdparty_single_image_uses_edits_and_isolated_key(self):
        ref = self.root / "source.png"
        ref.write_bytes(fake_png(2, 2))
        requests = []
        def fake_open(request, timeout):
            requests.append(request)
            return FakeResponse(fake_png(2048, 2048))
        with mock.patch.dict(os.environ, {"AICODEME_API_KEY": "thirdparty-fake-key"}):
            with mock.patch.object(poster, "safe_open", side_effect=fake_open):
                poster.render(self.write_plan([ref.name]), self.root / "out", provider="thirdparty")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.full_url, "https://image.aicodeme.xyz/v1/images/edits")
        self.assertIn(b'name="image"', request.data)
        self.assertNotIn(b'name="image[]"', request.data)
        self.assertIn(b'name="response_format"', request.data)
        self.assertIn(b'name="size"\r\n\r\n2048x2048', request.data)
        self.assertIn(b'name="n"\r\n\r\n1', request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer thirdparty-fake-key")
        self.assertEqual(len(list((self.root / "out").glob("*.png"))), 1)
        record = (self.root / "out" / "poster-quiet-meadow.json").read_text()
        self.assertNotIn("thirdparty-fake-key", record)

    def test_redirect_handler_refuses_forwarding_key(self):
        request = poster.urllib.request.Request("https://api.openai.com/v1/models",
                                                headers={"Authorization": "Bearer fake"})
        self.assertIsNone(poster.NoRedirect().redirect_request(request, None, 302, "redirect", {},
                                                               "https://example.org/steal"))

    def test_macos_python_uses_system_ca_when_default_bundle_missing(self):
        request = poster.urllib.request.Request("https://image.aicodeme.xyz/v1/models")
        with mock.patch.object(poster.sys, "platform", "darwin"), \
             mock.patch.object(poster.ssl, "get_default_verify_paths", return_value=mock.Mock(cafile=None)), \
             mock.patch.object(poster.Path, "is_file", return_value=True), \
             mock.patch.object(poster.ssl, "create_default_context", return_value=mock.Mock()) as context, \
             mock.patch.object(poster.urllib.request, "build_opener") as opener:
            poster.safe_open(request, timeout=8)
        context.assert_called_once_with(cafile="/etc/ssl/cert.pem")
        opener.return_value.open.assert_called_once_with(request, timeout=8)


if __name__ == "__main__":
    unittest.main()
