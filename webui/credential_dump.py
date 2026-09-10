"""接码成品发货格式解析、入库与导出。

卖家常见发货是「一行一个 JSON」（NDJSON），字段与本项目 registered 凭证结构接近：
email / password / totp_secret / access_token / refresh_token / mailbox.pickup_url 等。

同时兼容：
- JSON 数组、单个对象、`{accounts:[...]}`、Sub2API 导入包
- 多对象首尾相接（pretty JSON 连在一起）
- `账号----密码----2FA` 以及带取件 URL 的四段文本
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from . import db, export_formats

logger = logging.getLogger("credential_dump")

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_DUMP_COL_KEYS = {
    "email", "password", "access_token", "session_token", "refresh_token",
    "id_token", "device_id", "csrf_token", "cookie_header",
    "totp_secret", "totp_factor_id", "reg_country", "reg_city", "reg_ip",
    "created_at",
}
_SKIP_EXTRA_KEYS = _DUMP_COL_KEYS | {
    "accessToken", "refreshToken", "idToken", "sessionToken",
    "totp", "two_fa", "2fa", "login_identity", "account_claims_email",
    "mailbox", "mailbox_connection", "mailbox_url", "mailbox_id",
    "version", "db_id", "platform", "last_used", "status",
    "credentials", "extra", "user", "account",
    "_source_line", "_raw_kind",
}


def _s(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _is_email(v: str) -> bool:
    return bool(v) and _EMAIL_RE.match(v) is not None


def _mask_secret(v: str, keep: int = 2) -> str:
    raw = _s(v)
    if not raw:
        return ""
    if len(raw) <= keep * 2:
        return raw[0] + "***" if len(raw) > 1 else "*"
    return f"{raw[:keep]}***{raw[-keep:]}"


def _as_dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _pickup_from_connection(conn: str) -> str:
    raw = _s(conn)
    if not raw:
        return ""
    if "----" in raw:
        parts = raw.split("----")
        for p in parts[1:]:
            p = p.strip()
            if p.startswith("http://") or p.startswith("https://"):
                return p
        return parts[-1].strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return ""


def _build_mail_oauth(obj: dict) -> dict:
    mailbox = obj.get("mailbox")
    existing = obj.get("mail_oauth")
    extra = _as_dict(obj.get("extra"))
    if not existing and isinstance(extra.get("mail_oauth"), dict):
        existing = extra["mail_oauth"]

    out: dict[str, Any] = {}
    if isinstance(existing, dict):
        out.update(existing)
    if isinstance(mailbox, dict):
        for k, v in mailbox.items():
            if k not in out or not out.get(k):
                out[k] = v

    pickup = (
        _s(out.get("pickup_url"))
        or _s(obj.get("mailbox_url"))
        or _s(obj.get("relay_url"))
        or _pickup_from_connection(_s(obj.get("mailbox_connection")))
    )
    if pickup:
        out["pickup_url"] = pickup

    kind = (
        _s(out.get("kind"))
        or _s(out.get("provider"))
        or _s(out.get("adapter_type"))
        or _s(obj.get("adapter_type"))
        or _s(obj.get("provider"))
    )
    if kind:
        out["kind"] = kind
    elif pickup:
        out["kind"] = "remail"

    if not out.get("email"):
        em = _s(obj.get("email")) or _s(out.get("email"))
        if em:
            out["email"] = em
    return out


def _tokens_from(obj: dict) -> dict[str, str]:
    cred = _as_dict(obj.get("credentials"))
    user = _as_dict(obj.get("user"))
    return {
        "access_token": (
            _s(obj.get("access_token")) or _s(obj.get("accessToken"))
            or _s(cred.get("access_token")) or _s(cred.get("accessToken"))
        ),
        "refresh_token": (
            _s(obj.get("refresh_token")) or _s(obj.get("refreshToken"))
            or _s(cred.get("refresh_token")) or _s(cred.get("refreshToken"))
        ),
        "id_token": (
            _s(obj.get("id_token")) or _s(obj.get("idToken"))
            or _s(cred.get("id_token")) or _s(user.get("id_token"))
        ),
        "session_token": (
            _s(obj.get("session_token")) or _s(obj.get("sessionToken"))
            or _s(cred.get("session_token"))
        ),
    }


def _email_from(obj: dict) -> str:
    cred = _as_dict(obj.get("credentials"))
    user = _as_dict(obj.get("user"))
    extra = _as_dict(obj.get("extra"))
    for v in (
        obj.get("email"),
        obj.get("login_identity"),
        obj.get("account_claims_email"),
        cred.get("email"),
        user.get("email"),
        extra.get("email"),
        obj.get("name"),
    ):
        em = _s(v).lower()
        if _is_email(em):
            return em
    return ""


def _totp_from(obj: dict) -> str:
    extra = _as_dict(obj.get("extra"))
    for v in (
        obj.get("totp_secret"),
        obj.get("totp"),
        obj.get("two_fa"),
        obj.get("2fa"),
        extra.get("totp_secret"),
    ):
        s = _s(v)
        if s:
            return s
    return ""


def normalize_account(obj: dict, source_line: int = 0) -> Optional[dict]:
    """把任意发货/导出对象压成 registered 可用的一行。"""
    if not isinstance(obj, dict):
        return None
    email = _email_from(obj)
    if not email:
        return None

    tokens = _tokens_from(obj)
    password = _s(obj.get("password"))
    totp = _totp_from(obj)
    mail_oauth = _build_mail_oauth(obj)
    pickup = _s(mail_oauth.get("pickup_url"))

    extra: dict[str, Any] = {}
    nested_extra = obj.get("extra")
    if isinstance(nested_extra, dict):
        for k, v in nested_extra.items():
            if k in _SKIP_EXTRA_KEYS:
                continue
            extra[k] = v
    for k, v in obj.items():
        if k in _SKIP_EXTRA_KEYS:
            continue
        if k not in extra:
            extra[k] = v
    if mail_oauth:
        extra["mail_oauth"] = mail_oauth
    extra.setdefault("source", "shipment_import")

    created_at = obj.get("created_at")
    try:
        created_at_f = float(created_at) if created_at else 0.0
    except (TypeError, ValueError):
        created_at_f = 0.0

    return {
        "email": email,
        "password": password,
        "access_token": tokens["access_token"],
        "session_token": tokens["session_token"],
        "refresh_token": tokens["refresh_token"],
        "id_token": tokens["id_token"],
        "device_id": _s(obj.get("device_id")),
        "csrf_token": _s(obj.get("csrf_token")),
        "cookie_header": _s(obj.get("cookie_header")),
        "totp_secret": totp,
        "totp_factor_id": _s(obj.get("totp_factor_id")),
        "reg_country": _s(obj.get("reg_country")),
        "reg_city": _s(obj.get("reg_city")),
        "reg_ip": _s(obj.get("reg_ip")),
        "created_at": created_at_f,
        "extra": extra,
        "relay_url": pickup,
        "mail_kind": _s(mail_oauth.get("kind")) or ("remail" if pickup else ""),
        "_source_line": source_line,
        "_raw_kind": "json",
    }


def _parse_delimited_line(line: str, source_line: int) -> tuple[Optional[dict], Optional[str]]:
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None, None
    sep = "----" if "----" in raw else ("---" if "---" in raw else "")
    if not sep:
        return None, "无法识别：既不是 JSON，也没有 ---- 分隔"
    parts = [p.strip() for p in raw.split(sep)]
    email = ""
    password = ""
    totp = ""
    pickup = ""
    for p in parts:
        if not email and _is_email(p.lower()):
            email = p.lower()
            continue
        if p.startswith("http://") or p.startswith("https://"):
            pickup = p
            continue
        if not password:
            password = p
            continue
        if not totp:
            totp = p
            continue
        if not pickup:
            pickup = p
    if not email:
        return None, "文本行没有识别到邮箱"
    obj = {
        "email": email,
        "password": password,
        "totp_secret": totp,
        "mailbox_url": pickup,
    }
    rec = normalize_account(obj, source_line)
    if rec:
        rec["_raw_kind"] = "delimited"
    return rec, None


def _flatten_payload(data: Any, source_line: int = 1) -> list[dict]:
    out: list[dict] = []
    if isinstance(data, list):
        for i, item in enumerate(data):
            out.extend(_flatten_payload(item, source_line=source_line + i))
        return out
    if not isinstance(data, dict):
        return out
    for key in ("accounts", "data", "items", "rows", "records"):
        nested = data.get(key)
        if isinstance(nested, list):
            for i, item in enumerate(nested):
                out.extend(_flatten_payload(item, source_line=source_line + i))
            if out:
                return out
    rec = normalize_account(data, source_line)
    if rec:
        out.append(rec)
    return out


def parse_credential_dump(text: str) -> tuple[list[dict], list[dict]]:
    """解析发货文本，返回 (账号列表, 错误列表)。"""
    raw = (text or "").replace("\ufeff", "").strip()
    accounts: list[dict] = []
    errors: list[dict] = []
    if not raw:
        return accounts, errors

    try:
        data = json.loads(raw)
        found = _flatten_payload(data, source_line=1)
        if found:
            return found, errors
        errors.append({"line": 1, "error": "JSON 已解析但没有识别到邮箱账号", "raw": raw[:80]})
        return accounts, errors
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    idx = 0
    n = len(raw)
    while idx < n:
        while idx < n and raw[idx] in " \t\r\n,;":
            idx += 1
        if idx >= n:
            break
        line_no = raw.count("\n", 0, idx) + 1
        ch = raw[idx]
        if ch in "{[":
            try:
                obj, end = decoder.raw_decode(raw, idx)
            except json.JSONDecodeError as e:
                line_end = raw.find("\n", idx)
                if line_end < 0:
                    line_end = n
                snippet = raw[idx:line_end].strip()[:120]
                errors.append({
                    "line": line_no,
                    "error": f"JSON 解析失败: {e.msg}",
                    "raw": snippet,
                })
                idx = line_end + 1 if line_end < n else n
                continue
            found = _flatten_payload(obj, source_line=line_no)
            if found:
                accounts.extend(found)
            else:
                errors.append({
                    "line": line_no,
                    "error": "JSON 对象没有识别到邮箱",
                    "raw": raw[idx:min(idx + 80, end)].replace("\n", " "),
                })
            idx = end
            continue

        line_end = raw.find("\n", idx)
        if line_end < 0:
            line_end = n
        line = raw[idx:line_end].strip()
        idx = line_end + 1 if line_end < n else n
        if not line:
            continue
        rec, err = _parse_delimited_line(line, line_no)
        if rec:
            accounts.append(rec)
        elif err:
            errors.append({"line": line_no, "error": err, "raw": line[:120]})

    return accounts, errors


def _has_any_token(rec: dict) -> bool:
    return bool(
        _s(rec.get("access_token"))
        or _s(rec.get("session_token"))
        or _s(rec.get("refresh_token"))
    )


def _has_login_secret(rec: dict) -> bool:
    return bool(_s(rec.get("password")) or _s(rec.get("totp_secret")))


def _dedup_keep_last(accounts: list[dict]) -> tuple[list[dict], int]:
    by_email: dict[str, dict] = {}
    dups = 0
    for rec in accounts:
        em = rec["email"]
        if em in by_email:
            dups += 1
        by_email[em] = rec
    return list(by_email.values()), dups


def analyze_credential_dump(text: str) -> dict:
    accounts, errors = parse_credential_dump(text)
    unique, internal_dups = _dedup_keep_last(accounts)

    existing: set[str] = set()
    try:
        con = db._conn()
        existing = {
            (r[0] or "").lower()
            for r in con.execute("SELECT lower(email) FROM registered").fetchall()
        }
    except Exception as e:
        logger.warning(f"analyze 读库失败: {e}")

    preview = []
    registered_dup = 0
    brand_new = 0
    seen: set[str] = set()
    for rec in accounts:
        em = rec["email"]
        is_internal = em in seen
        seen.add(em)
        in_db = em in existing
        if in_db:
            registered_dup += 1
            db_status, db_label = "registered", "库内已有"
        elif is_internal:
            db_status, db_label = "internal_dup", "批次内重复"
        else:
            brand_new += 1
            db_status, db_label = "brand_new", "全新"
        if len(preview) < 40:
            preview.append({
                "line": rec.get("_source_line") or 0,
                "email": em,
                "password_masked": _mask_secret(rec.get("password") or ""),
                "has_password": bool(_s(rec.get("password"))),
                "has_2fa": bool(_s(rec.get("totp_secret"))),
                "at_len": len(_s(rec.get("access_token"))),
                "rt_len": len(_s(rec.get("refresh_token"))),
                "st_len": len(_s(rec.get("session_token"))),
                "has_pickup": bool(_s(rec.get("relay_url"))),
                "has_token": _has_any_token(rec),
                "raw_kind": rec.get("_raw_kind") or "json",
                "db_status": db_status,
                "db_label": db_label,
            })

    return {
        "ok": True,
        "total_objects": len(accounts),
        "valid_count": len(unique),
        "invalid_count": len(errors),
        "internal_dup_count": internal_dups,
        "registered_dup_count": len({r["email"] for r in unique if r["email"] in existing}),
        "brand_new_count": len({r["email"] for r in unique if r["email"] not in existing}),
        "with_password": sum(1 for r in unique if _s(r.get("password"))),
        "with_2fa": sum(1 for r in unique if _s(r.get("totp_secret"))),
        "with_at": sum(1 for r in unique if _s(r.get("access_token"))),
        "with_rt": sum(1 for r in unique if _s(r.get("refresh_token"))),
        "with_st": sum(1 for r in unique if _s(r.get("session_token"))),
        "with_pickup": sum(1 for r in unique if _s(r.get("relay_url"))),
        "with_token": sum(1 for r in unique if _has_any_token(r)),
        "preview_rows": preview,
        "errors": errors[:80],
        "dup_rate": round((internal_dups / len(accounts) * 100) if accounts else 0, 1),
    }


def to_export_rows(accounts: list[dict]) -> list[dict]:
    rows = []
    unique, _ = _dedup_keep_last(accounts)
    for rec in unique:
        extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
        rows.append({
            "email": rec["email"],
            "password": rec.get("password") or "",
            "access_token": rec.get("access_token") or "",
            "session_token": rec.get("session_token") or "",
            "refresh_token": rec.get("refresh_token") or "",
            "id_token": rec.get("id_token") or "",
            "totp_secret": rec.get("totp_secret") or "",
            "relay_url": rec.get("relay_url") or "",
            "extra": extra,
            "extra_json": json.dumps(extra, ensure_ascii=False) if extra else None,
            "chatgpt_user_id": extra.get("chatgpt_user_id") or "",
            "chatgpt_account_id": extra.get("chatgpt_account_id") or "",
        })
    return rows


def export_credential_dump(text: str, fmt_id: str, delimiter: str = "----") -> dict:
    accounts, errors = parse_credential_dump(text)
    if not accounts:
        raise ValueError("没有解析到任何账号")
    fmt = export_formats.get_format(fmt_id)
    if fmt is None:
        raise ValueError(f"未知导出格式: {fmt_id}")
    rows = to_export_rows(accounts)
    if fmt_id == "sub2api_json":
        tokened = [
            r for r in rows
            if _s(r.get("access_token")) or _s(r.get("refresh_token")) or _s(r.get("email"))
        ]
        if not tokened:
            raise ValueError(
                "没有可供导出为 Sub2 JSON 的账号数据。"
            )
        rows = tokened
    rows, skipped_incomplete = export_formats.filter_rows_for_format(rows, fmt)
    filename = fmt.filename
    mime = fmt.mime
    delim = delimiter if delimiter is not None else "----"
    base = {
        "ok": True,
        "count": len(rows),
        "skipped": len(skipped_incomplete),
        "filename": filename,
        "label": fmt.label,
        "format": fmt.id,
        "mode": fmt.mode,
        "mime": mime,
        "emails": [r.get("email") or "" for r in rows],
        "skipped_errors": len(errors),
        "delimiter": delim,
    }
    if fmt.mode == "download":
        blob = export_formats.render_bytes(rows, fmt)
        import base64
        return {
            **base,
            "b64": base64.b64encode(blob).decode("ascii"),
            "size": len(blob),
        }
    return {**base, "text": export_formats.render_text(rows, fmt, delimiter=delim)}


def _merge_extra(old_raw: Any, new_extra: dict) -> dict:
    old: dict = {}
    if old_raw:
        try:
            parsed = json.loads(old_raw) if isinstance(old_raw, str) else old_raw
            if isinstance(parsed, dict):
                old = parsed
        except Exception:
            old = {}
    merged = dict(old)
    for k, v in (new_extra or {}).items():
        if k == "mail_oauth" and isinstance(v, dict):
            prev = merged.get("mail_oauth") if isinstance(merged.get("mail_oauth"), dict) else {}
            mo = dict(prev)
            mo.update({ik: iv for ik, iv in v.items() if iv not in (None, "")})
            merged["mail_oauth"] = mo
        elif v not in (None, ""):
            merged[k] = v
    return merged


def import_credential_dump(text: str, strategy: str = "smart_merge") -> dict:
    """把发货账号写入 registered，并把取件链接落到 extra.mail_oauth / 号池。"""
    t0 = time.time()
    accounts, errors = parse_credential_dump(text)
    unique, internal_dups = _dedup_keep_last(accounts)
    strategy = (strategy or "smart_merge").strip() or "smart_merge"

    inserted = updated = skipped = skipped_empty = 0
    now = time.time()

    with db._lock:
        con = db._conn()
        existing_rows = {
            (r["email"] or "").lower(): dict(r)
            for r in con.execute("SELECT * FROM registered").fetchall()
        }
        pool_rows = {
            (r["email"] or "").lower(): dict(r)
            for r in con.execute("SELECT email, relay_url, kind, status FROM outlook_accounts").fetchall()
        }

        insert_reg: list[tuple] = []
        update_reg: list[tuple] = []
        upsert_pool: list[tuple] = []

        for rec in unique:
            em = rec["email"]
            if not _has_any_token(rec) and not _has_login_secret(rec):
                skipped_empty += 1
                continue

            if strategy == "skip_duplicates" and em in existing_rows:
                skipped += 1
                continue

            extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
            at_val = rec.get("access_token") or ""
            exp = db.jwt_exp_unix(at_val)
            created = rec.get("created_at") or now
            if created <= 0:
                created = now

            pickup = rec.get("relay_url") or ""
            kind = rec.get("mail_kind") or "remail"

            if em in existing_rows and strategy != "overwrite":
                old = existing_rows[em]
                password = rec.get("password") or old.get("password") or ""
                totp = rec.get("totp_secret") or old.get("totp_secret") or ""
                totp_fid = rec.get("totp_factor_id") or old.get("totp_factor_id") or ""
                at = at_val or old.get("access_token") or ""
                st = rec.get("session_token") or old.get("session_token") or ""
                rt = rec.get("refresh_token") or old.get("refresh_token") or ""
                it = rec.get("id_token") or old.get("id_token") or ""
                device = rec.get("device_id") or old.get("device_id") or ""
                csrf = rec.get("csrf_token") or old.get("csrf_token") or ""
                cookie = rec.get("cookie_header") or old.get("cookie_header") or ""
                country = rec.get("reg_country") or old.get("reg_country") or ""
                city = rec.get("reg_city") or old.get("reg_city") or ""
                ip = rec.get("reg_ip") or old.get("reg_ip") or ""
                merged_extra = _merge_extra(old.get("extra_json"), extra)
                new_exp = db.jwt_exp_unix(at) or old.get("at_expires_at") or 0
                update_reg.append((
                    password, at, st, rt, it, device, csrf, cookie,
                    totp, totp_fid, country, city, ip,
                    json.dumps(merged_extra, ensure_ascii=False),
                    new_exp, em,
                ))
                updated += 1
            elif em in existing_rows and strategy == "overwrite":
                old = existing_rows[em]
                merged_extra = _merge_extra(old.get("extra_json"), extra)
                at = at_val or old.get("access_token") or ""
                update_reg.append((
                    rec.get("password") or old.get("password") or "",
                    at,
                    rec.get("session_token") or "",
                    rec.get("refresh_token") or old.get("refresh_token") or "",
                    rec.get("id_token") or "",
                    rec.get("device_id") or "",
                    rec.get("csrf_token") or "",
                    rec.get("cookie_header") or "",
                    rec.get("totp_secret") or old.get("totp_secret") or "",
                    rec.get("totp_factor_id") or old.get("totp_factor_id") or "",
                    rec.get("reg_country") or old.get("reg_country") or "",
                    rec.get("reg_city") or old.get("reg_city") or "",
                    rec.get("reg_ip") or old.get("reg_ip") or "",
                    json.dumps(merged_extra, ensure_ascii=False),
                    db.jwt_exp_unix(at),
                    em,
                ))
                updated += 1
            else:
                insert_reg.append((
                    em,
                    rec.get("password") or "",
                    at_val,
                    rec.get("session_token") or "",
                    rec.get("refresh_token") or "",
                    rec.get("id_token") or "",
                    rec.get("device_id") or "",
                    rec.get("csrf_token") or "",
                    rec.get("cookie_header") or "",
                    rec.get("totp_secret") or "",
                    rec.get("totp_factor_id") or "",
                    rec.get("reg_country") or "",
                    rec.get("reg_city") or "",
                    rec.get("reg_ip") or "",
                    json.dumps(extra, ensure_ascii=False) if extra else None,
                    created,
                    exp,
                ))
                inserted += 1

            if pickup:
                upsert_pool.append((
                    em, rec.get("password") or "", pickup, kind, now, now,
                ))

        if insert_reg:
            con.executemany(
                "INSERT INTO registered "
                "(email, password, access_token, session_token, refresh_token, "
                "id_token, device_id, csrf_token, cookie_header, "
                "totp_secret, totp_factor_id, reg_country, reg_city, reg_ip, "
                "extra_json, created_at, at_expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                insert_reg,
            )
        if update_reg:
            con.executemany(
                "UPDATE registered SET "
                "password=?, access_token=?, session_token=?, refresh_token=?, id_token=?, "
                "device_id=?, csrf_token=?, cookie_header=?, "
                "totp_secret=?, totp_factor_id=?, reg_country=?, reg_city=?, reg_ip=?, "
                "extra_json=?, at_expires_at=? "
                "WHERE lower(email)=?",
                update_reg,
            )
        if upsert_pool:
            for em, pw, pickup, kind, imported_at, finished_at in upsert_pool:
                old = pool_rows.get(em)
                if old:
                    con.execute(
                        "UPDATE outlook_accounts SET "
                        "relay_url=?, kind=?, password=COALESCE(NULLIF(?, ''), password) "
                        "WHERE lower(email)=?",
                        (pickup, kind, pw, em),
                    )
                else:
                    con.execute(
                        "INSERT INTO outlook_accounts "
                        "(email, password, client_id, refresh_token, relay_url, kind, "
                        "status, imported_at, finished_at, fail_reason) "
                        "VALUES (?, ?, '', '', ?, ?, 'done', ?, ?, 'shipment_import')",
                        (em, pw, pickup, kind, imported_at, finished_at),
                    )

        con.commit()
        db.invalidate_registered_caches()

    return {
        "ok": True,
        "parsed": len(accounts),
        "unique": len(unique),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "skipped_empty": skipped_empty,
        "internal_dups": internal_dups,
        "invalid": len(errors),
        "errors": errors[:80],
        "cost_seconds": round(time.time() - t0, 3),
    }
