"""批量导出格式注册表。

**以后要加导出格式，只改这一个文件**：往 `FORMATS` 里加一条就行。
后端路由、前端下拉框都是照着这张表自动长出来的，一行都不用动。

两种 mode：
  - `text`     一行一条记录，前端弹窗预览 + 复制 + 下载（`render` 逐行）
  - `download` 整份文档，前端拿到直接下载、不弹预览（`render_all` 返回 bytes）

约定（主人定的）：
- **不跳行**。勾了几个号就出几行，字段为空就留空，
  分隔符照样保留（`邮箱----`），方便主人自己在文本里对齐、补齐。
- 行序 = 「注册结果」表格里的顺序（created_at 倒序），好核对。

注：CPA / SUB2API 的**手动导出已移除**（2026-08-06）。这两个面板都是
「一次只吃一个号」的接口形态，而且必须先用 refresh_token 把 access_token
换成 Codex 风格才认，手动导出满足不了，实测导出来用不了。
要往这两个面板送号请用**自动推送**（注册完成后由 `exporter.run_exports` 直接 POST），
在「导出配置」里开就行。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExportFormat:
    id: str                                       # 前端 command 用的唯一标识
    label: str                                    # 下拉菜单里显示的名字
    filename: str                                 # 下载的文件名
    mode: str = "text"                            # "text" | "download"
    mime: str = "text/plain; charset=utf-8"
    render: Optional[Callable[[dict], str]] = None          # mode=text：一行记录 -> 一行文本
    render_all: Optional[Callable[[list], bytes]] = None    # mode=download：整批 -> 文件字节
    note: str = ""                                # 下拉菜单里的灰色小字说明


def _s(row: dict, key: str) -> str:
    """取字段并转成干净字符串（None / 非 str 都兜住）。"""
    v = row.get(key)
    if v is None:
        return ""
    return str(v).strip()


def _get_relay_or_pickup_url(r: dict) -> str:
    """提取邮箱取件 URL：优先取 Remail / 中转的 pickup_url，其次取号池表 relay_url。"""
    extra = r.get("extra") if isinstance(r.get("extra"), dict) else {}
    if not extra and r.get("extra_json"):
        try:
            import json as _j
            extra = _j.loads(r["extra_json"])
        except Exception:
            extra = {}
    if isinstance(extra, dict):
        mo = extra.get("mail_oauth") or {}
        if isinstance(mo, dict) and mo.get("pickup_url"):
            return str(mo["pickup_url"]).strip()
    return _s(r, "relay_url")


def get_or_build_cpa_token_data(r: dict) -> dict:
    """提取或生成标准的 CPA (CLI Proxy API) 单文件认证格式（非数组，单个 JSON 对象）。"""
    import json
    from datetime import datetime, timezone, timedelta
    from .exporter import _decode_jwt_payload, _get_auth, _build_compat_id_token

    email = _s(r, "email")
    at = _s(r, "access_token") or _s(r, "accessToken")
    rt = _s(r, "refresh_token") or _s(r, "refreshToken")
    it = _s(r, "id_token") or _s(r, "idToken")

    if isinstance(r.get("user"), dict):
        u = r["user"]
        if not email:
            email = str(u.get("email") or u.get("name") or "").strip()
        if not it:
            it = str(u.get("id_token") or "").strip()

    if not it and at:
        try:
            it = _build_compat_id_token(access_token=at, email=email)
        except Exception:
            it = ""

    payload = _decode_jwt_payload(at) if at else {}
    if not email and payload.get("email"):
        email = str(payload.get("email")).strip()

    auth_info = _get_auth(payload)
    account_id = str(
        auth_info.get("chatgpt_account_id")
        or (r.get("account") or {}).get("id")
        or r.get("account_id")
        or ""
    ).strip()

    tz_cn = timezone(timedelta(hours=8))
    expired_str = ""
    exp = payload.get("exp")
    if isinstance(exp, int) and exp > 0:
        expired_str = datetime.fromtimestamp(exp, tz=tz_cn).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    last_refresh = datetime.now(tz=tz_cn).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    return {
        "type": "codex",
        "email": email,
        "expired": expired_str,
        "id_token": it,
        "account_id": account_id,
        "access_token": at,
        "last_refresh": last_refresh,
        "refresh_token": rt,
    }


def _render_cpa_json_all(rows: list[dict]) -> bytes:
    import json
    import io
    import zipfile

    if not rows:
        return b"{}"

    # 单个账号：直接输出标准单个 JSON 对象（CPAMC 认证文件管理直接可读，不会报 array unmarshal 错误）
    if len(rows) == 1:
        doc = get_or_build_cpa_token_data(rows[0])
        return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")

    # 多个账号：自动打包为 ZIP（内含每个账号独立的 {email}.json 认证文件），CPAMC 可直接一键批量上传或解压拖入
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            em = _s(r, "email") or f"account_{len(zf.namelist()) + 1}"
            doc = get_or_build_cpa_token_data(r)
            zf.writestr(f"{em}.json", json.dumps(doc, ensure_ascii=False, indent=2))
    return buf.getvalue()


def _render_cpa_json_single_line(r: dict) -> str:
    import json
    data = get_or_build_cpa_token_data(r)
    return json.dumps(data, ensure_ascii=False)


def get_or_build_sub2api_account_data(r: dict) -> dict:
    """提取或生成标准的 Sub2API 单账号 JSON 结构 (支持从 Session 数据或 DB 行动态转换)。"""
    import json
    from datetime import datetime, timezone
    from .exporter import _decode_jwt_payload, _get_auth, _build_compat_id_token

    email = _s(r, "email")
    at = _s(r, "access_token") or _s(r, "accessToken")
    rt = _s(r, "refresh_token") or _s(r, "refreshToken")
    it = _s(r, "id_token") or _s(r, "idToken")

    # 若输入本身包含 user 对象 (ChatGPT 官方 session 格式)
    if isinstance(r.get("user"), dict):
        u = r["user"]
        if not email:
            email = str(u.get("email") or u.get("name") or "").strip()
        if not it:
            it = str(u.get("id_token") or "").strip()

    if not it and at:
        try:
            it = _build_compat_id_token(access_token=at, email=email)
        except Exception:
            it = ""

    payload = _decode_jwt_payload(at) if at else {}
    auth_info = _get_auth(payload)
    if not email and payload.get("email"):
        email = str(payload.get("email")).strip()

    chatgpt_account_id = str(
        auth_info.get("chatgpt_account_id")
        or (r.get("account") or {}).get("id")
        or r.get("account_id")
        or ""
    ).strip()
    chatgpt_user_id = str(
        auth_info.get("chatgpt_user_id")
        or auth_info.get("user_id")
        or payload.get("sub")
        or (r.get("user") or {}).get("id")
        or ""
    ).strip()

    plan_type = str(
        r.get("plan_type")
        or (r.get("account") or {}).get("planType")
        or auth_info.get("chatgpt_plan_type")
        or auth_info.get("plan_type")
        or "free"
    ).strip().lower()

    now = datetime.now(timezone.utc)
    now_iso_ms = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    now_ts = now.timestamp()
    exp = payload.get("exp")
    expires_at = datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if exp else ""
    expires_in = max(0, int(exp - now_ts)) if exp else 864000
    email_key = email.replace("@", "_").replace(".", "_")

    return {
        "name": email,
        "platform": "openai",
        "type": "oauth",
        "concurrency": 10,
        "priority": 1,
        "credentials": {
            "access_token": at,
            "refresh_token": rt,
            "id_token": it,
            "chatgpt_account_id": chatgpt_account_id,
            "chatgpt_user_id": chatgpt_user_id,
            "email": email,
            "expires_at": expires_at,
            "expires_in": expires_in,
            "plan_type": plan_type,
        },
        "extra": {
            "email": email,
            "email_key": email_key,
            "name": email,
            "source": "chatgpt_web_session",
            "last_refresh": now_iso_ms,
        },
    }


def _render_sub2api_json_all(rows: list[dict]) -> bytes:
    import json
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    now_iso_ms = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    accounts = []
    for r in rows or []:
        at = _s(r, "access_token") or _s(r, "accessToken")
        rt = _s(r, "refresh_token") or _s(r, "refreshToken")
        if not at and not rt:
            continue
        accounts.append(get_or_build_sub2api_account_data(r))

    root = {
        "exported_at": now_iso_ms,
        "proxies": [],
        "accounts": accounts,
    }
    return json.dumps(root, ensure_ascii=False, indent=2).encode("utf-8")


def get_or_build_session_data(r: dict) -> dict:
    """提取或生成标准的 https://chatgpt.com/api/auth/session 完整数据结构。"""
    import json
    import time
    from .oauth_export import _get_account_claims

    extra = r.get("extra") if isinstance(r.get("extra"), dict) else {}
    if not extra and r.get("extra_json"):
        try:
            extra = json.loads(r["extra_json"])
        except Exception:
            extra = {}
    if isinstance(r.get("session_data"), dict) and r["session_data"]:
        return r["session_data"]
    if isinstance(extra.get("session_data"), dict) and extra["session_data"]:
        return extra["session_data"]

    email = _s(r, "email")
    at = _s(r, "access_token")
    st = _s(r, "session_token")
    claims = _get_account_claims(at) if at else {}

    user_id = claims.get("user_id") or f"user-{email.split('@')[0] if '@' in email else email}"
    account_id = claims.get("account_id") or ""
    plan_type = claims.get("plan_type") or "free"
    name = claims.get("name") or (email.split("@")[0] if "@" in email else email)
    exp_iso = claims.get("exp_iso") or ""

    return {
        "WARNING_BANNER": "!!!!!!!!!!!!!!!!!!!! DO NOT SHARE ANY PART OF THE INFORMATION YOU SEE HERE. THIS INFORMATION IS SENSITIVE AND CAN GRANT ACCESS TO YOUR ACCOUNT. SHARING THIS INFORMATION IS LIKE SHARING YOUR PASSWORD. !!!!!!!!!!!!!!!!!!!!",
        "user": {
            "id": user_id,
            "name": name,
            "email": email,
            "image": "https://cdn.oaistatic.com/assets/favicon-32x32-p60t9m4g.png",
            "picture": "https://cdn.oaistatic.com/assets/favicon-32x32-p60t9m4g.png",
            "idp": "auth0",
            "iat": int(r.get("created_at") or time.time()),
            "mfa": bool(_s(r, "totp_secret")),
        },
        "expires": exp_iso,
        "account": {
            "id": account_id,
            "createdTime": float(r.get("created_at") or time.time()),
            "planType": plan_type,
            "structure": "personal",
            "isUsageBasedSeatEnabled": False,
            "isConversationClassifierEnabledForWorkspace": True,
            "hasFloraFeature": False,
            "isFedrampCompliantWorkspace": False,
            "isDelinquent": False,
            "residencyRegion": "no_constraint",
            "computeResidency": "no_constraint",
        },
        "accessToken": at,
        "authProvider": "openai",
        "sessionToken": st,
    }


