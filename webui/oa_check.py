"""OAICS 资格检测任务管理器（集成 PP链_oaics账号批量筛选_fast.py 的探测逻辑）。

对注册结果表里勾选的账号批量做 OpenAI Checkout 资格检测：
    - 每个账号用自己的 access_token 打 POST /backend-api/payments/checkout
    - 判断返回 session id 是 oaics_（可转卖资格）还是 cs_ / 其他
    - 代理池按顺序轮流分配，多 worker 并发
    - 进度通过 queue 广播，app.py 用 SSE 推给前端
    - 每个账号的结果写回 registered.oa_check 列（JSON）

探测核心逻辑与 PP链_oaics账号批量筛选_fast.py 保持一致
（默认账单国家 DE / 币种 EUR / 代理出口 BR / UI custom / 促销默认关）。
"""
from __future__ import annotations

import base64
import json
import queue
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlsplit, urlunsplit

try:
    from curl_cffi.requests import Session as CurlSession
except ImportError:  # pragma: no cover
    CurlSession = None  # type: ignore[assignment]

from . import db  # noqa: E402

CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
CLIENT_VERSION = "prod-db390ebea64862bf1899c420a4c736e0cf639747"
CLIENT_BUILD = "7904904"

DEFAULT_BILLING = "DE"
DEFAULT_CURRENCY = "EUR"
DEFAULT_PROXY_COUNTRY = "BR"

_MAX_WORKERS = 20


# ════════════════════════ 探测核心（与 PP链脚本一致） ════════════════════════


@dataclass
class ProbeResult:
    ok: bool
    state: str  # OAICS / CS / OAIC / NONE / ERROR / UNKNOWN
    session_id: str = ""
    session_id_masked: str = ""
    processor_entity: str = ""
    billing_country: str = DEFAULT_BILLING
    currency: str = DEFAULT_CURRENCY
    proxy_country: str = DEFAULT_PROXY_COUNTRY
    proxy: str = ""
    http_status: int = 0
    elapsed_ms: int = 0
    proxy_check_ms: int = 0
    checkout_ms: int = 0
    error: str = ""
    attempt: int = 1
    account_email: str = ""


def mask(value: str, keep_start: int = 10, keep_end: int = 6) -> str:
    text = str(value or "")
    if len(text) <= keep_start + keep_end + 3:
        return text
    return f"{text[:keep_start]}***{text[-keep_end:]}"


def safe_close(session: Any) -> None:
    if session is None:
        return
    try:
        session.close()
    except Exception:
        pass


def normalize_proxy_url(proxy: str) -> str:
    """Accept user:pass@host:port, user:pass:host:port, host:port:user:pass, http://..."""
    proxy = str(proxy or "").strip()
    if not proxy or proxy.startswith("#"):
        return ""
    if "://" in proxy:
        return proxy
    if "@" in proxy:
        return f"http://{proxy}"

    # user:pass:host:port  (password may contain -CC-session-ttl)
    raw_parts = proxy.rsplit(":", 2)
    if len(raw_parts) == 3 and raw_parts[2].isdigit() and ":" in raw_parts[0]:
        credentials, host, port = raw_parts
        username, password = credentials.split(":", 1)
        if username and password and host:
            return (
                f"http://{quote(username, safe='-._~')}:"
                f"{quote(password, safe='-._~')}@{host}:{port}"
            )

    # host:port:user:pass
    parts = proxy.split(":", 3)
    if len(parts) == 4 and parts[1].isdigit() and "@" not in proxy:
        host, port, username, password = parts
        return (
            f"http://{quote(username, safe='-._~')}:"
            f"{quote(password, safe='-._~')}@{host}:{port}"
        )
    return f"http://{proxy}"


def proxy_url_with_credentials(parsed: Any, username: str, password: str) -> str:
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"{quote(username, safe='-._~')}:{quote(password, safe='-._~')}@{host}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


def new_proxy_session_id() -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choices(chars, k=8))


