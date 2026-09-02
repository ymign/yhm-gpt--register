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
import logging
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
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA cache_size = -64000")  # 64MB 内存查询缓存
    con.execute("PRAGMA busy_timeout = 15000")  # 15秒写锁自动重试，消除高并发冲突
    con.execute("PRAGMA temp_store = MEMORY")
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
        CREATE INDEX IF NOT EXISTS idx_outlook_lower_email ON outlook_accounts(lower(email));
        -- idx_registered_lower_email 挪到下面 registered 建表之后：
        -- 全新空库上这个脚本里表还没建，索引放前面会当场报 no such table。
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

        CREATE INDEX IF NOT EXISTS idx_registered_lower_email ON registered(lower(email));

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
    # 全格式导出留痕：记录最后一次导出的时间、导出格式及用户备注
    if "at_exported_at" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN at_exported_at REAL")
        con.commit()
    if "at_export_note" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN at_export_note TEXT DEFAULT ''")
        con.commit()
    if "exported_at" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN exported_at REAL")
        con.commit()
    if "export_fmt" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN export_fmt TEXT DEFAULT ''")
        con.commit()
    if "export_fmt_label" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN export_fmt_label TEXT DEFAULT ''")
        con.commit()
    if "export_note" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN export_note TEXT DEFAULT ''")
        con.commit()
    # 账号保温保鲜字段：记录最后一次保温时间、保温累计次数与状态
    if "last_warmed_at" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN last_warmed_at REAL")
        con.commit()
    if "warm_count" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN warm_count INTEGER DEFAULT 0")
        con.commit()
    if "warm_status" not in reg_cols:
        con.execute("ALTER TABLE registered ADD COLUMN warm_status TEXT DEFAULT ''")
        con.commit()

    # 高频覆盖索引（保证十万级数据秒开）
    con.execute("CREATE INDEX IF NOT EXISTS idx_reg_created ON registered(created_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_reg_country_created ON registered(reg_country, created_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_reg_export_created ON registered(exported_at, created_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_reg_oauth_status ON registered(oauth_status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pool_status_kind ON outlook_accounts(status, kind)")
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

    # 代理健康度：按 (代理模板 × 出口国家) 聚合注册号数与验死数。
    # 动态住宅代理一号一个 session（完整串每号都不同），所以键用抹掉 session
    # 的归一化模板（网关+账号结构），配合出口国家 —— US 组合脏了不影响 JP。
    # 死亡率超阈值自动拉黑该组合，注册选国家时跳过 —— 脏出口自动出局。
    con.execute("""
        CREATE TABLE IF NOT EXISTS proxy_health (
            template        TEXT NOT NULL,       -- 归一化代理模板（session 已抹掉）
            country         TEXT NOT NULL DEFAULT '',  -- 出口国家码（大写）
            total           INTEGER NOT NULL DEFAULT 0,  -- 该组合注册成功的号数
            dead            INTEGER NOT NULL DEFAULT 0,  -- 其中事后验死数
            last_used       REAL,                        -- 最后一次注册成功
            last_dead       REAL,                        -- 最近一次验死
            blacklisted     INTEGER NOT NULL DEFAULT 0,  -- 1=已拉黑
            blacklisted_at  REAL,
            blacklist_reason TEXT DEFAULT '',
            PRIMARY KEY (template, country)
        )
    """)
    # 旧版表（按完整代理串做主键，动态代理下每行只有 1 个号，聚合失灵）→ 重建。
    # 该表 2026-08-28 才上线，最多只有测试数据，直接丢弃换新结构。
    try:
        _ph_cols = {r[1] for r in con.execute("PRAGMA table_info(proxy_health)").fetchall()}
        if _ph_cols and "template" not in _ph_cols:
            con.execute("DROP TABLE proxy_health")
            con.execute("""
                CREATE TABLE proxy_health (
                    template        TEXT NOT NULL,
                    country         TEXT NOT NULL DEFAULT '',
                    total           INTEGER NOT NULL DEFAULT 0,
                    dead            INTEGER NOT NULL DEFAULT 0,
                    last_used       REAL,
                    last_dead       REAL,
                    blacklisted     INTEGER NOT NULL DEFAULT 0,
                    blacklisted_at  REAL,
                    blacklist_reason TEXT DEFAULT '',
                    PRIMARY KEY (template, country)
                )
            """)
    except Exception as _e:
        logging.getLogger("db").warning(f"[proxy_health] 旧表迁移失败（忽略）: {_e}")

    # Remail 已购未用邮箱智能复用池（未完成注册时自动回收，杜绝非账号异常浪费积分）
    con.execute("""
        CREATE TABLE IF NOT EXISTS remail_recycle_pool (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT NOT NULL UNIQUE,
            service_token   TEXT NOT NULL,
            order_no        TEXT DEFAULT '',
            project_id      INTEGER NOT NULL DEFAULT 2,
            email_suffix    TEXT NOT NULL DEFAULT 'icloud.com',
            service_mode    TEXT NOT NULL DEFAULT 'purchase',
            receive_until   TEXT DEFAULT '',
            expires_at      REAL NOT NULL,
            created_at      REAL NOT NULL,
            is_used         INTEGER NOT NULL DEFAULT 0,  -- 0: 未用且有效, 1: 注册成功已消费, 2: 超时/超限废弃, 3: 处理锁定中
            fail_count      INTEGER NOT NULL DEFAULT 0   -- 累计失败重试次数
        );
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_remail_pool_query
        ON remail_recycle_pool(project_id, email_suffix, service_mode, is_used, expires_at);
    """)
    con.commit()

    # 老 DB migrate：remail_recycle_pool 补 fail_count 列
    cur = con.execute("PRAGMA table_info(remail_recycle_pool)")
    rp_cols = {r[1] for r in cur.fetchall()}
    if "fail_count" not in rp_cols:
        con.execute("ALTER TABLE remail_recycle_pool ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0")
        con.commit()

    # 默认开箱即用设置预置（新电脑 clone 后自动就绪，无需手动配置）
    default_kvs = {
        "mail_source": "remail",
        "remail_api_key": "rk-a18f1eed-cc59-4eaf-9c5f-ac4d711c758d",
        "remail_project_id": "2",
        "remail_email_suffix": "outlook.com",
        "remail_service_mode": "purchase",
        "remail_base_url": "https://remail.aishop6.com",
        "remail_max_recycle_retries": "3",
        "proxy": "socks5h://egyd1230749-region-US-sid-auto:3wnuqht8@us.cliproxy.io:3010",
        "cf_api_url": "https://mail-api.shaosiming.online",
        "cf_admin_token": "sayd82k4lzbmp6g3",
        "cf_domain": "yhmsiming.site",
        "sms_provider": "smsbower",
        "sms_country": "6",
        "sms_max_price": "0.008",
        "sms_max_phone_attempts": "3",
        "sms_per_phone_timeout": "120",
        "sms_enabled": "0",
        "sms_api_key": "NnsAKSMAA7IhyTNQXk0J4I2om6bpdb1Q",
        "sms_provider_ids": "3237",
        "sms_except_provider_ids": "3327,1170,2953,3251",
    }
    for k, v in default_kvs.items():
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            (k, str(v)),
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


