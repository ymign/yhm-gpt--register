"""QuickJS-driven Sentinel token generator.

Adapted from
https://github.com/zc-zhangchen/any-auto-register
platforms/chatgpt/sentinel_browser.py:`_get_sentinel_token_via_quickjs`
+ scripts/js/openai_sentinel_quickjs.js (MIT License).

Why this exists:
  Pure-Python `sentinel.py` computes a synthetic PoW that *passes* OpenAI's
  surface validation (200 OK on `/sentinel/req`, `/authorize/continue`, etc.)
  but the OTP-dispatch service runs the actual sentinel SDK JS server-side
  to verify the token. Our synthetic token fails the deeper check → email
  silent-drop. To pass, we must run OpenAI's real `sdk.js` (downloaded from
  `sentinel.openai.com/sentinel/<ver>/sdk.js`) inside a JS VM and emit the
  same token the real browser would.

Implementation:
  - Spawn `node -e <wrapper>` per token request
  - Wrapper loads OpenAI's sdk.js + `openai_sentinel_quickjs.js` (a thin
    adapter that exposes `requirements`/`solve` actions over stdin/stdout)
  - Two passes: action=requirements → `request_p`, then `/sentinel/req` →
    challenge, then action=solve → `final_p` + `t`
  - Returns the same JSON-string shape `{p, t, c, id, flow}` as our
    pure-Python `build_sentinel_token`, so callers don't need to change

Public API:
  - `get_sentinel_token_via_quickjs(session, device_id, flow, ...) -> str | None`
"""
from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


SENTINEL_VERSION = "20260219f9f6"
SENTINEL_SDK_URL = f"https://sentinel.openai.com/sentinel/{SENTINEL_VERSION}/sdk.js"
SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"

# ─── PoW 算力槽位并发控制器 ──────────────────────────────────
# 无论外层网络并发（HTTP / IMAP 邮件 / SMS）开了 10 个还是 20 个，
# 将 CPU 密集的 Node.js PoW 碰撞限制在「槽位数」个同时进行，
# 避免高并发时互相争抢 CPU 导致整机发热降频、每个号算力耗时翻倍。
# 默认 6：i5-13500H 是 4 性能核 + 8 能效核，6 = 4 个 P 核打满 + 2 个 E 核分担。
# 可被环境变量 SENTINEL_MAX_POW_WORKERS 覆盖；运行期可用 set_pow_slots()
# 动态调整（WebUI「全自动批量」页的 PoW 槽位设置，存 settings 表持久化）。
_DEFAULT_POW_SLOTS = int(os.getenv("SENTINEL_MAX_POW_WORKERS", "6"))
_pow_slots = max(1, min(16, _DEFAULT_POW_SLOTS))
_POW_SEMAPHORE = threading.BoundedSemaphore(_pow_slots)


def get_pow_slots() -> int:
    """当前 PoW 算力槽位数。"""
    return _pow_slots


def set_pow_slots(n: int) -> int:
    """运行期动态调整 PoW 算力槽位（WebUI 设置保存时调用）。

    直接替换全局信号量：正在解算的 worker 持有的是旧信号量对象，
    with 退出时 release 的也是旧对象，计数自洽、安全。替换瞬间会短暂出现
    旧 N + 新 M 并存（多几个 node 进程跑几秒），槽位本就是软限制，无害。
    """
    global _POW_SEMAPHORE, _pow_slots
    n = int(6 if n is None else n)  # None = 用默认；0/负数会被下面 clamp 到 1
    n = max(1, min(16, n))
    _pow_slots = n
    _POW_SEMAPHORE = threading.BoundedSemaphore(n)
    return n


def _resolve_node_binary() -> str:
    return (os.getenv("OPENAI_SENTINEL_NODE_PATH", "") or "").strip() or "node"


def _quickjs_script_path() -> Path:
    return Path(__file__).resolve().parent / "openai_sentinel_quickjs.js"


_sdk_file_cache: Optional[Path] = None