def route_proxy_country(proxy: str, country: str, session_id: str = "") -> str:
    """智能重写 sticky 代理国家与会话 ID。

    支持常见代理商格式：
      1. 用户名中带 -region-XX- / -country-XX- / -sid-XXX / -session-XXX（如 cliproxy、lunaproxy 等）
      2. 密码中带 prefix-CC-session-ttl（如常见海外住宅代理）
      3. 用户名中带 _country-xx_session-xxx 格式
    """
    proxy = normalize_proxy_url(proxy)
    if not proxy:
        return proxy
    parsed = urlsplit(proxy)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if not parsed.hostname:
        return proxy

    sid = session_id or new_proxy_session_id()
    cc = (country or "").strip().upper()

    changed_user = False
    new_username = username

    # 1. 检查 username 是否包含 -region-XX- / -country-XX- / -sid- / -session-
    if username:
        if cc:
            # 替换 -region-XX / -country-XX
            new_u, n1 = re.subn(r"(?i)(-region-)[a-z]{2}\b", rf"\g<1>{cc}", new_username)
            if n1 > 0:
                new_username = new_u
                changed_user = True
            new_u, n2 = re.subn(r"(?i)(-country-)[a-z]{2}\b", rf"\g<1>{cc}", new_username)
            if n2 > 0:
                new_username = new_u
                changed_user = True
            new_u, n3 = re.subn(r"(?i)(_country-)[a-z]{2}\b", rf"\g<1>{cc.lower()}", new_username)
            if n3 > 0:
                new_username = new_u
                changed_user = True

        if sid:
            new_u, n4 = re.subn(r"(?i)(-sid-)[a-z0-9]+\b", rf"\g<1>{sid}", new_username)
            if n4 > 0:
                new_username = new_u
                changed_user = True
            new_u, n5 = re.subn(r"(?i)(-session-)[a-z0-9]+\b", rf"\g<1>{sid}", new_username)
            if n5 > 0:
                new_username = new_u
                changed_user = True
            new_u, n6 = re.subn(r"(?i)(_session-)[a-z0-9]+\b", rf"\g<1>{sid}", new_username)
            if n6 > 0:
                new_username = new_u
                changed_user = True

    # 2. 检查 password 是否符合 prefix-CC-session-ttl
    changed_pass = False
    new_password = password
    if password:
        match = re.fullmatch(
            r"(?P<prefix>.+)-(?P<country>[A-Za-z]{2})-(?P<session>\d+)-(?P<ttl>\d+[A-Za-z]+)",
            password,
        )
        if match:
            routed_country = cc or match.group("country")
            routed_sid = sid if sid.isdigit() else str(random.randint(10_000_000, 99_999_999))
            new_password = (
                f"{match.group('prefix')}-{routed_country.upper()}-"
                f"{routed_sid}-{match.group('ttl')}"
            )
            changed_pass = True

    if changed_user or changed_pass:
        return proxy_url_with_credentials(parsed, new_username, new_password)
    return proxy


def proxy_endpoint_label(proxy: str) -> str:
    try:
        parsed = urlsplit(normalize_proxy_url(proxy))
        host = parsed.hostname or "?"
        return f"{host}:{parsed.port}" if parsed.port else host
    except Exception:
        return "?"


def set_proxy(session: Any, proxy: str) -> None:
    proxy = normalize_proxy_url(proxy)
    session.proxies = {"http": proxy, "https": proxy} if proxy else {}


def classify(session_id: str) -> str:
    text = str(session_id or "").strip()
    if text.startswith("oaics_"):
        return "OAICS"
    if text.startswith("cs_"):
        return "CS"
    if text.startswith("oaic_"):
        return "OAIC"
    if not text:
        return "NONE"
    return "UNKNOWN"


def extract_session_id(payload: dict[str, Any]) -> str:
    for key in ("checkout_session_id", "session_id", "id"):
        text = str(payload.get(key) or "").strip()
        if text.startswith(("oaics_", "oaic_", "cs_")):
            return text
    blob = json.dumps(payload, ensure_ascii=False)
    for pattern in (
        r"\boaics_[A-Za-z0-9_-]+\b",
        r"\boaic_[A-Za-z0-9_-]+\b",
        r"\bcs_(?:live|test)?_[A-Za-z0-9_-]+\b",
    ):
        match = re.search(pattern, blob)
        if match:
            return match.group(0)
    return ""


