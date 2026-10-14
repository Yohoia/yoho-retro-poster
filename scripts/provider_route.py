#!/usr/bin/env python3
"""Select a credential-isolated image provider without sending a test image."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "scripts" / "credential-ui" / "src" / "profile.ts"
POSTER = ROOT / "scripts" / "poster.py"


def invoke(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=ROOT, text=True, capture_output=True, timeout=45, check=False)


def status(provider: str) -> str:
    node = shutil.which("node")
    if not node:
        return "error"
    result = invoke(node, str(PROFILE), "status", provider)
    if result.returncode == 2:
        return "missing"
    if result.returncode:
        return "error"
    try:
        return "configured" if json.loads(result.stdout)["configured"] is True else "missing"
    except (ValueError, KeyError, TypeError):
        return "error"


def probe(provider: str) -> dict:
    node = shutil.which("node")
    if not node:
        return {"status": "pause", "reason": "Node.js 不可用"}
    result = invoke(node, str(PROFILE), "run", provider, "--", sys.executable, str(POSTER), "probe", provider)
    if result.returncode:
        return {"status": "pause", "reason": f"{provider} 接口检查未确认成功；没有发起生图请求"}
    try:
        response = json.loads(result.stdout)
    except ValueError:
        return {"status": "pause", "reason": f"{provider} 模型列表响应无法解析"}
    if response.get("status") not in ("available", "unavailable"):
        return {"status": "pause", "reason": f"{provider} 检查结果不明确"}
    return response


def select() -> dict:
    official = status("official")
    if official == "error":
        return {"status": "pause", "reason": "无法检查官方凭据状态"}
    if official == "configured":
        checked = probe("official")
        if checked["status"] == "available":
            return {"status": "selected", "provider": "official", "check": checked}
        if checked["status"] == "pause":
            return checked
        fallback_reason = checked.get("reason", "official_unavailable")
    else:
        fallback_reason = "official_credential_missing"
    thirdparty = status("thirdparty")
    if thirdparty == "error":
        return {"status": "pause", "reason": "无法检查第三方凭据状态"}
    if thirdparty == "missing":
        return {"status": "setup_required", "provider": "thirdparty",
                "base_url": "https://image.aicodeme.xyz/v1",
                "reason": fallback_reason,
                "setup_command": "node scripts/credential-setup.ts thirdparty"}
    checked = probe("thirdparty")
    if checked["status"] == "available":
        return {"status": "selected", "provider": "thirdparty", "check": checked,
                "reason": fallback_reason}
    if checked["status"] == "pause":
        return checked
    detail = checked.get("reason")
    if detail == "HTTP 401":
        reason = "第三方接口返回 HTTP 401；请在凭据页替换为开发者 SK"
    elif detail == "HTTP 403":
        reason = "第三方接口返回 HTTP 403；请检查密钥权限"
    elif detail == "model_missing":
        reason = "第三方模型列表中没有当前模型；请核对模型设置"
    else:
        reason = "第三方凭据或模型不可用，请检查配置页的测试结果"
    return {"status": "pause", "reason": reason}


def main() -> int:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更新版本", file=sys.stderr)
        return 2
    try:
        result = select()
    except (OSError, subprocess.TimeoutExpired):
        result = {"status": "pause", "reason": "服务检查超时或运行环境不可用；没有发起生图请求"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "selected" else 2


if __name__ == "__main__":
    raise SystemExit(main())