def _identity_http_headers(
    *,
    user_agent: str = "",
    lang_full: str = "",
    sec_ch_ua: str = "",
    sec_ch_ua_mobile: str = "",
    sec_ch_ua_platform: str = "",
    sec_ch_ua_full_version_list: str = "",
    sec_ch_ua_arch: str = "",
    sec_ch_ua_bitness: str = "",
    sec_ch_ua_model: str = "",
    sec_ch_ua_platform_version: str = "",
) -> dict[str, str]:
    """Sentinel HTTP 层与 PoW 画像用同一套 UA / 语言 / Client Hints。"""
    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    if lang_full:
        headers["accept-language"] = lang_full
    if sec_ch_ua:
        headers["sec-ch-ua"] = sec_ch_ua
        headers["sec-ch-ua-mobile"] = sec_ch_ua_mobile or "?0"
        if sec_ch_ua_platform:
            headers["sec-ch-ua-platform"] = sec_ch_ua_platform
        if sec_ch_ua_full_version_list:
            headers["sec-ch-ua-full-version-list"] = sec_ch_ua_full_version_list
        if sec_ch_ua_arch:
            headers["sec-ch-ua-arch"] = sec_ch_ua_arch
        if sec_ch_ua_bitness:
            headers["sec-ch-ua-bitness"] = sec_ch_ua_bitness
        if sec_ch_ua_model:
            headers["sec-ch-ua-model"] = sec_ch_ua_model
        if sec_ch_ua_platform_version:
            headers["sec-ch-ua-platform-version"] = sec_ch_ua_platform_version
    return headers