def extract_processor(payload: dict[str, Any]) -> str:
    for key in ("processor_entity", "processorEntity", "processor"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def new_session(token: str, proxy: str, device_id: str, session_id: str) -> Any:
    if CurlSession is None:
        raise RuntimeError('curl_cffi is required: pip install "curl_cffi>=0.15.0"')
    session = CurlSession(impersonate="chrome136")
    if hasattr(session, "trust_env"):
        session.trust_env = False
    session.headers.update(
        {
            "User-Agent": DEFAULT_UA,
            "Accept": "*/*",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Authorization": f"Bearer {token}",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "Content-Type": "application/json",
            "oai-device-id": device_id,
            "oai-language": "de-DE",
            "oai-session-id": session_id,
            "oai-client-version": CLIENT_VERSION,
            "oai-client-build-number": CLIENT_BUILD,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sec-ch-ua": '"Google Chrome";v="136", "Not.A/Brand";v="8", "Chromium";v="136"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Cookie": f"oai-did={device_id}",
        }
    )
    set_proxy(session, proxy)
    return session


def probe_proxy_country(proxy: str, expected: str, timeout: float = 8.0) -> tuple[str, int]:
    if CurlSession is None:
        raise RuntimeError('curl_cffi is required: pip install "curl_cffi>=0.15.0"')
    started = time.time()
    session = CurlSession(impersonate="chrome136")
    try:
        if hasattr(session, "trust_env"):
            session.trust_env = False
        set_proxy(session, proxy)
        response = session.get(TRACE_URL, timeout=timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise RuntimeError(f"Cloudflare trace HTTP {status}")
        fields = dict(
            line.split("=", 1)
            for line in str(getattr(response, "text", "") or "").splitlines()
            if "=" in line
        )
        observed = str(fields.get("loc") or "").upper()
        if expected and observed != expected.upper():
            raise RuntimeError(
                f"代理出口国家不匹配: 实际为 {observed}，设置为 {expected.upper()}。"
                f"（可勾选【跳过代理出口校验】或将代理出口设置为 {observed}）"
            )
        return observed, int((time.time() - started) * 1000)
    finally:
        safe_close(session)


def create_checkout(
    session: Any,
    *,
    billing_country: str,
    currency: str,
    with_promo: bool,
    timeout: float,
) -> tuple[dict[str, Any], int]:
    body: dict[str, Any] = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {
            "country": billing_country,
            "currency": currency,
        },
        "checkout_ui_mode": "custom",
    }
    if with_promo:
        body["promo_campaign"] = {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        }
    response = session.post(
        CHECKOUT_URL,
        json=body,
        headers={
            "Referer": "https://chatgpt.com/",
            "x-openai-target-path": "/backend-api/payments/checkout",
            "x-openai-target-route": "/backend-api/payments/checkout",
        },
        timeout=timeout,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    try:
        payload = response.json() or {}
    except Exception:
        payload = {"_raw": (getattr(response, "text", "") or "")[:500]}
    if not isinstance(payload, dict):
        payload = {"_non_object": payload}
    return payload, status


def probe_once(
    token: str,
    proxy: str,
    *,
    billing_country: str = DEFAULT_BILLING,
    currency: str = DEFAULT_CURRENCY,
    proxy_country: str = DEFAULT_PROXY_COUNTRY,
    with_promo: bool = False,
    skip_proxy_check: bool = False,
    rotate_session: bool = True,
    timeout: float = 30.0,
    attempt: int = 1,
    account_email: str = "",
) -> ProbeResult:
    started = time.time()
    if rotate_session:
        routed = route_proxy_country(proxy, proxy_country, new_proxy_session_id())
    else:
        routed = route_proxy_country(proxy, proxy_country)
    if not routed:
        routed = normalize_proxy_url(proxy)

    result = ProbeResult(
        ok=False,
        state="ERROR",
        billing_country=billing_country,
        currency=currency,
        proxy_country=proxy_country,
        proxy=proxy_endpoint_label(routed),
        attempt=attempt,
        account_email=account_email,
    )

    session = None
    try:
        if not skip_proxy_check:
            t0 = time.time()
            observed, _ = probe_proxy_country(routed, proxy_country, timeout=min(timeout, 8.0))
            result.proxy_check_ms = int((time.time() - t0) * 1000)

        device_id = str(uuid.uuid4())
        oai_sid = str(uuid.uuid4())
        session = new_session(token, routed, device_id, oai_sid)

        t1 = time.time()
        payload, status = create_checkout(
            session,
            billing_country=billing_country,
            currency=currency,
            with_promo=with_promo,
            timeout=timeout,
        )
        result.checkout_ms = int((time.time() - t1) * 1000)
        result.http_status = status

        if status >= 400:
            detail = ""
            if isinstance(payload, dict):
                detail = str(
                    payload.get("detail")
                    or payload.get("error")
                    or payload.get("_raw")
                    or ""
                )
            result.error = f"HTTP {status}: {detail[:200]}"
            return result

        session_id = extract_session_id(payload)
        state = classify(session_id)
        result.session_id = session_id
        result.session_id_masked = mask(session_id)
        result.state = state
        result.processor_entity = extract_processor(payload)
        result.ok = state == "OAICS"
        if not result.ok:
            result.error = (
                f"oaics_ not found (state={state}, "
                f"sid={result.session_id_masked or 'EMPTY'})"
            )
        return result
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.state = "ERROR"
        result.ok = False
        return result
    finally:
        safe_close(session)
        result.elapsed_ms = int((time.time() - started) * 1000)


# ════════════════════════ 任务管理器 ════════════════════════


class OACheckTask:
    """单个资格检测任务：emails 状态表 + 事件队列 + 汇总。"""

    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.config = config
        self.proxies: list[str] = config.get("proxies") or []
        self._proxy_idx = 0
        self._idx_lock = threading.Lock()
        # email -> {status: pending/running/done/cancelled, result: dict|None}
        self.items: dict[str, dict] = {e: {"status": "pending", "result": None} for e in emails}
        self.queue: queue.Queue = queue.Queue()
        self.done_count = 0
        self.hit_count = 0
        self.cancelled = False
        self._done_lock = threading.Lock()

    def next_proxy(self) -> str:
        """代理池 round-robin 轮流取。"""
        if not self.proxies:
            return ""
        with self._idx_lock:
            proxy = self.proxies[self._proxy_idx % len(self.proxies)]
            self._proxy_idx += 1
            return proxy

    def set_status(self, email: str, status: str, result: Optional[dict] = None) -> None:
        item = self.items.get(email)
        if item is None:
            return
        item["status"] = status
        if result is not None:
            item["result"] = result
        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": status,
            "result": result,
        })

    def mark_done(self, email: str, result: dict) -> None:
        with self._done_lock:
            self.done_count += 1
            if result.get("state") == "OAICS":
                self.hit_count += 1
        self.set_status(email, "done", result)


