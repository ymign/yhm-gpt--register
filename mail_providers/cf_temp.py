"""Cloudflare Worker 自建域名临时邮箱 provider（dreamhunter2333/cloudflare_temp_email）。

完整兼容 grokcli-2api 的 CF 临时邮箱协议栈：
  1. 智能域名发现：自动请求 /open_api/settings 与 /api/settings，支持配置多域名或留空自动选域。
  2. 智能邮箱创建：优先 POST /admin/new_address（支持 x-admin-auth），自动回退 POST /api/new_address。
  3. 双通道收件机制（Dual-Path Inbox Polling）：
     - 通道 A（Address JWT）：携带 Authorization: Bearer <jwt> 轮询 /api/parsed_mails 与 /api/mails；
     - 通道 B（Admin Auth）：回退携带 x-admin-auth 轮询 /admin/mails?address=<email>。
  4. MIME RFC822 深度解析与 OTP 智能抽取（支持 multipart html/text、HTML <span> 提取与严格防误判正则）。
  5. 快速预读 peek_otp 与自检 self_test。

能力声明：
  pooled = False（动态生成地址，不占号池）
  ephemeral = True（每次新地址，OpenAI 当新号处理）
"""
from __future__ import annotations

import email
import json as _json
import logging
import random
import re
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email import policy
from typing import Any, Optional

from .base import ConfigField, MailProvider, extract_otp, register

logger = logging.getLogger(__name__)

# 默认官方 Workers Demo 示例（提示占位）
CFMAIL_DEFAULT_BASE_URL = "https://mail-api.shaosiming.online"


def _normalize_base_url(base_url: str | None) -> str:
    """标准化 Cloudflare Worker URL，去除尾部斜杠及误粘贴的 /api /admin 路径。"""
    raw = (base_url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlparse(raw)
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}".rstrip("/")
    return origin


def _cf_headers(
    *,
    api_key: str | None = None,
    site_password: str | None = None,
    content_type: bool = True,
) -> dict[str, str]:
    """构造 Cloudflare Temp Email 请求头。

    - Address JWT: Authorization: Bearer <jwt>
    - Admin Password: x-admin-auth
    - Site Password: x-custom-auth
    """
    headers = {
        "accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
    }
    if content_type:
        headers["content-type"] = "application/json"
    key = (api_key or "").strip()
    if key:
        parts = key.split(".")
        if len(parts) == 3 and all(parts):
            headers["Authorization"] = f"Bearer {key}"
        else:
            headers["x-admin-auth"] = key
    site = (site_password or "").strip()
    if site:
        headers["x-custom-auth"] = site
    return headers


