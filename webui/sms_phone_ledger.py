"""接码号码台账：内存热路径 + SQLite 落库。只跳过拒号，已用号可再次接码。"""
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
_inflight: set[str] = set()
_loaded = False
_load_ms = 0.0


def _ensure_loaded() -> None:
    global _loaded, _load_ms
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        t0 = time.perf_counter()
        data = db.load_active_sms_phone_ledger()
        _rejected.update(data.get("rejected") or [])
        _used.update(data.get("used") or [])
        _loaded = True
        _load_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "[sms_ledger] 预热完成 拒号=%s 已用=%s %.1fms",
            len(_rejected), len(_used), _load_ms,
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
            "load_ms": round(_load_ms, 1),
        }


def normalize(phone: str) -> str:
    return db.normalize_phone_e164(phone)


def should_skip(phone: str) -> str:
    """命中则返回原因，否则空串。已用号不拉黑，同一号还能再接码。"""
    _ensure_loaded()
    e164 = normalize(phone)
    if not e164:
        return ""
    with _lock:
        if e164 in _inflight:
            return "in_flight"
        if e164 in _rejected:
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
            _rejected.discard(e164)
        elif out in ("rejected_openai", "rejected", "already_in_use"):
            _rejected.add(e164)
    return e164
