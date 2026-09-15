"""RT 极速刷新。从主工程 webui/token_refresh_service.refresh_token_fast 原样抽出。"""
from __future__ import annotations

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_SCOPE = "openid email profile offline_access"
OPENAI_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"


def refresh_token_fast(
    refresh_token: str,
    proxy: str = "",
    timeout: float = 25.0,
    client_id: str = CODEX_CLIENT_ID,
    impersonate: str = "",
    user_agent: str = "",
) -> dict:
    rt = str(refresh_token or "").strip()
    if not rt:
        raise ValueError("缺少 refresh_token")

    from http_client import create_http_session

    imp = (impersonate or "chrome142").strip() or "chrome142"
    ua = (user_agent or "").strip() or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    )
    session = create_http_session(proxy=proxy or None, impersonate=imp, user_agent=ua)
    body = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": rt,
        "scope": CODEX_SCOPE,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Origin": "https://auth.openai.com",
        "Referer": "https://auth.openai.com/",
        "User-Agent": ua,
    }
    resp = session.post(
        OPENAI_TOKEN_ENDPOINT,
        headers=headers,
        data=body,
        timeout=timeout,
    )
    if resp.status_code != 200:
        err_msg = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
        raise RuntimeError(f"RT 换取 Token 失败 (HTTP {resp.status_code}): {err_msg}")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError("OpenAI 返回非 JSON 数据")
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError("OpenAI 响应中未包含 access_token")
    return data