_tasks: dict[str, OACheckTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 30


def _prune_tasks_locked() -> None:
    if len(_tasks) > _MAX_HISTORY_TASKS:
        oldest_keys = list(_tasks.keys())[:-15]
        for k in oldest_keys:
            _tasks.pop(k, None)


def log_event(task: OACheckTask, line: str) -> None:
    try:
        task.queue.put({"kind": "log", "line": line})
    except Exception:
        pass


def _check_one_email(task: OACheckTask, email: str) -> None:
    """单个账号的检测流程：取 AT → 用代理探测 rounds 轮 → 写库 + 推进度。"""
    if task.cancelled:
        task.set_status(email, "cancelled", {"state": "CANCELLED", "error": "任务已取消"})
        return

    rounds = max(1, int(task.config.get("rounds") or 1))
    task.set_status(email, "running")

    final_result: dict = {
        "state": "ERROR",
        "checked_at": time.time(),
        "error": "内部错误",
    }
    try:
        cred = db.get_registered(email)
        if not cred:
            final_result = {"state": "ERROR", "checked_at": time.time(), "error": "未找到凭证记录"}
            return
        cred_at = (cred.get("access_token") or "").strip()
        if not cred_at:
            final_result = {"state": "NO_AT", "checked_at": time.time(), "error": "该号无 access_token"}
            return

        def _run_round(proxy: str, attempt: int) -> ProbeResult:
            return probe_once(
                cred_at,
                proxy,
                billing_country=task.config.get("billing_country") or DEFAULT_BILLING,
                currency=task.config.get("currency") or DEFAULT_CURRENCY,
                proxy_country=task.config.get("proxy_country") or DEFAULT_PROXY_COUNTRY,
                with_promo=bool(task.config.get("with_promo")),
                skip_proxy_check=bool(task.config.get("skip_proxy_check")),
                rotate_session=True,
                timeout=float(task.config.get("timeout") or 30.0),
                attempt=attempt,
                account_email=email,
            )

        last: Optional[ProbeResult] = None
        for attempt in range(1, rounds + 1):
            if task.cancelled:
                final_result = {"state": "CANCELLED", "checked_at": time.time(), "error": "任务已取消"}
                return
            proxy = task.next_proxy()
            log_event(task, f"[{email}] 第 {attempt}/{rounds} 轮探测, proxy={proxy_endpoint_label(proxy) or '(直连)'}")
            last = _run_round(proxy, attempt)
            mark = "HIT" if last.ok else "MISS"
            log_event(task, f"[{email}] {mark} state={last.state} sid={last.session_id_masked or '-'} "
                            f"http={last.http_status} {last.elapsed_ms}ms"
                            + (f" err={last.error}" if last.error else ""))
            if last.ok:  # 命中 oaics_ 提前收工
                break
        if last is None:
            final_result = {"state": "ERROR", "checked_at": time.time(), "error": "探测无结果"}
            return
        final_result = {
            "state": last.state,
            "ok": last.ok,
            "session_id_masked": last.session_id_masked,
            "processor_entity": last.processor_entity,
            "billing_country": last.billing_country,
            "currency": last.currency,
            "proxy_country": last.proxy_country,
            "proxy": last.proxy,
            "http_status": last.http_status,
            "elapsed_ms": last.elapsed_ms,
            "attempt": last.attempt,
            "error": last.error or "",
            "checked_at": time.time(),
        }
    except Exception as exc:  # noqa: BLE001
        final_result = {
            "state": "ERROR",
            "checked_at": time.time(),
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if final_result.get("state") not in ("CANCELLED", "ERROR"):
            try:
                db.update_oa_check(email, final_result)
            except Exception as exc:  # noqa: BLE001
                log_event(task, f"[{email}] 写库失败: {exc}")
        elif final_result.get("state") == "ERROR":
            # 记录错误信息到数据库
            try:
                db.update_oa_check(email, final_result)
            except Exception:
                pass
        task.mark_done(email, final_result)


def start(emails: list[str], config: dict) -> str:
    """启动一个资格检测任务，返回 task_id。

    config:
        proxies: list[str]           代理池（每行一个，支持 sticky 代理）
        workers: int                 并发数（1-20）
        rounds: int                  每号探测轮数（默认 1，命中 oaics_ 提前结束）
        billing_country / currency / proxy_country / with_promo / skip_proxy_check / timeout
    """
    cleaned = [e.strip().lower() for e in emails if e and e.strip()]
    if not cleaned:
        raise ValueError("emails 不能为空")

    task_id = uuid.uuid4().hex[:12]
    task = OACheckTask(task_id, cleaned, config)
    with _tasks_lock:
        _prune_tasks_locked()
        _tasks[task_id] = task

    workers = max(1, min(_MAX_WORKERS, int(config.get("workers") or 1)))
    workers = min(workers, len(cleaned))

    log_event(task, f"[task] 启动 {task_id}: {len(cleaned)} 个账号, workers={workers}, "
                    f"rounds={config.get('rounds') or 1}, proxies={len(task.proxies)}")

    def _run() -> None:
        try:
            if workers == 1:
                for email in cleaned:
                    if task.cancelled:
                        break
                    _check_one_email(task, email)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_check_one_email, task, email) for email in cleaned]
                    for _ in as_completed(futures):
                        pass
            # 处理未完成的
            if task.cancelled:
                log_event(task, f"[task] 任务已停止: 完成 {task.done_count}/{len(cleaned)}")
            else:
                log_event(task, f"[task] 完成: 命中 {task.hit_count}/{task.done_count}")
        except Exception as exc:  # noqa: BLE001
            log_event(task, f"[task] 任务异常: {exc}")
        finally:
            task.queue.put({"kind": "end"})
            task.queue.put(None)  # sentinel: SSE 流结束

    th = threading.Thread(target=_run, daemon=True, name=f"oa-check-{task_id}")
    th.start()
    return task_id


def stop(task_id: str) -> bool:
    """停止指定的资格检测任务。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task:
        task.cancelled = True
        log_event(task, "[task] 收到停止指令，正在停止剩余任务...")
        return True
    return False


def get_queue(task_id: str) -> Optional[queue.Queue]:
    with _tasks_lock:
        task = _tasks.get(task_id)
    return task.queue if task else None


def snapshot(task_id: str) -> Optional[dict]:
    """SSE 连接时的全量快照（断线重连后能恢复现场）。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        return None
    return {
        "items": {email: dict(item) for email, item in task.items.items()},
        "done": task.done_count,
        "hit": task.hit_count,
        "total": len(task.items),
        "config": task.config,
        "cancelled": task.cancelled,
    }


def remove(task_id: str) -> None:
    with _tasks_lock:
        _tasks.pop(task_id, None)
