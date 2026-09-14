"""接码号码台账：内存热路径 + SQLite 落库。

从未成功过的官方拒号才跳过。已成功接过 GPT 的号默认还能再用，满 3 次才跳过。
"""
from __future__ import annotations

import logging
import threading
import time

try:
    from . import db
except ImportError:
    import db

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_rejected: set[str] = set()
_used: set[str] = set()
_used_count: dict[str, int] = {}
_inflight: set[str] = set()
_loaded = False
_load_ms = 0.0
_MAX_USES = 3


def _ensure_loaded() -> None:
    global _loaded, _load_ms, _MAX_USES
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        t0 = time.perf_counter()
        data = db.load_active_sms_phone_ledger()
        _rejected.update(data.get("rejected") or [])
        _used.update(data.get("used") or [])
        for k, v in (data.get("used_count") or {}).items():
            try:
                _used_count[str(k)] = int(v or 0)
            except (TypeError, ValueError):
                continue
        try:
            _MAX_USES = int(getattr(db, "SMS_PHONE_MAX_USES", 3) or 3)
        except (TypeError, ValueError):
            _MAX_USES = 3
        _loaded = True
        _load_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "[sms_ledger] 预热完成 拒号=%s 已用=%s 满次跳过=%s %.1fms",
            len(_rejected), len(_used), _MAX_USES, _load_ms,
        )


def warmup() -> dict:
    """授权启动前把有效拒号/已用号装进内存，避免第一枚号码撞 SQLite。"""
    _ensure_loaded()
    return stats()


def stats() -> dict:
    _ensure_loaded()
    with _lock:
        return {
            "rejected": len(_rejected),
            "used": len(_used),
            "in_flight": len(_inflight),
            "max_uses": _MAX_USES,
            "load_ms": round(_load_ms, 1),
        }


def normalize(phone: str) -> str:
    return db.normalize_phone_e164(phone)


def used_count(phone: str) -> int:
    _ensure_loaded()
    e164 = normalize(phone)
    if not e164:
        return 0
    with _lock:
        return int(_used_count.get(e164, 0) or 0)


def should_skip(phone: str) -> str:
    """命中则返回原因，否则空串。已用号未满 3 次不跳过。"""
    _ensure_loaded()
    e164 = normalize(phone)
    if not e164:
        return ""
    with _lock:
        if e164 in _inflight:
            return "in_flight"
        n = int(_used_count.get(e164, 0) or 0)
        if n >= _MAX_USES:
            return "used_quota"
        if e164 in _rejected and n <= 0:
            return "rejected_openai"
    return ""


def mark_inflight(phone: str) -> str:
    _ensure_loaded()
    e164 = normalize(phone)
    if not e164:
        return ""
    with _lock:
        _inflight.add(e164)
    return e164


def clear_inflight(phone: str) -> None:
    e164 = normalize(phone)
    if not e164:
        return
    with _lock:
        _inflight.discard(e164)


def remember(
    phone: str,
    outcome: str,
    *,
    country: str = "",
    price_tier: str = "",
    provider: str = "vaksms",
    activation_id: str = "",
    reject_reason: str = "",
    submitted_to_openai: bool = False,
    email: str = "",
    task_id: str = "",
) -> str:
    _ensure_loaded()
    out = str(outcome or "").strip().lower()
    # 跳过/超时/取消不能覆盖「已拒号/已成功」记录，否则重启后脏号会再打给 OpenAI。
    if out in ("skipped_unsubmitted", "timeout_no_sms", "cancelled", "session_expired"):
        clear_inflight(phone)
        return normalize(phone)
    e164 = db.upsert_sms_phone_ledger(
        phone,
        outcome=outcome,
        country=country,
        price_tier=price_tier,
        provider=provider,
        activation_id=activation_id,
        reject_reason=reject_reason,
        submitted_to_openai=submitted_to_openai,
        email=email,
        task_id=task_id,
    )
    if not e164:
        return ""
    with _lock:
        _inflight.discard(e164)
        if out in ("success", "used_success"):
            _used.add(e164)
            _used_count[e164] = int(_used_count.get(e164, 0) or 0) + 1
            if _used_count[e164] < _MAX_USES:
                _rejected.discard(e164)
            else:
                _rejected.add(e164)
        elif out in ("rejected_openai", "rejected", "already_in_use"):
            n = int(_used_count.get(e164, 0) or 0)
            if n >= _MAX_USES or n <= 0:
                _rejected.add(e164)
    return e164
