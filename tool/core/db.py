"""db.py — 独立工具专属 SQLite 本地持久化数据库驱动
=========================================================
支持账号数据落库、OAuth 凭据存储、改密历史留痕、代理池与系统配置持久化。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("db")

DB_DIR = Path(__file__).resolve().parents[1]
DB_PATH = DB_DIR / "data.db"

_db_lock = threading.RLock()


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接并配置 WAL 模式与 Row 转换器。"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db():
    """初始化数据库表结构。"""
    with _db_lock:
        with get_connection() as conn:
            # 1. 账号主表
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL DEFAULT '',
                    totp_secret TEXT DEFAULT '',
                    new_password TEXT DEFAULT '',
                    status TEXT DEFAULT 'idle',       -- idle / running / success / failed
                    step TEXT DEFAULT '等待处理',
                    error TEXT DEFAULT '',
                    access_token TEXT DEFAULT '',
                    refresh_token TEXT DEFAULT '',
                    id_token TEXT DEFAULT '',
                    session_json TEXT DEFAULT '',
                    plan_type TEXT DEFAULT '',
                    account_id TEXT DEFAULT '',
                    logs TEXT DEFAULT '[]',           -- JSON 格式时序日志
                    auth_time INTEGER DEFAULT 0,      -- 上次授权成功时间戳
                    created_at INTEGER DEFAULT 0,
                    updated_at INTEGER DEFAULT 0,
                    notes TEXT DEFAULT ''
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);")

            # 2. 系统配置与代理池表
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER DEFAULT 0
                );
                """
            )
            conn.commit()
    logger.info(f"SQLite 数据库初始化完成: {DB_PATH}")


# ──────────────────────── 系统配置与代理池持久化 ────────────────────────


def get_setting(key: str, default: str = "") -> str:
    """获取单个配置项。"""
    with _db_lock:
        with get_connection() as conn:
            cur = conn.execute("SELECT value FROM settings WHERE key = ? LIMIT 1;", (key,))
            row = cur.fetchone()
            if row:
                return str(row["value"] or "")
            return default


def set_setting(key: str, value: str):
    """保存或更新单个配置项。"""
    now = int(time.time())
    with _db_lock:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                """,
                (key, str(value or ""), now),
            )
            conn.commit()


def get_all_settings() -> dict[str, str]:
    """获取全部配置项字典。"""
    with _db_lock:
        with get_connection() as conn:
            cur = conn.execute("SELECT key, value FROM settings;")
            return {str(row["key"]): str(row["value"] or "") for row in cur.fetchall()}


def save_settings_dict(settings_map: dict[str, Any]):
    """批量保存配置字典。"""
    now = int(time.time())
    with _db_lock:
        with get_connection() as conn:
            for k, v in settings_map.items():
                val_str = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                    """,
                    (k, val_str, now),
                )
            conn.commit()


# ──────────────────────── 账号数据持久化增删改查 ────────────────────────


def row_to_dict(row: sqlite3.Row) -> dict:
    """将 sqlite3.Row 转换为标准 Python 字典。"""
    d = dict(row)
    # 反序列化 logs
    if "logs" in d and isinstance(d["logs"], str):
        try:
            d["logs"] = json.loads(d["logs"]) if d["logs"] else []
        except Exception:
            d["logs"] = []
    return d