def _ensure_sdk_file(session: Any, timeout_ms: int, identity_headers: Optional[dict] = None) -> Path:
    """Download OpenAI's actual sdk.js to /tmp cache (one-shot per version)."""
    global _sdk_file_cache
    if _sdk_file_cache and _sdk_file_cache.exists():
        return _sdk_file_cache

    cache_dir = Path(tempfile.gettempdir()) / "openai-sentinel-demo" / SENTINEL_VERSION
    cache_dir.mkdir(parents=True, exist_ok=True)
    sdk_file = cache_dir / "sdk.js"
    if sdk_file.exists() and sdk_file.stat().st_size > 0:
        _sdk_file_cache = sdk_file
        return sdk_file

    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "referer": "https://auth.openai.com/",
        "sec-fetch-dest": "script",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "same-site",
    }
    if identity_headers:
        headers.update(identity_headers)
    resp = session.get(
        SENTINEL_SDK_URL,
        headers=headers,
        timeout=max(10, int(timeout_ms / 1000)),
    )
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"下载 sdk.js 失败: HTTP {resp.status_code}")
    content = getattr(resp, "content", b"") or (resp.text or "").encode()
    if not content:
        raise RuntimeError("下载 sdk.js 失败: 响应为空")
    sdk_file.write_bytes(content)
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
        timeout=max(10, int(timeout_ms / 1000) + 5),
        env={
            **os.environ,
            "OPENAI_SENTINEL_SDK_FILE": str(sdk_file),
        },
    )
    if proc.returncode != 0:
        raise RuntimeError(f"QuickJS 执行失败: {(proc.stderr or proc.stdout or 'unknown').strip()[:300]}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("QuickJS 返回空输出")
    data = json.loads(out)
    if not isinstance(data, dict):
        raise RuntimeError("QuickJS 输出不是 JSON 对象")
    return data


def _fetch_sentinel_challenge(
    session: Any,
    *,
    device_id: str,
    flow: str,
    request_p: str,
    timeout_ms: int,
    lang_full: str = "",
    identity_headers: Optional[dict] = None,
) -> dict:
    body = {"p": request_p, "id": device_id, "flow": flow}
    accept_lang = lang_full or "en-US,en;q=0.9"
    headers = {
        "origin": "https://sentinel.openai.com",
        "referer": f"https://sentinel.openai.com/backend-api/sentinel/frame.html?sv={SENTINEL_VERSION}",
        "content-type": "text/plain;charset=UTF-8",
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": accept_lang,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    if identity_headers:
        headers.update(identity_headers)
    resp = session.post(
        SENTINEL_REQ_URL,
        data=json.dumps(body, separators=(",", ":")),
        headers=headers,
        timeout=max(10, int(timeout_ms / 1000)),
    )
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"/sentinel/req HTTP {resp.status_code}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Sentinel challenge 响应不是 JSON 对象")
    return payload


def get_sentinel_token_via_quickjs(
    session: Any,
    device_id: str,
    *,
    flow: str = "authorize_continue",
    timeout_ms: int = 45000,
    log: Optional[Callable[[str], None]] = None,
    user_agent: str = "",
    screen: str = "",
    lang: str = "",
    lang_full: str = "",
    browser_type: str = "",
    platform: str = "",
    vendor: Optional[str] = None,
    hardware_concurrency: int = 0,
    device_memory: Optional[int] = None,
    max_touch_points: int = 0,
    device_pixel_ratio: float = 0.0,
    timezone: str = "",  # IANA 时区名（如 Asia/Tokyo）
    # Client Hints：PoW 画像与 /sentinel/req、sdk.js 下载共用
    sec_ch_ua: str = "",
    sec_ch_ua_platform: str = "",
    sec_ch_ua_mobile: str = "",
    sec_ch_ua_full_version_list: str = "",
    sec_ch_ua_arch: str = "",
    sec_ch_ua_bitness: str = "",
    sec_ch_ua_model: str = "",
    sec_ch_ua_platform_version: str = "",
    webgl_vendor: str = "",
    webgl_renderer: str = "",
    js_heap_size_limit: int = 0,
    color_depth: int = 24,
    avail_width: Optional[int] = None,
    avail_height: Optional[int] = None,
    connection_rtt: Optional[int] = None,
    connection_downlink: Optional[float] = None,
    connection_effective_type: str = "4g",
) -> Optional[tuple[str, str]]:
    """Try the QuickJS path. Return JSON string on success, None on any failure.

    Caller is expected to fall back to pure-Python sentinel on None.

    指纹一致性：``platform`` / ``vendor`` / ``hardware_concurrency`` 等按调用方
    传入的浏览器家族画像喂给 sdk.js 的 navigator，避免 UA 说 Windows Chrome 但
    navigator 报 MacIntel/Apple 的硬伤。未传时按 UA 推断合理默认值。
    """
    log = log or (lambda m: logger.info(m))
    quickjs_script = _quickjs_script_path()
    if not quickjs_script.exists():
        log(f"Sentinel QuickJS 脚本不存在: {quickjs_script}")
        return None

    did = str(device_id or uuid.uuid4())

    screen_w, screen_h = "1920", "1080"
    if screen and "x" in screen:
        parts = screen.split("x", 1)
        screen_w, screen_h = parts[0], parts[1]

    lang_primary = lang or "en-US"
    # HAR / Roxy 真 Chrome：navigator.languages 是主语言单值（如 ["ja-JP"]），
    # 不是把 Accept-Language 的 q 权重链拆进去。拆开会让 Sentinel p[8] 和真浏览器对不上。
    languages = [lang_primary]

    # ── 指纹一致性：platform / vendor 未显式传入时按 UA 推断，绝不写死 MacIntel ──
    ua_l = (user_agent or "").lower()
    if not platform:
        if "iphone" in ua_l:
            platform = "iPhone"
        elif "windows" in ua_l:
            platform = "Win32"
        elif "mac" in ua_l:
            platform = "MacIntel"
        else:
            platform = "Win32"
    if vendor is None:
        if "firefox" in ua_l:
            vendor = ""                       # Firefox navigator.vendor 为空串
        elif "chrome" in ua_l:
            vendor = "Google Inc."
        else:
            vendor = "Apple Computer, Inc."   # Safari / iOS
    hw_conc = int(hardware_concurrency) if hardware_concurrency else 8

    env_payload = {
        "device_id": did,
        "user_agent": user_agent or "Mozilla/5.0",
        "screen_width": screen_w,
        "screen_height": screen_h,
        "language": lang_primary,
        "languages": languages,
        "platform": platform,
        "vendor": vendor,
        "hardware_concurrency": hw_conc,
        "browser_type": browser_type or "",
        "device_pixel_ratio": float(device_pixel_ratio) if device_pixel_ratio else 2.0,
        "max_touch_points": int(max_touch_points),
        "timezone": timezone or "UTC",
        "webgl_vendor": webgl_vendor or "",
        "webgl_renderer": webgl_renderer or "",
        "js_heap_size_limit": int(js_heap_size_limit or 4294967296),
        "color_depth": int(color_depth or 24),
        "avail_width": int(avail_width or screen_w),
        "avail_height": int(avail_height or screen_h),
        "connection_rtt": int(connection_rtt or 50),
        "connection_downlink": float(connection_downlink or 10),
        "connection_effective_type": connection_effective_type or "4g",
    }
    # deviceMemory 仅 Chromium 暴露；None 时不下发该键，JS 侧保持 undefined
    if device_memory is not None:
        env_payload["device_memory"] = int(device_memory)

    identity_headers = _identity_http_headers(
        user_agent=user_agent,
        lang_full=lang_full or lang_primary,
        sec_ch_ua=sec_ch_ua,
        sec_ch_ua_mobile=sec_ch_ua_mobile,
        sec_ch_ua_platform=sec_ch_ua_platform,
        sec_ch_ua_full_version_list=sec_ch_ua_full_version_list,
        sec_ch_ua_arch=sec_ch_ua_arch,
        sec_ch_ua_bitness=sec_ch_ua_bitness,
        sec_ch_ua_model=sec_ch_ua_model,
        sec_ch_ua_platform_version=sec_ch_ua_platform_version,
    )

    try:
        sdk_file = _ensure_sdk_file(session, timeout_ms, identity_headers=identity_headers)

        requirements = _run_quickjs_action(
            action="requirements",
            sdk_file=sdk_file,
            quickjs_script=quickjs_script,
            payload=env_payload,
            timeout_ms=timeout_ms,
        )
        request_p = str(requirements.get("request_p") or "").strip()
        if not request_p:
            log("Sentinel QuickJS 失败: requirements 未返回 request_p")
            return None

        challenge = _fetch_sentinel_challenge(
            session,
            device_id=did,
            flow=flow,
            request_p=request_p,
            timeout_ms=timeout_ms,
            lang_full=lang_full,
            identity_headers=identity_headers,
        )
        c_value = str(challenge.get("token") or "").strip()
        if not c_value:
            log("Sentinel QuickJS 失败: challenge token 为空")
            return None

        solve_payload = dict(env_payload)
        # 精简模拟行为时长，从原本写死的 4200ms 优化为 1500~2000ms 动态随机值，大幅缩短单次等待
        behavior_ms = int(os.getenv("SENTINEL_BEHAVIOR_MS", str(random.randint(1500, 2000))))
        solve_payload.update({
            "request_p": request_p,
            "challenge": challenge,
            "flow": flow,
            "behavior_duration_ms": behavior_ms,
        })

        # 核心算力隔离：获取 PoW 算力槽位（限制同时碰撞的核数，避免高并发 CPU 争抢打架）
        with _POW_SEMAPHORE:
            solved = _run_quickjs_action(
                action="solve",
                sdk_file=sdk_file,
                quickjs_script=quickjs_script,
                payload=solve_payload,
                timeout_ms=timeout_ms,
            )

        so_token_raw = str(solved.get("so_token") or "").strip()

        # SO token 要不要，是**服务端在 challenge 里说了算**的，不是每个 flow 都有。
        # sdk.js 里 SO 采集器的启动条件（去混淆）：
        #     challenge.so.required === true && typeof challenge.so.collector_dx === 'string'
        # 实测 2026-08-06 三个 flow 的 /sentinel/req 响应：
        #     authorize_continue    → 有 so 块, required=true
        #     oauth_create_account  → 有 so 块, required=true
        #     username_password_create → **顶层根本没有 so 键**
        # 也就是说真实浏览器跑 username_password_create 同样不会有 SO token。
        # 以前这里无条件要求 so_token 非空，把「服务端没要」误判成「我们没算出来」，
        # 打出「中止以避免封号」——是误报。更糟的是调用方降级时会沿用上一个 flow 的
        # SO token 继续发，等于给一个明说不需要 SO 的请求塞了个别的 flow 的凭证，
        # 比不发更像异常特征。现在按服务端的要求判定。
        so_required = bool((challenge.get("so") or {}).get("required") is True)

        sdk_token = str(solved.get("token") or "").strip()
        if not sdk_token:
            log("Sentinel QuickJS 失败: SDK token 为空，中止以避免封号")
            return None
        if so_required and not so_token_raw:
            # 服务端确实要了 SO token 但我们没算出来 —— 这才是真异常，保持中止
            log("Sentinel QuickJS 失败: 服务端要求 SO token 但求解为空，中止以避免封号")
            return None
        log(f"Sentinel QuickJS OK (len={len(sdk_token)}, "
            f"so={'Y' if so_token_raw else 'N/A(服务端未要求)'})")
        return (sdk_token, so_token_raw)
    except Exception as e:
        # ⚠️ 这里曾经是个纯 catch-all：任何异常都降级成一行 INFO 日志 + return None，
        #    上层只能看到"主 token 缺失"，真因全被掩盖。2026-08-10 主人批量跑 10 个号，
        #    其中一次失败日志是「Sentinel QuickJS 失败（主 token 缺失…）」，看着像 PoW
        #    算不出来，实际是 /sentinel/req 那个 POST 撞了链路级 TLS 瞬断
        #    （curl:(35)，全局 5.4% 偶发）—— 排查方向被带偏了一整轮。
        #    网络类异常现在原样抛出去，让 registrar 的 classify_error 判成 network，
        #    也让 http_client 的 TLS 重试有机会先兜住；真正的 JS/PoW 问题才 return None。
        from http_client import _is_tls_handshake_error

        if _is_tls_handshake_error(e):
            log(f"Sentinel 网络异常（非 PoW 问题，原样上抛）: {e}")
            raise
        log(f"Sentinel QuickJS 异常: {e}")
        return None