def _render_session_json_all(rows: list[dict]) -> bytes:
    import json
    items = [get_or_build_session_data(r) for r in rows or []]
    return json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")


def _render_session_json_single_line(r: dict) -> str:
    import json
    data = get_or_build_session_data(r)
    return json.dumps(data, ensure_ascii=False)


# ──────────────────────── 注册表 ────────────────────────


def convert_session_payload_to_sub2api(data: Any) -> dict:
    """将任意单个或多个 Session 数据 (JSON对象、列表或包含 user/accessToken 的字典) 转换为标准的 Sub2API 导入结构。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    now_iso_ms = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    rows = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        if "accounts" in data and isinstance(data["accounts"], list):
            rows = data["accounts"]
        else:
            rows = [data]

    accounts = []
    for r in rows:
        if isinstance(r, dict):
            acc = get_or_build_sub2api_account_data(r)
            if acc.get("credentials", {}).get("access_token") or acc.get("credentials", {}).get("refresh_token"):
                accounts.append(acc)

    return {
        "exported_at": now_iso_ms,
        "proxies": [],
        "accounts": accounts,
    }


def convert_session_payload_to_cpa(data: Any) -> list[dict]:
    """将任意单个或多个 Session 数据转换为 CPA 对象列表。"""
    rows = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        if "accounts" in data and isinstance(data["accounts"], list):
            rows = data["accounts"]
        else:
            rows = [data]

    cpa_list = []
    for r in rows:
        if isinstance(r, dict):
            cpa = get_or_build_cpa_token_data(r)
            if cpa.get("access_token") or cpa.get("refresh_token"):
                cpa_list.append(cpa)
    return cpa_list


FORMATS: list[ExportFormat] = [
    ExportFormat(
        id="at",
        label="access_token",
        filename="AT.txt",
        render=lambda r: _s(r, "access_token"),
    ),
    ExportFormat(
        id="email_at",
        label="邮箱----AT",
        filename="邮箱AT.txt",
        render=lambda r: f'{_s(r, "email")}----{_s(r, "access_token")}',
    ),
    ExportFormat(
        id="email_pw",
        label="邮箱----密码",
        filename="账号密码.txt",
        render=lambda r: f'{_s(r, "email")}----{_s(r, "password")}',
    ),
    # 2FA secret 只在绑定那一刻下发一次、服务端取不回，丢了这个号就永久锁死，
    # 所以必须有能把它带出去的导出格式。没绑 2FA 的号照约定留空、分隔符保留。
    ExportFormat(
        id="email_pw_2fa",
        label="邮箱----密码----2FA",
        filename="账号密码2FA.txt",
        render=lambda r: (
            f'{_s(r, "email")}----{_s(r, "password")}----{_s(r, "totp_secret")}'
        ),
        note="secret 仅下发一次，取不回，务必留存",
    ),
    # 比上面那条多一段中转取件链接。
    # ⚠️ relay_url 不在 registered 表里，是 db.list_registered_full /
    #    list_registered_by_emails 从号池表（outlook_accounts）LEFT JOIN 带出来的。
    #    所以：① 只有 icloud_relay 这类「一号一条取件链接」的号有值；
    #          ② 号池那行被删掉了就是空 —— 照约定留空、分隔符保留，不跳行。
    #    链接里嵌着 token，等于这个邮箱的收件权限，导出来的文件请当密码保管。
    ExportFormat(
        id="email_pw_2fa_relay",
        label="邮箱----密码----2FA----取件url",
        filename="账号密码2FA取件url.txt",
        render=lambda r: (
            f'{_s(r, "email")}----{_s(r, "password")}----'
            f'{_s(r, "totp_secret")}----{_get_relay_or_pickup_url(r)}'
        ),
        note="取件链接含 token，等同收件权限，妥善保管",
    ),
    # 📦 面板 JSON 格式 (支持直接下载为 CPA / Sub2API 标准 JSON 认证文件)
    ExportFormat(
        id="cpa_json",
        label="📦 CPA 认证文件 (.json 单号 / 批量 .zip 包 · CPAMC专用)",
        filename="cpa_auth_files.zip",
        mode="download",
        mime="application/zip",
        render_all=_render_cpa_json_all,
        note="单号直接下载为 {email}.json；多号为 zip 包（解压后可全选所有 .json 批量拖入 CPAMC）",
    ),
    ExportFormat(
        id="cpa_json_lines",
        label="📦 CPA JSONL (一行一条 JSON 文本 · 脚本专用)",
        filename="cpa_accounts.jsonl",
        mime="application/json; charset=utf-8",
        render=_render_cpa_json_single_line,
        note="每行一个独立 JSON 字符串，供程序脚本解析（请勿直接将多行文件上传到 CPAMC 网页）",
    ),
    ExportFormat(
        id="sub2api_json",
        label="📦 Sub2API JSON (.json 账号导入)",
        filename="sub2api_accounts.json",
        mode="download",
        mime="application/json; charset=utf-8",
        render_all=_render_sub2api_json_all,
        note="Sub2API 标准账号导入 JSON",
    ),
    # 🌐 ChatGPT 官方完整 Session JSON (支持批量导出与复制)
    ExportFormat(
        id="session_json",
        label="🌐 ChatGPT Session JSON (完整 .json 文件)",
        filename="chatgpt_sessions.json",
        mode="download",
        mime="application/json; charset=utf-8",
        render_all=_render_session_json_all,
        note="完整 session 接口结构体 (accessToken + sessionToken + user + account)",
    ),
    ExportFormat(
        id="session_json_lines",
        label="🌐 ChatGPT Session JSON (.json 文件 · 一行一条)",
        filename="chatgpt_sessions.json",
        mime="application/json; charset=utf-8",
        render=_render_session_json_single_line,
        note="每行一个压缩完整的 Session JSON",
    ),
]

_BY_ID = {f.id: f for f in FORMATS}


def list_formats() -> list[dict]:
    """给前端的精简清单（不含 render 函数）。"""
    return [
        {
            "id": f.id,
            "label": f.label,
            "filename": f.filename,
            "mode": f.mode,
            "mime": f.mime,
            "note": f.note,
        }
        for f in FORMATS
    ]


def get_format(fmt_id: str) -> Optional[ExportFormat]:
    return _BY_ID.get((fmt_id or "").strip())


def render_text(rows: list, fmt: "ExportFormat | str") -> str:
    """mode=text：一行一条记录。

    单条渲染炸了不整体失败 —— 那一行留空，其余照常导出。
    """
    f = get_format(fmt) if isinstance(fmt, str) else fmt
    if f is None:
        raise KeyError(f"未知导出格式: {fmt}")
    if not f.render:
        raise RuntimeError(f"格式 {f.id} 不是文本格式")

    lines = []
    for r in rows or []:
        try:
            lines.append(f.render(r))
        except Exception:
            lines.append("")
    return "\n".join(lines)


def render_bytes(rows: list, fmt: "ExportFormat | str") -> bytes:
    """mode=download：整份文件字节。"""
    f = get_format(fmt) if isinstance(fmt, str) else fmt
    if f is None:
        raise KeyError(f"未知导出格式: {fmt}")
    if not f.render_all:
        raise RuntimeError(f"格式 {f.id} 不是下载格式")
    return f.render_all(rows or [])


# 兼容旧调用名
def render(rows: list, fmt: "ExportFormat | str") -> str:
    return render_text(rows, fmt)


def render_chunked(rows: list, fmt: "ExportFormat | str", chunk_size: int) -> bytes:
    """分卷导出：每 chunk_size 条一个文件，全部打进一个 zip 返回。

    - text 格式按行分组（行序不变、不跳行），download 格式按行分组后整组
      走 render_all（如 CPA zip 每卷仍是标准 zip 包，外层再套一层分卷 zip）。
    - 卷内文件名沿用格式 filename 的主干：`AT_001.txt`、`AT_002.txt` …
    """
    import io
    import zipfile

    f = get_format(fmt) if isinstance(fmt, str) else fmt
    if f is None:
        raise KeyError(f"未知导出格式: {fmt}")
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正整数")

    stem, dot, ext = f.filename.rpartition(".")
    if not dot:
        stem, dot, ext = f.filename, ".", ""

    rows = rows or []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        part = 1
        for i in range(0, len(rows), chunk_size):
            grp = rows[i:i + chunk_size]
            if f.mode == "download":
                data = render_bytes(grp, f)
            else:
                data = render_text(grp, f).encode("utf-8")
            zf.writestr(f"{stem}_{part:03d}{dot}{ext}", data)
            part += 1
    return buf.getvalue()


def count_chunks(total: int, chunk_size: int) -> int:
    """分卷后的文件数（chunk_size 为 0/负数按不分卷算 1）。"""
    if chunk_size <= 0:
        return 1
    return max(1, -(-max(0, total) // chunk_size))