def import_accounts(account_list: list[dict]) -> tuple[int, int]:
    """批量导入或更新账号列表到 SQLite。

    Returns:
        (inserted_count, updated_count)
    """
    now = int(time.time())
    inserted = 0
    updated = 0

    with _db_lock:
        with get_connection() as conn:
            for item in account_list:
                email = (item.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    continue

                pwd = (item.get("password") or "").strip()
                totp = (item.get("totp_secret") or "").strip()
                inline_new_pwd = (item.get("inline_new_pwd") or "").strip()

                cur = conn.execute("SELECT id, password, totp_secret, new_password FROM accounts WHERE email = ? LIMIT 1;", (email,))
                existing = cur.fetchone()

                if existing:
                    # 更新密码与 2FA（若新传入不为空）
                    updates = ["updated_at = ?"]
                    params: list[Any] = [now]
                    if pwd:
                        updates.append("password = ?")
                        params.append(pwd)
                    if totp:
                        updates.append("totp_secret = ?")
                        params.append(totp)
                    if inline_new_pwd:
                        updates.append("new_password = ?")
                        params.append(inline_new_pwd)

                    params.append(email)
                    conn.execute(f"UPDATE accounts SET {', '.join(updates)} WHERE email = ?;", tuple(params))
                    updated += 1
                else:
                    # 插入新账号
                    conn.execute(
                        """
                        INSERT INTO accounts (
                            email, password, totp_secret, new_password, status, step,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'idle', '等待处理', ?, ?);
                        """,
                        (email, pwd, totp, inline_new_pwd, now, now),
                    )
                    inserted += 1
            conn.commit()

    return inserted, updated


def get_account_by_email(email: str) -> Optional[dict]:
    """按邮箱获取单条账号记录。"""
    with _db_lock:
        with get_connection() as conn:
            cur = conn.execute("SELECT * FROM accounts WHERE email = ? LIMIT 1;", (email.strip().lower(),))
            row = cur.fetchone()
            return row_to_dict(row) if row else None


def get_account_by_id(account_id: int) -> Optional[dict]:
    """按 ID 获取单条账号记录。"""
    with _db_lock:
        with get_connection() as conn:
            cur = conn.execute("SELECT * FROM accounts WHERE id = ? LIMIT 1;", (account_id,))
            row = cur.fetchone()
            return row_to_dict(row) if row else None


def list_accounts(
    keyword: str = "",
    status: str = "",
    has_token: Optional[bool] = None,
    limit: int = 1000,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """查询账号列表，支持关键字搜索与条件筛选。

    Returns:
        (accounts_list, total_count)
    """
    where_clauses = []
    params: list[Any] = []

    if keyword:
        kw = f"%{keyword.strip().lower()}%"
        where_clauses.append("(email LIKE ? OR notes LIKE ? OR new_password LIKE ?)")
        params.extend([kw, kw, kw])

    if status:
        where_clauses.append("status = ?")
        params.append(status)

    if has_token is True:
        where_clauses.append("access_token != '' AND access_token IS NOT NULL")
    elif has_token is False:
        where_clauses.append("(access_token = '' OR access_token IS NULL)")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    with _db_lock:
        with get_connection() as conn:
            # 统计总数
            count_cur = conn.execute(f"SELECT COUNT(*) AS total FROM accounts {where_sql};", tuple(params))
            total = count_cur.fetchone()["total"]

            # 查询分页数据
            query_sql = f"""
                SELECT * FROM accounts
                {where_sql}
                ORDER BY id ASC
                LIMIT ? OFFSET ?;
            """
            query_params = list(params) + [limit, offset]
            cur = conn.execute(query_sql, tuple(query_params))
            rows = [row_to_dict(r) for r in cur.fetchall()]

    return rows, total


def update_account_fields(email: str, fields: dict[str, Any]):
    """更新指定账号的字段并同步 updated_at。"""
    if not fields:
        return
    now = int(time.time())
    fields_copy = dict(fields)
    fields_copy["updated_at"] = now

    if "logs" in fields_copy and not isinstance(fields_copy["logs"], str):
        fields_copy["logs"] = json.dumps(fields_copy["logs"], ensure_ascii=False)

    set_clauses = [f"{k} = ?" for k in fields_copy.keys()]
    values = list(fields_copy.values()) + [email.strip().lower()]

    with _db_lock:
        with get_connection() as conn:
            conn.execute(f"UPDATE accounts SET {', '.join(set_clauses)} WHERE email = ?;", tuple(values))
            conn.commit()


def append_account_log(email: str, message: str, level: str = "info"):
    """为指定账号追加一条结构化时序日志。"""
    t_str = time.strftime("%H:%M:%S")
    entry = {"time": t_str, "message": message, "level": level}

    with _db_lock:
        acc = get_account_by_email(email)
        if not acc:
            return
        logs = acc.get("logs") or []
        if not isinstance(logs, list):
            logs = []
        logs.append(entry)
        if len(logs) > 300:
            logs = logs[-300:]
        update_account_fields(email, {"logs": logs})


def delete_accounts_by_emails(emails: list[str]) -> int:
    """按邮箱列表批量删除账号。"""
    if not emails:
        return 0
    clean_emails = [e.strip().lower() for e in emails if e.strip()]
    placeholders = ",".join(["?"] * len(clean_emails))
    with _db_lock:
        with get_connection() as conn:
            cur = conn.execute(f"DELETE FROM accounts WHERE email IN ({placeholders});", tuple(clean_emails))
            conn.commit()
            return cur.rowcount


def clear_all_accounts() -> int:
    """清空所有账号数据。"""
    with _db_lock:
        with get_connection() as conn:
            cur = conn.execute("DELETE FROM accounts;")
            conn.commit()
            return cur.rowcount


# 模块导入时自动确保 DB 初始化
init_db()