def analyze_import_data(text: str, kind: str = "") -> dict:
    """导入前多维数据透视与去重分析引擎。

    1. 逐行智能清理与容错解析（支持任意分隔符、乱序、首尾字符清理）；
    2. 批次内部重复检测与去重统计；
    3. 极速内存对比号池（outlook_accounts）与已注册库（registered）；
    4. 计算全新号、号池重复、已注册老号的分布与综合重复率；
    5. 返回抽样数据及非法错误列表供前端可视化 HUD 渲染。
    """
    from mail_providers.base import split_import_records
    from mail_providers.smart_parser import parse_smart_account_line

    numbered = split_import_records(text or "")
    total_lines = len(numbered)
    if total_lines == 0:
        return {
            "ok": True,
            "total_lines": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "invalid_errors": [],
            "unique_count": 0,
            "internal_dup_count": 0,
            "internal_dup_emails": [],
            "brand_new_count": 0,
            "pool_dup_count": 0,
            "pool_breakdown": {},
            "registered_dup_count": 0,
            "dup_rate": 0.0,
            "preview_rows": [],
        }

    parsed_rows = []
    invalid_errors = []
    email_occurrence: dict[str, list[int]] = {}

    for line_no, raw_line in numbered:
        res = parse_smart_account_line(raw_line, default_kind=kind or "outlook")
        if res.get("ok"):
            em = res["email"].lower()
            if em not in email_occurrence:
                email_occurrence[em] = []
            email_occurrence[em].append(line_no)
            res["_line_no"] = line_no
            parsed_rows.append(res)
        else:
            invalid_errors.append({
                "line": line_no,
                "raw": raw_line[:80],
                "error": res.get("error") or "无法识别有效邮箱凭据",
            })

    valid_count = len(parsed_rows)
    invalid_count = len(invalid_errors)
    unique_count = len(email_occurrence)
    internal_dup_count = sum(len(lines) - 1 for lines in email_occurrence.values() if len(lines) > 1)
    internal_dup_emails = [em for em, lines in email_occurrence.items() if len(lines) > 1]

    # 一次性批量读取数据库中的号池和已注册库内存映射
    with _lock:
        con = _conn()
        cur_reg = con.execute("SELECT lower(email) FROM registered")
        reg_set = {row[0] for row in cur_reg.fetchall()}

        cur_pool = con.execute("SELECT lower(email), status FROM outlook_accounts")
        pool_map = {row[0]: row[1] or "available" for row in cur_pool.fetchall()}

    brand_new_count = 0
    pool_dup_count = 0
    pool_breakdown: dict[str, int] = {
        "available": 0,
        "in_use": 0,
        "done": 0,
        "failed": 0,
        "archived": 0,
    }
    registered_dup_count = 0

    seen_unique_email = set()
    for row in parsed_rows:
        em = row["email"]
        if em in seen_unique_email:
            continue
        seen_unique_email.add(em)

        if em in reg_set:
            registered_dup_count += 1
        elif em in pool_map:
            pool_dup_count += 1
            st = pool_map[em]
            pool_breakdown[st] = pool_breakdown.get(st, 0) + 1
        else:
            brand_new_count += 1

    # 综合重复率：(内部重复 + 库内已存在) / 总有效行数
    dup_lines_total = internal_dup_count + pool_dup_count + registered_dup_count
    dup_rate = round((dup_lines_total / total_lines) * 100, 1) if total_lines > 0 else 0.0

    # 生成抽样预览列表 (前 30 条)
    preview_rows = []
    seen_in_preview = set()
    for row in parsed_rows[:30]:
        em = row["email"]
        is_internal_dup = em in seen_in_preview
        seen_in_preview.add(em)

        db_status = "brand_new"
        db_label = "全新未入库"
        if is_internal_dup:
            db_status = "internal_dup"
            db_label = "批次内重复"
        elif em in reg_set:
            db_status = "registered_gpt"
            db_label = "GPT老号 (已注册)"
        elif em in pool_map:
            st = pool_map[em]
            st_map = {
                "available": ("pool_available", "号池可用"),
                "in_use": ("pool_in_use", "号池运行中"),
                "done": ("pool_done", "号池已完成"),
                "failed": ("pool_failed", "号池已失败"),
                "archived": ("pool_archived", "号池已归档"),
            }
            db_status, db_label = st_map.get(st, ("pool_existing", "号池已存在"))

        pwd = row.get("password") or ""
        masked_pwd = (pwd[:2] + "****" + pwd[-2:]) if len(pwd) >= 5 else ("***" if pwd else "")

        preview_rows.append({
            "line": row["_line_no"],
            "email": em,
            "password_masked": masked_pwd,
            "has_password": bool(pwd),
            "client_id": row.get("client_id") or "",
            "rt_len": len(row.get("refresh_token") or ""),
            "relay_url": row.get("relay_url") or "",
            "kind": row.get("kind") or kind or "outlook",
            "detected_format": row.get("detected_format") or "标准格式",
            "db_status": db_status,
            "db_label": db_label,
            "is_valid": True,
        })

    return {
        "ok": True,
        "total_lines": total_lines,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "invalid_errors": invalid_errors[:50],
        "unique_count": unique_count,
        "internal_dup_count": internal_dup_count,
        "internal_dup_emails": internal_dup_emails[:50],
        "brand_new_count": brand_new_count,
        "pool_dup_count": pool_dup_count,
        "pool_breakdown": pool_breakdown,
        "registered_dup_count": registered_dup_count,
        "dup_rate": dup_rate,
        "preview_rows": preview_rows,
    }


