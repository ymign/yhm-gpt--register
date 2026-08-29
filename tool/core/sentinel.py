"""sentinel.py — OpenAI Sentinel 工作量证明 (PoW) 求解与拟真指纹生成
========================================================================
内置对 OpenAI 官方 Sentinel SDK 逆向沙箱求解，支持自动获取 PoW Token 及 SO Token。
"""
from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("sentinel")

SENTINEL_VERSION = "20260219f9f6"
SENTINEL_SDK_URL = f"https://sentinel.openai.com/sentinel/{SENTINEL_VERSION}/sdk.js"
SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"

_sdk_file_cache: Optional[Path] = None
_POW_SEMAPHORE = threading.BoundedSemaphore(6)


def _resolve_node_binary() -> str:
    return (os.getenv("NODE_PATH", "") or "").strip() or "node"


def _quickjs_script_path() -> Path:
    return Path(__file__).resolve().parent / "sentinel_quickjs.js"


def _ensure_sdk_file(session: Any, timeout_ms: int = 15000) -> Path:
    global _sdk_file_cache
    if _sdk_file_cache and _sdk_file_cache.exists() and _sdk_file_cache.stat().st_size > 0:
        return _sdk_file_cache

    cache_dir = Path(tempfile.gettempdir()) / "openai-sentinel-cache" / SENTINEL_VERSION
    cache_dir.mkdir(parents=True, exist_ok=True)
    sdk_file = cache_dir / "sdk.js"
    if sdk_file.exists() and sdk_file.stat().st_size > 0:
        _sdk_file_cache = sdk_file
        return sdk_file

    try:
        resp = session.get(
            SENTINEL_SDK_URL,
            headers={
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "referer": "https://auth.openai.com/",
                "sec-fetch-dest": "script",
                "sec-fetch-mode": "no-cors",
                "sec-fetch-site": "same-site",
            },
            timeout=max(10, int(timeout_ms / 1000)),
        )
        content = getattr(resp, "content", b"") or (getattr(resp, "text", "") or "").encode("utf-8")
        if content and len(content) > 1000:
            sdk_file.write_bytes(content)
            _sdk_file_cache = sdk_file
            return sdk_file
    except Exception as e:
        logger.warning(f"下载 Sentinel sdk.js 失败: {e}")

    _sdk_file_cache = sdk_file
    return sdk_file


