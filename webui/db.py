"""SQLite 号池 + 注册结果存储。

表结构：
  outlook_accounts: 接码号池（多种邮箱混放，kind 列区分 + 状态机）
  registered:       注册成功结果（凭证 JSON）

关于 outlook_accounts 这个表名：
    它现在装的不止 outlook（还有 gmail / icloud / qq ...），名字已经不准，
    但改表名要动迁移和一堆 SQL，收益只是好看一点，风险不值。
    真正区分类型的是 kind 列。

凭证字段用「并集列」而不是 extra_json：
    outlook/gmail 用 password+client_id+refresh_token，
    icloud 这类中转只用 relay_url，各自把不用的列留空。
    几种邮箱的规模下，并集列比 JSON 好 —— 能建索引、能加约束、
    SQL 里直接看得见。加新邮箱时如果要新字段，就再 ALTER 加一列。
"""
from __future__ import annotations

import base64
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB_PATH = Path(__file__).resolve().parent / "webui.db"

_lock = threading.Lock()  # SQLite 写入串行化


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    con = _conn()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS outlook_accounts (
            email           TEXT PRIMARY KEY,
            password        TEXT,
            client_id       TEXT,
            refresh_token   TEXT,
            relay_url       TEXT,       -- 中转取码 URL（icloud 类用，其余留空）
            kind            TEXT NOT NULL DEFAULT 'outlook',
                            -- 邮箱类型，对应 mail_providers 注册表的 kind
            status          TEXT NOT NULL DEFAULT 'available',
                            -- available / in_use / done / failed
            imported_at     REAL,
            claimed_at      REAL,
            finished_at     REAL,
            fail_reason     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_outlook_status ON outlook_accounts(status);
        -- idx_outlook_kind 不在这里建：老库此刻还没有 kind 列，
        -- 建索引会当场报错。放到下面补完列之后再建。

        CREATE TABLE IF NOT EXISTS settings (
            key     TEXT PRIMARY KEY,
            value   TEXT
        );

        CREATE TABLE IF NOT EXISTS registered (
            email           TEXT PRIMARY KEY,
            password        TEXT,
            access_token    TEXT,
            session_token   TEXT,
            refresh_token   TEXT,
            id_token        TEXT,
            device_id       TEXT,
            csrf_token      TEXT,
            cookie_header   TEXT,
            totp_secret     TEXT,
            totp_factor_id  TEXT,
            reg_country     TEXT,
            reg_city        TEXT,
            reg_ip          TEXT,
            extra_json      TEXT,
            oa_check        TEXT,
            created_at      REAL
        );

        CREATE TABLE IF NOT EXISTS runs (
            run_id          TEXT PRIMARY KEY,
            email           TEXT,
            status          TEXT,        -- running / done / failed
            started_at      REAL,
            finished_at     REAL,
            log_path        TEXT,
            error           TEXT,
            error_category  TEXT         -- network / account / unknown
        );
    """)
    con.commit()
    # 老 DB migrate：error_category 在后期才加，对已建表补列
    cur = con.execute("PRAGMA table_info(runs)")
    cols = {r[1] for r in cur.fetchall()}
    if "error_category" not in cols:
        con.execute("ALTER TABLE runs ADD COLUMN error_category TEXT")
        con.commit()

    # 老 DB migrate：号池多邮箱混放（kind / relay_url 在后期才加）
    # 存量行全部是 outlook 时代导进去的，DEFAULT 'outlook' 正好把它们
    # 归位，不需要额外 UPDATE。重复执行无副作用。
    cur = con.execute("PRAGMA table_info(outlook_accounts)")
    acc_cols = {r[1] for r in cur.fetchall()}
    if "kind" not in acc_cols:
        con.execute(
            "ALTER TABLE outlook_accounts ADD COLUMN kind TEXT NOT NULL DEFAULT 'outlook'"
        )
        con.commit()
    if "relay_url" not in acc_cols:
        con.execute("ALTER TABLE outlook_accounts ADD COLUMN relay_url TEXT")
        con.commit()
    # 索引建在补列之后，否则老库上 CREATE INDEX 会因为没有 kind 列而失败
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_outlook_kind ON outlook_accounts(kind, status)"
    )
    con.commit()

    # 老 DB migrate：registered 的 2FA 两列（totp_secret / totp_factor_id）后期才加。
    # secret 一次性下发、服务端取不回，务必单独补列持久化。重复执行无副作用。
    cur = con.execute("PRAGMA table_info(registered)")
    reg_cols = {r[1] for r in cur.fetchall()}
    if "totp_secret" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN totp_secret TEXT")
        con.commit()
    if "totp_factor_id" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN totp_factor_id TEXT")
        con.commit()
    if "oa_check" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN oa_check TEXT")
        con.commit()
    if "reg_country" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN reg_country TEXT")
        con.commit()
    if "reg_city" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN reg_city TEXT")
        con.commit()
    if "reg_ip" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN reg_ip TEXT")
        con.commit()
    if "oauth_status" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN oauth_status TEXT DEFAULT ''")
        con.commit()
    if "oauth_updated_at" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN oauth_updated_at REAL")
        con.commit()

    # 自动清理历史中没有任何凭证（AT/ST/RT 全为空）的未完成半成品脏数据
    con.execute("""
        DELETE FROM registered
        WHERE (access_token IS NULL OR access_token = '')
          AND (session_token IS NULL OR session_token = '')
          AND (refresh_token IS NULL OR refresh_token = '')
    """)
    con.commit()

    con.execute("""
        CREATE TABLE IF NOT EXISTS oauth_attempt_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            task_id TEXT,
            email TEXT,
            email_domain TEXT,
            mail_kind TEXT,
            outcome TEXT,
            error_class TEXT,
            error_text TEXT,
            duration_ms INTEGER,
            has_password INTEGER,
            has_totp INTEGER,
            has_mail_cred INTEGER,
            account_age_days REAL,
            plan_type TEXT,
            proxy_country TEXT,
            proxy_host TEXT,
            impersonate TEXT,
            browser_type TEXT,
            ua_family TEXT,
            screen TEXT,
            timezone TEXT,
            lang TEXT,
            first_page_type TEXT,
            login_path TEXT,
            need_otp INTEGER,
            need_phone INTEGER,
            phone_verified INTEGER,
            sms_enabled INTEGER,
            sms_provider TEXT,
            sms_country TEXT,
            sms_country_used TEXT,
            sms_provider_ids TEXT,
            sms_except_provider_ids TEXT,
            sms_price_spec TEXT,
            sms_phone_prefix TEXT,
            sms_cost REAL,
            sms_operator TEXT,
            sms_attempts INTEGER,
            continue_page_type TEXT,
            continue_kind TEXT,
            extra_json TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_oauth_feat_outcome ON oauth_attempt_features(outcome, created_at)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_oauth_feat_email ON oauth_attempt_features(email, created_at)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_oauth_feat_combo ON oauth_attempt_features(proxy_country, impersonate, sms_country, outcome)"
    )
    con.commit()


# ──────────────────────── outlook 号池 ────────────────────────


def parse_lines(text: str, kind: str = "") -> list[dict]:
    """解析导入文本，委托给 mail_providers 注册表。

    kind 指定 → 用该 provider 的格式解析（推荐）
    kind 为空 → 按段数猜（段数唯一时才行，Outlook/Gmail 都是 4 段会猜不出）

    非法行抛 ImportValidationError（带行号和原因），**不再静默跳过**。
    以前这里是 `if len(parts) != 4: continue`，用户看到"导入成功"
    但号少了几个，完全没法排查。
    """
    from mail_providers import parse_import_text

    return parse_import_text(text or "", kind)