def import_accounts(text: str, kind: str = "", strategy: str = "smart_merge") -> dict:
    """批量入库（万级数据毫秒级极速写入与智能去重策略）。

    参数：
        text: 导入文本
        kind: 邮箱协议来源
        strategy: 导入策略
            - 'smart_merge' (默认推荐): 全新号入库；老号 (registered) 自动绑定 OAuth 凭据并标记已用；号池已有账号若提供新凭据则自动更新。
            - 'skip_duplicates': 只要在号池或已注册库已存在，直接跳过，仅写入全新号。
            - 'overwrite': 强制重置号池已有账号为 available 状态并覆盖更新凭据。
    """
    t0 = time.time()
    rows = parse_lines(text, kind)
    now = time.time()
    inserted = updated = skipped = skipped_registered = 0

    # 预先整理去重输入行（同批次内重复的以最后一行有效凭证为准，但保留总解析行数）
    dedup_rows: dict[str, dict] = {}
    for r in rows:
        em = (r.get("email") or "").strip().lower()
        if em:
            dedup_rows[em] = r

    with _lock:
        con = _conn()

        # 1. 一次性批量读取 registered 已注册库的内存映射
        cur_reg = con.execute("SELECT lower(email), extra_json FROM registered")
        reg_map = {row[0]: row[1] for row in cur_reg.fetchall()}

        # 2. 一次性批量读取 outlook_accounts 当前号池的内存映射
        cur_pool = con.execute("SELECT lower(email), refresh_token, relay_url, kind, status FROM outlook_accounts")
        pool_map = {row[0]: (row[1] or "", row[2] or "", row[3] or "", row[4] or "available") for row in cur_pool.fetchall()}

        update_registered_extra = []
        update_pool_registered_done = []
        insert_pool_records = []
        update_pool_records = []

        for em, r in dedup_rows.items():
            row_kind = r.get("kind") or kind or "outlook"
            password = r.get("password", "") or ""
            client_id = r.get("client_id", "") or ""
            refresh = r.get("refresh_token", "") or ""
            relay = r.get("relay_url", "") or ""

            # 策略：跳过所有库内重复账号
            if strategy == "skip_duplicates":
                if em in reg_map or em in pool_map:
                    skipped += 1
                    continue
                insert_pool_records.append((em, password, client_id, refresh, relay, row_kind, now))
                pool_map[em] = (refresh, relay, row_kind, "available")
                inserted += 1
                continue

            # 分支 A: 已注册老号
            if em in reg_map:
                if client_id or refresh or password:
                    raw_extra = reg_map[em]
                    try:
                        ex = json.loads(raw_extra) if raw_extra else {}
                    except Exception:
                        ex = {}
                    ex["mail_oauth"] = {
                        "client_id": client_id,
                        "refresh_token": refresh,
                        "password": password,
                        "kind": row_kind,
                    }
                    update_registered_extra.append((json.dumps(ex, ensure_ascii=False), em))

                update_pool_registered_done.append((now, em))
                skipped_registered += 1
                continue

            # 分支 B: 未在 registered 库中的新号
            if em not in pool_map:
                insert_pool_records.append((em, password, client_id, refresh, relay, row_kind, now))
                pool_map[em] = (refresh, relay, row_kind, "available")
                inserted += 1
            else:
                old_refresh, old_relay, old_kind, old_status = pool_map[em]
                # 策略：overwrite 覆盖模式 或 凭证变更模式
                if strategy == "overwrite" or old_refresh != refresh or old_relay != relay or old_kind != row_kind:
                    update_pool_records.append((refresh, password, client_id, relay, row_kind, now, em))
                    pool_map[em] = (refresh, relay, row_kind, "available")
                    updated += 1
                else:
                    skipped += 1

        # 3. 单事务高并发批量执行 (executemany)
        if update_registered_extra:
            con.executemany("UPDATE registered SET extra_json=? WHERE lower(email)=?", update_registered_extra)
        if update_pool_registered_done:
            con.executemany("UPDATE outlook_accounts SET status='done', finished_at=?, fail_reason='already_registered' WHERE lower(email)=?", update_pool_registered_done)
        if insert_pool_records:
            con.executemany(
                "INSERT INTO outlook_accounts(email, password, client_id, refresh_token, relay_url, kind, status, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'available', ?)",
                insert_pool_records,
            )
        if update_pool_records:
            con.executemany(
                "UPDATE outlook_accounts SET refresh_token=?, password=?, client_id=?, relay_url=?, kind=?, status='available', imported_at=?, fail_reason=NULL "
                "WHERE lower(email)=?",
                update_pool_records,
            )

        con.commit()

    cost_seconds = round(time.time() - t0, 3)
    return {
        "parsed": len(rows),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "skipped_registered": skipped_registered,
        "cost_seconds": cost_seconds,
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
            k, {"available": 0, "in_use": 0, "done": 0, "failed": 0, "archived": 0, "total": 0}
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
    """按状态批量删除。status 必须是 available/in_use/done/failed/archived 之一；
    传 'all' 删全部。返回受影响行数。"""
    valid = {"available", "in_use", "done", "failed", "archived", "all"}
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


def export_pool_accounts(
    status: str = "",
    kind: str = "",
    emails: Optional[list[str]] = None,
    reason_like: str = "",
) -> list[dict]:
    """查询指定状态、类型、错误原因或邮箱列表的号池账号，供导出。"""
    con = _conn()
    sql = "SELECT email, password, client_id, refresh_token, relay_url, kind, status, fail_reason, imported_at FROM outlook_accounts"
    where, args = [], []
    if emails and len(emails) > 0:
        cleaned = [e.strip().lower() for e in emails if e and e.strip()]
        placeholders = ",".join("?" for _ in cleaned)
        where.append(f"lower(email) IN ({placeholders})")
        args.extend(cleaned)
    else:
        if status and status.lower() != "all":
            where.append("status=?")
            args.append(status.lower())
        if kind and kind.lower() != "all":
            where.append("kind=?")
            args.append(kind.strip().lower())
        if reason_like:
            where.append("(fail_reason LIKE ? OR fail_reason LIKE ?)")
            args.extend([f"%{reason_like}%", f"%{reason_like.lower()}%"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY imported_at ASC"
    return [dict(r) for r in con.execute(sql, args).fetchall()]



def archive_failed_accounts() -> int:
    """把所有 failed 号一次性归档为 archived。

    归档 = 只留存、不再使用：claim_next / claim_account 只捞 available/failed，
    archived 的号自动退出注册与验活队列；fail_reason 原样保留，随时可查证。
    取消归档见 unarchive_accounts（archived -> failed）。
    """
    with _lock:
        con = _conn()
        rc = con.execute("UPDATE outlook_accounts SET status='archived' WHERE status='failed'")
        con.commit()
        return rc.rowcount


def unarchive_accounts() -> int:
    """把所有 archived 号退回归档，恢复为 failed（失败原因原样保留）。"""
    with _lock:
        con = _conn()
        rc = con.execute("UPDATE outlook_accounts SET status='failed' WHERE status='archived'")
        con.commit()
        return rc.rowcount


def stats() -> dict:
    con = _conn()
    cur = con.execute(
        "SELECT status, COUNT(*) AS n FROM outlook_accounts GROUP BY status"
    )
    out = {"available": 0, "in_use": 0, "done": 0, "failed": 0, "archived": 0, "total": 0}
    for r in cur.fetchall():
        out[r["status"]] = r["n"]
        out["total"] += r["n"]
    return out


def get_dashboard_summary() -> dict:
    """获取仪表盘全能概览数据（号池统计、注册资产矩阵、国家分布 Top 榜、安全加固率）。"""
    con = _conn()
    p_stats = stats()

    # 注册资产核心指标
    cur_reg = con.execute("""
        SELECT
            COUNT(*) AS total_reg,
            SUM(CASE WHEN totp_secret IS NOT NULL AND trim(totp_secret) != '' THEN 1 ELSE 0 END) AS with_2fa,
            SUM(CASE WHEN password IS NOT NULL AND trim(password) != '' THEN 1 ELSE 0 END) AS with_pwd,
            SUM(CASE WHEN oauth_status IN ('success', 'success_phone', 'success_direct') THEN 1 ELSE 0 END) AS with_oauth,
            SUM(CASE WHEN (exported_at IS NOT NULL AND exported_at > 0) OR (at_exported_at IS NOT NULL AND at_exported_at > 0) THEN 1 ELSE 0 END) AS exported_cnt,
            SUM(CASE WHEN (exported_at IS NULL OR exported_at = 0) AND (at_exported_at IS NULL OR at_exported_at = 0) THEN 1 ELSE 0 END) AS unexported_cnt
        FROM registered
    """).fetchone()

    total_reg = cur_reg["total_reg"] or 0
    with_2fa = cur_reg["with_2fa"] or 0
    with_pwd = cur_reg["with_pwd"] or 0
    with_oauth = cur_reg["with_oauth"] or 0
    exported_cnt = cur_reg["exported_cnt"] or 0
    unexported_cnt = cur_reg["unexported_cnt"] or 0

    # 出口国家分布 TOP 8
    cur_geo = con.execute("""
        SELECT upper(trim(reg_country)) AS country, COUNT(*) AS n
        FROM registered
        WHERE reg_country IS NOT NULL AND trim(reg_country) != ''
        GROUP BY upper(trim(reg_country))
        ORDER BY n DESC LIMIT 8
    """)
    top_countries = [{"country": r["country"], "count": r["n"]} for r in cur_geo.fetchall()]

    # Remail 暂存复用池统计
    remail_cnt = 0
    try:
        cur_rem = con.execute("SELECT COUNT(*) FROM remail_recycle_pool WHERE is_used=0 AND expires_at >= ?", (time.time(),)).fetchone()
        remail_cnt = cur_rem[0] if cur_rem else 0
    except Exception:
        pass

    # 最近入库的 5 个账号（脱敏）
    cur_recent = con.execute("""
        SELECT email, reg_country, totp_secret, password, oauth_status, created_at
        FROM registered
        ORDER BY created_at DESC LIMIT 5
    """)
    recent_items = []
    for r in cur_recent.fetchall():
        em = r["email"]
        masked = em
        if "@" in em:
            u, d = em.split("@", 1)
            masked = (u[:3] + "***@" + d) if len(u) > 3 else (u[0] + "***@" + d)
        recent_items.append({
            "email": em,
            "masked_email": masked,
            "country": r["reg_country"] or "",
            "has_2fa": bool(r["totp_secret"]),
            "has_pwd": bool(r["password"]),
            "oauth_status": r["oauth_status"] or "",
            "created_at": r["created_at"] or 0,
        })

    # 计算注册安全加固率与成功率
    sec_rate = round((with_2fa / total_reg * 100), 1) if total_reg > 0 else 0
    pwd_rate = round((with_pwd / total_reg * 100), 1) if total_reg > 0 else 0
    total_processed = (p_stats.get("done", 0) + p_stats.get("failed", 0))
    success_rate = round((p_stats.get("done", 0) / total_processed * 100), 1) if total_processed > 0 else 0

    return {
        "pool": p_stats,
        "registered": {
            "total": total_reg,
            "with_2fa": with_2fa,
            "with_pwd": with_pwd,
            "with_oauth": with_oauth,
            "exported": exported_cnt,
            "unexported": unexported_cnt,
            "sec_rate": sec_rate,
            "pwd_rate": pwd_rate,
            "success_rate": success_rate,
        },
        "countries": top_countries,
        "recent": recent_items,
        "remail_active_cached": remail_cnt,
    }


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

        row = con.execute(
            "SELECT password, totp_secret, totp_factor_id, reg_country, reg_city, reg_ip, extra_json "
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
            if row["extra_json"]:
                try:
                    old_ex = json.loads(row["extra_json"])
                    if isinstance(old_ex, dict):
                        # 保留旧 extra 中未被新 extra 覆盖的字段（如 mail_oauth, plus_check 等）
                        for ok, ov in old_ex.items():
                            if ok not in extra:
                                extra[ok] = ov
                except Exception:
                    pass

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

    # 代理健康度记账：只有**新建**的号才 +1（同号重跑覆盖不计）。
    # reg_proxy 由 registrar 在注册成功时写进 d，随 extra_json 落库；
    # 按 (归一化模板, 出口国家) 聚合（动态代理一号一 session，完整串不可聚合）。
    if row is None:
        note_proxy_registered(
            str((extra or {}).get("reg_proxy") or ""), reg_country or ""
        )


def update_registered_oauth(
    email: str,
    access_token: str = "",
    refresh_token: str = "",
    id_token: str = "",
    session_token: str = "",
    cookie_header: str = "",
    extra_data: Optional[dict] = None,
    oauth_status: Optional[str] = None,
) -> bool:
    """OAuth 导出与 Token 刷新成功后回写 access_token / refresh_token / id_token / session_token / cookie_header 及 extra_json。

    只有真实完成 Codex OAuth 授权或 RT 换取成功的账号才会标记 oauth_status='success'；
    普通 Web 登录重登/Session 刷新仅更新 Web 会话凭证，不产生虚假的 OAuth 授权成功标记。
    """
    email = (email or "").strip().lower()
    if not email:
        return False
    with _lock:
        con = _conn()
        row = con.execute("SELECT * FROM registered WHERE lower(email)=?", (email,)).fetchone()
        if not row:
            return False
        d = dict(row)
        extra = {}
        if d.get("extra_json"):
            try:
                extra = json.loads(d["extra_json"])
            except Exception:
                extra = {}
        if extra_data:
            extra.update(extra_data)

        new_at = access_token.strip() or d.get("access_token") or ""
        new_rt = refresh_token.strip() or d.get("refresh_token") or ""
        new_it = id_token.strip() or d.get("id_token") or ""
        new_st = session_token.strip() or d.get("session_token") or ""
        new_cookie = cookie_header.strip() or d.get("cookie_header") or ""

        # 核心自愈：若有新的有效 Token，自动将历史残留的 "token_invalid" (凭证失效) 状态重置为正常套餐状态
        if new_at or new_st or new_rt:
            plus_chk = extra.get("plus_check")
            if isinstance(plus_chk, dict):
                old_st = str(plus_chk.get("status") or "").lower()
                if old_st in ("token_invalid", "error", "failed") or not old_st:
                    claims_plan = (extra.get("oauth_export") or {}).get("claims", {}).get("plan_type") or "free"
                    extra["plus_check"] = {
                        "status": claims_plan,
                        "label": "Free" if claims_plan == "free" else claims_plan.upper(),
                        "updated_at": time.time(),
                        "reason": "Token 刷新成功，状态已自动更新",
                    }

        # 计算精确的 OAuth 授权状态：
        # 1. 显式指定了 oauth_status 则使用指定的；
        # 2. 若本次提供了新的非空 refresh_token，则确认为 success；
        # 3. 否则保留原数据库中的 oauth_status（避免将普通 Web 登录误打为 OAuth 成功）
        final_oauth_status = d.get("oauth_status") or ""
        if oauth_status is not None:
            final_oauth_status = oauth_status
        elif refresh_token.strip():
            final_oauth_status = "success"

        con.execute(
            "UPDATE registered SET access_token=?, refresh_token=?, id_token=?, session_token=?, "
            "cookie_header=?, oauth_status=?, oauth_updated_at=?, extra_json=? WHERE lower(email)=?",
            (new_at, new_rt, new_it, new_st, new_cookie, final_oauth_status, time.time(), json.dumps(extra, ensure_ascii=False), email),
        )
        con.commit()
        return True


def update_registered_oauth_status(email: str, status: str, error: str = "") -> bool:
    """更新账号的 OAuth 授权状态 (success / need_phone / failed)。

    安全防呆保护：如果账号原本已有有效的 Refresh Token，偶发网络重试失败时
    绝不将整体状态直接抹杀为 failed，保留其原本的授权成功标记，避免用户误判凭证丢失。
    """
    email = (email or "").strip().lower()
    if not email:
        return False
    status = (status or "").strip().lower()
    with _lock:
        con = _conn()
        row = con.execute("SELECT refresh_token, oauth_status, extra_json FROM registered WHERE email=?", (email,)).fetchone()
        if not row:
            return False
        extra = {}
        if row["extra_json"]:
            try:
                extra = json.loads(row["extra_json"])
            except Exception:
                extra = {}
        oauth_meta = extra.get("oauth_export") or {}

        # 核心防呆保护：如果原本有有效 RT，重试遇到网络错误时不抹除成功态
        existing_rt = str(row["refresh_token"] or "").strip()
        existing_st = str(row["oauth_status"] or "").strip().lower()
        final_status = status
        if existing_rt and status in ("failed", "error", "cancelled"):
            if existing_st.startswith("success"):
                final_status = existing_st
            else:
                final_status = "success"
            oauth_meta["last_retry_error"] = error or status
        else:
            oauth_meta["status"] = status
            if error:
                oauth_meta["error"] = error

        oauth_meta["updated_at"] = time.time()
        extra["oauth_export"] = oauth_meta

        con.execute(
            "UPDATE registered SET oauth_status=?, oauth_updated_at=?, extra_json=? WHERE email=?",
            (final_status, time.time(), json.dumps(extra, ensure_ascii=False), email),
        )
        con.commit()
        return True


def recover_oauth_credentials(emails: Optional[list[str]] = None) -> dict:
    """一键扫描并找回/自愈历史授权凭证。

    双重找回策略：
      1. 数据库内存盘自愈：账号已具备 RT/AT 但 status 误被置为 failed/空时，立即修复为 success；
         同时将历史在 oauth_attempt_features 中接码成功的账号自动打标为 success_phone；
      2. 本地历史导出文件全自动溯源找回：扫描 webui/exports/cpa/ 和 webui/exports/sub2api/，
         提取历史已导出的 Codex RT/AT 并自动回填入库，瞬间满血恢复！
    """
    from pathlib import Path

    con = _conn()
    cpa_dir = Path(__file__).resolve().parent / "exports" / "cpa"
    sub2_dir = Path(__file__).resolve().parent / "exports" / "sub2api"

    # 先查询所有在 oauth_attempt_features 表里成功接码过的邮箱集合
    phone_verified_emails = set()
    try:
        cur_pv = con.execute("SELECT DISTINCT lower(email) FROM oauth_attempt_features WHERE phone_verified = 1")
        phone_verified_emails = {r[0].strip().lower() for r in cur_pv.fetchall() if r[0]}
    except Exception:
        phone_verified_emails = set()

    target_emails = [e.strip().lower() for e in emails if e and e.strip()] if emails else []
    if target_emails:
        placeholders = ",".join("?" for _ in target_emails)
        cur = con.execute(f"SELECT email, access_token, refresh_token, id_token, oauth_status, extra_json FROM registered WHERE lower(email) IN ({placeholders})", target_emails)
    else:
        cur = con.execute("SELECT email, access_token, refresh_token, id_token, oauth_status, extra_json FROM registered")

    rows = [dict(r) for r in cur.fetchall()]
    recovered_from_db = 0
    recovered_from_files = 0

    with _lock:
        for r in rows:
            em = r["email"].strip().lower()
            at = str(r.get("access_token") or "").strip()
            rt = str(r.get("refresh_token") or "").strip()
            it = str(r.get("id_token") or "").strip()
            st = str(r.get("oauth_status") or "").strip().lower()
            extra = {}
            if r.get("extra_json"):
                try:
                    extra = json.loads(r["extra_json"])
                except Exception:
                    extra = {}

            is_phone = (em in phone_verified_emails) or bool(extra.get("oauth_export", {}).get("phone_verified"))

            # 策略 1: 库内已有 RT 但状态未同步、误打为 failed 或需补齐接码成功标记
            if rt:
                need_update = False
                new_st = "success_phone" if is_phone else (st if st in ("success_phone", "success_direct") else "success")
                if not st.startswith("success") or st in ("failed", "error", ""):
                    need_update = True
                elif is_phone and st != "success_phone":
                    new_st = "success_phone"
                    need_update = True

                if need_update:
                    extra.setdefault("oauth_export", {})["status"] = new_st
                    if is_phone:
                        extra["oauth_export"]["phone_verified"] = True
                        extra["oauth_export"]["auth_method"] = "phone_verified"
                    extra["oauth_export"]["updated_at"] = time.time()
                    con.execute(
                        "UPDATE registered SET oauth_status=?, extra_json=? WHERE lower(email)=?",
                        (new_st, json.dumps(extra, ensure_ascii=False), em),
                    )
                    recovered_from_db += 1
                    continue

            # 策略 2: 库内缺 RT，尝试从本地 exports 历史文件找回
            if not rt:
                file_found = False
                cpa_file = cpa_dir / f"codex-{em}.json"
                sub2_file = sub2_dir / f"sub2-{em}.json"
                file_at, file_rt, file_it = "", "", ""
                if cpa_file.exists():
                    try:
                        c_data = json.loads(cpa_file.read_text(encoding="utf-8"))
                        file_at = str(c_data.get("access_token") or "").strip()
                        file_rt = str(c_data.get("refresh_token") or "").strip()
                        file_it = str(c_data.get("id_token") or "").strip()
                        file_found = bool(file_rt or file_at)
                    except Exception:
                        pass
                if not file_found and sub2_file.exists():
                    try:
                        s_data = json.loads(sub2_file.read_text(encoding="utf-8"))
                        creds = s_data.get("credentials") or {}
                        file_at = str(creds.get("access_token") or "").strip()
                        file_rt = str(creds.get("refresh_token") or "").strip()
                        file_it = str(creds.get("id_token") or "").strip()
                        file_found = bool(file_rt or file_at)
                    except Exception:
                        pass

                if file_found and file_rt:
                    new_st = "success_phone" if is_phone else "success_direct"
                    extra.setdefault("oauth_export", {})["status"] = new_st
                    if is_phone:
                        extra["oauth_export"]["phone_verified"] = True
                        extra["oauth_export"]["auth_method"] = "phone_verified"
                    extra["oauth_export"]["recovered_from_file"] = True
                    extra["oauth_export"]["updated_at"] = time.time()
                    con.execute(
                        "UPDATE registered SET access_token=?, refresh_token=?, id_token=?, oauth_status=?, extra_json=? WHERE lower(email)=?",
                        (file_at or at, file_rt, file_it or it, new_st, json.dumps(extra, ensure_ascii=False), em),
                    )
                    recovered_from_files += 1

        con.commit()

    return {
        "recovered_from_db": recovered_from_db,
        "recovered_from_files": recovered_from_files,
        "total_recovered": recovered_from_db + recovered_from_files,
        "total_checked": len(rows),
    }


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
    """把 Plus 检查结果写入 extra_json.plus_check。

    验死反哺：状态翻转为 banned / token_invalid 时，反查该号注册时的
    reg_proxy + reg_country 给 proxy_health 计一次死亡（同一号反复验死只计一次）。
    """
    email = email.lower()
    con = _conn()
    cur = con.execute(
        "SELECT extra_json, reg_country FROM registered WHERE email=?", (email,)
    )
    row = cur.fetchone()
    if not row:
        return
    extra = {}
    if row["extra_json"]:
        try:
            extra = json.loads(row["extra_json"])
        except Exception:
            extra = {}
    old_pc = extra.get("plus_check") or {}
    extra["plus_check"] = plus_info
    with _lock:
        con.execute(
            "UPDATE registered SET extra_json=? WHERE email=?",
            (json.dumps(extra, ensure_ascii=False), email),
        )
        con.commit()

    def _is_dead(pc: dict) -> bool:
        st = str((pc or {}).get("plus_type") or (pc or {}).get("status") or "").lower()
        return st in ("banned", "token_invalid")

    # 非死 → 死 的翻转才计数，避免反复验活重复累计
    if _is_dead(plus_info) and not _is_dead(old_pc):
        note_proxy_dead(
            str(extra.get("reg_proxy") or ""), (row["reg_country"] or "")
        )


# ──────────────────────── 代理健康度（死号反哺拉黑） ────────────────────────
# 背景（2026-08 实测数据）：延迟封号按 IP 段连坐 —— US 出口死亡率 3.2% 是
# JP/PH 的 10~32 倍，且同天同段批量死。按 (代理模板×国家) 记账，死亡率超阈值
# 自动拉黑该组合，注册选国家时跳过 —— 脏出口无需人工发现就自动出局。
#
# 键的形态：动态住宅代理一号一个 session，完整串每号都不同，聚合失灵；
# 所以用 proxy_util.normalize_proxy_key 抹掉 session 得到模板，配出口国家。


# 自动拉黑阈值：该组合注册满 3 个号、死了至少 2 个、且死亡率 >= 25% 才拉黑。
# 正常组合死亡率应 < 5%；小样本要求更极端（3 个死 2 个 = 67%）。
_PROXY_BL_MIN_TOTAL = 3
_PROXY_BL_MIN_DEAD = 2
_PROXY_BL_RATE = 0.25


def _proxy_key(proxy_raw: str, country: str = "") -> tuple[str, str]:
    """原始代理串 + 国家 → (归一化模板, 国家码)。空串代理返回 ("", "")。"""
    from .proxy_util import normalize_proxy_key

    t = normalize_proxy_key(proxy_raw or "")
    if not t:
        return ("", "")
    c = (country or "").strip().upper()
    return (t, c)


def note_proxy_registered(proxy_raw: str, country: str = "") -> None:
    """注册成功记账：该 (模板, 国家) 组合 total+1（save_registered 新建号时自动调用）。"""
    t, c = _proxy_key(proxy_raw, country)
    if not t:
        return
    now = time.time()
    with _lock:
        con = _conn()
        con.execute(
            "INSERT INTO proxy_health(template, country, total, last_used) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(template, country) DO UPDATE SET total=total+1, last_used=?",
            (t, c, now, now),
        )
        con.commit()


def note_proxy_dead(proxy_raw: str, country: str = "") -> Optional[dict]:
    """验死记账：该 (模板, 国家) 组合 dead+1，超阈值自动拉黑。返回最新健康行。"""
    t, c = _proxy_key(proxy_raw, country)
    if not t:
        return None
    now = time.time()
    with _lock:
        con = _conn()
        rc = con.execute(
            "UPDATE proxy_health SET dead=dead+1, last_dead=? "
            "WHERE template=? AND country=?",
            (now, t, c),
        )
        if rc.rowcount == 0:
            return None  # 该组合没记账过（老号无存档），跳过
        row = con.execute(
            "SELECT * FROM proxy_health WHERE template=? AND country=?", (t, c)
        ).fetchone()
        if row and not row["blacklisted"] \
                and row["total"] >= _PROXY_BL_MIN_TOTAL \
                and row["dead"] >= _PROXY_BL_MIN_DEAD \
                and row["dead"] >= row["total"] * _PROXY_BL_RATE:
            reason = f"死亡率 {row['dead']}/{row['total']} 自动拉黑"
            con.execute(
                "UPDATE proxy_health SET blacklisted=1, blacklisted_at=?, blacklist_reason=? "
                "WHERE template=? AND country=?",
                (now, reason, t, c),
            )
            row = con.execute(
                "SELECT * FROM proxy_health WHERE template=? AND country=?", (t, c)
            ).fetchone()
            logging.getLogger("db").warning(
                f"[proxy_health] 出口 {c or '?'} ({t[:40]}…) {reason}"
            )
        con.commit()
        return dict(row) if row else None


def get_blacklist() -> dict:
    """拉黑清单（三种粒度，各自带通配行）：
    - combos: {(模板, 国家)} 组合级拉黑（注册选国家时跳过）
    - templates: 整模板拉黑（(模板,'') 通配行 或 全部国家行都黑）
    - countries: 国家级拉黑（('',国家) 通配行 —— 按国家死亡率 chip 拉黑，
      对未记账的模板组合也生效）
    """
    con = _conn()
    rows = con.execute(
        "SELECT template, country FROM proxy_health WHERE blacklisted=1"
    ).fetchall()
    combos = {(r["template"], r["country"]) for r in rows if r["template"] and r["country"]}
    templates = {r["template"] for r in rows if r["template"] and not r["country"]}
    countries = {r["country"] for r in rows if not r["template"] and r["country"]}
    # 「已有国家行全部拉黑」也视为整模板黑
    for r in con.execute(
        "SELECT template, COUNT(*) AS n, SUM(blacklisted) AS b "
        "FROM proxy_health WHERE template != '' AND country != '' GROUP BY template"
    ).fetchall():
        if (r["b"] or 0) >= r["n"]:
            templates.add(r["template"])
    return {"combos": combos, "templates": templates, "countries": countries}


def is_combo_blacklisted(proxy_raw: str, country: str = "") -> bool:
    """该 (模板, 国家) 组合是否已被任一粒度拉黑。"""
    t, c = _proxy_key(proxy_raw, country)
    if not t:
        return False
    bl = get_blacklist()
    return (
        (t, c) in bl["combos"]
        or t in bl["templates"]
        or (c in bl["countries"] if c else False)
    )


def pick_healthy_country(proxy_raw: str, candidates: list[str]) -> Optional[str]:
    """从候选国家里挑一个未拉黑且死亡率最低的。

    给注册流程「目标国家被拉黑时自动换国」用。候选全被拉黑返回 None。
    """
    t, _ = _proxy_key(proxy_raw)
    if not t or not candidates:
        return None
    con = _conn()
    ph = con.execute(
        "SELECT country, dead, total FROM proxy_health WHERE template=?", (t,)
    ).fetchall()
    stats = {r["country"]: (r["dead"], r["total"]) for r in ph}
    bl = get_blacklist()
    best, best_rate = None, None
    for c in candidates:
        c = (c or "").strip().upper()
        if not c or (t, c) in bl["combos"] or c in bl["countries"]:
            continue
        dead, total = stats.get(c, (0, 0))
        rate = dead / total if total else 0
        if best_rate is None or rate < best_rate:
            best, best_rate = c, rate
    return best


def list_proxy_health() -> list[dict]:
    """全量健康度清单（前端代理池页展示），按模板聚合并带分国家明细。

    返回 [{template, total, dead, blacklisted, last_used, last_dead,
           countries: [{country, total, dead, blacklisted}]}]
    前端拿代理池条目归一化成 template 后 join。
    """
    con = _conn()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM proxy_health ORDER BY last_used DESC"
    ).fetchall()]
    out: dict[str, dict] = {}
    for r in rows:
        t = r["template"]
        slot = out.setdefault(t, {
            "template": t, "total": 0, "dead": 0,
            "blacklisted": False, "last_used": 0, "last_dead": 0,
            "countries": [],
        })
        if not r["country"]:
            # 通配行（整模板手动拉黑的占位）→ 只影响拉黑态，不计数量
            if r["blacklisted"]:
                slot["blacklisted"] = True
            continue
        slot["total"] += r["total"]
        slot["dead"] += r["dead"]
        if r["blacklisted"]:
            slot["blacklisted"] = True
        slot["last_used"] = max(slot["last_used"], r["last_used"] or 0)
        slot["last_dead"] = max(slot["last_dead"], r["last_dead"] or 0)
        slot["countries"].append({
            "country": r["country"], "total": r["total"], "dead": r["dead"],
            "blacklisted": bool(r["blacklisted"]),
        })
    for slot in out.values():
        slot["countries"].sort(key=lambda c: (-c["dead"], c["country"]))
    return sorted(out.values(), key=lambda s: (-(s["dead"] or 0), -(s["total"] or 0)))


def set_proxy_blacklist(proxy_raw: str, country: str, on: bool, reason: str = "") -> None:
    """手动拉黑 / 取消拉黑。三种粒度：

    - proxy + 国家码：拉黑该 (模板, 国家) 组合 —— 注册选国家时自动换国
    - proxy + 空/'*'：整模板拉黑（写 (模板,'') 通配行，未记账国家也挡住）
    - 空 proxy + 国家码：拉黑**所有模板**的该国家出口（按国家死亡率 chip 用）
    """
    t, _ = _proxy_key(proxy_raw) if (proxy_raw or "").strip() else ("", "")
    c = (country or "").strip().upper()
    now = time.time()
    with _lock:
        con = _conn()
        if not t and not c:
            return
        if on:
            if not t:
                # 按国家拉黑所有模板：写 ('', 国家) 通配行 + 已有该国家行也拉黑
                reason = reason or f"手动拉黑 {c} 出口（全部模板）"
                con.execute(
                    "INSERT INTO proxy_health(template, country, blacklisted, blacklisted_at, blacklist_reason) "
                    "VALUES ('', ?, 1, ?, ?) "
                    "ON CONFLICT(template, country) DO UPDATE SET blacklisted=1, blacklisted_at=?, blacklist_reason=?",
                    (c, now, reason, now, reason),
                )
                con.execute(
                    "UPDATE proxy_health SET blacklisted=1, blacklisted_at=?, blacklist_reason=? "
                    "WHERE country=? AND template != ''",
                    (now, reason, c),
                )
            elif c in ("", "*"):
                # 整模板拉黑：通配行 + 已有国家行全部拉黑
                reason = reason or "手动拉黑整个模板"
                con.execute(
                    "INSERT INTO proxy_health(template, country, blacklisted, blacklisted_at, blacklist_reason) "
                    "VALUES (?, '', 1, ?, ?) "
                    "ON CONFLICT(template, country) DO UPDATE SET blacklisted=1, blacklisted_at=?, blacklist_reason=?",
                    (t, now, reason, now, reason),
                )
                con.execute(
                    "UPDATE proxy_health SET blacklisted=1, blacklisted_at=?, blacklist_reason=? "
                    "WHERE template=? AND country != ''",
                    (now, reason, t),
                )
            else:
                reason = reason or f"手动拉黑 {c} 出口"
                con.execute(
                    "INSERT INTO proxy_health(template, country, blacklisted, blacklisted_at, blacklist_reason) "
                    "VALUES (?, ?, 1, ?, ?) "
                    "ON CONFLICT(template, country) DO UPDATE SET blacklisted=1, blacklisted_at=?, blacklist_reason=?",
                    (t, c, now, reason, now, reason),
                )
        else:
            if not t:
                con.execute(
                    "UPDATE proxy_health SET blacklisted=0, blacklist_reason='' "
                    "WHERE country=? AND (template != '' OR template = '')",
                    (c,),
                )
            elif c in ("", "*"):
                con.execute(
                    "UPDATE proxy_health SET blacklisted=0, blacklist_reason='' WHERE template=?",
                    (t,),
                )
            else:
                con.execute(
                    "UPDATE proxy_health SET blacklisted=0, blacklist_reason='' "
                    "WHERE template=? AND country=?",
                    (t, c),
                )
        con.commit()


def proxy_health_overview() -> dict:
    """健康度总览面板数据：汇总统计 + 按国家死亡率 + 问题出口榜 + 最近死亡号。"""
    con = _conn()

    # ── 汇总统计（country='' 通配行不计入跟踪数）──
    row = con.execute(
        "SELECT COUNT(*) AS tracked, "
        "SUM(COALESCE(blacklisted, 0)) AS blacklisted, "
        "SUM(COALESCE(total, 0)) AS total_reg, "
        "SUM(COALESCE(dead, 0)) AS dead "
        "FROM proxy_health WHERE country != ''"
    ).fetchone()
    tracked = row["tracked"] or 0
    blacklisted = row["blacklisted"] or 0
    total_reg = row["total_reg"] or 0
    dead = row["dead"] or 0

    # ── 按国家死亡率（面板核心视角：哪国出口在杀号）──
    by_country = [
        {
            "country": r["country"],
            "total": r["total"],
            "dead": r["dead"],
            "rate": round(r["dead"] / r["total"], 4) if r["total"] else 0,
            "blacklisted": bool(r["bl"]),
        }
        for r in con.execute(
            "SELECT country, SUM(total) AS total, SUM(dead) AS dead, "
            "MAX(blacklisted) AS bl FROM proxy_health "
            "WHERE country != '' GROUP BY country "
            "ORDER BY dead * 1.0 / MAX(total, 1) DESC, dead DESC"
        ).fetchall()
    ]

    # ── 问题出口榜：(模板, 国家) 有死亡的按死亡率降序，最多 8 个 ──
    worst = [
        dict(r) for r in con.execute(
            "SELECT template, country, total, dead, blacklisted, blacklist_reason, last_dead "
            "FROM proxy_health WHERE dead > 0 AND country != '' "
            "ORDER BY dead * 1.0 / total DESC, dead DESC LIMIT 8"
        ).fetchall()
    ]

    # ── 最近死亡号：扫 plus_check 死亡的号（几千行 json 解析，面板打开时一次）──
    recent_dead = []
    try:
        cur = con.execute(
            "SELECT email, reg_country, created_at, extra_json FROM registered "
            "WHERE extra_json LIKE '%\"banned\"%' OR extra_json LIKE '%\"token_invalid\"%' "
            "ORDER BY created_at DESC LIMIT 3000"
        )
        for r in cur.fetchall():
            if len(recent_dead) >= 10:
                break
            try:
                extra = json.loads(r["extra_json"] or "{}")
            except Exception:
                continue
            pc = extra.get("plus_check") or {}
            st = str(pc.get("plus_type") or pc.get("status") or "").lower()
            if st not in ("banned", "token_invalid"):
                continue
            ts = pc.get("checked_at") or pc.get("updated_at") or r["created_at"] or 0
            recent_dead.append({
                "email": r["email"],
                "status": st,
                "country": (r["reg_country"] or "").upper(),
                "proxy": str(extra.get("reg_proxy") or ""),
                "ts": ts,
            })
        recent_dead.sort(key=lambda x: x["ts"], reverse=True)
        recent_dead = recent_dead[:10]
    except Exception as e:
        logging.getLogger("db").warning(f"[proxy_health] 最近死亡号统计失败: {e}")

    return {
        "summary": {
            "tracked": tracked,             # 有注册记录的 (模板,国家) 组合数
            "blacklisted": blacklisted,     # 已拉黑组合数
            "total_registered": total_reg,  # 有档案号的总注册数
            "total_dead": dead,             # 总验死数
            "death_rate": round(dead / total_reg, 4) if total_reg else 0,
        },
        "by_country": by_country,
        "worst": worst,
        "recent_dead": recent_dead,
    }


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
    if f in ("unchecked", "health_unchecked", "unverified"):
        return "(extra_json IS NULL OR extra_json NOT LIKE '%\"plus_check\"%')"
    if f == "pro":
        return "(extra_json LIKE '%\"pro_20x\"%' OR extra_json LIKE '%\"pro_5x\"%' OR extra_json LIKE '%\"pro_active\"%' OR extra_json LIKE '%\"pro_eligible\"%')"
    if f == "team":
        return "extra_json LIKE '%\"team_active\"%'"
    if f == "plus":
        return "(extra_json LIKE '%\"plus_eligible\"%' OR extra_json LIKE '%\"plus_active\"%')"
    if f == "plus_active":
        return "extra_json LIKE '%\"plus_active\"%'"
    if f == "plus_eligible":
        return "extra_json LIKE '%\"plus_eligible\"%'"
    if f == "free":
        return "(extra_json LIKE '%\"free\"%' AND extra_json NOT LIKE '%\"banned\"%' AND extra_json NOT LIKE '%\"token_invalid\"%' AND extra_json NOT LIKE '%\"account_deactivated\"%')"
    # ── 封号检测与凭证失效精准/健壮筛选 ──
    if f in ("banned", "deactivated", "account_deactivated", "disabled"):
        return (
            "(extra_json LIKE '%\"banned\"%' "
            "OR extra_json LIKE '%\"account_deactivated\"%' "
            "OR extra_json LIKE '%\"deactivated\"%' "
            "OR extra_json LIKE '%封号%' "
            "OR extra_json LIKE '%账号已被禁用%')"
        )
    if f in ("token_invalid", "invalid_token", "token_expired", "expired", "401"):
        return (
            "(extra_json LIKE '%\"token_invalid\"%' "
            "OR extra_json LIKE '%\"token_expired\"%' "
            "OR extra_json LIKE '%\"401 Unauthorized\"%' "
            "OR extra_json LIKE '%凭证失效%' "
            "OR extra_json LIKE '%Token失效%')"
        )
    if f in ("dead", "all_dead", "invalid_or_banned", "failed_check", "bad"):
        return (
            "(extra_json LIKE '%\"banned\"%' "
            "OR extra_json LIKE '%\"token_invalid\"%' "
            "OR extra_json LIKE '%\"account_deactivated\"%' "
            "OR extra_json LIKE '%\"token_expired\"%' "
            "OR extra_json LIKE '%封号%' "
            "OR extra_json LIKE '%凭证失效%')"
        )
    if f in ("alive", "valid", "normal", "healthy"):
        return (
            "(extra_json LIKE '%\"plus_check\"%' "
            "AND extra_json NOT LIKE '%\"banned\"%' "
            "AND extra_json NOT LIKE '%\"token_invalid\"%' "
            "AND extra_json NOT LIKE '%\"account_deactivated\"%' "
            "AND extra_json NOT LIKE '%\"token_expired\"%' "
            "AND extra_json NOT LIKE '%封号%' "
            "AND extra_json NOT LIKE '%凭证失效%')"
        )
    # ── OAICS 资格检测筛选 ──
    if f == "oa_unchecked":
        return "(oa_check IS NULL OR oa_check = '')"
    if f == "oa_hit":
        return "oa_check LIKE '%\"state\":\"OAICS\"%'"
    if f == "oa_miss":
        return "(oa_check IS NOT NULL AND oa_check != '' AND oa_check NOT LIKE '%\"state\":\"OAICS\"%')"
    # ── OAuth 授权状态筛选 ──
    if f == "oauth_success":
        return "(oauth_status = 'success' OR oauth_status = 'success_phone' OR oauth_status = 'success_direct')"
    if f in ("oauth_phone_verified", "phone_verified", "oauth_phone"):
        return "(oauth_status = 'success_phone' OR extra_json LIKE '%\"phone_verified\": true%' OR extra_json LIKE '%\"phone_verified\":true%')"
    if f in ("oauth_no_phone", "no_phone_needed", "oauth_direct"):
        return "(oauth_status = 'success_direct' OR (oauth_status = 'success' AND (extra_json NOT LIKE '%\"phone_verified\": true%' AND extra_json NOT LIKE '%\"phone_verified\":true%')))"
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


def get_registered_countries() -> list[dict]:
    """统计当前数据库中所有注册账号的出口国家分布及数量。"""
    con = _conn()
    cur = con.execute("""
        SELECT
            upper(trim(reg_country)) AS country,
            COUNT(*) AS count
        FROM registered
        WHERE reg_country IS NOT NULL AND trim(reg_country) != ''
        GROUP BY country
        ORDER BY count DESC
    """)
    return [{"country": r[0], "count": r[1]} for r in cur.fetchall() if r[0]]


def _registered_where(
    filt: str = "all",
    search: str = "",
    filter_plan: str = "",
    filter_sec: str = "",
    filter_extract: str = "",
    filter_oauth: str = "",
    filter_domain: str = "",
    filter_country: str = "",
    filter_at_export: str = "",
    filter_health: str = "",
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
    for sub in (filter_health, filter_plan, filter_sec, filter_extract, filter_oauth):
        if sub and sub != "all":
            c = _parse_single_filter_clause(sub)
            if c and c not in conditions:
                conditions.append(c)

    if filter_domain and filter_domain != "all":
        c, d_args = _parse_domain_filter_clause(filter_domain)
        if c:
            conditions.append(c)
            args.extend(d_args)

    if filter_country and filter_country != "all":
        c_country = filter_country.strip().upper()
        if c_country in ("NONE", "EMPTY", "UNKNOWN", "NULL"):
            conditions.append("(reg_country IS NULL OR trim(reg_country) = '')")
        elif c_country:
            conditions.append("upper(trim(reg_country)) = ?")
            args.append(c_country)

    # 导出留痕筛选：
    # - "all": 全部
    # - "exported": 导出过任意格式
    # - "unexported": 从未导出过
    # - "at": 仅导出过 AT (access_token / 邮箱----AT)
    # - "email_pw": 仅导出过账密 / 2FA 系列
    # - "sub2api": 仅导出过 Sub2API JSON
    # - "cpa": 仅导出过 CPA 系列
    # - "session": 仅导出过 Session JSON
    exp_filter = (filter_at_export or "").strip().lower()
    if exp_filter in ("exported", "yes", "true", "1"):
        conditions.append(
            "((exported_at IS NOT NULL AND exported_at > 0) OR (at_exported_at IS NOT NULL AND at_exported_at > 0))"
        )
    elif exp_filter in ("unexported", "no", "false", "0"):
        conditions.append(
            "((exported_at IS NULL OR exported_at = 0) AND (at_exported_at IS NULL OR at_exported_at = 0))"
        )
    elif exp_filter == "at":
        conditions.append(
            "(export_fmt IN ('at', 'email_at') OR (at_exported_at IS NOT NULL AND at_exported_at > 0))"
        )
    elif exp_filter in ("email_pw", "pwd", "2fa"):
        conditions.append(
            "export_fmt IN ('email_pw', 'email_pw_2fa', 'email_pw_2fa_relay')"
        )
    elif exp_filter in ("sub2api", "sub2"):
        conditions.append(
            "(export_fmt LIKE '%sub2api%' OR export_fmt_label LIKE '%Sub2API%')"
        )
    elif exp_filter == "cpa":
        conditions.append(
            "(export_fmt LIKE '%cpa%' OR export_fmt_label LIKE '%CPA%')"
        )
    elif exp_filter in ("session", "session_json"):
        conditions.append(
            "(export_fmt LIKE '%session%' OR export_fmt_label LIKE '%Session%')"
        )

    search_cleaned = (search or "").strip().lower()
    if search_cleaned:
        conditions.append(
            "(lower(email) LIKE ? OR lower(coalesce(export_note, '')) LIKE ? OR lower(coalesce(at_export_note, '')) LIKE ? OR lower(coalesce(export_fmt_label, '')) LIKE ?)"
        )
        args.extend([f"%{search_cleaned}%", f"%{search_cleaned}%", f"%{search_cleaned}%", f"%{search_cleaned}%"])

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
    filter_country: str = "",
    filter_at_export: str = "",
    filter_health: str = "",
) -> int:
    con = _conn()
    where, args = _registered_where(
        filter_rt, search,
        filter_plan=filter_plan, filter_sec=filter_sec,
        filter_extract=filter_extract, filter_oauth=filter_oauth,
        filter_domain=filter_domain,
        filter_country=filter_country,
        filter_at_export=filter_at_export,
        filter_health=filter_health,
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
    filter_country: str = "",
    filter_at_export: str = "",
    filter_health: str = "",
) -> list[str]:
    """返回符合过滤条件的所有注册邮箱列表。"""
    con = _conn()
    where, args = _registered_where(
        filter_rt, search,
        filter_plan=filter_plan, filter_sec=filter_sec,
        filter_extract=filter_extract, filter_oauth=filter_oauth,
        filter_domain=filter_domain,
        filter_country=filter_country,
        filter_at_export=filter_at_export,
        filter_health=filter_health,
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
    filter_country: str = "",
    filter_at_export: str = "",
    filter_health: str = "",
) -> list[dict]:
    con = _conn()
    where, args = _registered_where(
        filter_rt, search,
        filter_plan=filter_plan, filter_sec=filter_sec,
        filter_extract=filter_extract, filter_oauth=filter_oauth,
        filter_domain=filter_domain,
        filter_country=filter_country,
        filter_at_export=filter_at_export,
        filter_health=filter_health,
    )
    cur = con.execute(
        f"SELECT email, password, totp_secret, reg_country, reg_city, reg_ip, "
        f"length(access_token) AS at_len, length(session_token) AS st_len, "
        f"length(refresh_token) AS rt_len, oauth_status, oauth_updated_at, "
        f"exported_at, export_fmt, export_fmt_label, export_note, "
        f"at_exported_at, at_export_note, "
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
        session_data = None
        if d.get("extra_json"):
            try:
                extra = json.loads(d["extra_json"])
                plus = extra.get("plus_check")
                oauth_meta = extra.get("oauth_export")
                extract_link = extra.get("extract_link")
                session_data = extra.get("session_data")
            except Exception:
                pass
        d["plus_check"] = plus
        d["oauth_export"] = oauth_meta
        d["extract_link"] = extract_link
        d["session_data"] = session_data
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


def mark_exported(emails: list[str], fmt_id: str, fmt_label: str = "", note: str = "") -> int:
    """给这批 email 打上导出留痕标记（导出时间 + 格式 + 格式标签 + 用户备注）。

    支持所有导出格式（AT、账密2FA、CPA、Sub2API、Session 等）。
    查不到的 email 静默跳过，返回实际打标的行数。重复导出会更新为最后一次导出的状态。
    若导出格式属于 AT，同时兼容更新原有的 at_exported_at / at_export_note。
    """
    cleaned = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    if not cleaned:
        return 0
    now = time.time()
    fmt_id = (fmt_id or "").strip()
    fmt_label = (fmt_label or "").strip()
    note = (note or "").strip()
    with _lock:
        con = _conn()
        n = 0
        CHUNK = 500
        for i in range(0, len(cleaned), CHUNK):
            part = cleaned[i:i + CHUNK]
            placeholders = ",".join("?" * len(part))
            if fmt_id in ("at", "email_at"):
                cur = con.execute(
                    f"UPDATE registered SET exported_at=?, export_fmt=?, export_fmt_label=?, export_note=?, at_exported_at=?, at_export_note=? "
                    f"WHERE email IN ({placeholders})",
                    [now, fmt_id, fmt_label, note, now, note] + part,
                )
            else:
                cur = con.execute(
                    f"UPDATE registered SET exported_at=?, export_fmt=?, export_fmt_label=?, export_note=? "
                    f"WHERE email IN ({placeholders})",
                    [now, fmt_id, fmt_label, note] + part,
                )
            n += cur.rowcount if cur.rowcount > 0 else 0
        con.commit()
    return n


def mark_at_exported(emails: list[str], note: str = "") -> int:
    """兼容旧接口：给这批 email 打上 AT 已导出标记。"""
    return mark_exported(emails, fmt_id="at", fmt_label="access_token", note=note)


def update_export_note(emails: list[str], note: str = "") -> int:
    """批量更新或修改账号的导出备注。"""
    cleaned = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    if not cleaned:
        return 0
    note = (note or "").strip()
    with _lock:
        con = _conn()
        n = 0
        CHUNK = 500
        for i in range(0, len(cleaned), CHUNK):
            part = cleaned[i:i + CHUNK]
            placeholders = ",".join("?" * len(part))
            cur = con.execute(
                f"UPDATE registered SET export_note=?, at_export_note=? "
                f"WHERE email IN ({placeholders})",
                [note, note] + part,
            )
            n += cur.rowcount if cur.rowcount > 0 else 0
        con.commit()
    return n


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


def update_run_email(run_id: str, email: str) -> None:
    """更新任务运行记录的真实邮箱（从虚拟占位符替换为真正分配/购买的邮箱）。"""
    if not run_id or not email:
        return
    with _lock:
        con = _conn()
        con.execute("UPDATE runs SET email=? WHERE run_id=?", (email.strip().lower(), run_id))
        con.commit()


def finish_run(run_id: str, status: str, error: str = "", category: str = "", email: str = "") -> None:
    with _lock:
        con = _conn()
        if email:
            con.execute(
                "UPDATE runs SET status=?, finished_at=?, error=?, error_category=?, email=? WHERE run_id=?",
                (status, time.time(), (error or "")[:500], category or None, email.strip().lower(), run_id),
            )
        else:
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


# ──────────────────────── Remail 已购未用邮箱智能复用池 ────────────────────────


def push_remail_recycled(
    email: str,
    service_token: str,
    order_no: str = "",
    project_id: int = 2,
    email_suffix: str = "icloud.com",
    service_mode: str = "purchase",
    receive_until: str = "",
    expires_at: float = 0.0,
) -> bool:
    """将已购买但尚未完成注册（未消耗 OTP）的 Remail 邮箱暂存入复用池（严格校验时效与失败重试次数）。"""
    email_clean = (email or "").strip().lower()
    token_clean = (service_token or "").strip()
    if not email_clean or not token_clean:
        return False

    now = time.time()
    if not expires_at or expires_at <= now:
        window = 3300 if service_mode == "purchase" else 540
        expires_at = now + window

    # 剩余安全窗口不足 8 分钟（480 秒）的不入池，直接废弃
    if (expires_at - now) < 480:
        logging.getLogger("db").info(
            f"[remail_pool] 邮箱 {email_clean} 剩余时效不足 8 分钟 ({int(expires_at - now)}s)，不暂存"
        )
        return False

    # 读取最大允许重试复用次数（默认 3 次）
    max_retries = 3
    try:
        max_retries = int(get_setting("remail_max_recycle_retries", "3") or 3)
    except Exception:
        max_retries = 3

    with _lock:
        con = _conn()
        cur_row = con.execute(
            "SELECT fail_count FROM remail_recycle_pool WHERE email=?", (email_clean,)
        ).fetchone()
        cur_fails = (int(cur_row["fail_count"] or 0) if cur_row else 0) + 1

        # 超过配置的最大失败次数则直接标记为 2 (超限废弃)，不再复用，防止异常账号无限死循环
        if cur_fails >= max_retries:
            new_is_used = 2
            log_msg = (
                f"[remail_pool] ⚠️ 邮箱 {email_clean} 失败复用次数已达上限 ({cur_fails}/{max_retries} 次)，"
                f"已自动标记废弃不再复用（下次将直接重新购入新邮箱）"
            )
            logging.getLogger("db").warning(log_msg)
        else:
            new_is_used = 0
            rem_min = int((expires_at - now) / 60)
            log_msg = (
                f"[remail_pool] ♻️ 成功暂存未用邮箱: {email_clean} (项目={project_id}, "
                f"失败重试={cur_fails}/{max_retries}次, 剩余有效={rem_min}分钟, 待后续复用)"
            )
            logging.getLogger("db").info(log_msg)

        con.execute(
            """
            INSERT INTO remail_recycle_pool(
                email, service_token, order_no, project_id, email_suffix,
                service_mode, receive_until, expires_at, created_at, is_used, fail_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                service_token=excluded.service_token,
                order_no=excluded.order_no,
                expires_at=excluded.expires_at,
                is_used=excluded.is_used,
                fail_count=excluded.fail_count
            """,
            (
                email_clean,
                token_clean,
                str(order_no or ""),
                int(project_id or 2),
                str(email_suffix or "icloud.com").strip().lower(),
                str(service_mode or "purchase").strip().lower(),
                str(receive_until or ""),
                float(expires_at),
                float(now),
                new_is_used,
                cur_fails,
            ),
        )
        con.commit()
    return True


def claim_remail_recycled(
    project_id: int = 2,
    email_suffix: str = "icloud.com",
    service_mode: str = "purchase",
    min_remaining_sec: int = 480,
) -> Optional[dict]:
    """从复用池检索并锁定一个仍在有效时效内且重试次数未超限的未用邮箱（优先复用，杜绝积分浪费）。"""
    now = time.time()
    min_expire = now + max(60, min_remaining_sec)
    pid = int(project_id or 2)
    suf = str(email_suffix or "icloud.com").strip().lower()
    mode = str(service_mode or "purchase").strip().lower()

    # 最大允许重试次数
    max_retries = 3
    try:
        max_retries = int(get_setting("remail_max_recycle_retries", "3") or 3)
    except Exception:
        max_retries = 3

    with _lock:
        con = _conn()
        # 1. 自动清理已超时的未用邮箱（剩余不足 8 分钟或失败超限的标记废弃）
        con.execute(
            "UPDATE remail_recycle_pool SET is_used=2 WHERE is_used=0 AND (expires_at < ? OR fail_count >= ?)",
            (min_expire, max_retries),
        )

        # 2. 查出 1 条剩余时效充足且失败次数小于上限的可用邮箱
        cur = con.execute(
            """
            SELECT id, email, service_token, order_no, project_id, email_suffix, service_mode, receive_until, expires_at, fail_count
            FROM remail_recycle_pool
            WHERE is_used = 0 AND project_id = ? AND email_suffix = ? AND service_mode = ? AND expires_at >= ? AND fail_count < ?
            ORDER BY expires_at DESC LIMIT 1
            """,
            (pid, suf, mode, min_expire, max_retries),
        )
        row = cur.fetchone()
        if not row:
            con.commit()
            return None

        # 3. 临时标记为处理锁定中（is_used=3 防止并发多 worker 领走同一个）
        con.execute("UPDATE remail_recycle_pool SET is_used=3 WHERE id = ?", (row["id"],))
        con.commit()

        remaining_min = round((row["expires_at"] - now) / 60, 1)
        f_cnt = int(row.get("fail_count") or 0)
        logging.getLogger("db").info(
            f"[remail_pool] ♻️ 命中复用池暂存邮箱: email={row['email']} (项目={pid}, 历史重试={f_cnt}/{max_retries}次, 剩余有效时长={remaining_min}分钟, 免花积分！)"
        )
        return dict(row)


def mark_remail_consumed(email: str) -> None:
    """注册成功后，彻底标记该邮箱已被 OpenAI 消费完毕。"""
    email_clean = (email or "").strip().lower()
    if not email_clean:
        return
    with _lock:
        con = _conn()
        con.execute("UPDATE remail_recycle_pool SET is_used=1 WHERE email = ?", (email_clean,))
        con.commit()


def release_remail_recycled(email: str) -> None:
    """领取后若因某种原因无法初始化，归还复用池。"""
    email_clean = (email or "").strip().lower()
    if not email_clean:
        return
    with _lock:
        con = _conn()
        con.execute(
            "UPDATE remail_recycle_pool SET is_used=0 WHERE email = ? AND is_used=3",
            (email_clean,),
        )
        con.commit()


def count_remail_recycled(project_id: int = 0) -> int:
    """统计当前复用池中有效未用的邮箱总数。"""
    now = time.time()
    min_expire = now + 480
    max_retries = 3
    try:
        max_retries = int(get_setting("remail_max_recycle_retries", "3") or 3)
    except Exception:
        max_retries = 3

    con = _conn()
    if project_id > 0:
        cur = con.execute(
            "SELECT COUNT(*) AS cnt FROM remail_recycle_pool WHERE is_used IN (0, 3) AND project_id = ? AND expires_at >= ? AND fail_count < ?",
            (int(project_id), min_expire, max_retries),
        )
    else:
        cur = con.execute(
            "SELECT COUNT(*) AS cnt FROM remail_recycle_pool WHERE is_used IN (0, 3) AND expires_at >= ? AND fail_count < ?",
            (min_expire, max_retries),
        )
    row = cur.fetchone()
    return int(row["cnt"]) if row else 0


def list_remail_recycled(limit: int = 50) -> list[dict]:
    """查看当前复用池中有效未用的邮箱明细列表。"""
    now = time.time()
    min_expire = now + 480
    con = _conn()
    cur = con.execute(
        """
        SELECT id, email, order_no, project_id, email_suffix, service_mode, receive_until, expires_at, created_at, is_used, fail_count
        FROM remail_recycle_pool
        WHERE is_used IN (0, 3) AND expires_at >= ?
        ORDER BY expires_at DESC LIMIT ?
        """,
        (min_expire, limit),
    )
    rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["remaining_minutes"] = max(0, round((d["expires_at"] - now) / 60, 1))
        out.append(d)
    return out


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
