"""超时未成功的接码号码后台取消。

Vak 没有「列出全部活跃号」接口，只能按租号时落库的 idNum 对账。
扫描间隔 20 秒；是否到期看 settings.sms_idle_cancel_sec（默认 300）。
同一号码最多取消 3 次。
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("sms_idle_sweeper")

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()
_SCAN_SEC = 20
_MAX_TRIES = 3
_STATE = {
    "running": False,
    "started_at": 0.0,
    "last_scan_at": 0.0,
    "last_open": 0,
    "last_cancelled": 0,
    "last_failed": 0,
    "last_skipped": 0,
    "last_error": "",
    "idle_sec": 300,
}


def start() -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    _STOP.clear()
    with _LOCK:
        _STATE["running"] = True
        _STATE["started_at"] = time.time()
        _STATE["last_error"] = ""
    _THREAD = threading.Thread(target=_loop, name="sms-idle-sweeper", daemon=True)
    _THREAD.start()


def snapshot() -> dict:
    from . import db
    alive = _THREAD is not None and _THREAD.is_alive()
    with _LOCK:
        out = dict(_STATE)
    out["running"] = bool(alive)
    out["idle_sec"] = db._sms_idle_cancel_sec()
    out["scan_sec"] = _SCAN_SEC
    out["max_tries"] = _MAX_TRIES
    last = float(out.get("last_scan_at") or 0)
    out["next_scan_at"] = (last + _SCAN_SEC) if last else time.time()
    out["counts"] = db.sms_activation_counts()
    return out


def _retryable_cancel_error(err: str) -> bool:
    s = str(err or "").lower().replace("_", "").replace(" ", "").replace("-", "")
    return any(
        x in s
        for x in (
            "earlycancel",
            "waitchange",
            "请稍后再试",
            "tooearly",
            "稍后",
        )
    )


def cancel_one(provider: str, activation_id: str) -> dict:
    """界面手动取消：立刻 setStatus=end，成功则关单。"""
    from . import db
    kind = str(provider or "").strip()
    aid = str(activation_id or "").strip()
    if not kind or not aid:
        return {"ok": False, "error": "缺少 provider 或 activation_id"}
    row = db.get_sms_activation(kind, aid)
    if not row:
        return {"ok": False, "error": "没有这条接码记录"}
    st = str(row.get("status") or "")
    if st == "verified":
        return {"ok": False, "error": "该号码已校验成功，不能取消"}
    if st == "cancelled":
        return {"ok": True, "already": True}
    try:
        prov = _make_provider(kind)
    except Exception as e:
        return {"ok": False, "error": f"无法创建接码平台: {e}"}
    if prov is None:
        return {"ok": False, "error": "该平台未配置密钥或不支持自动取消"}
    try:
        ok = bool(prov.cancel(aid))
        err = ""
    except Exception as e:
        ok = False
        err = str(e)[:200]
    if ok:
        db.close_sms_activation(
            kind, aid, "cancelled", error="manual",
            from_statuses=("open", "cancel_failed"),
        )
        return {"ok": True}
    if st == "open":
        tries = db.bump_sms_cancel_try(kind, aid, err or "cancel_false")
        if tries >= _MAX_TRIES:
            db.close_sms_activation(kind, aid, "cancel_failed", error=err or "max_tries")
        return {"ok": False, "error": err or "平台未确认取消", "cancel_tries": tries}
    return {"ok": False, "error": err or "平台未确认取消"}


def _loop() -> None:
    from . import db
    idle = db._sms_idle_cancel_sec()
    logger.info("接码超时兜底已启动：超过 %ss 未成功则取消，最多 %s 次", idle, _MAX_TRIES)
    try:
        _sweep()
    except Exception as e:
        logger.warning("接码超时兜底首次扫描失败: %s", e)
        with _LOCK:
            _STATE["last_error"] = str(e)[:200]
    while not _STOP.wait(_SCAN_SEC):
        try:
            _sweep()
        except Exception as e:
            with _LOCK:
                _STATE["last_error"] = str(e)[:200]
            logger.warning("接码超时兜底扫描失败: %s", e)


def _make_provider(kind: str):
    from sms_providers import create_sms_provider, get_provider_class, uses_cdk_pool
    from . import db

    if uses_cdk_pool(kind):
        return None
    try:
        cls = get_provider_class(kind)
    except Exception:
        return None
    cfg = db.get_sms_internal_config(provider=kind)
    if getattr(cls, "needs_api_key", True) and not str(cfg.get("sms_api_key") or "").strip():
        logger.warning("兜底跳过 %s：未配置 API Key，不计入取消次数", kind)
        return None
    return create_sms_provider(kind, cfg)


def _sweep() -> None:
    from . import db

    idle = db._sms_idle_cancel_sec()
    now = time.time()
    rows = db.list_stale_sms_activations(now=now, idle_sec=idle)
    cancelled = 0
    failed = 0
    skipped = 0
    if not rows:
        with _LOCK:
            _STATE["idle_sec"] = idle
            _STATE["last_scan_at"] = now
            _STATE["last_open"] = 0
            _STATE["last_cancelled"] = 0
            _STATE["last_failed"] = 0
            _STATE["last_skipped"] = 0
            _STATE["last_error"] = ""
        return
    cache: dict = {}
    for row in rows:
        kind = str(row.get("provider") or "")
        aid = str(row.get("activation_id") or "")
        if not kind or not aid:
            continue
        if kind not in cache:
            try:
                cache[kind] = _make_provider(kind)
            except Exception as e:
                logger.warning("兜底无法创建接码平台 %s: %s", kind, e)
                cache[kind] = None
        prov = cache[kind]
        if prov is None:
            # 缺密钥 / CDK：不要空耗 3 次重试，否则一分钟就标 cancel_failed。
            skipped += 1
            continue
        try:
            ok = bool(prov.cancel(aid))
            err = ""
        except Exception as e:
            ok = False
            err = str(e)[:200]
        age = int(now - float(row.get("rented_at") or now))
        if ok:
            db.close_sms_activation(kind, aid, "cancelled")
            cancelled += 1
            logger.info(
                "兜底已取消 %s id=%s phone=%s 闲置=%ss",
                kind, aid, row.get("phone") or "", age,
            )
            continue
        if _retryable_cancel_error(err):
            skipped += 1
            logger.info("兜底暂不可取消 %s id=%s %s，下次再试", kind, aid, err)
            continue
        failed += 1
        tries = db.bump_sms_cancel_try(kind, aid, err or "cancel_false")
        logger.warning("兜底取消失败 %s id=%s try=%s/%s %s", kind, aid, tries, _MAX_TRIES, err)
        if tries >= _MAX_TRIES:
            db.close_sms_activation(kind, aid, "cancel_failed", error=err or "max_tries")
    with _LOCK:
        _STATE["idle_sec"] = idle
        _STATE["last_scan_at"] = now
        _STATE["last_open"] = len(rows)
        _STATE["last_cancelled"] = cancelled
        _STATE["last_failed"] = failed
        _STATE["last_skipped"] = skipped
        _STATE["last_error"] = ""