def import_accounts(text: str, kind: str = "") -> dict:
    """批量入库。已存在的 email 仅在凭证变化时更新。

    若邮箱已在 registered（已注册结果库）中存在，自动将其 4 段授权凭证同步写入
    registered.extra_json.mail_oauth（终身绑定接码凭证），但不作为待注册号入库/重置
    为 available，自动跳过老号，防止老号被重新跑注册报错。
    """
    rows = parse_lines(text, kind)
    now = time.time()
    inserted = updated = skipped = skipped_registered = 0
    with _lock:
        con = _conn()
        for r in rows:
            row_kind = r.get("kind") or kind or "outlook"
            # 凭证并集：不同 provider 用不同子集，没有的留空字符串
            password = r.get("password", "") or ""
            client_id = r.get("client_id", "") or ""
            refresh = r.get("refresh_token", "") or ""
            relay = r.get("relay_url", "") or ""
            em = r["email"].strip().lower()

            # 1. 检查是否在 registered（已注册库）中已存在
            reg_row = con.execute(
                "SELECT extra_json FROM registered WHERE lower(email)=?",
                (em,),
            ).fetchone()
            if reg_row:
                # 已注册老号：自动将 4 段取件凭证更新到 registered.extra_json.mail_oauth
                if client_id or refresh or password:
                    try:
                        ex = json.loads(reg_row["extra_json"]) if reg_row["extra_json"] else {}
                    except Exception:
                        ex = {}
                    ex["mail_oauth"] = {
                        "client_id": client_id,
                        "refresh_token": refresh,
                        "password": password,
                        "kind": row_kind,
                    }
                    con.execute(
                        "UPDATE registered SET extra_json=? WHERE lower(email)=?",
                        (json.dumps(ex, ensure_ascii=False), em),
                    )

                # 确保 outlook_accounts 号池中将其标记为已完成（done），避免进入待注册队列
                con.execute(
                    "UPDATE outlook_accounts SET status='done', finished_at=?, fail_reason='already_registered' WHERE lower(email)=?",
                    (now, em),
                )
                skipped_registered += 1
                continue

            # 2. 未注册新号：正常入库 outlook_accounts
            cur = con.execute(
                "SELECT refresh_token, relay_url, kind FROM outlook_accounts WHERE lower(email)=?",
                (em,),
            )
            existing = cur.fetchone()
            if existing is None:
                con.execute(
                    "INSERT INTO outlook_accounts(email, password, client_id, refresh_token, "
                    "relay_url, kind, status, imported_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'available', ?)",
                    (em, password, client_id, refresh, relay, row_kind, now),
                )
                inserted += 1
            elif (
                (existing["refresh_token"] or "") != refresh
                or (existing["relay_url"] or "") != relay
                or (existing["kind"] or "") != row_kind
            ):
                # 凭证或类型变了 → 覆盖并重置为可用
                con.execute(
                    "UPDATE outlook_accounts SET refresh_token=?, password=?, client_id=?, "
                    "relay_url=?, kind=?, status='available', imported_at=?, fail_reason=NULL "
                    "WHERE lower(email)=?",
                    (refresh, password, client_id, relay, row_kind, now, em),
                )
                updated += 1
            else:
                skipped += 1

        con.commit()
    return {
        "parsed": len(rows),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "skipped_registered": skipped_registered,
    }


def clean_registered_from_pool(mode: str = "delete") -> dict:
    """比对号池与本地已注册库，清理号池中所有已在 registered 表中存在的账号。

    自动将 4 段授权凭证同步备份到 registered.extra_json.mail_oauth，
    然后将这些已注册号从待注册号池（outlook_accounts）中彻底移除（或更新为 done 状态）。
    """
    now = time.time()
    with _lock:
        con = _conn()
        cur = con.execute("""
            SELECT a.email, a.password, a.client_id, a.refresh_token, a.kind, a.status, r.extra_json
            FROM outlook_accounts a
            INNER JOIN registered r ON lower(a.email) = lower(r.email)
            WHERE a.status IN ('available', 'in_use', 'failed')
        """)
        rows = cur.fetchall()
        cleaned = len(rows)
        for r in rows:
            em = r["email"].strip().lower()
            client_id = r["client_id"] or ""
            refresh = r["refresh_token"] or ""
            password = r["password"] or ""
            row_kind = r["kind"] or "outlook"

            if client_id or refresh or password:
                try:
                    ex = json.loads(r["extra_json"]) if r["extra_json"] else {}
                except Exception:
                    ex = {}
                ex["mail_oauth"] = {
                    "client_id": client_id,
                    "refresh_token": refresh,
                    "password": password,
                    "kind": row_kind,
                }
                con.execute(
                    "UPDATE registered SET extra_json=? WHERE lower(email)=?",
                    (json.dumps(ex, ensure_ascii=False), em),
                )

            if mode == "delete":
                con.execute("DELETE FROM outlook_accounts WHERE lower(email)=?", (em,))
            else:
                con.execute(
                    "UPDATE outlook_accounts SET status='done', finished_at=?, fail_reason='already_registered' WHERE lower(email)=?",
                    (now, em),
                )
        con.commit()
    return {"cleaned": cleaned}