def _run_quickjs_action(
    *,
    action: str,
    sdk_file: Path,
    quickjs_script: Path,
    payload: dict,
    timeout_ms: int,
) -> dict:
    body = dict(payload)
    body["action"] = action
    proc = subprocess.run(
        [_resolve_node_binary(), str(quickjs_script)],
        input=json.dumps(body, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=max(10, int(timeout_ms / 1000) + 6),
        env={
            **os.environ,
            "OPENAI_SENTINEL_SDK_FILE": str(sdk_file),
            "NODE_NO_WARNINGS": "1",
        },
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"Sentinel JS 执行失败 ({proc.returncode}): {err[:200]}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("Sentinel JS 输出为空")
    return json.loads(out)


def get_sentinel_token(
    session: Any,
    device_id: str,
    flow: str = "authorize_continue",
    user_agent: str = "",
    sec_ch_ua: str = "",
    sec_ch_ua_platform: str = "",
    sec_ch_ua_mobile: str = "",
    sec_ch_ua_full_version_list: str = "",
    sec_ch_ua_arch: str = "",
    sec_ch_ua_bitness: str = "",
    sec_ch_ua_model: str = "",
    sec_ch_ua_platform_version: str = "",
    screen: str = "1920x1080",
    lang: str = "zh-CN",
    lang_full: str = "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    browser_type: str = "chrome",
    navigator_platform: str = "Win32",
    navigator_vendor: str = "Google Inc.",
    timeout_ms: int = 15000,
    log_fn: Optional[Any] = None,
) -> tuple[str, str]:
    """完整执行 Sentinel 挑战求解，返回 (token, so_token)。"""
    def _log(msg: str):
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    sdk_file = _ensure_sdk_file(session, timeout_ms=timeout_ms)
    quickjs_script = _quickjs_script_path()
    if not sdk_file.exists() or sdk_file.stat().st_size == 0:
        _log("未找到 SDK 缓存，跳过 PoW")
        return "", ""

    w, h = 1920, 1080
    if "x" in screen:
        try:
            parts = screen.split("x")
            w, h = int(parts[0]), int(parts[1])
        except Exception:
            pass

    env_payload = {
        "device_id": device_id,
        "user_agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "language": lang or "zh-CN",
        "languages": [lang] if lang else ["zh-CN", "zh", "en-US", "en"],
        "platform": navigator_platform or "Win32",
        "vendor": navigator_vendor or "Google Inc.",
        "screen_width": w,
        "screen_height": h,
        "device_pixel_ratio": 1.0,
        "hardware_concurrency": 8,
        "device_memory": 8,
        "max_touch_points": 0,
        "timezone": "Asia/Shanghai",
        "flow": flow,
    }

    try:
        # Step 1: requirements
        req_res = _run_quickjs_action(
            action="requirements",
            sdk_file=sdk_file,
            quickjs_script=quickjs_script,
            payload=env_payload,
            timeout_ms=timeout_ms,
        )
        request_p = str(req_res.get("request_p") or "").strip()
        if not request_p:
            return "", ""

        # Step 2: request challenge
        req_headers = {
            "accept": "*/*",
            "accept-language": lang_full or "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "text/plain;charset=UTF-8",
            "origin": "https://auth.openai.com",
            "referer": "https://auth.openai.com/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }
        if user_agent:
            req_headers["user-agent"] = user_agent
        if sec_ch_ua:
            req_headers["sec-ch-ua"] = sec_ch_ua
        if sec_ch_ua_platform:
            req_headers["sec-ch-ua-platform"] = sec_ch_ua_platform
        if sec_ch_ua_mobile:
            req_headers["sec-ch-ua-mobile"] = sec_ch_ua_mobile

        req_body = json.dumps({"p": request_p, "flow": flow, "did": device_id}, ensure_ascii=False)
        resp = session.post(
            SENTINEL_REQ_URL,
            headers=req_headers,
            data=req_body.encode("utf-8"),
            timeout=max(10, int(timeout_ms / 1000)),
        )
        if getattr(resp, "status_code", 0) != 200:
            return "", ""

        challenge = resp.json() if hasattr(resp, "json") else json.loads(resp.text)
        if not challenge or not challenge.get("token"):
            return "", ""

        # Step 3: solve with semaphore
        solve_payload = dict(env_payload)
        solve_payload.update({
            "request_p": request_p,
            "challenge": challenge,
            "flow": flow,
            "behavior_duration_ms": random.randint(1500, 2000),
        })

        with _POW_SEMAPHORE:
            solved = _run_quickjs_action(
                action="solve",
                sdk_file=sdk_file,
                quickjs_script=quickjs_script,
                payload=solve_payload,
                timeout_ms=timeout_ms,
            )

        token = str(solved.get("token") or "").strip()
        so_token = str(solved.get("so_token") or "").strip()
        return token, so_token
    except Exception as e:
        logger.debug(f"Sentinel 获取跳过/异常: {e}")
        return "", ""


def generate_fingerprint(country_code: str = "JP") -> dict:
    """生成一套高逼真且各账号完全隔离的 Chrome 浏览器多维软硬件指纹。"""
    # 使用 curl_cffi 官方严格支持的 impersonate 版本
    supported_impersonates = ["chrome136", "chrome124", "chrome120"]
    chosen_imp = random.choice(supported_impersonates)

    if chosen_imp == "chrome136":
        ver, full_ver = "136", "136.0.7024.120"
    elif chosen_imp == "chrome124":
        ver, full_ver = "124", "124.0.6367.207"
    else:
        ver, full_ver = "120", "120.0.6099.130"

    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    screens = ["1920x1080", "2560x1440", "1536x864", "1440x900", "1680x1050", "2160x1440"]
    memories = [8, 16, 32]
    concurrencies = [8, 12, 16, 20]

    return {
        "user_agent": ua,
        "browser_type": "chrome",
        "impersonate": chosen_imp,
        "sec_ch_ua": f'"Chromium";v="{ver}", "Not.A/Brand";v="24", "Google Chrome";v="{ver}"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_full_version_list": f'"Chromium";v="{full_ver}", "Not.A/Brand";v="24.0.0.0", "Google Chrome";v="{full_ver}"',
        "sec_ch_ua_arch": '"x86"',
        "sec_ch_ua_bitness": '"64"',
        "sec_ch_ua_model": '""',
        "sec_ch_ua_platform_version": '"15.0.0"',
        "screen": random.choice(screens),
        "device_memory": random.choice(memories),
        "hardware_concurrency": random.choice(concurrencies),
        "lang": "en-US",
        "lang_full": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "timezone": "Asia/Tokyo" if country_code.upper() == "JP" else "America/New_York",
        "navigator_platform": "Win32",
        "navigator_vendor": "Google Inc.",
    }
