"""解 401：用用户提供的代理重提 RT/AT，给 CPA / Sub2 主机用。

和账号页「重新授权」分开：
  - 用户代理原样使用，不改国家、不换 sticky 会话
  - 已官方封号的跳过，不打 OpenAI
  - 有 RT 先走该代理极速刷新；失败再 Codex 重登（不接码）
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlparse

from . import db
from auth_flow import is_official_account_dead
from mail_providers import create_mail_provider

logger = logging.getLogger(__name__)

_DEAD = ("banned", "deactivated", "account_deactivated")


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _normalize_user_proxy(raw: str) -> str:
    p = (raw or "").strip()
    if not p or p.startswith("#"):
        return ""
    # 写了协议就原样用（socks5 不改成 http）。没写协议才补 http://。
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


def _is_official_banned(cred: dict) -> bool:
    if not cred:
        return False
    st = str(cred.get("account_status") or "").strip().lower()
    if st in _DEAD:
        return True
    oauth = str(cred.get("oauth_status") or "").strip().lower()
    if oauth in _DEAD:
        return True
    extra = cred.get("extra") if isinstance(cred.get("extra"), dict) else {}
    pc = extra.get("plus_check") if isinstance(extra.get("plus_check"), dict) else {}
    pst = str(pc.get("plus_type") or pc.get("status") or "").strip().lower()
    return pst in _DEAD


def _usable_rt(rt: str) -> str:
    s = (rt or "").strip()
    if not s or s in ("1", "true", "null", "none"):
        return ""
    if len(s) < 20:
        return ""
    return s


def _account_fingerprint(cred: dict, country_code: str = "") -> dict:
    from fingerprint import fingerprint_from_account
    return fingerprint_from_account(
        cred,
        country_code=(country_code or "").strip().upper(),
        generate_if_missing=False,
    ) or {}


def _public_result(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {}
    err = str(result.get("error") or "")
    return {
        "status": result.get("status") or "",
        "method": result.get("method") or "",
        "label": result.get("label") or "",
        "error": err[:240],
        "plan_type": result.get("plan_type") or "",
        "access_token_len": result.get("access_token_len") or 0,
        "refresh_token_len": result.get("refresh_token_len") or 0,
        "expires_at": result.get("expires_at") or "",
    }


def _build_cpa(email: str, at: str, rt: str, it: str = "", plan_type: str = "", account_id: str = "", exp_iso: str = "") -> tuple[dict, dict]:
    from .export_formats import get_or_build_cpa_token_data
    from .oauth_export import cpa_credential_to_sub2_account

    cpa = get_or_build_cpa_token_data({
        "email": email,
        "access_token": at,
        "refresh_token": rt,
        "id_token": it,
        "account_id": account_id,
    })
    plan = (plan_type or cpa.get("plan_type") or "free").strip() or "free"
    cpa["plan_type"] = plan
    cpa["chatgpt_plan_type"] = plan
    if exp_iso:
        cpa["expired"] = exp_iso
    if account_id:
        cpa["account_id"] = account_id
        cpa["chatgpt_account_id"] = account_id
    sub2 = cpa_credential_to_sub2_account(cpa)
    return cpa, sub2


def _write_export_files(email: str, cpa: dict, sub2: dict) -> None:
    from .oauth_export import CPA_DIR, SUB2_DIR
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


def _make_mail_provider(email: str, cred: dict, log_fn=None):
    email_lower = _norm_email(email)
    extra = cred.get("extra") if isinstance(cred.get("extra"), dict) else {}
    saved = extra.get("mail_oauth") if isinstance(extra.get("mail_oauth"), dict) else {}
    mail_account = db.get_account(email_lower)
    mail_source = ""

    if saved.get("kind") == "remail" or saved.get("service_token") or saved.get("pickup_url"):
        mail_source = "remail"
        mail_account = {
            "email": email_lower,
            "service_token": saved.get("service_token", ""),
            "pickup_url": saved.get("pickup_url", ""),
            "order_no": saved.get("order_no", ""),
            "project_id": saved.get("project_id", 2),
            "email_suffix": saved.get("email_suffix", "icloud.com"),
            "service_mode": saved.get("service_mode", "purchase"),
            "kind": "remail",
        }
    elif saved.get("kind") == "icloud_relay" or saved.get("relay_url"):
        mail_source = "icloud_relay"
        mail_account = {
            "email": email_lower,
            "relay_url": saved.get("relay_url", ""),
            "kind": "icloud_relay",
        }
    elif not mail_account and (saved.get("refresh_token") or saved.get("password")):
        mail_account = {
            "email": email_lower,
            "password": saved.get("password", ""),
            "client_id": saved.get("client_id", ""),
            "refresh_token": saved.get("refresh_token", ""),
            "kind": saved.get("kind", "outlook"),
        }

    if not mail_source:
        try:
            cur = db._conn().execute(
                "SELECT service_token, order_no, project_id, email_suffix, service_mode "
                "FROM remail_recycle_pool WHERE email=?",
                (email_lower,),
            ).fetchone()
            if cur and cur["service_token"]:
                mail_source = "remail"
                mail_account = {
                    "email": email_lower,
                    "service_token": cur["service_token"],
                    "order_no": cur["order_no"] if "order_no" in cur.keys() else "",
                    "project_id": cur["project_id"] if "project_id" in cur.keys() else 2,
                    "email_suffix": cur["email_suffix"] if "email_suffix" in cur.keys() else "icloud.com",
                    "service_mode": cur["service_mode"] if "service_mode" in cur.keys() else "purchase",
                    "kind": "remail",
                }
        except Exception:
            pass

    if not mail_source:
        if mail_account and mail_account.get("kind"):
            mail_source = str(mail_account.get("kind")).strip().lower()
        elif any(d in email_lower for d in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
            mail_source = "outlook"
        elif any(d in email_lower for d in ("@icloud.", "@me.", "@mac.")):
            def_source = (db.get_setting("mail_source", "") or "").strip().lower()
            mail_source = "remail" if def_source == "remail" else "icloud_relay"
        else:
            mail_source = (db.get_setting("mail_source", "") or "cf_temp").strip().lower()

    account_for_mail = {**cred, **(mail_account or {"email": email_lower})}
    try:
        return create_mail_provider(mail_source, db.get_mail_settings(), account_for_mail)
    except Exception as e:
        if log_fn:
            log_fn(f"邮箱渠道 {mail_source} 初始化提示: {e}")
        return None


class Fix401Task:
    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.config = config or {}
        self.proxies: list[str] = list(self.config.get("proxies") or [])
        self._proxy_idx = 0
        self._idx_lock = threading.Lock()
        # 并发按用户配置。未填代理才在 start() 里压到 1。
        self.oauth_gate = threading.Semaphore(max(1, int(self.config.get("workers") or 2)))
        self.started_at = time.time()
        self.finished_at = 0.0
        self.cancelled = False
        self.done_count = 0
        self.queue: queue.Queue = queue.Queue(maxsize=4000)
        self._lock = threading.Lock()
        self._last_sse_step: dict[str, float] = {}
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
        # 只填一条就人人用这一条，不换 sid。多条才在你填的列表里轮。
        if len(self.proxies) == 1:
            return self.proxies[0]
        with self._idx_lock:
            p = self.proxies[self._proxy_idx % len(self.proxies)]
            self._proxy_idx += 1
            return p

    def add_email_log(self, email: str, line: str) -> None:
        formatted = f"{time.strftime('%H:%M:%S')} {line}"
        with self._lock:
            it = self.items.get(email)
            if not it:
                return
            logs = it["logs"]
            logs.append(formatted)
            if len(logs) > 160:
                del logs[:-160]

    def _put(self, payload: dict) -> None:
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except Exception:
                pass
            try:
                self.queue.put_nowait(payload)
            except Exception:
                pass

    def set_running(self, email: str, step_text: str) -> None:
        now = time.time()
        with self._lock:
            it = self.items.get(email)
            if not it:
                return
            it["status"] = "running"
            it["step_text"] = step_text
            it["started_at"] = now
        self._put({
            "kind": "progress",
            "email": email,
            "status": "running",
            "step_text": step_text,
            "started_at": now,
        })

    def set_step(self, email: str, step: str, step_text: str) -> None:
        now = time.time()
        send = False
        with self._lock:
            it = self.items.get(email)
            if not it:
                return
            if it.get("step_text") == step_text:
                return
            it["step_text"] = step_text
            last = self._last_sse_step.get(email, 0.0)
            if now - last >= 0.4:
                self._last_sse_step[email] = now
                send = True
        if send:
            self._put({
                "kind": "progress",
                "email": email,
                "status": "running",
                "step": step,
                "step_text": step_text,
            })

    def mark_done(self, email: str, result: dict) -> None:
        now = time.time()
        elapsed = 0.0
        stats_snap = {}
        done_count = 0
        with self._lock:
            self.done_count += 1
            st = str((result or {}).get("status") or "failed")
            method = str((result or {}).get("method") or "")
            if st == "success":
                self.stats["success"] += 1
                if method == "rt_fast":
                    self.stats["rt_fast"] += 1
                else:
                    self.stats["oauth"] += 1
            elif st in ("banned", "deactivated"):
                self.stats["banned"] += 1
            elif st == "need_phone":
                self.stats["need_phone"] += 1
            elif st == "missing":
                self.stats["missing"] += 1
            elif st == "rt_expired":
                self.stats["rt_expired"] += 1
            else:
                self.stats["failed"] += 1
            it = self.items.get(email)
            if it:
                it["status"] = "done"
                it["result"] = result
                it["cpa"] = (result or {}).get("cpa")
                it["sub2api"] = (result or {}).get("sub2api")
                it["finished_at"] = now
                elapsed = round(now - (it["started_at"] or self.started_at), 1)
                it["elapsed"] = elapsed
                it["step_text"] = (result or {}).get("label") or "完成"
            stats_snap = dict(self.stats)
            done_count = self.done_count
        self._put({
            "kind": "progress",
            "email": email,
            "status": "done",
            "result": _public_result(result),
            "step_text": (result or {}).get("label") or "完成",
            "elapsed": elapsed,
            "done_count": done_count,
            "stats": stats_snap,
        })


_tasks: dict[str, Fix401Task] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY = 20


def get_task(task_id: str) -> Optional[Fix401Task]:
    with _tasks_lock:
        return _tasks.get(task_id)


def _prune_locked() -> None:
    if len(_tasks) > _MAX_HISTORY:
        for k in list(_tasks.keys())[:-10]:
            _tasks.pop(k, None)


def preview(emails: list[str]) -> dict:
    unique = list(dict.fromkeys(_norm_email(e) for e in (emails or []) if _norm_email(e)))
    found_map = { _norm_email(r.get("email")): r for r in db.list_registered_by_emails(unique) }
    missing: list[str] = []
    banned: list[str] = []
    queued: list[str] = []
    token_invalid: list[str] = []
    for em in unique:
        cred = found_map.get(em)
        if not cred:
            missing.append(em)
            continue
        if _is_official_banned(cred):
            banned.append(em)
            continue
        queued.append(em)
        extra = cred.get("extra") if isinstance(cred.get("extra"), dict) else {}
        pc = extra.get("plus_check") if isinstance(extra.get("plus_check"), dict) else {}
        pst = str(pc.get("plus_type") or pc.get("status") or "").lower()
        if pst in ("token_invalid", "token_expired") or str(cred.get("account_status") or "").lower() == "token_invalid":
            token_invalid.append(em)
    return {
        "ok": True,
        "parsed": len(unique),
        "found": len(found_map),
        "queued": len(queued),
        "queued_emails": queued,
        "missing": missing,
        "banned": banned,
        "token_invalid": token_invalid,
    }


def check_user_proxy(proxy: str) -> dict:
    p = _normalize_user_proxy(proxy)
    if not p:
        raise ValueError("请填写代理")
    from http_client import create_http_session

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


def _run_one(task: Fix401Task, email: str) -> None:
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被停止"})
        return

    task.set_running(email, "读取号池凭证")
    cred = db.get_registered(email)
    if not cred:
        task.add_email_log(email, "号池没有这个邮箱")
        task.mark_done(email, {"status": "missing", "label": "号池没有", "error": "数据库中无此账号"})
        return
    if _is_official_banned(cred):
        task.add_email_log(email, "库内已是官方封号，跳过，不打 OpenAI")
        task.mark_done(email, {"status": "banned", "label": "已封号，已跳过", "error": "库内已是封号"})
        return

    extra = cred.get("extra") if isinstance(cred.get("extra"), dict) else {}
    bp = extra.get("browser_profile") if isinstance(extra.get("browser_profile"), dict) else {}
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
    # 重登时区跟出口走（只改语言/时区，不换浏览器）。RT 刷新不改画像。
    oauth_country = proxy_country or reg_country
    fp = _account_fingerprint(cred, oauth_country)

    def _persist_success(at: str, rt: str, it: str, method: str, claims: Optional[dict] = None, extra_data: Optional[dict] = None):
        from .oauth_export import _get_account_claims
        claims = claims or _get_account_claims(at)
        plan = str((claims or {}).get("plan_type") or "free")
        account_id = str((claims or {}).get("account_id") or "")
        exp_iso = str((claims or {}).get("exp_iso") or "")
        cpa, sub2 = _build_cpa(email, at, rt, it, plan, account_id, exp_iso)
        meta = {
            "status": "success",
            "updated_at": time.time(),
            "method": method,
            "source": "fix401",
            "plan_type": plan,
            "account_id": account_id,
        }
        payload = {"oauth_export": meta, "fix401": meta}
        if extra_data:
            payload.update(extra_data)
        # 不显式写 oauth_status，避免把 success_phone 降成 success_direct
        db.update_registered_oauth(
            email=email,
            access_token=at,
            refresh_token=rt,
            id_token=it,
            session_token=str(cred.get("session_token") or ""),
            cookie_header=str(cred.get("cookie_header") or ""),
            extra_data=payload,
        )
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
            from .token_refresh_service import refresh_token_fast
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
                from .token_refresh_service import persist_token_refresh_ban
                persist_token_refresh_ban(email, err)
                task.mark_done(email, {
                    "status": "banned",
                    "label": "官方已封号",
                    "error": err[:500],
                })
                return
            task.add_email_log(email, f"RT 刷新未成功（{err[:180]}）")
            if not force_full:
                task.add_email_log(email, "默认不自动重登。要走密码+2FA 请勾选「允许重新登录」")
                task.mark_done(email, {
                    "status": "rt_expired",
                    "label": "RT 已失效",
                    "error": err[:500],
                })
                return

    if not force_full:
        if not existing_rt:
            task.add_email_log(email, "库里没有可用 RT，默认不自动重登")
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

    if not fp.get("user_agent"):
        task.add_email_log(email, "号池没有注册画像，拒绝当新设备重登（只允许 RT 刷新）")
        task.mark_done(email, {
            "status": "failed",
            "label": "无注册画像",
            "error": "库内没有 browser_profile，重登会变成新电脑，已拒绝",
        })
        return
    if not cred.get("device_id"):
        task.add_email_log(email, "号池没有 device_id，重登会被当成新电脑，已拒绝")
        task.mark_done(email, {
            "status": "failed",
            "label": "无设备 ID",
            "error": "库内没有 device_id，拒绝重登",
        })
        return

    task.set_step(email, "oauth", "经用户代理重新登录拿新 RT")
    geo_tip = f"时区跟出口 {oauth_country}" if oauth_country else "时区跟注册"
    task.add_email_log(email, f"启动 Codex OAuth 重登（不接码，钉代理，{geo_tip}）")
    mail = _make_mail_provider(email, cred, log_fn=lambda m: task.add_email_log(email, m))
    try:
        from .oauth_export import execute_codex_oauth_flow
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
            from .token_refresh_service import persist_token_refresh_ban
            persist_token_refresh_ban(email, err)
            task.mark_done(email, {
                "status": "banned",
                "label": "官方已封号",
                "error": err[:500],
            })
            return
        task.add_email_log(email, f"重新登录失败: {err[:300]}")
        db.update_registered_oauth_status(email, "failed", err[:300])
        task.mark_done(email, {"status": "failed", "label": "失败", "error": err[:500]})
        return

    if not flow_res:
        task.mark_done(email, {"status": "failed", "label": "失败", "error": "空结果"})
        return
    st = str(flow_res.get("status") or "")
    if st == "need_phone":
        task.add_email_log(email, "OpenAI 要求绑手机，本功能不接码，已跳过")
        db.update_registered_oauth_status(email, "need_phone", "需要手机号验证 (解401已跳过)")
        task.mark_done(email, {
            "status": "need_phone",
            "label": "需绑手机",
            "error": "OpenAI 要求绑定手机号",
        })
        return
    if st == "cancelled":
        task.mark_done(email, {"status": "cancelled", "label": "已取消"})
        return
    at = flow_res.get("access_token") or ""
    rt = flow_res.get("refresh_token") or ""
    it = flow_res.get("id_token") or ""
    if st not in ("success", "", None) and not at and not rt:
        err = str(flow_res.get("error") or flow_res.get("label") or st)
        if is_official_account_dead(err) or st in _DEAD:
            from .token_refresh_service import persist_token_refresh_ban
            persist_token_refresh_ban(email, err)
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
    emails: list[str],
    proxies: Optional[list[str]] = None,
    proxy_text: str = "",
    proxy_country: str = "",
    workers: int = 2,
    timeout: int = 45,
    force_full_login: bool = False,
) -> dict:
    unique = list(dict.fromkeys(_norm_email(e) for e in (emails or []) if _norm_email(e)))
    if not unique:
        raise ValueError("请填写至少一个邮箱")
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
    task_id = uuid.uuid4().hex[:12]
    task = Fix401Task(task_id, unique, config)

    queued: list[str] = []
    for em in unique:
        cred = db.get_registered(em)
        if not cred:
            task.mark_done(em, {"status": "missing", "label": "号池没有", "error": "数据库中无此账号"})
            continue
        if _is_official_banned(cred):
            task.mark_done(em, {"status": "banned", "label": "已封号，已跳过", "error": "库内已是封号"})
            continue
        queued.append(em)

    with _tasks_lock:
        _prune_locked()
        _tasks[task_id] = task

    if not queued:
        task.finished_at = time.time()
        task._put({
            "kind": "end",
            "task_id": task_id,
            "cancelled": False,
            "stats": dict(task.stats),
            "done_count": task.done_count,
        })
        return {
            "ok": True,
            "task_id": task_id,
            "taskId": task_id,
            "total": len(unique),
            "queued": 0,
            "workers": workers_n,
            "has_proxy": bool(proxy_list),
        }

    def _runner():
        work_q: queue.Queue = queue.Queue()
        for em in queued:
            work_q.put(em)
        max_w = min(config["workers"], len(queued))

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

        if task.cancelled:
            leftover = []
            while True:
                try:
                    leftover.append(work_q.get_nowait())
                except queue.Empty:
                    break
            for em in leftover:
                it = task.items.get(em)
                if it and it.get("status") != "done":
                    task.mark_done(em, {"status": "cancelled", "label": "已取消", "error": "任务被停止"})

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
        "queued": len(queued),
        "workers": workers_n,
        "has_proxy": bool(proxy_list),
    }


def stop(task_id: str) -> bool:
    task = get_task(task_id)
    if not task:
        return False
    task.cancelled = True
    return True


def get_log(task_id: str, email: str) -> list[str]:
    task = get_task(task_id)
    if not task:
        return []
    em = _norm_email(email)
    with task._lock:
        it = task.items.get(em)
        return list(it.get("logs") or []) if it else []


def snapshot(task_id: str) -> Optional[dict]:
    task = get_task(task_id)
    if not task:
        return None
    with task._lock:
        items = []
        for it in task.items.values():
            items.append({
                "email": it["email"],
                "status": it["status"],
                "step_text": it.get("step_text") or "",
                "result": _public_result(it.get("result")),
                "elapsed": it.get("elapsed") or 0,
                "started_at": it.get("started_at") or 0,
                "finished_at": it.get("finished_at") or 0,
            })
        return {
            "task_id": task.task_id,
            "running": bool(not task.finished_at and not task.cancelled),
            "finished": bool(task.finished_at),
            "cancelled": bool(task.cancelled),
            "stats": dict(task.stats),
            "items": items,
            "total": len(task.items),
            "done": task.done_count,
            "has_proxy": bool(task.proxies),
        }


def success_cpa_list(task_id: str) -> list[dict]:
    task = get_task(task_id)
    if not task:
        return []
    rows = []
    with task._lock:
        for it in task.items.values():
            res = it.get("result") or {}
            if res.get("status") == "success" and it.get("cpa"):
                rows.append(it["cpa"])
    return rows