def count_accounts(status: str = "", kind: str = "") -> int:
    con = _conn()
    sql = "SELECT COUNT(*) FROM outlook_accounts"
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if kind:
        where.append("kind=?")
        args.append(kind.strip().lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    return con.execute(sql, args).fetchone()[0]


def list_accounts(
    status: str = "", limit: int = 50, offset: int = 0, kind: str = ""
) -> list[dict]:
    con = _conn()
    sql = "SELECT * FROM outlook_accounts"
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if kind:
        where.append("kind=?")
        args.append(kind.strip().lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY imported_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def stats_by_kind() -> dict:
    """按邮箱类型分组统计，给 WebUI 顶部展示"每种邮箱各有多少号"。"""
    con = _conn()
    cur = con.execute(
        "SELECT kind, status, COUNT(*) AS n FROM outlook_accounts GROUP BY kind, status"
    )
    out: dict[str, dict] = {}
    for r in cur.fetchall():
        k = r["kind"] or "outlook"
        slot = out.setdefault(
            k, {"available": 0, "in_use": 0, "done": 0, "failed": 0, "total": 0}
        )
        slot[r["status"]] = r["n"]
        slot["total"] += r["n"]
    return out


def get_account(email: str) -> Optional[dict]:
    con = _conn()
    cur = con.execute("SELECT * FROM outlook_accounts WHERE email=?", (email.lower(),))
    row = cur.fetchone()
    return dict(row) if row else None


def claim_account(email: str) -> Optional[dict]:
    """原子 claim 指定邮箱（available / failed -> in_use）。

    failed 也允许重试 claim：之前 OpenAI 风控误判 / 网络抖动等导致 fail 的号
    应允许用户手动重试，已 done 的号才禁止重 claim（防误覆盖凭证）。

    按 email 指定时不过滤 kind —— 用户点名要这个号，它是什么类型
    由记录自己的 kind 列说了算，调用方读 account["kind"] 即可。
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    with _lock:
        con = _conn()
        # 前置校验：若该号已在 registered 表中存在，自动归档为 done 并拦截
        is_reg = con.execute("SELECT 1 FROM registered WHERE lower(email)=?", (email,)).fetchone()
        if is_reg:
            con.execute(
                "UPDATE outlook_accounts SET status='done', finished_at=?, fail_reason='already_registered' "
                "WHERE lower(email)=?",
                (time.time(), email),
            )
            con.commit()
            return None

        cur = con.execute(
            "SELECT * FROM outlook_accounts WHERE lower(email)=? AND status IN ('available', 'failed')",
            (email,),
        )
        row = cur.fetchone()
        if not row:
            return None
        rc = con.execute(
            "UPDATE outlook_accounts SET status='in_use', claimed_at=?, fail_reason=NULL "
            "WHERE lower(email)=? AND status IN ('available', 'failed')",
            (time.time(), email),
        )
        con.commit()
        if rc.rowcount != 1:
            return None
        return dict(row)


def claim_next(kind: str = "") -> Optional[dict]:
    """原子 claim 任一 available 号。

    kind 指定 → 只从该类型里挑（"选了 gmail 就只跑 gmail 号"）
    kind 为空 → 全池子里挑最早导入的

    多类型混放的关键就在这里：号池里 outlook 和 gmail 并存，
    但当前配置选了哪种，就只 claim 哪种，不会串。
    """
    k = (kind or "").strip().lower()
    with _lock:
        con = _conn()
        for _ in range(50):  # 有限重试，避免并发抢号时无限递归爆栈
            if k:
                cur = con.execute(
                    "SELECT * FROM outlook_accounts WHERE status='available' AND kind=? "
                    "ORDER BY imported_at ASC LIMIT 1",
                    (k,),
                )
            else:
                cur = con.execute(
                    "SELECT * FROM outlook_accounts WHERE status='available' "
                    "ORDER BY imported_at ASC LIMIT 1"
                )
            row = cur.fetchone()
            if not row:
                return None

            # 前置校验：若该号已在 registered 表中存在，自动归档为 done 并跳过，继续取下一个真正的新号
            is_reg = con.execute("SELECT 1 FROM registered WHERE lower(email)=?", (row["email"].lower(),)).fetchone()
            if is_reg:
                con.execute(
                    "UPDATE outlook_accounts SET status='done', finished_at=?, fail_reason='already_registered' "
                    "WHERE lower(email)=?",
                    (time.time(), row["email"].lower()),
                )
                con.commit()
                continue

            rc = con.execute(
                "UPDATE outlook_accounts SET status='in_use', claimed_at=? "
                "WHERE lower(email)=? AND status='available'",
                (time.time(), row["email"].lower()),
            )
            con.commit()
            if rc.rowcount == 1:
                return dict(row)
            # 被别的线程抢走了，换下一个再试
        return None


def mark_done(email: str) -> None:
    with _lock:
        con = _conn()
        con.execute(
            "UPDATE outlook_accounts SET status='done', finished_at=?, fail_reason=NULL WHERE email=?",
            (time.time(), email.lower()),
        )
        con.commit()


def mark_failed(email: str, reason: str = "") -> None:
    with _lock:
        con = _conn()
        con.execute(
            "UPDATE outlook_accounts SET status='failed', finished_at=?, fail_reason=? WHERE email=?",
            (time.time(), (reason or "")[:500], email.lower()),
        )
        con.commit()


def release_unused(email: str) -> None:
    """claim 后没真注册（异常 / 用户取消）→ 还回 available。"""
    with _lock:
        con = _conn()
        con.execute(
            "UPDATE outlook_accounts SET status='available', claimed_at=NULL "
            "WHERE email=? AND status='in_use'",
            (email.lower(),),
        )
        con.commit()


def reset_to_available(email: str) -> bool:
    """手动重置单个号：done / failed → available，清空时间戳和失败原因。

    场景：注册成功但 refresh_token 没拿到，主人想重新跑一遍这个号。
    """
    with _lock:
        con = _conn()
        rc = con.execute(
            "UPDATE outlook_accounts SET status='available', claimed_at=NULL, "
            "finished_at=NULL, fail_reason=NULL "
            "WHERE lower(email)=lower(?)",
            (email,),
        )
        con.commit()
        return rc.rowcount > 0


def bulk_reset_to_available(emails: list[str]) -> int:
    """批量重置多个号。返回实际被改的行数。"""
    if not emails:
        return 0
    with _lock:
        con = _conn()
        rc = con.execute(
            f"UPDATE outlook_accounts SET status='available', claimed_at=NULL, "
            f"finished_at=NULL, fail_reason=NULL "
            f"WHERE lower(email) IN ({','.join(['lower(?)'] * len(emails))})",
            emails,
        )
        con.commit()
        return rc.rowcount


def reset_failed_to_available() -> int:
    """把所有 failed 号一次性重置为 available（清掉 fail_reason）。返回受影响行数。

    场景：代理短暂抽风导致一波号被冤枉标 failed，主人想给它们一次机会。
    """
    with _lock:
        con = _conn()
        rc = con.execute(
            "UPDATE outlook_accounts SET status='available', fail_reason=NULL, "
            "finished_at=NULL WHERE status='failed'"
        )
        con.commit()
        return rc.rowcount


def release_stale_in_use(stale_seconds: float = 1800) -> int:
    """把 claimed_at 超过 N 秒还在 in_use 的号释放回 available。

    场景：上次 webui 强退/进程崩溃，号卡在 in_use 永远不释放。默认 30 分钟。
    """
    with _lock:
        con = _conn()
        cutoff = time.time() - stale_seconds
        rc = con.execute(
            "UPDATE outlook_accounts SET status='available', claimed_at=NULL "
            "WHERE status='in_use' AND (claimed_at IS NULL OR claimed_at < ?)",
            (cutoff,),
        )
        con.commit()
        return rc.rowcount


def delete_account(email: str) -> bool:
    with _lock:
        con = _conn()
        rc = con.execute("DELETE FROM outlook_accounts WHERE email=?", (email.lower(),))
        con.commit()
        return rc.rowcount > 0


def delete_accounts_by_status(status: str) -> int:
    """按状态批量删除。status 必须是 available/in_use/done/failed 之一；
    传 'all' 删全部。返回受影响行数。"""
    valid = {"available", "in_use", "done", "failed", "all"}
    s = (status or "").strip().lower()
    if s not in valid:
        return 0
    with _lock:
        con = _conn()
        if s == "all":
            rc = con.execute("DELETE FROM outlook_accounts")
        else:
            rc = con.execute("DELETE FROM outlook_accounts WHERE status=?", (s,))
        con.commit()
        return rc.rowcount


def delete_accounts_by_emails(emails: list[str]) -> int:
    """按 email 列表批量删除。返回受影响行数。"""
    cleaned = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    if not cleaned:
        return 0
    with _lock:
        con = _conn()
        placeholders = ",".join("?" * len(cleaned))
        rc = con.execute(
            f"DELETE FROM outlook_accounts WHERE email IN ({placeholders})",
            cleaned,
        )
        con.commit()
        return rc.rowcount


def stats() -> dict:
    con = _conn()
    cur = con.execute(
        "SELECT status, COUNT(*) AS n FROM outlook_accounts GROUP BY status"
    )
    out = {"available": 0, "in_use": 0, "done": 0, "failed": 0, "total": 0}
    for r in cur.fetchall():
        out[r["status"]] = r["n"]
        out["total"] += r["n"]
    return out


# ──────────────────────── 注册结果存储 ────────────────────────


def save_registered(d: dict) -> None:
    """保存注册成功（或部分成功）的凭证。覆盖同邮箱旧记录。

    凭证三件套（access_token / session_token / refresh_token）单独存列；
    注册出口（reg_country / reg_city / reg_ip）单独存列；
    其余字段（如 device_id / cookie_header / id_token / 自定义元数据）打包进 extra_json。
    """
    email = (d.get("email") or "").lower()
    if not email:
        return
    # 必须至少有 access_token / session_token / refresh_token 之一才落库
    has_token = bool(
        (d.get("access_token") or "").strip()
        or (d.get("session_token") or "").strip()
        or (d.get("refresh_token") or "").strip()
    )
    if not has_token:
        logging.getLogger("db").warning(f"[save_registered] {email} 没有任何有效 Token 凭证，放弃落盘")
        return

    password = d.get("password", "") or ""
    extra = {k: v for k, v in d.items() if k not in {
        "email", "password", "access_token", "session_token", "refresh_token",
        "id_token", "device_id", "csrf_token", "cookie_header",
        "totp_secret", "totp_factor_id", "reg_country", "reg_city", "reg_ip",
    }}
    with _lock:
        con = _conn()
        totp_secret = (d.get("totp_secret") or "").strip()
        totp_factor_id = (d.get("totp_factor_id") or "").strip()
        reg_country = (d.get("reg_country") or "").strip()
        reg_city = (d.get("reg_city") or "").strip()
        reg_ip = (d.get("reg_ip") or "").strip()

        if not password or not totp_secret:
            row = con.execute(
                "SELECT password, totp_secret, totp_factor_id, reg_country, reg_city, reg_ip "
                "FROM registered WHERE email=?",
                (email,),
            ).fetchone()
            if row:
                if not password and (row["password"] or "").strip():
                    password = row["password"]
                if not totp_secret and (row["totp_secret"] or "").strip():
                    totp_secret = row["totp_secret"]
                    totp_factor_id = totp_factor_id or (row["totp_factor_id"] or "")
                if not reg_country and (row["reg_country"] or "").strip():
                    reg_country = row["reg_country"]

        con.execute(
            "INSERT OR REPLACE INTO registered "
            "(email, password, access_token, session_token, refresh_token, "
            "id_token, device_id, csrf_token, cookie_header, "
            "totp_secret, totp_factor_id, reg_country, reg_city, reg_ip, extra_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                email,
                password,
                d.get("access_token", ""),
                d.get("session_token", ""),
                d.get("refresh_token", ""),
                d.get("id_token", ""),
                d.get("device_id", ""),
                d.get("csrf_token", ""),
                d.get("cookie_header", ""),
                totp_secret,
                totp_factor_id,
                reg_country,
                reg_city,
                reg_ip,
                json.dumps(extra, ensure_ascii=False) if extra else None,
                time.time(),
            ),
        )
        con.commit()


def update_registered_oauth(
    email: str,
    access_token: str = "",
    refresh_token: str = "",
    id_token: str = "",
    session_token: str = "",
    cookie_header: str = "",
    extra_data: Optional[dict] = None,
) -> bool:
    """OAuth 导出与 Token 刷新成功后回写 access_token / refresh_token / id_token / session_token / cookie_header 及 extra_json。"""
    email = (email or "").strip().lower()
    if not email:
        return False
    with _lock:
        con = _conn()
        row = con.execute("SELECT * FROM registered WHERE email=?", (email,)).fetchone()
        if not row:
            return False
        d = dict(row)
        extra = json.loads(d.get("extra_json") or "{}")
        if extra_data:
            extra.update(extra_data)

        new_at = access_token.strip() or d.get("access_token") or ""
        new_rt = refresh_token.strip() or d.get("refresh_token") or ""
        new_it = id_token.strip() or d.get("id_token") or ""
        new_st = session_token.strip() or d.get("session_token") or ""
        new_cookie = cookie_header.strip() or d.get("cookie_header") or ""

        con.execute(
            "UPDATE registered SET access_token=?, refresh_token=?, id_token=?, session_token=?, "
            "cookie_header=?, oauth_status='success', oauth_updated_at=?, extra_json=? WHERE email=?",
            (new_at, new_rt, new_it, new_st, new_cookie, time.time(), json.dumps(extra, ensure_ascii=False), email),
        )
        con.commit()
        return True


def update_registered_oauth_status(email: str, status: str, error: str = "") -> bool:
    """更新账号的 OAuth 授权状态 (success / need_phone / failed)。"""
    email = (email or "").strip().lower()
    if not email:
        return False
    status = (status or "").strip().lower()
    with _lock:
        con = _conn()
        row = con.execute("SELECT extra_json FROM registered WHERE email=?", (email,)).fetchone()
        if not row:
            return False
        extra = {}
        if row["extra_json"]:
            try:
                extra = json.loads(row["extra_json"])
            except Exception:
                extra = {}
        oauth_meta = extra.get("oauth_export") or {}
        oauth_meta["status"] = status
        oauth_meta["updated_at"] = time.time()
        if error:
            oauth_meta["error"] = error
        extra["oauth_export"] = oauth_meta

        con.execute(
            "UPDATE registered SET oauth_status=?, oauth_updated_at=?, extra_json=? WHERE email=?",
            (status, time.time(), json.dumps(extra, ensure_ascii=False), email),
        )
        con.commit()
        return True


_OAUTH_FEATURE_COLS = (
    "created_at", "task_id", "email", "email_domain", "mail_kind",
    "outcome", "error_class", "error_text", "duration_ms",
    "has_password", "has_totp", "has_mail_cred", "account_age_days", "plan_type",
    "proxy_country", "proxy_host", "impersonate", "browser_type", "ua_family",
    "screen", "timezone", "lang",
    "first_page_type", "login_path", "need_otp", "need_phone", "phone_verified",
    "sms_enabled", "sms_provider", "sms_country", "sms_country_used",
    "sms_provider_ids", "sms_except_provider_ids", "sms_price_spec",
    "sms_phone_prefix", "sms_cost", "sms_operator", "sms_attempts",
    "continue_page_type", "continue_kind", "extra_json",
)


def insert_oauth_attempt_feature(feat: dict) -> int:
    """写入一次 OAuth 授权尝试的特征（成功/失败/需接码都记）。返回 row id。"""
    if not isinstance(feat, dict):
        return 0
    row = {k: feat.get(k) for k in _OAUTH_FEATURE_COLS}
    row["created_at"] = float(row.get("created_at") or time.time())
    email = str(row.get("email") or "").strip().lower()
    row["email"] = email
    if email and not row.get("email_domain"):
        row["email_domain"] = email.split("@")[-1] if "@" in email else ""
    extra = feat.get("extra")
    if extra is None:
        extra = {k: v for k, v in feat.items() if k not in _OAUTH_FEATURE_COLS and k != "extra"}
    if extra:
        row["extra_json"] = json.dumps(extra, ensure_ascii=False)
    for bkey in (
        "has_password", "has_totp", "has_mail_cred",
        "need_otp", "need_phone", "phone_verified", "sms_enabled",
    ):
        if row.get(bkey) is not None:
            row[bkey] = 1 if row[bkey] else 0
    err = str(row.get("error_text") or "")
    if len(err) > 400:
        row["error_text"] = err[:400]
    cols = [c for c in _OAUTH_FEATURE_COLS]
    placeholders = ",".join("?" for _ in cols)
    values = [row.get(c) for c in cols]
    with _lock:
        con = _conn()
        cur = con.execute(
            f"INSERT INTO oauth_attempt_features ({','.join(cols)}) VALUES ({placeholders})",
            values,
        )
        con.commit()
        return int(cur.lastrowid or 0)


def list_oauth_attempt_features(limit: int = 100, outcome: str = "") -> list[dict]:
    limit = max(1, min(500, int(limit or 100)))
    sql = "SELECT * FROM oauth_attempt_features"
    args: list = []
    if outcome:
        sql += " WHERE outcome=?"
        args.append(str(outcome).strip())
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    con = _conn()
    rows = con.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def get_oauth_feature_weights(min_n: int = 1) -> dict:
    """按关键特征组合统计成功率，给后续加权选路用。"""
    min_n = max(1, int(min_n or 1))
    con = _conn()
    total_row = con.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS ok FROM oauth_attempt_features"
    ).fetchone()
    overall_n = int(total_row["n"] or 0)
    overall_ok = int(total_row["ok"] or 0)

    def _group(cols: list[str]) -> list[dict]:
        sel = ", ".join(cols)
        sql = (
            f"SELECT {sel}, COUNT(*) AS n, "
            f"SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS ok "
            f"FROM oauth_attempt_features GROUP BY {sel} HAVING n >= ? "
            f"ORDER BY (ok * 1.0 / n) DESC, n DESC LIMIT 80"
        )
        out = []
        for r in con.execute(sql, (min_n,)).fetchall():
            d = dict(r)
            n = int(d.get("n") or 0)
            ok = int(d.get("ok") or 0)
            d["rate"] = round(ok / n, 4) if n else 0.0
            out.append(d)
        return out

    return {
        "overall": {
            "n": overall_n,
            "ok": overall_ok,
            "rate": round(overall_ok / overall_n, 4) if overall_n else 0.0,
        },
        "by_proxy_country": _group(["proxy_country"]),
        "by_impersonate": _group(["impersonate"]),
        "by_browser": _group(["browser_type"]),
        "by_sms_country": _group(["sms_country"]),
        "by_sms_operator": _group(["sms_operator"]),
        "by_login_path": _group(["login_path"]),
        "by_error_class": _group(["error_class"]),
        "by_combo": _group(["proxy_country", "impersonate", "sms_country"]),
    }


def save_password_early(email: str, password: str) -> None:
    """密码一在 OpenAI 侧生效就落盘，不等整个注册流程跑完。"""
    email = (email or "").strip().lower()
    password = (password or "").strip()
    if not email or not password:
        return
    with _lock:
        con = _conn()
        con.execute(
            "INSERT INTO registered "
            "(email, password, access_token, session_token, refresh_token, "
            "id_token, device_id, csrf_token, cookie_header, extra_json, created_at) "
            "VALUES (?, ?, '', '', '', '', '', '', '', ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET password=excluded.password",
            (
                email,
                password,
                json.dumps({"pending": True}, ensure_ascii=False),
                time.time(),
            ),
        )
        con.commit()


def cleanup_pending_registered(email: str) -> None:
    """清理未完成注册且无有效凭证的半成品账号记录。"""
    email = (email or "").strip().lower()
    if not email:
        return
    with _lock:
        con = _conn()
        con.execute(
            "DELETE FROM registered WHERE email=? AND "
            "(access_token IS NULL OR access_token = '') AND "
            "(session_token IS NULL OR session_token = '') AND "
            "(refresh_token IS NULL OR refresh_token = '')",
            (email,)
        )
        con.commit()


def normalize_totp_secret(raw: str) -> str:
    """把用户手填的 TOTP secret 规范化成可用的 base32，非法值抛 ValueError。

    登录侧（auth_flow._totp_now）拿到 secret 直接 b32decode，**不做任何校验** ——
    脏值存进去要等到真登录时才炸，那时只看到一句 base32 解码异常，
    根本看不出是手填填错了。所以校验必须挡在写库这一关。

    接受的输入：
      - 裸 base32:  JBSWY3DPEHPK3PXP / jbswy3dp ehpk 3pxp / JBSW-Y3DP-EHPK
      - otpauth URI: otpauth://totp/ChatGPT:a@b.com?secret=JBSWY3DP&issuer=...
        （从手机 App 导出/二维码解码出来的就是这个格式，直接粘进来很常见）
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # otpauth:// URI 抽 secret 参数
    if s.lower().startswith("otpauth://"):
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(s).query)
            s = (qs.get("secret") or [""])[0]
        except Exception:
            raise ValueError("otpauth 链接解析失败，请直接填 secret")
        if not s:
            raise ValueError("otpauth 链接里没有 secret 参数")
    # 去掉分隔符（手机 App 展示时常带空格/连字符）并统一大写
    s = s.replace(" ", "").replace("-", "").replace("_", "").upper()
    # base32 只有 A-Z 和 2-7，先挡掉明显非法字符再解码，报错更好懂
    if not s or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=" for c in s):
        raise ValueError("TOTP secret 含非法字符（base32 只允许 A-Z 和 2-7）")
    try:
        # 补 padding 后试解，解得开才算合法。auth_flow 那边也是这么补的。
        decoded = base64.b32decode(s + "=" * (-len(s) % 8))
    except Exception:
        raise ValueError("TOTP secret 不是合法的 base32")
    if len(decoded) < 10:
        raise ValueError(f"TOTP secret 太短（解出 {len(decoded)} 字节，通常应为 20 字节）")
    return s


def clean_empty_token_accounts() -> int:
    """清理没有任何有效凭证（AT/ST/RT 全为空）的未完成半成品账号。"""
    with _lock:
        con = _conn()
        cur = con.execute("""
            DELETE FROM registered
            WHERE (access_token IS NULL OR access_token = '')
              AND (session_token IS NULL OR session_token = '')
              AND (refresh_token IS NULL OR refresh_token = '')
        """)
        con.commit()
        return cur.rowcount


def update_registered_manual(email: str, password: Optional[str] = None,
                             totp_secret: Optional[str] = None) -> bool:
    """手动修正某个已注册账号的密码 / TOTP secret。

    ⚠️ 只改**本地库**，不会同步到 OpenAI —— 这里改密码不等于改了账号密码。
       用途是把外部已知的凭证补进来，或修正记录错误。

    传 None = 该字段不动（不是清空）。用 None 而不是空串做"不修改"的标记，
    是为了留出"主人真想清空某字段"的余地（传空串即清空）。

    totp_secret 会先过 normalize_totp_secret 校验，非法直接抛 ValueError；
    宁可这里报错，也不能让脏值躺进库里等登录时才炸。

    返回 False 表示该邮箱不存在（不会凭空插入新行 —— 手填是"修正已有记录"，
    真要新增外部账号是另一件事，走单独的导入功能）。
    """
    email = (email or "").strip().lower()
    if not email:
        return False
    sets, vals = [], []
    if password is not None:
        sets.append("password=?")
        vals.append(password)
    if totp_secret is not None:
        # 空串 = 主人主动清空；非空则必须过校验
        sets.append("totp_secret=?")
        vals.append(normalize_totp_secret(totp_secret) if totp_secret.strip() else "")
    if not sets:
        return False
    with _lock:
        con = _conn()
        row = con.execute("SELECT email FROM registered WHERE email=?", (email,)).fetchone()
        if not row:
            return False
        vals.append(email)
        con.execute(f"UPDATE registered SET {', '.join(sets)} WHERE email=?", vals)
        con.commit()
        return True


def update_plus_check(email: str, plus_info: dict) -> None:
    """把 Plus 检查结果写入 extra_json.plus_check。"""
    email = email.lower()
    con = _conn()
    cur = con.execute("SELECT extra_json FROM registered WHERE email=?", (email,))
    row = cur.fetchone()
    if not row:
        return
    extra = {}
    if row["extra_json"]:
        try:
            extra = json.loads(row["extra_json"])
        except Exception:
            extra = {}
    extra["plus_check"] = plus_info
    with _lock:
        con.execute(
            "UPDATE registered SET extra_json=? WHERE email=?",
            (json.dumps(extra, ensure_ascii=False), email),
        )
        con.commit()


def update_oa_check(email: str, oa_info: dict) -> None:
    """把 OAICS 资格检测结果写入 registered.oa_check 列（JSON）。"""
    email = (email or "").strip().lower()
    if not email:
        return
    with _lock:
        con = _conn()
        con.execute(
            "UPDATE registered SET oa_check=? WHERE email=?",
            (json.dumps(oa_info, ensure_ascii=False), email),
        )
        con.commit()


def update_registered_extract(email: str, extract_data: dict) -> bool:
    """把提链结果 (status, link_url, ba_token, channel 等) 写入 registered.extra_json.extract_link。"""
    email = (email or "").strip().lower()
    if not email:
        return False
    with _lock:
        con = _conn()
        row = con.execute("SELECT extra_json FROM registered WHERE email=?", (email,)).fetchone()
        if not row:
            return False
        extra = {}
        if row["extra_json"]:
            try:
                extra = json.loads(row["extra_json"])
            except Exception:
                extra = {}
        extra["extract_link"] = extract_data
        con.execute(
            "UPDATE registered SET extra_json=? WHERE email=?",
            (json.dumps(extra, ensure_ascii=False), email),
        )
        con.commit()
        return True


def _parse_single_filter_clause(filt: str) -> Optional[str]:
    """将单个过滤代码转换为 SQL 片段。"""
    f = (filt or "").strip().lower()
    if not f or f == "all":
        return None
    if f == "has_rt":
        return "length(refresh_token) > 0"
    if f == "no_rt":
        return "coalesce(length(refresh_token),0) = 0"
    if f == "unchecked":
        return "(extra_json IS NULL OR extra_json NOT LIKE '%\"plus_check\"%')"
    if f == "pro":
        return "(extra_json LIKE '%\"pro_20x\"%' OR extra_json LIKE '%\"pro_5x\"%' OR extra_json LIKE '%\"pro_active\"%' OR extra_json LIKE '%\"pro_eligible\"%')"
    if f == "team":
        return "extra_json LIKE '%\"team_active\"%'"
    if f == "plus":
        return "(extra_json LIKE '%\"plus_eligible\"%' OR extra_json LIKE '%\"plus_active\"%')"
    if f == "free":
        return "extra_json LIKE '%\"free\"%'"
    if f == "banned":
        return "extra_json LIKE '%\"banned\"%'"
    if f == "token_invalid":
        return "extra_json LIKE '%\"token_invalid\"%'"
    # ── OAICS 资格检测筛选 ──
    if f == "oa_unchecked":
        return "(oa_check IS NULL OR oa_check = '')"
    if f == "oa_hit":
        return "oa_check LIKE '%\"state\":\"OAICS\"%'"
    if f == "oa_miss":
        return "(oa_check IS NOT NULL AND oa_check != '' AND oa_check NOT LIKE '%\"state\":\"OAICS\"%')"
    # ── OAuth 授权状态筛选 ──
    if f == "oauth_success":
        return "oauth_status = 'success'"
    if f == "oauth_need_phone":
        return "oauth_status = 'need_phone'"
    if f in ("oauth_failed", "failed"):
        return "(oauth_status = 'failed' AND (extra_json IS NULL OR (extra_json NOT LIKE '%CFTemp%' AND extra_json NOT LIKE '%Timeout%')))"
    if f in ("oauth_error", "error", "oauth_exception"):
        return "(oauth_status = 'error' OR (oauth_status = 'failed' AND (extra_json LIKE '%CFTemp%' OR extra_json LIKE '%Timeout%')))"
    if f in ("oauth_all_failed", "oauth_failed_or_error"):
        return "(oauth_status = 'failed' OR oauth_status = 'error')"
    if f in ("oauth_unchecked", "oauth_never", "never_oauth", "no_oauth", "unchecked_oauth"):
        return "(oauth_status IS NULL OR oauth_status = '')"
    # ── 密码与 2FA 安全状态筛选 ──
    if f == "no_password":
        return "(password IS NULL OR password = '')"
    if f == "has_password":
        return "(password IS NOT NULL AND password != '')"
    if f == "no_2fa":
        return "(totp_secret IS NULL OR totp_secret = '')"
    if f == "has_2fa":
        return "(totp_secret IS NOT NULL AND totp_secret != '')"
    if f == "missing_security":
        return "((password IS NULL OR password = '') OR (totp_secret IS NULL OR totp_secret = ''))"
    if f == "both_secured":
        return "(password IS NOT NULL AND password != '' AND totp_secret IS NOT NULL AND totp_secret != '')"
    # ── 提链状态筛选 ──
    if f == "extract_eligible":
        return "(extra_json LIKE '%\"plus_eligible\"%' AND (extra_json NOT LIKE '%\"extract_link\"%' OR extra_json NOT LIKE '%\"status\":\"success\"%'))"
    if f == "extract_success":
        return "(extra_json LIKE '%\"extract_link\"%' AND (extra_json LIKE '%\"status\":\"success\"%' OR extra_json LIKE '%\"status\": \"success\"%'))"
    if f == "extract_failed":
        return "(extra_json LIKE '%\"extract_link\"%' AND (extra_json LIKE '%\"status\":\"failed\"%' OR extra_json LIKE '%\"status\":\"error\"%' OR extra_json LIKE '%\"status\": \"failed\"%'))"
    return None


def _parse_domain_filter_clause(domain_str: str) -> tuple[Optional[str], list]:
    """解析邮箱格式/域名筛选。"""
    d = (domain_str or "").strip().lower()
    if not d or d == "all":
        return None, []
    if d in ("microsoft", "ms", "ms_all"):
        return "(lower(email) LIKE '%@outlook.%' OR lower(email) LIKE '%@hotmail.%' OR lower(email) LIKE '%@live.%' OR lower(email) LIKE '%@msn.%')", []
    if d in ("outlook", "outlook.com"):
        return "lower(email) LIKE '%@outlook.%'", []
    if d in ("hotmail", "hotmail.com"):
        return "lower(email) LIKE '%@hotmail.%'", []
    if d in ("live", "live.com"):
        return "lower(email) LIKE '%@live.%'", []
    if d in ("gmail", "gmail.com"):
        return "lower(email) LIKE '%@gmail.%'", []
    if d in ("yahoo", "yahoo.com"):
        return "lower(email) LIKE '%@yahoo.%'", []
    if d in ("icloud", "icloud.com"):
        return "lower(email) LIKE '%@icloud.%'", []
    if d in ("qq", "qq.com"):
        return "lower(email) LIKE '%@qq.%'", []
    if d in ("163", "163.com"):
        return "lower(email) LIKE '%@163.%'", []
    if d in ("custom", "custom_domain", "other", "self_domain"):
        return (
            "(lower(email) NOT LIKE '%@outlook.%' AND lower(email) NOT LIKE '%@hotmail.%' "
            "AND lower(email) NOT LIKE '%@live.%' AND lower(email) NOT LIKE '%@msn.%' "
            "AND lower(email) NOT LIKE '%@gmail.%' AND lower(email) NOT LIKE '%@yahoo.%' "
            "AND lower(email) NOT LIKE '%@icloud.%' AND lower(email) NOT LIKE '%@qq.%' "
            "AND lower(email) NOT LIKE '%@163.%')", []
        )
    # 具体后缀，如 "@shaosiming.online" 或 "shaosiming.online"
    if d.startswith("@"):
        return "lower(email) LIKE ?", [f"%{d}"]
    elif "." in d:
        return "lower(email) LIKE ?", [f"%@{d}"]
    else:
        return "(lower(email) LIKE ? OR lower(email) LIKE ?)", [f"%@{d}%", f"%{d}%"]


def get_registered_domains() -> list[dict]:
    """统计当前数据库中所有注册账号的邮箱后缀域名及数量。"""
    con = _conn()
    cur = con.execute("""
        SELECT
            CASE
                WHEN instr(email, '@') > 0 THEN lower(substr(email, instr(email, '@')))
                ELSE 'other'
            END AS domain,
            COUNT(*) AS count
        FROM registered
        GROUP BY domain
        ORDER BY count DESC
    """)
    return [{"domain": r[0], "count": r[1]} for r in cur.fetchall() if r[0]]


def _registered_where(
    filt: str = "all",
    search: str = "",
    filter_plan: str = "",
    filter_sec: str = "",
    filter_extract: str = "",
    filter_oauth: str = "",
    filter_domain: str = "",
) -> tuple[str, list]:
    conditions = []
    args = []

    # 支持单一老参数（逗号或单一code）
    if filt and filt != "all":
        parts = [p.strip() for p in filt.split(",") if p.strip()]
        for p in parts:
            c = _parse_single_filter_clause(p)
            if c:
                conditions.append(c)

    # 支持多维度组合参数
    for sub in (filter_plan, filter_sec, filter_extract, filter_oauth):
        if sub and sub != "all":
            c = _parse_single_filter_clause(sub)
            if c and c not in conditions:
                conditions.append(c)

    if filter_domain and filter_domain != "all":
        c, d_args = _parse_domain_filter_clause(filter_domain)
        if c:
            conditions.append(c)
            args.extend(d_args)

    search_cleaned = (search or "").strip().lower()
    if search_cleaned:
        conditions.append("lower(email) LIKE ?")
        args.append(f"%{search_cleaned}%")

    if conditions:
        return "WHERE " + " AND ".join(conditions), args
    return "", args


def count_registered(
    filter_rt: str = "all",
    search: str = "",
    filter_plan: str = "",
    filter_sec: str = "",
    filter_extract: str = "",
    filter_oauth: str = "",
    filter_domain: str = "",
) -> int:
    con = _conn()
    where, args = _registered_where(
        filter_rt, search,
        filter_plan=filter_plan, filter_sec=filter_sec,
        filter_extract=filter_extract, filter_oauth=filter_oauth,
        filter_domain=filter_domain,
    )
    cur = con.execute(f"SELECT COUNT(*) FROM registered {where}", args)
    return cur.fetchone()[0]


def list_registered_emails(
    filter_rt: str = "all",
    search: str = "",
    limit: int = 100000,
    filter_plan: str = "",
    filter_sec: str = "",
    filter_extract: str = "",
    filter_oauth: str = "",
    filter_domain: str = "",
) -> list[str]:
    """返回符合过滤条件的所有注册邮箱列表。"""
    con = _conn()
    where, args = _registered_where(
        filter_rt, search,
        filter_plan=filter_plan, filter_sec=filter_sec,
        filter_extract=filter_extract, filter_oauth=filter_oauth,
        filter_domain=filter_domain,
    )
    cur = con.execute(
        f"SELECT email FROM registered {where} ORDER BY created_at DESC LIMIT ?",
        args + [limit],
    )
    return [r[0] for r in cur.fetchall()]


def list_registered(
    limit: int = 20,
    offset: int = 0,
    filter_rt: str = "all",
    search: str = "",
    filter_plan: str = "",
    filter_sec: str = "",
    filter_extract: str = "",
    filter_oauth: str = "",
    filter_domain: str = "",
) -> list[dict]:
    con = _conn()
    where, args = _registered_where(
        filter_rt, search,
        filter_plan=filter_plan, filter_sec=filter_sec,
        filter_extract=filter_extract, filter_oauth=filter_oauth,
        filter_domain=filter_domain,
    )
    cur = con.execute(
        f"SELECT email, password, totp_secret, reg_country, reg_city, reg_ip, "
        f"length(access_token) AS at_len, length(session_token) AS st_len, "
        f"length(refresh_token) AS rt_len, oauth_status, oauth_updated_at, "
        f"extra_json, oa_check, created_at "
        f"FROM registered {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        args + [limit, offset],
    )
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        plus = None
        oauth_meta = None
        extract_link = None
        if d.get("extra_json"):
            try:
                extra = json.loads(d["extra_json"])
                plus = extra.get("plus_check")
                oauth_meta = extra.get("oauth_export")
                extract_link = extra.get("extract_link")
            except Exception:
                pass
        d["plus_check"] = plus
        d["oauth_export"] = oauth_meta
        d["extract_link"] = extract_link
        d.pop("extra_json", None)
        oa = None
        if d.get("oa_check"):
            try:
                oa = json.loads(d["oa_check"])
            except Exception:
                pass
        d["oa_check"] = oa
        rows.append(d)
    return rows


def list_registered_full(limit: int = 5000) -> list[dict]:
    """返回完整凭证（用于批量导出）。每行同 get_registered 的格式，外加 relay_url。

    ⚠️ relay_url（中转取件链接）**不在 registered 表里**，它跟着号池那一行走
       （outlook_accounts.relay_url，icloud_relay 这类号一号一条 token）。
       导出格式「邮箱----密码----2FA----取件url」要用它，所以这里 LEFT JOIN 带出来。
       用 JOIN 而不是给 registered 加列的原因：不用迁移、**已经注册完的老号也能导**
       （只要号池那行还在）；号池行被删掉就是空串，照约定留空、分隔符保留。
    """
    con = _conn()
    cur = con.execute(
        "SELECT r.*, a.relay_url AS relay_url "
        "FROM registered r LEFT JOIN outlook_accounts a ON a.email = r.email "
        "ORDER BY r.created_at DESC LIMIT ?",
        (limit,),
    )
    out = []
    for row in cur.fetchall():
        d = dict(row)
        if d.get("extra_json"):
            try:
                d["extra"] = json.loads(d["extra_json"])
            except Exception:
                d["extra"] = {}
        d.pop("extra_json", None)
        out.append(d)
    return out


def list_registered_by_emails(emails: list[str]) -> list[dict]:
    """按 email 列表返回完整凭证（批量导出勾选的号用）。

    - 行序 = created_at 倒序，和「注册结果」表格里看到的一致，方便核对。
    - 查不到的 email 直接不出现（号已被删掉的情况），不报错。
    - SQLite 单条语句变量数有上限（默认 999），所以分批查。
    - relay_url 从号池表 LEFT JOIN 带出（原因见 list_registered_full）。
    """
    cleaned = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    if not cleaned:
        return []

    con = _conn()
    out = []
    CHUNK = 500
    for i in range(0, len(cleaned), CHUNK):
        part = cleaned[i:i + CHUNK]
        placeholders = ",".join("?" * len(part))
        cur = con.execute(
            f"SELECT r.*, a.relay_url AS relay_url "
            f"FROM registered r LEFT JOIN outlook_accounts a ON a.email = r.email "
            f"WHERE r.email IN ({placeholders})",
            part,
        )
        for row in cur.fetchall():
            d = dict(row)
            if d.get("extra_json"):
                try:
                    d["extra"] = json.loads(d["extra_json"])
                except Exception:
                    d["extra"] = {}
            d.pop("extra_json", None)
            out.append(d)

    out.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return out


def get_registered(email: str) -> Optional[dict]:
    con = _conn()
    cur = con.execute("SELECT * FROM registered WHERE email=?", (email.lower(),))
    row = cur.fetchone()
    if not row:
        return None
    out = dict(row)
    if out.get("extra_json"):
        try:
            out["extra"] = json.loads(out["extra_json"])
        except Exception:
            out["extra"] = {}
    out.pop("extra_json", None)
    if out.get("oa_check"):
        try:
            out["oa_check"] = json.loads(out["oa_check"])
        except Exception:
            pass
    return out


def delete_registered(email: str) -> bool:
    with _lock:
        con = _conn()
        rc = con.execute("DELETE FROM registered WHERE email=?", (email.lower(),))
        con.commit()
        return rc.rowcount > 0


def delete_registered_by_emails(emails: list[str]) -> int:
    cleaned = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    if not cleaned:
        return 0
    with _lock:
        con = _conn()
        placeholders = ",".join("?" * len(cleaned))
        rc = con.execute(
            f"DELETE FROM registered WHERE email IN ({placeholders})",
            cleaned,
        )
        con.commit()
        return rc.rowcount


def delete_all_registered() -> int:
    with _lock:
        con = _conn()
        rc = con.execute("DELETE FROM registered")
        con.commit()
        return rc.rowcount


# ──────────────────────── 运行记录 ────────────────────────


def create_run(run_id: str, email: str, log_path: str) -> None:
    with _lock:
        con = _conn()
        con.execute(
            "INSERT INTO runs(run_id, email, status, started_at, log_path) "
            "VALUES (?, ?, 'running', ?, ?)",
            (run_id, email.lower(), time.time(), log_path),
        )
        con.commit()


def finish_run(run_id: str, status: str, error: str = "", category: str = "") -> None:
    with _lock:
        con = _conn()
        con.execute(
            "UPDATE runs SET status=?, finished_at=?, error=?, error_category=? WHERE run_id=?",
            (status, time.time(), (error or "")[:500], category or None, run_id),
        )
        con.commit()


def list_runs(limit: int = 50) -> list[dict]:
    con = _conn()
    cur = con.execute(
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,),
    )
    return [dict(r) for r in cur.fetchall()]


