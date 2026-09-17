"""Gmail 加号分裂号池。

导入一行母号：
    sumeetkafle71@gmail.com----https://gapi.mailsapi.com/api/get-code?uid=...

入库时写入主号本身 + 5 个别名（local+5位随机字母数字@gmail.com），共用同一个取码 URL。
领号优先主号。同一母号同时只允许一个 worker 在用，避免 OTP 串号。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .base import MailProvider, MailProviderError, register, validate_email

logger = logging.getLogger(__name__)

_RE_OTP6 = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_WAITING_STATUS = {601, 204, 404}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
_OTP_LOG_PATH = Path(__file__).resolve().parents[1] / "临时日志.txt"
_otp_log_lock = threading.Lock()


def append_otp_temp_log(email: str, otp: str, *, base_email: str = "", note: str = "") -> None:
    """把邮箱验证码追加到仓库根目录 临时日志.txt（不入库、不提交）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    em = (email or "").strip().lower()
    base = (base_email or "").strip().lower()
    role = "主号" if em and base and em == base else ("子号" if base else "邮箱")
    parts = [ts, "[gmail_split]", role, em, f"验证码={otp}"]
    if base and em != base:
        parts.append(f"母号={base}")
    if note:
        parts.append(note)
    line = "  ".join(parts) + "\n"
    try:
        with _otp_log_lock:
            with open(_OTP_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        logger.debug("[gmail_split] 写临时日志失败", exc_info=True)


def _is_http_status(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 100 <= value <= 999
    if isinstance(value, str) and value.isdigit():
        n = int(value)
        return 100 <= n <= 999
    return False


def extract_mailsapi_otp(payload: Any) -> Optional[str]:
    """从 mailsapi JSON / 纯文本里抠 6 位码。601 等待态返回 None。"""
    if payload is None:
        return None
    if isinstance(payload, bool):
        return None
    if isinstance(payload, (int, float)):
        if _is_http_status(int(payload)):
            return None
        s = str(int(payload))
        return s if len(s) == 6 and s.isdigit() else None
    if isinstance(payload, str):
        s = payload.strip()
        if not s:
            return None
        if s.isdigit() and len(s) == 6:
            return s
        try:
            return extract_mailsapi_otp(json.loads(s))
        except Exception:
            m = _RE_OTP6.search(s)
            return m.group(1) if m else None
    if isinstance(payload, list):
        for item in payload:
            otp = extract_mailsapi_otp(item)
            if otp:
                return otp
        return None
    if not isinstance(payload, dict):
        return None

    api_code = payload.get("code")
    data = payload.get("data")
    message = str(payload.get("message") or "")
    waiting = (
        api_code in _WAITING_STATUS
        or str(api_code) in {"601", "204", "404"}
        or "wait" in message.lower()
    )
    if waiting and data in (None, "", [], {}):
        return None

    for key in (
        "otp",
        "verification_code",
        "verifyCode",
        "verify_code",
        "emailCode",
        "email_code",
        "data",
        "result",
        "code",
        "msg",
        "message",
    ):
        val = payload.get(key)
        if val is None:
            continue
        if key in ("code", "msg", "message") and _is_http_status(val):
            continue
        otp = extract_mailsapi_otp(val)
        if otp:
            return otp
    return None


@register
class GmailSplitProvider(MailProvider):
    """Gmail plus-address 号池，取码走 mailsapi get-code。"""

    kind = "gmail_split"
    display_name = "🇬 谷歌邮箱（加号分裂）"
    pooled = True
    ephemeral = False
    accepts_existing_account = False

    line_segments = 2
    import_hint = "每行一个母号：email----取码链接；导入主号 + 5 个 +别名，注册优先主号"
    import_placeholder = (
        "sumeetkafle71@gmail.com----https://gapi.mailsapi.com/api/get-code?uid=s41c064bfeba634cabd"
    )
    config_fields = []

    def __init__(self, email: str, code_url: str, base_email: str = "", timeout: int = 15):
        email = (email or "").strip().lower()
        code_url = (code_url or "").strip()
        if not email:
            raise ValueError("Gmail 地址不能为空")
        validate_email(email)
        if not code_url.lower().startswith(("http://", "https://")):
            raise ValueError("取码链接必须是 http(s):// 开头")
        self.email = email
        self.code_url = code_url
        self.base_email = (base_email or "").strip().lower() or email.split("+", 1)[0] + "@" + email.split("@", 1)[1]
        self.http_timeout = timeout
        self.last_persona = None
        self._seen_codes: set[str] = set()
        self._snapshot_done = False

    @classmethod
    def from_config(cls, settings: dict, account: Optional[dict] = None):
        if not account:
            raise MailProviderError(
                "谷歌邮箱是号池型：请先到「导入邮箱」粘贴 邮箱----取码链接",
                fatal=False,
                kind=cls.kind,
            )
        email = (account.get("email") or "").strip()
        url = (account.get("code_url") or account.get("relay_url") or "").strip()
        if not url:
            raise MailProviderError(
                f"号池里的 {email} 没有取码链接，请按 email----取码链接 重新导入",
                fatal=True,
                kind=cls.kind,
            )
        try:
            return cls(
                email=email,
                code_url=url,
                base_email=account.get("base_email") or "",
            )
        except ValueError as e:
            raise MailProviderError(str(e), fatal=True, kind=cls.kind) from e

    @classmethod
    def parse_line(cls, line: str) -> dict:
        parts = [p.strip() for p in (line or "").replace("|", "----").replace("\t", "----").split("----")]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            raise ValueError("需要 2 段（email----取码链接）")
        email, url = parts[0].lower(), parts[1]
        validate_email(email)
        domain = email.split("@", 1)[1]
        if domain not in ("gmail.com", "googlemail.com"):
            raise ValueError("只支持 gmail.com / googlemail.com")
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("第 2 段必须是 http(s):// 开头的取码链接")
        return {
            "email": email,
            "kind": cls.kind,
            "relay_url": url,
            "code_url": url,
            "base_email": email,
        }

    def create_mailbox(self) -> str:
        logger.info(
            "[gmail_split] 使用别名 %s（母号 %s）",
            self.email,
            self.base_email,
        )
        return self.email

    def _fetch_payload(self) -> Any:
        req = urllib.request.Request(
            self.code_url,
            headers={"User-Agent": _UA, "Accept": "application/json, text/plain, */*"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.http_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if e.code in (401, 403, 404, 410):
                raise MailProviderError(
                    f"取码链接失效 HTTP {e.code}（{self.base_email}）{body[:80]}",
                    fatal=True,
                    kind=self.kind,
                ) from e
            raise MailProviderError(
                f"取码接口 HTTP {e.code}：{body[:120] or e.reason}",
                fatal=False,
                kind=self.kind,
            ) from e
        except urllib.error.URLError as e:
            raise MailProviderError(
                f"取码接口网络错误：{e.reason}",
                fatal=False,
                kind=self.kind,
            ) from e
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def _poll_otp(self) -> Optional[str]:
        payload = self._fetch_payload()
        otp = extract_mailsapi_otp(payload)
        if otp and otp not in self._seen_codes:
            return otp
        return None

    def peek_otp(
        self,
        email_addr: str,
        issued_after: Optional[float] = None,
        wait: float = 0.0,
    ) -> Optional[str]:
        if wait > 0:
            time.sleep(wait)
        try:
            return self._poll_otp()
        except MailProviderError:
            raise
        except Exception as e:
            logger.debug("[gmail_split] peek 失败: %s", e)
            return None

    def wait_for_otp(
        self,
        email_addr: str,
        timeout: int = 120,
        issued_after: Optional[float] = None,
    ) -> str:
        timeout = max(int(timeout or 120), 60)
        deadline = time.time() + timeout
        logger.info(
            "[gmail_split] 等待 OTP -> %s 母号=%s timeout=%ss",
            email_addr,
            self.base_email,
            timeout,
        )
        if not self._snapshot_done:
            try:
                payload = self._fetch_payload()
                stale = extract_mailsapi_otp(payload)
                if stale:
                    self._seen_codes.add(stale)
                    logger.info("[gmail_split] 忽略取码接口里已有的旧码 %s", stale)
                    append_otp_temp_log(
                        email_addr, stale,
                        base_email=self.base_email,
                        note="忽略旧码",
                    )
            except MailProviderError:
                raise
            except Exception as e:
                logger.warning("[gmail_split] 初始快照失败: %s", e)
            self._snapshot_done = True

        last_log = 0.0
        while time.time() < deadline:
            try:
                otp = self._poll_otp()
            except MailProviderError as e:
                if e.fatal:
                    raise
                logger.warning("[gmail_split] 取码暂失败（将重试）: %s", e)
                otp = None
            if otp:
                self._seen_codes.add(otp)
                logger.info("[gmail_split] ✅ OTP=%s 邮箱=%s", otp, email_addr)
                append_otp_temp_log(email_addr, otp, base_email=self.base_email)
                return otp
            now = time.time()
            if now - last_log >= 10:
                logger.info(
                    "[gmail_split] ⏳ 等待验证码 %ss / %ss（%s）",
                    int(now - (deadline - timeout)),
                    timeout,
                    email_addr,
                )
                last_log = now
            time.sleep(2.5)

        raise TimeoutError(
            f"谷歌邮箱取码超时 {timeout}s（{email_addr} / 母号 {self.base_email}）"
        )

    def self_test(self) -> dict:
        try:
            payload = self._fetch_payload()
        except MailProviderError as e:
            return {"ok": False, "message": str(e)}
        except Exception as e:
            return {"ok": False, "message": f"取码链接不可用: {e}"}
        otp = extract_mailsapi_otp(payload)
        if otp:
            return {"ok": True, "message": f"取码链接可用，当前已有验证码 {otp}"}
        return {
            "ok": True,
            "message": f"取码链接可用，正在等待验证码（{self.email}）",
        }
