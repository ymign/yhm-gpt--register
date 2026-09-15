"""解 401 引擎：逻辑对齐主工程 webui/fix401_service.py。

- 用户代理原串钉死，不改协议、不换 sticky sid
- 有可用 RT 先极速刷新；失败才按勾选走密码+2FA 重登
- 重登 skip_sms，OpenAI 要绑手机就标记 need_phone
- 并发按配置；没填代理压到 1
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from auth_flow import is_official_account_dead
from fingerprint import fingerprint_from_account, generate_fingerprint
from http_client import create_http_session
from mail_providers.base import MailProvider
from webui.oauth_export import (
    _get_account_claims,
    cpa_credential_to_sub2_account,
    execute_codex_oauth_flow,
)
from webui.token_refresh_fast import refresh_token_fast

logger = logging.getLogger("fix401")

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
EXPORT_DIR = ROOT / "exports"
CPA_DIR = EXPORT_DIR / "cpa"
SUB2_DIR = EXPORT_DIR / "sub2api"
STORE_PATH = DATA_DIR / "accounts.json"

_DEAD = ("banned", "deactivated", "account_deactivated")
_tasks_lock = threading.Lock()
_tasks: dict[str, "Fix401Task"] = {}


class TotpOnlyMail(MailProvider):
    """独立包不解邮箱 OTP，登录靠账密 + TOTP。"""

    kind = "totp_only"
    display_name = "账密+2FA"
    accepts_existing_account = True

    def __init__(self, email: str):
        self._email = (email or "").strip().lower()

    def create_mailbox(self) -> str:
        return self._email

    def wait_for_otp(self, email_addr: str, timeout: int = 120, issued_after: Optional[float] = None) -> str:
        raise TimeoutError("本包不解邮箱验证码。请提供 TOTP 2FA，保证登录走密码+动态码")

    def peek_otp(self, email_addr: str, issued_after: Optional[float] = None, wait: float = 0.0):
        return None


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _normalize_user_proxy(raw: str) -> str:
    p = (raw or "").strip()
    if not p or p.startswith("#"):
        return ""
    if "://" not in p:
        p = "http://" + p
    return p


def parse_user_proxies(text: str = "", proxies: Optional[list[str]] = None) -> list[str]:
    lines: list[str] = []
    if proxies:
        lines.extend(str(x) for x in proxies)
    if text:
        lines.extend(str(text).splitlines())
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        p = _normalize_user_proxy(line)
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _proxy_label(proxy: str) -> str:
    p = (proxy or "").strip()
    if not p:
        return "直连"
    try:
        u = urlparse(p if "://" in p else "http://" + p)
        host = u.hostname or ""
        port = f":{u.port}" if u.port else ""
        return f"{host}{port}" if host else p.split("@")[-1]
    except Exception:
        return p.split("@")[-1] if "@" in p else p


def _usable_rt(rt: str) -> str:
    s = (rt or "").strip()
    if not s or s.lower() in ("1", "true", "null", "none"):
        return ""
    if len(s) < 20:
        return ""
    return s


def _looks_like_rt(s: str) -> bool:
    t = (s or "").strip()
    return len(t) >= 40 and ("." in t or t.startswith("rt_") or len(t) >= 80)


def parse_account_text(text: str) -> list[dict]:
    """解析批量账号。支持：
    邮箱----密码----2FA
    邮箱----密码----2FA----RT
    JSON 数组 [{email,password,totp_secret,refresh_token,...}]
    """
    raw = (text or "").strip()
    if not raw:
        return []
    if raw.startswith("[") or raw.startswith("{"):
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        out = []
        for row in data:
            if not isinstance(row, dict):
                continue
            em = _norm_email(row.get("email") or row.get("username") or "")
            if not em:
                continue
            out.append(_normalize_account_dict(row, em))
        return out

    out = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.replace("|", "----").replace("\t", "----").split("----")]
        parts = [p for p in parts if p != ""]
        if len(parts) < 3:
            raise ValueError(f"格式不对（至少 邮箱----密码----2FA）: {s[:80]}")
        em = _norm_email(parts[0])
        rec = {
            "email": em,
            "password": parts[1],
            "totp_secret": parts[2].replace(" ", "").upper(),
            "refresh_token": "",
            "device_id": "",
        }
        extra = parts[3:]
        for piece in extra:
            if _looks_like_rt(piece) and not rec["refresh_token"]:
                rec["refresh_token"] = piece
            elif len(piece) >= 8 and not rec["device_id"] and "://" not in piece:
                rec["device_id"] = piece
        out.append(rec)
    return out


def _normalize_account_dict(row: dict, em: str) -> dict:
    totp = str(row.get("totp_secret") or row.get("totp") or row.get("2fa") or "").replace(" ", "").upper()
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    bp = row.get("browser_profile") or extra.get("browser_profile") or {}
    return {
        "email": em,
        "password": str(row.get("password") or ""),
        "totp_secret": totp,
        "refresh_token": str(row.get("refresh_token") or row.get("rt") or ""),
        "access_token": str(row.get("access_token") or row.get("at") or ""),
        "id_token": str(row.get("id_token") or ""),
        "device_id": str(row.get("device_id") or extra.get("device_id") or ""),
        "browser_profile": bp if isinstance(bp, dict) else {},
        "reg_country": str(row.get("reg_country") or extra.get("geo_country") or "").upper(),
        "account_status": str(row.get("account_status") or ""),
    }


def load_store() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        return {}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_store(store: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)


def upsert_accounts(accounts: list[dict]) -> list[dict]:
    store = load_store()
    out = []
    for acc in accounts:
        em = _norm_email(acc.get("email"))
        if not em:
            continue
        old = store.get(em) if isinstance(store.get(em), dict) else {}
        merged = {**old, **{k: v for k, v in acc.items() if v not in ("", None, {})}}
        merged["email"] = em
        store[em] = merged
        out.append(merged)
    save_store(store)
    return out


def check_user_proxy(proxy: str) -> dict:
    p = _normalize_user_proxy(proxy)
    if not p:
        raise ValueError("请填写代理")
    session = create_http_session(proxy=p, impersonate="chrome142")
    ip = ""
    loc = ""
    last_err = ""
    for url in ("https://cloudflare.com/cdn-cgi/trace", "https://1.1.1.1/cdn-cgi/trace"):
        try:
            resp = session.get(url, timeout=15)
            text = resp.text or ""
            for line in text.splitlines():
                if line.startswith("ip="):
                    ip = line.split("=", 1)[1].strip()
                elif line.startswith("loc="):
                    loc = line.split("=", 1)[1].strip()
            if ip:
                break
        except Exception as e:
            last_err = str(e)
    if not ip:
        raise RuntimeError(last_err or "代理无响应")
    return {"ok": True, "ip": ip, "loc": loc, "proxy": _proxy_label(p)}


def _account_fingerprint(cred: dict, country_code: str = "") -> dict:
    fp = fingerprint_from_account(
        cred,
        country_code=(country_code or "").strip().upper(),
        generate_if_missing=False,
    ) or {}
    if fp.get("user_agent"):
        return fp
    cc = (country_code or "").strip().upper()
    fp = generate_fingerprint(country_code=cc or "")
    extra = cred.get("extra") if isinstance(cred.get("extra"), dict) else {}
    extra["browser_profile"] = dict(fp)
    cred["extra"] = extra
    cred["browser_profile"] = dict(fp)
    return fp


def _build_cpa(email: str, at: str, rt: str, it: str = "", plan_type: str = "", account_id: str = "", exp_iso: str = "") -> tuple[dict, dict]:
    from datetime import datetime, timezone, timedelta

    claims = _get_account_claims(at) if at else {}
    plan = (plan_type or claims.get("plan_type") or "free").strip() or "free"
    aid = account_id or claims.get("account_id") or ""
    expired = exp_iso or claims.get("exp_iso") or ""
    if not expired:
        exp = claims.get("exp")
        if isinstance(exp, int) and exp > 0:
            expired = datetime.fromtimestamp(exp, timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    last_refresh = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    cpa = {
        "type": "codex",
        "email": email,
        "name": email,
        "expired": expired,
        "id_token": it or "",
        "account_id": aid,
        "chatgpt_account_id": aid,
        "access_token": at,
        "last_refresh": last_refresh,
        "refresh_token": rt or "1",
        "plan_type": plan,
        "chatgpt_plan_type": plan,
    }
    return cpa, cpa_credential_to_sub2_account(cpa)


def _write_export_files(email: str, cpa: dict, sub2: dict) -> None:
    try:
        CPA_DIR.mkdir(parents=True, exist_ok=True)
        SUB2_DIR.mkdir(parents=True, exist_ok=True)
        (CPA_DIR / f"codex-{email}.json").write_text(
            json.dumps(cpa, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (SUB2_DIR / f"sub2-{email}.json").write_text(
            json.dumps(sub2, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception as e:
        logger.warning("[fix401] 落盘失败 %s: %s", email, e)


def _persist_account(cred: dict, at: str, rt: str, it: str, fp: dict) -> None:
    store = load_store()
    em = _norm_email(cred.get("email"))
    row = dict(store.get(em) or cred)
    row["email"] = em
    if at:
        row["access_token"] = at
    if rt:
        row["refresh_token"] = rt
    if it:
        row["id_token"] = it
    if fp:
        row["browser_profile"] = fp
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        extra["browser_profile"] = fp
        if fp.get("device_id"):
            extra["device_id"] = fp.get("device_id")
        row["extra"] = extra
        if fp.get("device_id"):
            row["device_id"] = fp.get("device_id")
    if cred.get("device_id"):
        row["device_id"] = cred["device_id"]
    store[em] = row
    save_store(store)


class Fix401Task:
    def __init__(self, task_id: str, emails: list[str], config: dict, creds: dict[str, dict]):
        self.task_id = task_id
        self.config = config or {}
        self.creds = creds
        self.proxies: list[str] = list(self.config.get("proxies") or [])
        self._proxy_idx = 0
        self._idx_lock = threading.Lock()
        self.oauth_gate = threading.Semaphore(max(1, int(self.config.get("workers") or 2)))
        self.started_at = time.time()
        self.finished_at = 0.0
        self.cancelled = False
        self.done_count = 0
        self.queue: queue.Queue = queue.Queue(maxsize=4000)
        self._lock = threading.Lock()
        self.stats = {
            "success": 0,
            "rt_fast": 0,
            "oauth": 0,
            "failed": 0,
            "banned": 0,
            "need_phone": 0,
            "missing": 0,
            "rt_expired": 0,
        }
        self.items: dict[str, dict] = {
            e: {
                "email": e,
                "status": "pending",
                "step_text": "排队",
                "result": None,
                "started_at": 0.0,
                "finished_at": 0.0,
                "elapsed": 0.0,
                "logs": [],
                "cpa": None,
                "sub2api": None,
            }
            for e in emails
        }

    def next_proxy(self) -> str:
        if not self.proxies:
            return ""
        if len(self.proxies) == 1:
            return self.proxies[0]
        with self._idx_lock:
            p = self.proxies[self._proxy_idx % len(self.proxies)]
            self._proxy_idx += 1
            return p

    def add_email_log(self, email: str, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        msg = f"{ts} {line}"
        with self._lock:
            it = self.items.get(email)
            if it is not None:
                it["logs"].append(msg)
                if len(it["logs"]) > 400:
                    it["logs"] = it["logs"][-400:]
        self._put({"kind": "log", "email": email, "line": msg})

    def set_running(self, email: str, text: str) -> None:
        with self._lock:
            it = self.items.get(email)
            if not it:
                return
            it["status"] = "running"
            it["step_text"] = text
            if not it.get("started_at"):
                it["started_at"] = time.time()
        self._put({"kind": "progress", "email": email, "status": "running", "step_text": text})

    def set_step(self, email: str, key: str, text: str) -> None:
        with self._lock:
            it = self.items.get(email)
            if it:
                it["step_text"] = text
        self._put({"kind": "progress", "email": email, "status": "running", "step_text": text, "step": key})

    def mark_done(self, email: str, result: dict) -> None:
        st = str((result or {}).get("status") or "failed")
        with self._lock:
            it = self.items.get(email)
            if not it:
                return
            it["status"] = "done"
            it["result"] = result
            it["finished_at"] = time.time()
            it["elapsed"] = round(it["finished_at"] - (it.get("started_at") or it["finished_at"]), 2)
            it["step_text"] = str((result or {}).get("label") or st)
            if result.get("cpa"):
                it["cpa"] = result["cpa"]
            if result.get("sub2api"):
                it["sub2api"] = result["sub2api"]
            self.done_count += 1
            if st == "success":
                self.stats["success"] += 1
                method = str(result.get("method") or "")
                if method == "rt_fast":
                    self.stats["rt_fast"] += 1
                else:
                    self.stats["oauth"] += 1
            elif st in self.stats:
                self.stats[st] += 1
            else:
                self.stats["failed"] += 1
        self._put({
            "kind": "progress",
            "email": email,
            "status": "done",
            "result": {
                "status": st,
                "method": result.get("method") or "",
                "label": result.get("label") or "",
                "error": str(result.get("error") or "")[:240],
                "plan_type": result.get("plan_type") or "",
                "access_token_len": result.get("access_token_len") or 0,
                "refresh_token_len": result.get("refresh_token_len") or 0,
            },
            "stats": dict(self.stats),
            "done_count": self.done_count,
        })

    def snapshot(self) -> dict:
        with self._lock:
            items = {}
            for em, it in self.items.items():
                items[em] = {
                    "email": em,
                    "status": it.get("status"),
                    "step_text": it.get("step_text"),
                    "elapsed": it.get("elapsed"),
                    "result": {
                        "status": (it.get("result") or {}).get("status"),
                        "method": (it.get("result") or {}).get("method"),
                        "label": (it.get("result") or {}).get("label"),
                        "error": str((it.get("result") or {}).get("error") or "")[:240],
                        "plan_type": (it.get("result") or {}).get("plan_type"),
                        "access_token_len": (it.get("result") or {}).get("access_token_len") or 0,
                        "refresh_token_len": (it.get("result") or {}).get("refresh_token_len") or 0,
                    } if it.get("result") else None,
                }
            return {
                "task_id": self.task_id,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "cancelled": self.cancelled,
                "done_count": self.done_count,
                "total": len(self.items),
                "stats": dict(self.stats),
                "items": items,
                "has_proxy": bool(self.proxies),
                "workers": int(self.config.get("workers") or 1),
            }

    def _put(self, payload: dict) -> None:
        payload["task_id"] = self.task_id
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(payload)
            except queue.Full:
                pass


def _run_one(task: Fix401Task, email: str) -> None:
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被停止"})
        return

    task.set_running(email, "读取账号")
    cred = dict(task.creds.get(email) or {})
    if not cred:
        task.add_email_log(email, "没有这个邮箱")
        task.mark_done(email, {"status": "missing", "label": "没有账号", "error": "未导入该邮箱"})
        return
    st = str(cred.get("account_status") or "").strip().lower()
    if st in _DEAD:
        task.add_email_log(email, "已是官方封号，跳过，不打 OpenAI")
        task.mark_done(email, {"status": "banned", "label": "已封号，已跳过", "error": "账号已封号"})
        return

    extra = cred.get("extra") if isinstance(cred.get("extra"), dict) else {}
    bp = cred.get("browser_profile") if isinstance(cred.get("browser_profile"), dict) else extra.get("browser_profile")
    if isinstance(bp, dict):
        extra["browser_profile"] = bp
        cred["extra"] = extra
    if not cred.get("device_id"):
        cred["device_id"] = extra.get("device_id") or (bp or {}).get("device_id") or ""

    proxy = task.next_proxy()
    egress_ip = str(task.config.get("proxy_ip") or "").strip()
    egress_cc = str(task.config.get("proxy_country") or "").strip().upper()
    if proxy:
        extra_tip = f" 实测出口 {egress_ip}" if egress_ip else " 出口未探测到"
        if egress_cc:
            extra_tip += f" {egress_cc}"
        scheme = ""
        try:
            scheme = (urlparse(proxy).scheme or "").lower()
        except Exception:
            scheme = ""
        scheme_tip = f"{scheme} " if scheme else ""
        task.add_email_log(
            email,
            f"使用你填的代理: {scheme_tip}{_proxy_label(proxy)}{extra_tip}（原串钉死，不改协议不换 sid）",
        )
    else:
        task.add_email_log(email, "未填代理，直连本机出口")

    force_full = bool(task.config.get("force_full_login"))
    timeout = float(task.config.get("timeout") or 45.0)
    existing_rt = _usable_rt(cred.get("refresh_token") or "")
    reg_country = str(cred.get("reg_country") or extra.get("geo_country") or "").strip().upper()
    proxy_country = str(task.config.get("proxy_country") or "").strip().upper()
    oauth_country = proxy_country or reg_country
    fp = _account_fingerprint(cred, oauth_country)
    if fp.get("device_id") and not cred.get("device_id"):
        cred["device_id"] = fp["device_id"]

    def _persist_success(at: str, rt: str, it: str, method: str, claims: Optional[dict] = None):
        claims = claims or _get_account_claims(at)
        plan = str((claims or {}).get("plan_type") or "free")
        account_id = str((claims or {}).get("account_id") or "")
        exp_iso = str((claims or {}).get("exp_iso") or "")
        cpa, sub2 = _build_cpa(email, at, rt, it, plan, account_id, exp_iso)
        _persist_account(cred, at, rt, it, fp)
        _write_export_files(email, cpa, sub2)
        return {
            "status": "success",
            "method": method,
            "label": "RT 已刷新" if method == "rt_fast" else "重新登录成功",
            "access_token_len": len(at or ""),
            "refresh_token_len": len(rt or ""),
            "plan_type": plan,
            "account_id": account_id,
            "expires_at": exp_iso,
            "cpa": cpa,
            "sub2api": sub2,
        }

    if existing_rt and not force_full:
        task.set_step(email, "rt", "经用户代理刷新 RT")
        task.add_email_log(
            email,
            f"先走 RT 极速刷新（len={len(existing_rt)}，TLS={fp.get('impersonate') or 'chrome142'}）",
        )
        try:
            data = refresh_token_fast(
                existing_rt,
                proxy=proxy,
                timeout=min(20.0, timeout),
                impersonate=str(fp.get("impersonate") or ""),
                user_agent=str(fp.get("user_agent") or ""),
            )
            new_at = data.get("access_token") or ""
            new_rt = data.get("refresh_token") or existing_rt
            new_it = data.get("id_token") or ""
            if new_at:
                task.add_email_log(email, f"RT 刷新成功 AT={len(new_at)} RT={len(new_rt)}")
                task.mark_done(email, _persist_success(new_at, new_rt, new_it, "rt_fast"))
                return
        except Exception as e:
            err = str(e)
            if is_official_account_dead(err):
                task.add_email_log(email, f"官方确认已注销: {err[:300]}")
                task.mark_done(email, {"status": "banned", "label": "官方已封号", "error": err[:500]})
                return
            task.add_email_log(email, f"RT 刷新未成功（{err[:180]}）")
            if not force_full:
                task.add_email_log(email, "默认不自动重登。要走密码+2FA 请勾选「允许重新登录」")
                task.mark_done(email, {"status": "rt_expired", "label": "RT 已失效", "error": err[:500]})
                return

    if not force_full:
        if not existing_rt:
            task.add_email_log(email, "没有可用 RT，默认不自动重登")
            task.mark_done(email, {
                "status": "rt_expired",
                "label": "无 RT",
                "error": "没有可用 refresh_token。勾选「允许重新登录」才会走密码+2FA",
            })
            return
        task.add_email_log(email, "未勾选允许重新登录，到此结束")
        task.mark_done(email, {
            "status": "rt_expired",
            "label": "RT 已失效",
            "error": "refresh_token 无效。勾选「允许重新登录」才会走密码+2FA",
        })
        return

    if not cred.get("password") or not cred.get("totp_secret"):
        task.add_email_log(email, "重登需要密码和 2FA 密钥")
        task.mark_done(email, {"status": "failed", "label": "缺密码/2FA", "error": "导入格式必须是 邮箱----密码----2FA"})
        return

    if not cred.get("device_id"):
        cred["device_id"] = str(uuid.uuid4())
        task.add_email_log(email, "没有 device_id，本号新开设备 ID（可能被当成新电脑）")

    task.set_step(email, "oauth", "经用户代理重新登录拿新 RT")
    geo_tip = f"时区跟出口 {oauth_country}" if oauth_country else "时区跟注册"
    task.add_email_log(email, f"启动 Codex OAuth 重登（不接码，钉代理，{geo_tip}）")
    mail = TotpOnlyMail(email)
    try:
        task.oauth_gate.acquire()
        try:
            if task.cancelled:
                task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被停止"})
                return
            flow_res = execute_codex_oauth_flow(
                email=email,
                mail_provider=mail,
                proxy=proxy,
                target_country=oauth_country,
                account_info=cred,
                log_fn=lambda msg: task.add_email_log(email, msg),
                step_fn=lambda k, t: task.set_step(email, k, t),
                skip_sms=True,
                timeout=timeout,
                stop_fn=lambda: task.cancelled,
                pin_proxy=True,
            )
        finally:
            task.oauth_gate.release()
    except Exception as e:
        err = str(e)
        if is_official_account_dead(err):
            task.add_email_log(email, f"官方确认已注销: {err[:300]}")
            task.mark_done(email, {"status": "banned", "label": "官方已封号", "error": err[:500]})
            return
        task.add_email_log(email, f"重新登录失败: {err[:300]}")
        task.mark_done(email, {"status": "failed", "label": "失败", "error": err[:500]})
        return

    if not flow_res:
        task.mark_done(email, {"status": "failed", "label": "失败", "error": "空结果"})
        return
    st2 = str(flow_res.get("status") or "")
    if st2 == "need_phone":
        task.add_email_log(email, "OpenAI 要求绑手机，本功能不接码，已跳过")
        task.mark_done(email, {"status": "need_phone", "label": "需绑手机", "error": "OpenAI 要求绑定手机号"})
        return
    if st2 == "cancelled":
        task.mark_done(email, {"status": "cancelled", "label": "已取消"})
        return
    at = flow_res.get("access_token") or ""
    rt = flow_res.get("refresh_token") or ""
    it = flow_res.get("id_token") or ""
    if st2 not in ("success", "", None) and not at and not rt:
        err = str(flow_res.get("error") or flow_res.get("label") or st2)
        if is_official_account_dead(err) or st2 in _DEAD:
            task.mark_done(email, {"status": "banned", "label": "官方已封号", "error": err[:500]})
            return
        task.mark_done(email, {"status": "failed", "label": "失败", "error": err[:500]})
        return
    if not rt:
        task.mark_done(email, {"status": "failed", "label": "失败", "error": "未拿到 refresh_token"})
        return
    claims = {
        "plan_type": flow_res.get("plan_type") or "free",
        "account_id": flow_res.get("account_id") or "",
        "exp_iso": flow_res.get("exp_iso") or "",
    }
    task.add_email_log(email, f"重登成功 RT={len(rt)} Plan={claims['plan_type']}")
    task.mark_done(email, _persist_success(at, rt, it, "oauth", claims))


def start(
    accounts: list[dict],
    proxies: Optional[list[str]] = None,
    proxy_text: str = "",
    proxy_country: str = "",
    workers: int = 2,
    timeout: int = 45,
    force_full_login: bool = False,
) -> dict:
    saved = upsert_accounts(accounts)
    unique = [a["email"] for a in saved]
    if not unique:
        raise ValueError("请填写至少一个账号")
    proxy_list = parse_user_proxies(proxy_text, proxies)
    workers_n = max(1, min(5, int(workers or 2)))
    if not proxy_list:
        workers_n = 1
        logger.warning("[fix401] 未填代理，并发已压到 1，直连批量登录容易触发风控")
    else:
        logger.info("[fix401] 并发按配置 workers=%s 代理数=%s", workers_n, len(proxy_list))
    config = {
        "proxies": proxy_list,
        "proxy_country": str(proxy_country or "").strip().upper(),
        "proxy_ip": "",
        "workers": workers_n,
        "timeout": max(15, min(120, int(timeout or 45))),
        "force_full_login": bool(force_full_login),
    }
    if proxy_list:
        try:
            info = check_user_proxy(proxy_list[0])
            config["proxy_ip"] = str(info.get("ip") or "").strip()
            loc = str(info.get("loc") or "").strip().upper()
            if loc and loc not in ("XX", "T1") and not config["proxy_country"]:
                config["proxy_country"] = loc
            logger.info("[fix401] 用户代理实测出口 ip=%s loc=%s", config["proxy_ip"], loc or config["proxy_country"])
        except Exception as e:
            logger.warning("[fix401] 用户代理出口探测失败，仍按原串使用: %s", e)

    creds = {a["email"]: a for a in saved}
    task_id = uuid.uuid4().hex[:12]
    task = Fix401Task(task_id, unique, config, creds)
    with _tasks_lock:
        _tasks[task_id] = task

    def _runner():
        work_q: queue.Queue = queue.Queue()
        for em in unique:
            work_q.put(em)
        max_w = min(config["workers"], len(unique))

        def _pool_worker():
            while not task.cancelled:
                try:
                    em = work_q.get_nowait()
                except queue.Empty:
                    return
                try:
                    _run_one(task, em)
                except Exception:
                    logger.exception("[fix401] %s worker 崩溃", em)
                    task.mark_done(em, {"status": "failed", "label": "失败", "error": "worker 异常"})

        with ThreadPoolExecutor(max_workers=max_w, thread_name_prefix=f"fix401_{task_id}") as pool:
            futs = [pool.submit(_pool_worker) for _ in range(max_w)]
            for f in futs:
                try:
                    f.result()
                except Exception:
                    logger.exception("[fix401] pool worker crashed")

        task.finished_at = time.time()
        task._put({
            "kind": "end",
            "task_id": task_id,
            "cancelled": task.cancelled,
            "stats": dict(task.stats),
            "done_count": task.done_count,
        })

    threading.Thread(target=_runner, daemon=True, name=f"fix401-{task_id}").start()
    return {
        "ok": True,
        "task_id": task_id,
        "taskId": task_id,
        "total": len(unique),
        "queued": len(unique),
        "workers": workers_n,
        "has_proxy": bool(proxy_list),
    }


def get_task(task_id: str) -> Optional[Fix401Task]:
    with _tasks_lock:
        return _tasks.get(task_id)


def stop(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    task.cancelled = True
    return {"ok": True, "task_id": task_id}


def collect_exports(task_id: str, kind: str = "cpa") -> list[dict]:
    task = get_task(task_id)
    if not task:
        return []
    out = []
    with task._lock:
        for it in task.items.values():
            doc = it.get("cpa") if kind == "cpa" else it.get("sub2api")
            if doc:
                out.append(doc)
    return out