# ──────────────────────── settings (KV) ────────────────────────


def get_setting(key: str, default: str = "") -> str:
    con = _conn()
    cur = con.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


def set_setting(key: str, value) -> None:
    with _lock:
        con = _conn()
        con.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        con.commit()


# ──────────────────────── 邮箱来源配置 ────────────────────────


def get_mail_config() -> dict:
    """返回邮箱来源配置（支持明文查看密钥）。"""
    from mail_providers import list_providers

    out = {"mail_source": get_setting("mail_source", "outlook")}
    for p in list_providers():
        for f in p["config_fields"]:
            key = f["key"]
            out[key] = get_setting(key, "")
    return out


def save_mail_config(data: dict) -> None:
    """保存邮箱配置。"""
    from mail_providers import get_provider_class, list_providers

    if "mail_source" in data:
        src = str(data["mail_source"]).strip().lower()
        get_provider_class(src)  # 未注册的 kind 会抛 MailProviderError
        set_setting("mail_source", src)

    # 按 provider 声明的字段保存，加新邮箱时这里零改动
    for p in list_providers():
        for f in p["config_fields"]:
            key = f["key"]
            if key not in data:
                continue
            val = data[key]
            if f.get("type") == "password" and (val is None or val == "***"):
                continue
            set_setting(key, str(val).strip())