def cf_list_domains(
    api_url: str,
    admin_token: str = "",
    site_password: str = "",
    timeout: float = 15.0,
) -> list[str]:
    """从 Worker 探测并获取可用域名列表（GET /open_api/settings & GET /api/settings）。"""
    base = _normalize_base_url(api_url)
    if not base:
        return []
    headers = _cf_headers(api_key=admin_token, site_password=site_password, content_type=False)
    data: dict[str, Any] = {}

    for path in ("/open_api/settings", "/api/settings"):
        url = f"{base}{path}"
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", errors="replace")
                d = _json.loads(text) if text else {}
                if isinstance(d, dict):
                    data = d
                    break
        except Exception:
            continue

    body = data.get("data") if isinstance(data, dict) and "data" in data else data
    if not isinstance(body, dict):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for k in (
        "defaultDomains",
        "default_domains",
        "domains",
        "randomSubdomainDomains",
        "random_subdomain_domains",
    ):
        items = body.get(k)
        if isinstance(items, str):
            items = [x.strip() for x in items.split(",") if x.strip()]
        if not isinstance(items, list):
            continue
        for item in items:
            name = item.get("domain") or item.get("name") or item.get("value") if isinstance(item, dict) else item
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip().lstrip("@").strip(".")
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def _parse_rfc822_raw(raw: str) -> dict[str, Any]:
    """对 CF Temp Email 原生 raw 邮件进行 RFC822 MIME 解析。"""
    out: dict[str, Any] = {}
    text = (raw or "").strip()
    if not text:
        return out
    try:
        msg = email.message_from_string(text, policy=policy.default)
    except Exception:
        out["text"] = text[:8000]
        return out

    out["subject"] = str(msg.get("subject") or "")
    out["from"] = str(msg.get("from") or "")
    out["to"] = str(msg.get("to") or "")
    texts: list[str] = []
    htmls: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = str(part.get_content_disposition() or "").lower()
            if disp == "attachment":
                continue
            try:
                payload = part.get_content()
            except Exception:
                try:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        payload = payload.decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace",
                        )
                except Exception:
                    payload = None
            if not isinstance(payload, str):
                continue
            if ctype == "text/html":
                htmls.append(payload)
            elif ctype.startswith("text/"):
                texts.append(payload)
    else:
        try:
            payload = msg.get_content()
        except Exception:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                payload = payload.decode(
                    msg.get_content_charset() or "utf-8", errors="replace"
                )
        if isinstance(payload, str):
            if (msg.get_content_type() or "").lower() == "text/html":
                htmls.append(payload)
            else:
                texts.append(payload)

    if texts:
        out["text"] = "\n".join(texts)
    if htmls:
        out["html"] = "\n".join(htmls)
    if not texts and not htmls:
        out["text"] = text[:8000]
    return out


def _gen_local_part(rng: Optional[random.Random] = None, length: int = 10) -> str:
    """生成随机邮箱前缀（10 位字母+数字）。"""
    r = rng or random
    chars = string.ascii_lowercase + string.digits
    return "".join(r.choices(chars, k=length))


# 导出别名保持兼容
_extract_otp = extract_otp