def get_secret_setting(key: str) -> str:
    """内部用：拿密码类配置的明文。"""
    return get_setting(key, "")


def get_mail_settings() -> dict:
    """内部用：给 create_mail_provider 的 settings（含明文密钥）。

    跟 get_mail_config 的区别：这个不打码，只在服务端构造 provider 时用，
    绝不能直接返回给前端。
    """
    from mail_providers import list_providers

    out = {"mail_source": get_setting("mail_source", "outlook")}
    for p in list_providers():
        for f in p["config_fields"]:
            out[f["key"]] = get_setting(f["key"], "")
    return out


def get_cf_admin_token() -> str:
    """内部用：拿明文 admin_token。"""
    return get_setting("cf_admin_token", "")


# ──────────────────────── SMS 接码配置 ────────────────────────


def get_sms_config() -> dict:
    """返回 SMS 接码配置（api_key 隐藏明文）。

    sms_enabled:        '0'/'1' 是否启用接码（命中 add-phone 时才会用）
    sms_provider:       smsbower
    sms_country:        国家代码或 ID（推荐 '52' = Thailand，OpenAI 走 SMS 的唯一稳定国家）
    sms_service:        服务代码（OpenAI = 'dr'）
    sms_max_price:      号码最高单价（SmsBower / SmsBower 用，单位平台货币；空 / -1 = 不限）
    sms_reuse_phone:    '0'/'1' 同号复用（SmsBower / SmsBower 支持，省钱）
    sms_phone_success_max: 同号最多复用几次（默认 3）
    sms_auto_country:   '0'/'1' 自动选最优国家（按价格 + 库存）
    sms_auto_min_stock: 自动选国家最低库存（默认 20）
    sms_auto_max_price: 自动选国家最高单价（默认 0 = 不限）
    """
    return {
        "sms_enabled":             get_setting("sms_enabled", "0"),
        "sms_provider":            get_setting("sms_provider", "smsbower"),
        "sms_api_key":             "***" if get_setting("sms_api_key") else "",
        "sms_country":             get_setting("sms_country", "52"),
        "sms_service":             get_setting("sms_service", "dr"),
        "sms_max_price":           get_setting("sms_max_price", ""),
        "sms_provider_ids":        get_setting("sms_provider_ids", get_setting("sms_operator", "")),
        "sms_except_provider_ids": get_setting("sms_except_provider_ids", ""),
        "sms_operator":            get_setting("sms_operator", ""),
        "sms_reuse_phone":         get_setting("sms_reuse_phone", "0"),
        "sms_phone_success_max":   get_setting("sms_phone_success_max", "3"),
        "sms_auto_country":        get_setting("sms_auto_country", "0"),
        "sms_strict_whitelist":    get_setting("sms_strict_whitelist", "0"),
        "sms_allowed_countries":   get_setting("sms_allowed_countries", ""),
        "sms_auto_min_stock":      get_setting("sms_auto_min_stock", "20"),
        "sms_auto_max_price":      get_setting("sms_auto_max_price", ""),
        "sms_max_phone_attempts":  get_setting("sms_max_phone_attempts", ""),
        "sms_per_phone_timeout":   get_setting("sms_per_phone_timeout", "80"),
    }


def save_sms_config(data: dict) -> None:
    """保存 SMS 配置。sms_api_key 传 '***' 表示不修改。"""
    # 校验 provider
    valid_providers = {"smsbower", "herosms"}
    if "sms_provider" in data:
        p = str(data["sms_provider"]).strip().lower()
        if p not in valid_providers:
            p = "smsbower"
        set_setting("sms_provider", p)
    # 字符串字段直接落
    for key in (
        "sms_country", "sms_service", "sms_max_price",
        "sms_provider_ids", "sms_except_provider_ids",
        "sms_phone_success_max", "sms_auto_min_stock", "sms_auto_max_price",
        "sms_max_phone_attempts", "sms_per_phone_timeout",
        "sms_allowed_countries",
    ):
        if key in data:
            set_setting(key, str(data[key]).strip())
    # 布尔字段（前端传 '0'/'1' 或 bool）
    for key in ("sms_enabled", "sms_reuse_phone", "sms_auto_country", "sms_strict_whitelist"):
        if key in data:
            v = data[key]
            if isinstance(v, bool):
                set_setting(key, "1" if v else "0")
            else:
                s = str(v).strip().lower()
                set_setting(key, "1" if s in ("1", "true", "yes", "on") else "0")
    # API key（'***' 不修改）
    if data.get("sms_api_key") and data["sms_api_key"] != "***":
        set_setting("sms_api_key", str(data["sms_api_key"]).strip())