@register
class CFTempEmailProvider(MailProvider):
    """Cloudflare Worker 自建域名临时邮箱 Provider。

    支持配置：
        cf_api_url: Worker 地址（如 https://mail-api.shaosiming.online）
        cf_admin_token: Worker 环境变量 ADMIN_PASSWORDS
        cf_domain: catch-all 域名（如 shaosiming.online，留空可自动探测）
        cf_site_password: 可选站点自定义密码（x-custom-auth）
    """

    kind = "cf_temp"
    display_name = "CF Worker 域名临时邮箱"
    pooled = False         # 动态生成地址，无限量
    ephemeral = True       # 每次新地址 → OpenAI 识别为全新用户

    line_segments = 0
    import_hint = ""
    import_placeholder = ""

    config_fields = [
        ConfigField(
            "cf_api_url", "Worker API 地址",
            placeholder="https://mail-api.shaosiming.online",
            help="Cloudflare Worker HTTPS 地址，如 https://mail-api.shaosiming.online",
        ),
        ConfigField(
            "cf_admin_token", "Admin Token / 密钥", type="password",
            help="Worker 环境变量 ADMIN_PASSWORDS 的值（如 sayd82k4lzbmp6g3）",
        ),
        ConfigField(
            "cf_domain", "收件域名",
            placeholder="shaosiming.online",
            required=False,
            help="Worker 配置的收信域名（如 shaosiming.online，留空则自动从 Worker 获取）",
        ),
    ]

    def __init__(
        self,
        api_url: str,
        admin_token: str = "",
        domain: str = "",
        site_password: str = "",
        session=None,
    ):
        norm_url = _normalize_base_url(api_url)
        if not norm_url:
            raise ValueError("Worker API 地址 (api_url) 不能为空")
        self.api_url = norm_url
        self.admin_token = (admin_token or "").strip()
        self.domain = (domain or "").strip().lstrip("@").strip(".")
        self.site_password = (site_password or "").strip()
        self._jwt: str = ""
        self._current_email: str = ""
        self._seen_mail_ids: set = set()
        self._rng = random.Random()
        self.last_persona = None

        if session is not None:
            self._session = session
        else:
            try:
                from curl_cffi.requests import Session as CffiSession
                self._session = CffiSession(impersonate="chrome136")
                self._session.trust_env = False
            except ImportError:
                self._session = None

    # ──────────────────────── 构造入口 ────────────────────────

    @classmethod
    def from_config(cls, settings: dict, account: Optional[dict] = None):
        api_url = (settings.get("cf_api_url") or settings.get("cfmail_base_url") or settings.get("base_url") or "").strip()
        token = (settings.get("cf_admin_token") or settings.get("cfmail_api_key") or settings.get("api_key") or "").strip()
        domain = (settings.get("cf_domain") or settings.get("cfmail_domain") or settings.get("domain") or "").strip()
        site_pw = (settings.get("cf_site_password") or settings.get("site_password") or "").strip()

        if not api_url:
            raise RuntimeError(
                "CF Temp Email 未配置 Worker 地址 (cf_api_url)，请前往「邮箱配置」填写"
            )
        return cls(
            api_url=api_url,
            admin_token=token,
            domain=domain,
            site_password=site_pw,
        )

    # ──────────────────────── HTTP 请求封装 ────────────────────────

    def _http_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[dict] = None,
        custom_headers: Optional[dict] = None,
        timeout: float = 20.0,
    ) -> tuple[int, dict, str]:
        """统一 HTTP 请求，优先 curl_cffi，回退 urllib。返回 (status_code, json_dict, raw_text)。"""
        url = f"{self.api_url}{path}"
        m = method.upper()
        headers = dict(custom_headers or _cf_headers(
            api_key=self.admin_token,
            site_password=self.site_password,
            content_type=(json_body is not None),
        ))

        # 尝试 curl_cffi
        if self._session is not None:
            try:
                if m == "GET":
                    resp = self._session.get(url, headers=headers, params=params, timeout=timeout)
                elif json_body is not None:
                    resp = self._session.post(
                        url,
                        headers=headers,
                        data=_json.dumps(json_body, separators=(",", ":")),
                        timeout=timeout,
                    )
                else:
                    resp = self._session.post(url, headers=headers, timeout=timeout)

                status = int(resp.status_code)
                text = resp.text or ""
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                return status, data, text
            except Exception as e:
                logger.debug(f"[cf_temp] curl_cffi 请求异常，转入 urllib: {e}")

        # urllib 兜底
        full_url = url
        if params:
            qs = urllib.parse.urlencode(params)
            full_url = f"{url}?{qs}"
        body_bytes = _json.dumps(json_body).encode("utf-8") if json_body is not None else None
        req = urllib.request.Request(full_url, data=body_bytes, headers=headers, method=m)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status = int(r.status)
                text = r.read().decode("utf-8", errors="replace")
                try:
                    data = _json.loads(text)
                except Exception:
                    data = {}
                return status, data, text
        except urllib.error.HTTPError as e:
            status = int(e.code)
            try:
                text = e.read().decode("utf-8", errors="replace")
            except Exception:
                text = ""
            try:
                data = _json.loads(text)
            except Exception:
                data = {}
            return status, data, text
        except Exception as e:
            logger.warning(f"[cf_temp] 网络请求异常 ({m} {path}): {e}")
            return 0, {}, str(e)

    @staticmethod
    def _mail_epoch(mail: dict) -> Optional[float]:
        """解析邮件创建时间的 UTC epoch 秒。"""
        raw = (mail.get("created_at") or mail.get("createdAt") or "").strip()
        if not raw:
            return None
        raw = raw.replace("T", " ").replace("Z", "").split(".")[0]
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            return None

    # ──────────────────────── 核心邮箱操作 ────────────────────────

    def resolve_domain(self) -> str:
        """确保收件域名可用：若未配置则自动从 Worker settings 获取。"""
        if self.domain:
            return self.domain
        domains = cf_list_domains(
            self.api_url,
            admin_token=self.admin_token,
            site_password=self.site_password,
        )
        if domains:
            self.domain = domains[0]
            logger.info(f"[cf_temp] 自动解析并选用 Worker 域名: {self.domain}")
            return self.domain
        raise RuntimeError(
            "Cloudflare Temp Email 未配置收件域名，且无法从 Worker 获取可用域名列表，"
            "请前往「邮箱配置」填写收件域名。"
        )

    def create_mailbox(self) -> str:
        """创建一个新邮箱地址：优先 POST /admin/new_address，回退 POST /api/new_address。"""
        dom = self.resolve_domain()
        local = _gen_local_part(self._rng, length=10)
        payload = {
            "name": local,
            "domain": dom,
            "enablePrefix": True,
        }

        # 优先使用 admin 接口创建
        status = 0
        data: dict = {}
        text = ""

        if self.admin_token:
            admin_headers = _cf_headers(
                api_key=self.admin_token,
                site_password=self.site_password,
                content_type=True,
            )
            status, data, text = self._http_request(
                "POST", "/admin/new_address",
                json_body=payload,
                custom_headers=admin_headers,
                timeout=15,
            )

        if status not in (200, 201):
            # 回退到 public api 创建
            pub_headers = _cf_headers(
                api_key=self.admin_token,
                site_password=self.site_password,
                content_type=True,
            )
            status, data, text = self._http_request(
                "POST", "/api/new_address",
                json_body=payload,
                custom_headers=pub_headers,
                timeout=15,
            )

        if status not in (200, 201):
            raise RuntimeError(
                f"CFTempEmail create_mailbox 失败 (status={status}): {text[:300]}"
            )

        body = data.get("data") if isinstance(data, dict) and "data" in data else data
        if not isinstance(body, dict):
            raise RuntimeError(f"CFTempEmail 创建响应格式异常: {data}")

        address = (
            body.get("address")
            or body.get("email")
            or body.get("mail")
            or body.get("name")
            or ""
        )
        jwt = (
            body.get("jwt")
            or body.get("token")
            or body.get("credential")
            or body.get("address_jwt")
            or ""
        )

        if not address or "@" not in str(address):
            if local and dom:
                address = f"{local}@{dom}"

        if not address or "@" not in str(address):
            raise RuntimeError(f"new_address 响应未返回完整邮箱地址: {data}")

        self._jwt = str(jwt or "")
        self._current_email = str(address).strip()
        self._seen_mail_ids = set()

        logger.info(
            f"[cf_temp] 成功生成临时邮箱: {self._current_email} "
            f"(jwt_len={len(self._jwt)})"
        )
        return self._current_email

    def _fetch_mails_jwt(self, limit: int = 20) -> list[dict]:
        """通道 A：使用 Address JWT (Bearer) 拉取收件箱邮件。"""
        if not self._jwt:
            return []
        headers = _cf_headers(api_key=self._jwt, site_password=self.site_password, content_type=False)
        out: list[dict] = []

        # 1. 尝试 /api/parsed_mails
        status, data, _ = self._http_request(
            "GET", "/api/parsed_mails",
            params={"limit": limit, "offset": 0},
            custom_headers=headers,
            timeout=10,
        )
        items = []
        if status == 200:
            body = data.get("data") if isinstance(data, dict) and "data" in data else data
            if isinstance(body, dict):
                items = body.get("results") or body.get("mails") or body.get("items") or []
            elif isinstance(body, list):
                items = body

        # 2. 若 parsed_mails 为空或不可用，尝试 /api/mails
        if not items:
            status, data, _ = self._http_request(
                "GET", "/api/mails",
                params={"limit": limit, "offset": 0},
                custom_headers=headers,
                timeout=10,
            )
            if status == 200:
                body = data.get("data") if isinstance(data, dict) and "data" in data else data
                if isinstance(body, dict):
                    items = body.get("results") or body.get("mails") or body.get("items") or []
                elif isinstance(body, list):
                    items = body

        if not isinstance(items, list):
            return []

        for raw in items[:limit]:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            msg_id = item.get("id") or item.get("mail_id") or item.get("message_id")
            # 若缺失正文，请求详情
            if msg_id and not (item.get("content") or item.get("html") or item.get("raw")):
                for detail_path in (f"/api/mail/{msg_id}", f"/api/mails/{msg_id}", f"/api/raw_mail/{msg_id}"):
                    d_status, d_data, _ = self._http_request("GET", detail_path, custom_headers=headers, timeout=8)
                    if d_status == 200:
                        d_body = d_data.get("data") if isinstance(d_data, dict) and "data" in d_data else d_data
                        if isinstance(d_body, dict):
                            item.update(d_body)
                        elif isinstance(d_body, str):
                            item.setdefault("raw", d_body)
                        break

            # RFC822 MIME 解析
            raw_rfc = item.get("raw") or item.get("message") or item.get("content") or ""
            if isinstance(raw_rfc, str) and ("\n" in raw_rfc or "From:" in raw_rfc or "Subject:" in raw_rfc):
                parsed = _parse_rfc822_raw(raw_rfc)
                for k, v in parsed.items():
                    item.setdefault(k, v)
                item.setdefault("raw", raw_rfc)

            out.append(item)
        return out

    def _fetch_mails_admin(self, email_addr: str, limit: int = 20) -> list[dict]:
        """通道 B：使用 Admin Auth (x-admin-auth) 从管理端直接检索指定邮箱。"""
        if not self.admin_token:
            return []
        headers = _cf_headers(api_key=self.admin_token, site_password=self.site_password, content_type=False)
        target_addr = email_addr.strip().lower()

        status, data, _ = self._http_request(
            "GET", "/admin/mails",
            params={"limit": limit, "offset": 0, "address": target_addr},
            custom_headers=headers,
            timeout=10,
        )
        if status != 200:
            # 兼容不认 address 参数的旧部署
            status, data, _ = self._http_request(
                "GET", "/admin/mails",
                params={"limit": limit, "offset": 0},
                custom_headers=headers,
                timeout=10,
            )

        if status != 200:
            return []

        body = data.get("data") if isinstance(data, dict) and "data" in data else data
        items = []
        if isinstance(body, dict):
            items = body.get("results") or body.get("mails") or body.get("items") or []
        elif isinstance(body, list):
            items = body

        if not isinstance(items, list):
            return []

        out: list[dict] = []
        for raw in items[:limit]:
            if not isinstance(raw, dict):
                continue
            item_addr = str(raw.get("address") or raw.get("to") or "").strip().lower()
            if target_addr and item_addr and target_addr not in item_addr and item_addr != target_addr:
                continue
            item = dict(raw)
            raw_rfc = item.get("raw") or item.get("message") or item.get("content") or ""
            if isinstance(raw_rfc, str) and ("\n" in raw_rfc or "From:" in raw_rfc or "Subject:" in raw_rfc):
                parsed = _parse_rfc822_raw(raw_rfc)
                for k, v in parsed.items():
                    item.setdefault(k, v)
            out.append(item)
        return out

    def _get_mails(self, email_addr: str) -> list[dict]:
        """双通道融合拉取邮件：先查 JWT 通道，若为空则回退 Admin 通道。"""
        mails = self._fetch_mails_jwt(limit=20)
        if not mails and self.admin_token:
            mails = self._fetch_mails_admin(email_addr, limit=20)
        return mails

    def _extract_otp_from_mail(self, mail: dict) -> Optional[str]:
        """从邮件字典中综合提取 6 位 OTP。"""
        # 1. 尝试从 raw 抽
        raw = str(mail.get("raw") or "")
        otp = extract_otp(raw) if raw else None
        if otp:
            return otp

        # 2. 尝试从 html / text / content 抽
        content = "\n".join(
            str(mail.get(k) or "") for k in ("html", "text", "content", "subject")
        )
        return extract_otp(content) if content.strip() else None

    # ──────────────────────── OTP 获取与等待 ────────────────────────

    def peek_otp(
        self,
        email_addr: str,
        issued_after: Optional[float] = None,
        wait: float = 0.0,
    ) -> Optional[str]:
        """非破坏性预读：若收件箱已躺着本轮验证码则直接返回。"""
        deadline = time.time() + max(0.0, float(wait))
        while True:
            try:
                mails = self._get_mails(email_addr)
                for mail in sorted(mails, key=lambda x: int(x.get("id", 0) or 0), reverse=True):
                    mid = str(mail.get("id", ""))
                    if not mid or mid in self._seen_mail_ids:
                        continue
                    if issued_after is not None:
                        ts = self._mail_epoch(mail)
                        if ts is not None and ts < issued_after - 2:
                            continue
                    otp = self._extract_otp_from_mail(mail)
                    if otp:
                        logger.info(f"[cf_temp] 👀 预读命中 OTP={otp} (mail_id={mid})")
                        return otp
            except Exception as e:
                logger.debug(f"[cf_temp] peek 异常: {e}")

            if time.time() >= deadline:
                return None
            time.sleep(1.0)

    def wait_for_otp(
        self,
        email_addr: str,
        timeout: int = 120,
        issued_after: Optional[float] = None,
    ) -> str:
        """轮询收件箱等待 6 位 OTP 验证码。"""
        timeout = max(int(timeout), 60)
        deadline = time.time() + timeout
        logger.info(f"[cf_temp] 等待 OTP 邮件 -> {email_addr} (timeout={timeout}s)")

        # 记录初始历史邮件 id
        try:
            init_mails = self._get_mails(email_addr)
            for m in init_mails:
                mid = str(m.get("id", ""))
                if not mid:
                    continue
                if issued_after is not None:
                    ts = self._mail_epoch(m)
                    if ts is not None and ts >= issued_after - 2:
                        continue
                self._seen_mail_ids.add(mid)
        except Exception as e:
            logger.warning(f"[cf_temp] 初始邮件快照拉取异常: {e}")

        while time.time() < deadline:
            try:
                mails = self._get_mails(email_addr)
                for mail in sorted(mails, key=lambda x: int(x.get("id", 0) or 0), reverse=True):
                    mid = str(mail.get("id", ""))
                    if not mid or mid in self._seen_mail_ids:
                        continue
                    self._seen_mail_ids.add(mid)

                    otp = self._extract_otp_from_mail(mail)
                    if otp:
                        logger.info(f"[cf_temp] ✅ 成功获取 OTP={otp} (mail_id={mid})")
                        return otp
            except Exception as e:
                logger.warning(f"[cf_temp] poll 收信异常: {e}")
            time.sleep(2.5)

        raise TimeoutError(f"CFTempEmail 等待 OTP 超时 ({timeout}s) - {email_addr}")

    # ──────────────────────── 连通性自检 ────────────────────────

    def self_test(self) -> dict:
        """测试 Worker 连通性、域名解析与创建邮箱。"""
        try:
            domains = cf_list_domains(
                self.api_url,
                admin_token=self.admin_token,
                site_password=self.site_password,
            )
            test_email = self.create_mailbox()
            # 探测收件箱接口
            mails = self._get_mails(test_email)
            dom_text = ", ".join(domains) if domains else (self.domain or "默认")
            return {
                "ok": True,
                "message": (
                    f"✅ Cloudflare Worker 连通正常！\n"
                    f"可用域名: [{dom_text}]\n"
                    f"测试邮箱: {test_email}\n"
                    f"收件通道握手: 正常 (拉取到 {len(mails)} 封)"
                ),
                "domains": domains,
                "email": test_email,
            }
        except Exception as e:
            return {"ok": False, "message": f"❌ Cloudflare Worker 测试失败: {e}"}