def get_sms_internal_config() -> dict:
    """内部用：拿明文 sms_api_key,供 sms_provider 实例化使用。"""
    return {
        "sms_enabled":             get_setting("sms_enabled", "0") in ("1", "true"),
        "sms_provider":            get_setting("sms_provider", "smsbower"),
        "sms_api_key":             get_setting("sms_api_key", ""),
        "sms_country":             get_setting("sms_country", "52"),
        "sms_service":             get_setting("sms_service", "dr"),
        "sms_max_price":           get_setting("sms_max_price", ""),
        "sms_provider_ids":        get_setting("sms_provider_ids", get_setting("sms_operator", "")),
        "sms_except_provider_ids": get_setting("sms_except_provider_ids", ""),
        "sms_operator":            get_setting("sms_operator", ""),
        "sms_reuse_phone":         get_setting("sms_reuse_phone", "0") in ("1", "true"),
        "sms_phone_success_max":   get_setting("sms_phone_success_max", "3"),
        "sms_auto_country":        get_setting("sms_auto_country", "0") in ("1", "true"),
        "sms_strict_whitelist":    get_setting("sms_strict_whitelist", "0") in ("1", "true"),
        "sms_allowed_countries":   get_setting("sms_allowed_countries", ""),
        "sms_auto_min_stock":      get_setting("sms_auto_min_stock", "20"),
        "sms_auto_max_price":      get_setting("sms_auto_max_price", ""),
        "sms_max_phone_attempts":  get_setting("sms_max_phone_attempts", ""),
        "sms_per_phone_timeout":   get_setting("sms_per_phone_timeout", "80"),
    }


# ──────────────────────── 自动导出配置 (CPA / SUB2API) ────────────────────────


def get_export_config() -> dict:
    """返回导出配置（敏感字段做明文/'***' 占位）。

    给前端展示用：
      cpa_mgmt_key / sub2api_api_key 已设置时返回 '***'，未设置返回 ''。
      保存时传 '***' 代表不修改。
    """
    return {
        # CPA
        "cpa_enabled":     get_setting("export_cpa_enabled", "0"),
        "cpa_url":         get_setting("export_cpa_url", ""),
        "cpa_mgmt_key":    "***" if get_setting("export_cpa_mgmt_key") else "",
        "cpa_timeout":     get_setting("export_cpa_timeout", "30"),
        # SUB2API
        "sub2api_enabled":    get_setting("export_sub2api_enabled", "0"),
        "sub2api_url":        get_setting("export_sub2api_url", ""),
        "sub2api_api_key":    "***" if get_setting("export_sub2api_api_key") else "",
        "sub2api_group_ids":  get_setting("export_sub2api_group_ids", "2"),
        "sub2api_timeout":    get_setting("export_sub2api_timeout", "30"),
    }


def save_export_config(data: dict) -> None:
    """保存导出配置。密文字段传 '***' 表示不修改。"""
    # 布尔开关
    for key_in, key_out in (
        ("cpa_enabled",     "export_cpa_enabled"),
        ("sub2api_enabled", "export_sub2api_enabled"),
    ):
        if key_in in data:
            v = data[key_in]
            if isinstance(v, bool):
                set_setting(key_out, "1" if v else "0")
            else:
                s = str(v).strip().lower()
                set_setting(key_out, "1" if s in ("1", "true", "yes", "on") else "0")
    # 字符串字段（明文）
    for key_in, key_out in (
        ("cpa_url",            "export_cpa_url"),
        ("cpa_timeout",        "export_cpa_timeout"),
        ("sub2api_url",        "export_sub2api_url"),
        ("sub2api_group_ids",  "export_sub2api_group_ids"),
        ("sub2api_timeout",    "export_sub2api_timeout"),
    ):
        if key_in in data:
            set_setting(key_out, str(data[key_in] or "").strip())
    # 密文字段（'***' 不修改）
    if data.get("cpa_mgmt_key") and data["cpa_mgmt_key"] != "***":
        set_setting("export_cpa_mgmt_key", str(data["cpa_mgmt_key"]).strip())
    if data.get("sub2api_api_key") and data["sub2api_api_key"] != "***":
        set_setting("export_sub2api_api_key", str(data["sub2api_api_key"]).strip())


def get_export_internal_config() -> dict:
    """内部用：拿明文密钥 + 解析后的 enabled 布尔。供 registrar / app.test 调用。

    返回两个子配置 dict，可分别传给 exporter.export_to_cpa / export_to_sub2api。
    """
    cpa = {
        "enabled":      get_setting("export_cpa_enabled", "0") in ("1", "true"),
        "cpa_url":      get_setting("export_cpa_url", ""),
        "cpa_mgmt_key": get_setting("export_cpa_mgmt_key", ""),
        "cpa_timeout":  get_setting("export_cpa_timeout", "30"),
    }
    sub2api = {
        "enabled":            get_setting("export_sub2api_enabled", "0") in ("1", "true"),
        "sub2api_url":        get_setting("export_sub2api_url", ""),
        "sub2api_api_key":    get_setting("export_sub2api_api_key", ""),
        "sub2api_group_ids":  get_setting("export_sub2api_group_ids", "2"),
        "sub2api_timeout":    get_setting("export_sub2api_timeout", "30"),
    }
    return {"cpa": cpa, "sub2api": sub2api}


# ──────────────────────── Plus 试用提链配置 (Extract Link) ────────────────────────


def get_extract_config() -> dict:
    """获取提链全局配置。"""
    cdk_raw = get_setting("extract_link_cdk", "")
    return {
        "extract_link_api_base": get_setting("extract_link_api_base", ""),
        "extract_link_cdk":      "***" if cdk_raw else "",
        "extract_link_type":     get_setting("extract_link_type", "pix"),
        "extract_link_workers":  get_setting("extract_link_workers", "3"),
    }


def get_extract_internal_config() -> dict:
    """内部用：获取明文提链配置。"""
    return {
        "extract_link_api_base": get_setting("extract_link_api_base", ""),
        "extract_link_cdk":      get_setting("extract_link_cdk", ""),
        "extract_link_type":     get_setting("extract_link_type", "pix"),
        "extract_link_workers":  get_setting("extract_link_workers", "3"),
    }


def save_extract_config(data: dict) -> None:
    """保存提链配置。"""
    if "extract_link_api_base" in data:
        set_setting("extract_link_api_base", str(data["extract_link_api_base"] or "").strip())
    if "extract_link_type" in data:
        set_setting("extract_link_type", str(data["extract_link_type"] or "pix").strip().lower())
    if "extract_link_workers" in data:
        set_setting("extract_link_workers", str(data["extract_link_workers"] or "3").strip())
    if data.get("extract_link_cdk") and data["extract_link_cdk"] != "***":
        set_setting("extract_link_cdk", str(data["extract_link_cdk"]).strip())


def update_registered_extract(email: str, extract_data: dict) -> bool:
    """更新账号的提链结果至 extra_json.extract_link。"""
    email = (email or "").strip().lower()
    if not email:
        return False
    with _lock:
        con = _conn()
        row = con.execute("SELECT extra_json FROM registered WHERE email=?", (email,)).fetchone()
        if not row:
            return False
        extra = {}
        if row["extra_json"]:
            try:
                extra = json.loads(row["extra_json"])
            except Exception:
                extra = {}
        extra["extract_link"] = extract_data
        # 若提链成功，自动将资格状态刷新为Plus试用
        if extract_data.get("status") == "success":
            curr_plus = extra.get("plus_check") or {}
            if curr_plus.get("status") in ("free", "unchecked", None):
                extra["plus_check"] = {
                    "status": "plus_eligible",
                    "label": "Plus试用",
                    "plan": "free",
                    "promo": "plus-1-month-free",
                    "checked_at": time.time(),
                }
        con.execute(
            "UPDATE registered SET extra_json=? WHERE email=?",
            (json.dumps(extra, ensure_ascii=False), email),
        )
        con.commit()
        return True


# 模块加载时自动建表
init_db()
