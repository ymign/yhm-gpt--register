"""FastAPI 主程序：路由 + SSE 流式日志。

启动:
    python -m webui.app
或者:
    python start_webui.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, Response as PlainResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from . import db, export_formats, registrar  # noqa: E402
from .auto_loop import CONTROLLER as AUTO_LOOP  # noqa: E402
from .exporter import _decode_jwt_payload, _get_auth  # noqa: E402
from mail_providers import (  # noqa: E402
    ImportValidationError,
    MailProviderError,
    create_mail_provider,
    get_provider_class,
    list_pooled_providers,
    list_providers,
)

# 启动时自动释放卡死的 in_use 号（上次进程崩溃 / 强退留下的）
try:
    _released = db.release_stale_in_use(stale_seconds=1800)
    if _released > 0:
        logging.getLogger("webui").info(f"[startup] 释放 {_released} 个卡死的 in_use 号")
except Exception as _e:
    logging.getLogger("webui").warning(f"[startup] release_stale 失败: {_e}")

# 启动时应用持久化的 PoW 算力槽位配置（WebUI「全自动批量」页可改，存 settings 表）
try:
    import sentinel_quickjs as _sq
    _slots = int(db.get_setting("sentinel_pow_slots", "") or _sq.get_pow_slots())
    _applied = _sq.set_pow_slots(_slots)
    logging.getLogger("webui").info(
        f"[startup] PoW 算力槽位 = {_applied}（前端「PoW 算力槽位」= 同时解算的 node 进程数；"
        f"启动日志里的「预计算池缓冲水位」是另一回事，不是这个值）"
    )
except Exception as _e:
    logging.getLogger("webui").warning(f"[startup] 应用 PoW 槽位配置失败: {_e}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("webui")

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="GPT Outlook Register WebUI", docs_url=None, redoc_url=None)


# ──────────────────────── Pydantic 模型 ────────────────────────


class ImportReq(BaseModel):
    text: str = Field(..., description="每行一个号，格式由 kind 决定或智能自动识别")
    kind: str = Field(
        "",
        description="邮箱来源（outlook / ...）。留空则按段数猜或智能嗅探",
    )
    strategy: str = Field(
        "smart_merge",
        description="导入策略: smart_merge(智能合并/更新凭证) / skip_duplicates(仅导入全新号) / overwrite(覆盖并重置可用)",
    )


class AnalyzeImportReq(BaseModel):
    text: str = Field(..., description="待分析的文本内容")
    kind: str = Field("", description="指定或默认邮箱来源协议")


class RegisterReq(BaseModel):
    email: Optional[str] = Field(None, description="留空 = 自动 claim 下一个 available 或动态造号")
    mail_source: Optional[str] = Field(None, description="指定邮箱渠道: cf_temp / outlook / icloud_relay 等")
    want_access_token: bool = True
    want_session_token: bool = True
    want_refresh_token: bool = True
    proxy: str = ""
    proxy_country: str = ""  # 目标代理国家，如 BR, DE, GB 等
    otp_timeout: int = 10
    allow_existing_login: bool = True
    want_password: bool = True  # 是否自动设置登录密码（默认开）
    want_2fa: bool = False


# ──────────────────────── API ────────────────────────


@app.get("/api/health")
def health():
    return {"ok": True, "stats": db.stats()}


@app.get("/api/dashboard/summary")
def api_dashboard_summary():
    """获取仪表盘全景指标矩阵（号池存量、注册资产大盘、国家分布 Top 榜、安全加固率）。"""
    return {"ok": True, **db.get_dashboard_summary()}


@app.post("/api/accounts/analyze_import")
def api_analyze_import(req: AnalyzeImportReq):
    """导入前多维数据透视与去重分析。

    自动识别任意分隔符、乱序格式与脏字符，比对号池与已注册库，
    返回重复率、全新号数、库内状态分布与解析错误明细。
    """
    try:
        res = db.analyze_import_data(req.text, kind=req.kind)
        return res
    except Exception as e:
        raise HTTPException(400, f"分析失败: {e}")


@app.post("/api/import")
def api_import(req: ImportReq):
    """批量导入号池（支持任意乱序自适应与多分隔符容错）。

    strategy 可选：
        - smart_merge: 全新号入库；已注册老号自动绑定 OAuth 凭据；号池已有账号若提供新凭据则自动更新。
        - skip_duplicates: 遇到库内已存在直接跳过，仅写入全新号。
        - overwrite: 强制重置号池已有账号为 available 状态并覆盖更新凭据。
    """
    try:
        result = db.import_accounts(req.text, kind=req.kind, strategy=req.strategy)
    except ImportValidationError as e:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "message": str(e), "errors": e.errors},
        )
    except MailProviderError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **result, "stats": db.stats()}


@app.get("/api/accounts")
def api_accounts(status: str = "", limit: int = 50, offset: int = 0, kind: str = ""):
    items = db.list_accounts(status=status, limit=limit, offset=offset, kind=kind)
    total = db.count_accounts(status=status, kind=kind)
    return {
        "ok": True,
        "items": items,
        "total": total,
        "by_kind": db.stats_by_kind(),
    }


@app.delete("/api/accounts/{email}")
def api_delete_account(email: str):
    ok = db.delete_account(email)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.post("/api/accounts/clean-registered")
def api_clean_registered(mode: str = "delete"):
    """比对号池与本地已注册库，清理号池中所有已在 registered 表中的账号。"""
    res = db.clean_registered_from_pool(mode=mode)
    return {"ok": True, **res, "stats": db.stats()}


class MailboxValidateReq(BaseModel):
    emails: Optional[list[str]] = None
    status_filter: Optional[str] = ""
    kind_filter: Optional[str] = ""
    action: Optional[str] = "mark_failed"  # mark_failed / delete
    workers: Optional[int] = 15
    proxy: Optional[str] = ""
    proxy_pool: Optional[str] = ""
    proxy_country: Optional[str] = ""


@app.post("/api/accounts/validate/start")
def api_validate_mailbox_start(req: MailboxValidateReq):
    """启动邮箱号池快速验活任务。"""
    from . import mailbox_validator
    try:
        task_id = mailbox_validator.start_mailbox_validation(
            emails=req.emails,
            config={
                "status_filter": req.status_filter,
                "kind_filter": req.kind_filter,
                "action": req.action,
                "workers": req.workers,
                "proxy": req.proxy,
                "proxy_pool": req.proxy_pool,
                "proxy_country": req.proxy_country,
            },
        )
        return {"ok": True, "task_id": task_id}
    except Exception as e:
        raise HTTPException(400, f"启动邮箱验活失败: {e}")


@app.get("/api/accounts/validate/{task_id}/stream")
async def api_validate_mailbox_stream(task_id: str, request: Request):
    """订阅邮箱验活进度与日志 SSE。"""
    from . import mailbox_validator
    task = mailbox_validator.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")

    loop = asyncio.get_event_loop()

    async def event_gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await loop.run_in_executor(None, _safe_get, task.queue)
                if msg == "" or msg is None:
                    if task.finished_at > 0 and task.queue.empty():
                        yield "event: end\ndata: {}\n\n"
                        break
                    continue
                if isinstance(msg, dict):
                    kind = msg.get("kind", "progress")
                    yield f"event: {kind}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    if kind == "end":
                        break
        except Exception as e:
            logger.warning(f"SSE 传输异常: {e}")

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/accounts/validate/{task_id}/stop")
def api_validate_mailbox_stop(task_id: str):
    from . import mailbox_validator
    ok = mailbox_validator.stop_task(task_id)
    return {"ok": ok}


class BulkDeleteReq(BaseModel):
    status: Optional[str] = Field(None, description="available/in_use/done/failed/all")
    emails: Optional[list[str]] = Field(None, description="按 email 列表删")


@app.post("/api/accounts/bulk_delete")
def api_bulk_delete(req: BulkDeleteReq):
    """按状态或 email 列表批量删除号池。两个参数二选一（status 优先）。"""
    if req.status:
        n = db.delete_accounts_by_status(req.status)
        return {"ok": True, "deleted": n, "by": "status", "stats": db.stats()}
    if req.emails:
        n = db.delete_accounts_by_emails(req.emails)
        return {"ok": True, "deleted": n, "by": "emails", "stats": db.stats()}
    raise HTTPException(400, "需要 status 或 emails")


@app.post("/api/accounts/reset_failed")
def api_reset_failed():
    n = db.reset_failed_to_available()
    return {"ok": True, "reset": n, "stats": db.stats()}


@app.post("/api/accounts/archive_failed")
def api_archive_failed():
    """一键归档：全部 failed → archived（只留存，退出注册/验活领取队列）。"""
    n = db.archive_failed_accounts()
    return {"ok": True, "archived": n, "stats": db.stats()}


@app.post("/api/accounts/unarchive")
def api_unarchive():
    """一键取消归档：全部 archived → failed（失败原因原样保留，可再重置/重试）。"""
    n = db.unarchive_accounts()
    return {"ok": True, "unarchived": n, "stats": db.stats()}


@app.post("/api/accounts/reset/{email}")
def api_reset_account(email: str):
    """重置单个号：done / failed → available。"""
    ok = db.reset_to_available(email)
    if not ok:
        raise HTTPException(404, f"邮箱 {email} 不存在")
    return {"ok": True, "email": email}


class BulkResetReq(BaseModel):
    emails: list[str]


@app.post("/api/accounts/bulk_reset")
def api_bulk_reset(req: BulkResetReq):
    """批量重置：done / failed → available。"""
    if not req.emails:
        raise HTTPException(400, "emails 不能为空")
    n = db.bulk_reset_to_available(req.emails)
    return {"ok": True, "reset": n, "stats": db.stats()}


@app.post("/api/accounts/release_stale")
def api_release_stale(stale_seconds: int = 1800):
    n = db.release_stale_in_use(stale_seconds=stale_seconds)
    return {"ok": True, "released": n, "stats": db.stats()}


class ExportPoolReq(BaseModel):
    status: Optional[str] = Field(None, description="available/in_use/done/failed/all")
    emails: Optional[list[str]] = Field(None, description="按 email 列表导出")
    kind: Optional[str] = Field(None, description="按邮箱类型过滤")
    all: bool = Field(False, description="是否导出全部")
    reason_like: Optional[str] = Field(None, description="按失败原因过滤，如 AADSTS70000")


@app.post("/api/accounts/export")
def api_export_pool_accounts(req: ExportPoolReq):
    """一键导出号池邮箱（支持按失败状态、特定错误原因如 AADSTS70000、全部可用或勾选列表导出 4 段原始格式）。"""
    status = "" if req.all else (req.status or "")
    rows = db.export_pool_accounts(
        status=status,
        kind=req.kind or "",
        emails=req.emails,
        reason_like=req.reason_like or "",
    )
    lines = []
    for r in rows:
        em = (r.get("email") or "").strip()
        pwd = (r.get("password") or "").strip()
        cid = (r.get("client_id") or "").strip()
        rt = (r.get("refresh_token") or "").strip()
        relay = (r.get("relay_url") or "").strip()

        if cid or rt:
            lines.append(f"{em}----{pwd}----{cid}----{rt}")
        elif relay:
            lines.append(f"{em}----{relay}")
        elif pwd:
            lines.append(f"{em}----{pwd}")
        else:
            lines.append(em)

    tag = "aadsts70000" if (req.reason_like and "70000" in req.reason_like) else (req.status or ("selected" if req.emails else "all"))
    filename = f"mailbox_{tag}_{int(time.time())}.txt"
    return {
        "ok": True,
        "count": len(lines),
        "text": "\n".join(lines),
        "filename": filename,
    }


@app.get("/api/stats")
def api_stats():
    return {"ok": True, "stats": db.stats()}


# ──────────────────────── 代理连通性测试 ────────────────────────


class ProxyTestReq(BaseModel):
    proxies: list[str] = Field(..., description="要测试的代理列表")
    timeout: int = Field(8, description="每个代理超时秒数")
    test_url: str = Field("https://api.ipify.org?format=json",
                          description="测试目标 URL（默认返回出口 IP）")


@app.post("/api/proxy/test")
def api_proxy_test(req: ProxyTestReq):
    """并发测试代理连通性。复用真实注册流程的 create_http_session（含 socks5->socks5h
    标准化、trust_env=False），保证「测试正常」== 「跑号能用」。返回 ok / 延迟 / 出口 IP。

    协议说明：不写协议的 `ip:port` 被 curl 按 HTTP 代理处理；SOCKS5 需显式写 socks5://。
    """
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    try:
        from http_client import create_http_session
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"加载 http_client 失败: {e}")

    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    timeout = max(1, min(int(req.timeout or 8), 60))
    test_url = (req.test_url or "https://api.ipify.org?format=json").strip()

    proxies = [p.strip() for p in (req.proxies or []) if p and p.strip()]
    if not proxies:
        raise HTTPException(400, "proxies 不能为空")

    def _test_one(proxy: str):
        t0 = _t.perf_counter()
        try:
            sess = create_http_session(proxy=proxy)
            resp = sess.get(test_url, timeout=timeout)
            latency = int((_t.perf_counter() - t0) * 1000)
            if resp.status_code != 200:
                return {"ok": False, "latency_ms": latency, "error": f"HTTP {resp.status_code}"}
            ip = ""
            try:
                ip = resp.json().get("ip", "")
            except Exception:
                ip = (resp.text or "").strip()[:64]
            return {"ok": True, "latency_ms": latency, "ip": ip}
        except Exception as e:  # noqa: BLE001
            latency = int((_t.perf_counter() - t0) * 1000)
            return {"ok": False, "latency_ms": latency, "error": str(e)[:140]}

    results = {}
    with ThreadPoolExecutor(max_workers=min(20, len(proxies))) as ex:
        for proxy, res in zip(proxies, ex.map(_test_one, proxies)):
            results[proxy] = res
    return {"ok": True, "results": results}


@app.post("/api/register")
def api_register(req: RegisterReq):
    """启动注册任务，返回 run_id。前端拿 run_id 去 /api/runs/{run_id}/stream 订阅 SSE。"""
    mail_source = (req.mail_source or db.get_setting("mail_source", "cf_temp")).strip().lower()
    try:
        provider_cls = get_provider_class(mail_source)
    except MailProviderError as e:
        raise HTTPException(400, str(e))

    # 要不要 claim 号池，由 provider 自己声明的 pooled 决定
    if not provider_cls.pooled:
        # 非池化：地址由 provider 现造，用占位 account 走完后面的流程
        import time as _t
        account = {
            "email": f"{mail_source}_placeholder_{int(_t.time())}@placeholder.local",
            "password": "",
            "client_id": "",
            "refresh_token": "",
            "relay_url": "",
            "kind": mail_source,
        }
    elif req.email:
        account = db.claim_account(req.email)
        if not account:
            raise HTTPException(400, f"邮箱 {req.email} 不可用 (不存在 / 已 in_use / 已完成)")
        if (account.get("kind") or "outlook") != mail_source:
            # 号池里混放多种邮箱，点名的号必须和当前来源一致
            db.release_unused(account["email"])
            raise HTTPException(
                400,
                f"{req.email} 是 {account.get('kind')} 的号，"
                f"当前所选邮箱渠道为 {provider_cls.display_name}，请切换对应渠道",
            )
    else:
        account = db.claim_next(kind=mail_source)
        if not account:
            raise HTTPException(
                400,
                f"号池里没有可用的 {provider_cls.display_name} 账号；请先前往「导入」页面添加号池",
            )

    options = {
        "mail_source": mail_source,
        "want_access_token": req.want_access_token,
        "want_session_token": req.want_session_token,
        "want_refresh_token": req.want_refresh_token,
        "proxy": req.proxy,
        "proxy_country": req.proxy_country,
        "otp_timeout": int(req.otp_timeout),
        "allow_existing_login": req.allow_existing_login,
        "want_2fa": req.want_2fa,
        "want_password": req.want_password,
    }
    run_id = registrar.start_registration(account, options)
    logger.info(f"[run] {run_id} -> {account['email']} (mail_source={mail_source})")
    return {"ok": True, "run_id": run_id, "email": account["email"]}


@app.get("/api/runs/{run_id}/stream")
async def api_stream(run_id: str, request: Request):
    """SSE 实时推送日志 + 事件。"""
    q = registrar.get_run_queue(run_id)
    if q is None:
        raise HTTPException(404, "run_id not found or finished")

    async def event_gen():
        loop = asyncio.get_event_loop()
        try:
            while True:
                if await request.is_disconnected():
                    break
                # 从队列取消息（用 run_in_executor 避免阻塞 event loop）
                msg = await loop.run_in_executor(None, _safe_get, q)
                if msg is None:
                    # sentinel: 任务结束
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg.startswith("__EVENT__:"):
                    yield f"event: status\ndata: {msg[len('__EVENT__:'):]}\n\n"
                else:
                    yield f"event: log\ndata: {json.dumps({'line': msg}, ensure_ascii=False)}\n\n"
        finally:
            registrar.remove_run_queue(run_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 避免 nginx 缓冲
            "Connection": "keep-alive",
        },
    )


def _safe_get(q):
    try:
        return q.get(timeout=60)
    except Exception:
        return ""  # 心跳：返空串让 SSE 检查 disconnect


@app.get("/api/runs")
def api_runs(limit: int = 50):
    return {"ok": True, "items": db.list_runs(limit=limit)}


@app.get("/api/runs/{run_id}/log")
def api_run_log(run_id: str):
    """读取指定 run_id 的完整日志文件内容。"""
    from . import registrar

    log_file = registrar.LOG_DIR / f"{run_id}.log"
    if not log_file.exists():
        return {"ok": True, "run_id": run_id, "text": "暂无日志文件或日志已被清理", "lines": []}
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        return {"ok": True, "run_id": run_id, "text": text, "lines": lines}
    except Exception as e:
        raise HTTPException(500, f"读取日志异常: {e}")


@app.get("/api/registered/summary")
def api_registered_summary():
    """获取已注册账号资产专项概览看板数据。"""
    return db.get_registered_summary()


@app.get("/api/registered")
def api_registered(
    limit: int = 20,
    offset: int = 0,
    filter: str = "all",
    q: str = "",
    search: str = "",
    filter_plan: str = "",
    filter_sec: str = "",
    filter_extract: str = "",
    filter_oauth: str = "",
    filter_domain: str = "",
    filter_country: str = "",
    filter_at_export: str = "",
    filter_export: str = "",
    filter_health: str = "",
):
    query_str = (q or search).strip()
    effective_export_filter = filter_export or filter_at_export
    items = db.list_registered(
        limit=limit,
        offset=offset,
        filter_rt=filter,
        search=query_str,
        filter_plan=filter_plan,
        filter_sec=filter_sec,
        filter_extract=filter_extract,
        filter_oauth=filter_oauth,
        filter_domain=filter_domain,
        filter_country=filter_country,
        filter_at_export=effective_export_filter,
        filter_health=filter_health,
    )
    total = db.count_registered(
        filter_rt=filter,
        search=query_str,
        filter_plan=filter_plan,
        filter_sec=filter_sec,
        filter_extract=filter_extract,
        filter_oauth=filter_oauth,
        filter_domain=filter_domain,
        filter_country=filter_country,
        filter_at_export=effective_export_filter,
        filter_health=filter_health,
    )
    return {"ok": True, "items": items, "total": total}


@app.get("/api/registered_emails")
def api_registered_emails(
    filter: str = "all",
    q: str = "",
    search: str = "",
    filter_plan: str = "",
    filter_sec: str = "",
    filter_extract: str = "",
    filter_oauth: str = "",
    filter_domain: str = "",
    filter_country: str = "",
    filter_at_export: str = "",
    filter_export: str = "",
    filter_health: str = "",
):
    query_str = (q or search).strip()
    effective_export_filter = filter_export or filter_at_export
    emails = db.list_registered_emails(
        filter_rt=filter,
        search=query_str,
        filter_plan=filter_plan,
        filter_sec=filter_sec,
        filter_extract=filter_extract,
        filter_oauth=filter_oauth,
        filter_domain=filter_domain,
        filter_country=filter_country,
        filter_at_export=effective_export_filter,
        filter_health=filter_health,
    )
    return {"ok": True, "emails": emails, "count": len(emails), "total": len(emails)}


@app.get("/api/registered/domains")
@app.get("/api/registered_domains")
def api_registered_domains():
    """获取所有已注册账号的邮箱后缀域名分布统计。"""
    domains = db.get_registered_domains()
    return {"ok": True, "domains": domains}


@app.get("/api/registered/countries")
@app.get("/api/registered_countries")
def api_registered_countries():
    """获取所有已注册账号的出口国家代码及分布统计。"""
    countries = db.get_registered_countries()
    return {"ok": True, "countries": countries}


@app.get("/api/registered/{email}")
def api_registered_one(email: str):
    row = db.get_registered(email)
    if not row:
        raise HTTPException(404, "not found")
    return {"ok": True, "data": row}


@app.delete("/api/registered/{email}")
def api_delete_registered(email: str):
    ok = db.delete_registered(email)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


class BulkDeleteRegisteredReq(BaseModel):
    emails: Optional[list[str]] = Field(None, description="按 email 列表删；留空 + all=true 则删全部")
    all: bool = False


@app.post("/api/registered/bulk_delete")
def api_bulk_delete_registered(req: BulkDeleteRegisteredReq):
    if req.all:
        n = db.delete_all_registered()
        return {"ok": True, "deleted": n, "by": "all"}
    if req.emails:
        n = db.delete_registered_by_emails(req.emails)
        return {"ok": True, "deleted": n, "by": "emails"}
    raise HTTPException(400, "需要 emails 或 all=true")


@app.post("/api/registered/clean_invalid")
def api_clean_invalid_registered():
    """清理没有任何有效凭证（AT/ST/RT 全为空）的未完成废号。"""
    n = db.clean_empty_token_accounts()
    return {"ok": True, "deleted": n}


class RecoverOAuthReq(BaseModel):
    emails: Optional[list[str]] = Field(None, description="要找回/自愈的邮箱列表（留空则扫描全库）")


@app.post("/api/registered/recover_oauth")
def api_recover_oauth(req: RecoverOAuthReq):
    """一键扫描并找回/自愈历史 OAuth 授权凭证 (支持库内 RT 自愈与本地 exports 历史文件找回)。"""
    res = db.recover_oauth_credentials(req.emails)
    return {"ok": True, "data": res}


# ──────────────────────── 批量导出（文本） ────────────────────────
# ⚠️ 路由顺序：
#   - formats 是 4 段路径，不会被 3 段的 GET /api/registered/{email} 吃掉；
#   - export 是 POST，而 {email} 那两条是 GET / DELETE，也不冲突。
# 要加新格式只改 webui/export_formats.py，这里和前端都不用动。


@app.get("/api/registered/export/formats")
def api_export_formats():
    """导出格式清单，前端下拉菜单据此渲染。"""
    return {"ok": True, "formats": export_formats.list_formats()}


class ExportRegisteredReq(BaseModel):
    format: str = Field(..., description="格式 id，见 GET /api/registered/export/formats")
    emails: Optional[list[str]] = Field(None, description="要导出的 email 列表")
    all: bool = Field(False, description="true = 导出全部（跨页），忽略 emails")
    chunk_size: int = Field(0, ge=0, description="每卷条数，0 = 不分卷（单文件）")
    note: str = Field("", description="导出备注（留痕归档用）")
    delimiter: Optional[str] = Field("----", description="文本格式自定义分隔符，默认 ----")


@app.post("/api/registered/export")
def api_export_registered(req: ExportRegisteredReq):
    fmt = export_formats.get_format(req.format)
    if fmt is None:
        raise HTTPException(400, f"未知导出格式: {req.format}")

    if req.all:
        rows = db.list_registered_full(limit=100000)
    elif req.emails:
        rows = db.list_registered_by_emails(req.emails)
    else:
        raise HTTPException(400, "需要 emails 或 all=true")

    filename = fmt.filename
    mime = fmt.mime
    if req.format == "cpa_json":
        if len(rows) == 1 and rows[0].get("email"):
            filename = f"{rows[0]['email']}.json"
            mime = "application/json; charset=utf-8"
        else:
            filename = "cpa_auth_files.zip"
            mime = "application/zip"
    elif req.format == "session_json" and len(rows) == 1 and rows[0].get("email"):
        filename = f"{rows[0]['email']}_session.json"

    # 全格式导出留痕：无论导出何种格式（AT、账密2FA、CPA、Sub2API、Session等），均记录导出时间、格式与用户备注
    # 顶栏「导出状态」筛选器及表格徽章据此展示。留痕失败不影响导出本身。
    try:
        db.mark_exported([(r.get("email") or "") for r in rows], fmt_id=fmt.id, fmt_label=fmt.label, note=req.note)
    except Exception as e:
        logger.warning(f"导出留痕记录异常（导出不受影响）: {e}")

    delim = req.delimiter if req.delimiter is not None else "----"

    base = {
        "ok": True,
        "count": len(rows),
        "filename": filename,
        "label": fmt.label,
        "format": fmt.id,
        "note": req.note,
        "delimiter": delim,
        "mode": fmt.mode,
        "mime": mime,
        # 这一批导出的 email 原样带回去 —— 前端「下载并删除」照着它删，删得准。
        "emails": [(r.get("email") or "") for r in rows],
    }

    if req.chunk_size > 0:
        # 分卷导出：每 chunk_size 条一个文件，全部打进一个 zip 一次下载。
        # 覆盖 mode/filename/mime —— 前端照普通 download 分支存盘即可。
        blob = export_formats.render_chunked(rows, fmt, req.chunk_size, delimiter=delim)
        stem = filename.rsplit(".", 1)[0]
        return {
            **base,
            "mode": "download",
            "mime": "application/zip",
            "filename": f"{stem}_分卷{export_formats.count_chunks(len(rows), req.chunk_size)}.zip",
            "parts": export_formats.count_chunks(len(rows), req.chunk_size),
            "b64": base64.b64encode(blob).decode("ascii"),
            "size": len(blob),
        }

    if fmt.mode == "download":
        # 二进制（zip / json 文件）走 base64，前端解出来直接存盘，不弹预览
        blob = export_formats.render_bytes(rows, fmt)
        return {**base, "b64": base64.b64encode(blob).decode("ascii"), "size": len(blob)}

    return {**base, "text": export_formats.render_text(rows, fmt, delimiter=delim)}


class UpdateExportNoteReq(BaseModel):
    emails: Optional[list[str]] = Field(None, description="目标邮箱列表")
    email: Optional[str] = Field(None, description="单个目标邮箱")
    note: str = Field("", description="新的导出备注内容")


@app.post("/api/registered/export_note")
def api_update_export_note(req: UpdateExportNoteReq):
    """更新指定账号的导出备注，方便随时标记或修改批次归属。"""
    targets = []
    if req.emails:
        targets.extend(req.emails)
    if req.email and req.email not in targets:
        targets.append(req.email)
    if not targets:
        raise HTTPException(400, "请提供至少一个邮箱")
    updated = db.update_export_note(targets, req.note)
    return {"ok": True, "updated": updated, "note": req.note}


class ConvertSessionReq(BaseModel):
    session_json: Optional[str] = Field("", description="待转换的 ChatGPT Web Session JSON / 文本 / 数组")
    email: Optional[str] = Field(None, description="可选：指定数据库中某个邮箱")


@app.post("/api/convert/session_to_sub2")
def api_convert_session_to_sub2(req: ConvertSessionReq):
    """将输入的 Session 数据或指定数据库账号的 Session 实时转换为 Sub2API 导入 JSON。"""
    try:
        raw_data = None
        if req.session_json and req.session_json.strip():
            txt = req.session_json.strip()
            if txt.startswith("{") or txt.startswith("["):
                raw_data = json.loads(txt)
            else:
                # 支持多行 JSONL 结构
                lines = [json.loads(line) for line in txt.splitlines() if line.strip() and (line.strip().startswith("{") or line.strip().startswith("["))]
                raw_data = lines if lines else txt
        elif req.email:
            row = db.get_registered(req.email)
            if not row:
                raise HTTPException(404, f"未找到账号 {req.email}")
            raw_data = export_formats.get_or_build_session_data(row)
        else:
            raise HTTPException(400, "请提供 session_json 内容或 email")

        sub2_res = export_formats.convert_session_payload_to_sub2api(raw_data)
        return {"ok": True, "data": sub2_res, "count": len(sub2_res.get("accounts") or [])}
    except Exception as e:
        raise HTTPException(400, f"转换 Sub2API 格式失败: {e}")


@app.post("/api/convert/session_to_cpa")
def api_convert_session_to_cpa(req: ConvertSessionReq):
    """将输入的 Session 数据或指定数据库账号的 Session 实时转换为 CPA JSON 结构。"""
    try:
        raw_data = None
        if req.session_json and req.session_json.strip():
            txt = req.session_json.strip()
            if txt.startswith("{") or txt.startswith("["):
                raw_data = json.loads(txt)
            else:
                lines = [json.loads(line) for line in txt.splitlines() if line.strip() and (line.strip().startswith("{") or line.strip().startswith("["))]
                raw_data = lines if lines else txt
        elif req.email:
            row = db.get_registered(req.email)
            if not row:
                raise HTTPException(404, f"未找到账号 {req.email}")
            raw_data = export_formats.get_or_build_session_data(row)
        else:
            raise HTTPException(400, "请提供 session_json 内容或 email")

        cpa_res = export_formats.convert_session_payload_to_cpa(raw_data)
        return {"ok": True, "data": cpa_res, "count": len(cpa_res)}
    except Exception as e:
        raise HTTPException(400, f"转换 CPA 格式失败: {e}")


# ──────────────────────── 邮箱来源配置 ────────────────────────


@app.get("/api/mail/providers")
def api_mail_providers(pooled_only: bool = False):
    """列出所有已注册的邮箱 provider 及其能力 / 配置项声明。

    前端据此渲染「邮箱来源」单选和对应的动态表单 ——
    以后加邮箱，前端一行都不用改。

        pooled_only=true  只返回能导入号池的（导入页用）
    """
    return {
        "ok": True,
        "providers": list_pooled_providers() if pooled_only else list_providers(),
        "current": db.get_setting("mail_source", "outlook"),
    }


@app.get("/api/settings/mail")
def api_get_mail_config():
    return {"ok": True, "config": db.get_mail_config()}


class SaveMailConfigReq(BaseModel):
    """字段不再写死。

    mail_source 之外的配置项由各 provider 的 config_fields 声明，
    前端原样回传，db.save_mail_config 按声明逐项存 ——
    加 provider 时这个模型不用动。
    """

    model_config = {"extra": "allow"}

    mail_source: Optional[str] = None


@app.post("/api/settings/mail")
def api_save_mail_config(req: SaveMailConfigReq):
    try:
        db.save_mail_config(req.model_dump(exclude_none=True))
    except MailProviderError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "config": db.get_mail_config()}


@app.post("/api/settings/mail/test")
def api_test_mail():
    """测试当前邮箱来源的连通性，具体怎么测由 provider 的 self_test() 决定。

    原来这里写死了 CF 的 api_url/domain/token 三个字段，
    换成让 provider 自检 —— 加邮箱不用回来改这个路由。
    """
    mail_source = db.get_setting("mail_source", "outlook")
    try:
        provider_cls = get_provider_class(mail_source)
    except MailProviderError as e:
        raise HTTPException(400, str(e))

    # 池化 provider 的连通性绑定在具体某个号上，没号可测 ——
    # 它的"测试"就是导入时的格式校验 + 跑一次注册。
    if provider_cls.pooled:
        raise HTTPException(
            400,
            f"{provider_cls.display_name} 是号池类型，不需要单独测试；"
            f"导入时会校验格式",
        )

    try:
        provider = create_mail_provider(mail_source, db.get_mail_settings())
    except MailProviderError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"构造 {provider_cls.display_name} 失败: {e}")

    try:
        result = provider.self_test()
    except Exception as e:
        raise HTTPException(500, f"连接失败: {e}")
    if not result.get("ok"):
        raise HTTPException(500, result.get("message") or "连接失败")
    return {
        "ok": True,
        "message": result.get("message", "连接成功"),
        "domains": result.get("domains", []),
        "email": result.get("email", ""),
    }


class FetchCfDomainsReq(BaseModel):
    api_url: Optional[str] = None
    admin_token: Optional[str] = None
    site_password: Optional[str] = None


@app.post("/api/mail/cf/domains")
def api_fetch_cf_domains(req: FetchCfDomainsReq):
    """从 Cloudflare Worker /open_api/settings 探测并获取可用收信域名列表。"""
    from mail_providers.cf_temp import cf_list_domains
    settings = db.get_mail_settings()
    api_url = (req.api_url or settings.get("cf_api_url") or "").strip()
    admin_token = (req.admin_token or settings.get("cf_admin_token") or "").strip()
    if admin_token == "***":
        admin_token = settings.get("cf_admin_token") or ""
    site_pw = (req.site_password or settings.get("cf_site_password") or "").strip()
    if not api_url:
        raise HTTPException(400, "请先填写 Cloudflare Worker API 地址")
    domains = cf_list_domains(api_url, admin_token=admin_token, site_password=site_pw)
    return {"ok": True, "domains": domains}


class FetchRemailProjectsReq(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@app.post("/api/mail/remail/projects")
def api_fetch_remail_projects(req: FetchRemailProjectsReq):
    """从 Remail 开放平台拉取当前钱包余额及所有可用项目和价格明细。"""
    from mail_providers.remail_icloud import fetch_remail_projects_and_wallet
    settings = db.get_mail_settings()
    api_key = (req.api_key or settings.get("remail_api_key") or "").strip()
    if api_key == "***":
        api_key = settings.get("remail_api_key") or ""
    base_url = (req.base_url or settings.get("remail_base_url") or "").strip()
    if not api_key:
        api_key = "rk-a18f1eed-cc59-4eaf-9c5f-ac4d711c758d"
    try:
        data = fetch_remail_projects_and_wallet(api_key=api_key, base_url=base_url)
        return data
    except Exception as e:
        raise HTTPException(400, f"拉取 Remail 项目与价格失败: {e}")


@app.get("/api/mail/remail/recycle_pool")
def api_get_remail_recycle_pool():
    """获取 Remail 暂存复用池中的有效未用邮箱数量与列表。"""
    return {
        "ok": True,
        "count": db.count_remail_recycled(),
        "items": db.list_remail_recycled(),
    }


# ──────────────────────── SMS 接码配置 ────────────────────────


@app.get("/api/sms/providers")
def api_sms_providers():
    """已注册接码渠道清单（能力声明 + 配置项），前端据此动态渲染。"""
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_providers import list_providers
    return {"ok": True, "providers": list_providers()}


@app.get("/api/settings/sms")
def api_get_sms_config():
    return {"ok": True, "config": db.get_sms_config()}


class SaveSmsConfigReq(BaseModel):
    sms_enabled: Optional[str] = None              # "0" / "1"
    sms_provider: Optional[str] = None             # 已注册接码渠道 kind
    sms_api_key: Optional[str] = None              # 传 '***' 表示不修改 (CDK模式下即为卡密)
    sms_cdk_url: Optional[str] = None              # 平台地址 (默认 https://ndk.cc.cd)
    sms_country: Optional[str] = None              # ID 或国家代码（'52' / 'th'）
    sms_service: Optional[str] = None              # OpenAI = 'dr'
    sms_max_price: Optional[str] = None
    sms_provider_ids: Optional[str] = None          # 指定供应商ID(如 3237)
    sms_except_provider_ids: Optional[str] = None   # 排除供应商ID(如 3327)
    sms_reuse_phone: Optional[str] = None
    sms_phone_success_max: Optional[str] = None
    sms_auto_country: Optional[str] = None
    sms_strict_whitelist: Optional[str] = None
    sms_allowed_countries: Optional[str] = None    # 逗号分隔的 ID 列表，自动选号时只从这里挑
    sms_auto_min_stock: Optional[str] = None
    sms_auto_max_price: Optional[str] = None
    sms_max_phone_attempts: Optional[str] = None   # 空 = 用 provider 默认；>0 = 自定义
    sms_per_phone_timeout: Optional[str] = None    # 单号等待秒数（默认 80）


@app.post("/api/settings/sms")
def api_save_sms_config(req: SaveSmsConfigReq):
    db.save_sms_config(req.model_dump(exclude_none=True))
    return {"ok": True, "config": db.get_sms_config()}


@app.post("/api/settings/sms/test")
def api_test_sms():
    """测试 SMS provider 连通性：查询余额或卡密兑换状态。"""
    cfg = db.get_sms_internal_config()
    p_key = (cfg.get("sms_provider") or "").strip().lower()
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_providers import create_sms_provider, uses_cdk_pool
    is_cdk = uses_cdk_pool(p_key)
    if not cfg.get("sms_api_key") and not is_cdk:
        raise HTTPException(400, "未配置 sms_api_key")
    try:
        provider = create_sms_provider(cfg["sms_provider"], cfg)
        if hasattr(provider, "get_detail_status"):
            detail = provider.get_detail_status()
            return {
                "ok": True,
                "provider": cfg["sms_provider"],
                "balance": provider.get_balance(),
                "message": detail.get("message", f"连接成功，剩余换号: {provider.get_balance()}次"),
                "data": detail,
            }
        balance = provider.get_balance()
        return {
            "ok": True,
            "provider": cfg["sms_provider"],
            "balance": balance,
            "message": f"连接成功，余额: {balance}",
        }
    except Exception as e:
        raise HTTPException(500, f"连接失败: {e}")


# ──────────────────────── CDK 号池工作台 API ────────────────────────


class ImportSmsCdkReq(BaseModel):
    cdks: str                                      # 多行卡密文本 (换行/逗号/分号分隔)
    max_use_count: Optional[int] = 0               # 0: 多次卡/长期不限次; 1: 单次卡; N: 限N次
    notes: Optional[str] = ""


class UpdateSmsCdkReq(BaseModel):
    status: Optional[str] = None                   # available / exhausted / expired
    max_use_count: Optional[int] = None
    notes: Optional[str] = None


class ClearSmsCdkReq(BaseModel):
    status: str = "all"                            # all / exhausted / expired / available


@app.get("/api/settings/sms/cdk_pool")
def api_get_sms_cdk_pool(
    status: str = "all",
    search: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """获取 CDK 卡密号池列表。"""
    res = db.list_sms_cdk_pool(status=status, search=search, limit=limit, offset=offset)
    return {"ok": True, **res}


@app.get("/api/settings/sms/cdk_pool/stats")
def api_get_sms_cdk_pool_stats():
    """获取 CDK 号池全景统计数据。"""
    stats = db.get_sms_cdk_pool_stats()
    return {"ok": True, "stats": stats}


@app.post("/api/settings/sms/cdk_pool/import")
def api_import_sms_cdks(req: ImportSmsCdkReq):
    """批量多行导入 CDK 卡密到号池。"""
    res = db.import_sms_cdks(
        cdk_inputs=req.cdks,
        max_use_count=req.max_use_count or 0,
        notes=req.notes or "",
    )
    return {"ok": True, "result": res}


@app.post("/api/settings/sms/cdk_pool/{cdk_id}/update")
def api_update_sms_cdk(cdk_id: int, req: UpdateSmsCdkReq):
    """更新单个卡密的使用上限、状态或备注。"""
    ok = db.update_sms_cdk_item(
        cdk_id=cdk_id,
        status=req.status,
        max_use_count=req.max_use_count,
        notes=req.notes,
    )
    if not ok:
        raise HTTPException(404, "卡密不存在或未做任何更改")
    return {"ok": True}


@app.delete("/api/settings/sms/cdk_pool/{cdk_id}")
def api_delete_sms_cdk(cdk_id: int):
    """删除指定 ID 的卡密。"""
    ok = db.delete_sms_cdk(cdk_id)
    return {"ok": ok}


@app.post("/api/settings/sms/cdk_pool/clear")
def api_clear_sms_cdk_pool(req: ClearSmsCdkReq):
    """按状态清空卡密号池。"""
    count = db.clear_sms_cdk_pool(status=req.status)
    return {"ok": True, "cleared": count}


@app.get("/api/settings/sms/countries")
def api_sms_top_countries():
    """查询当前接码平台的国家排名（价格 + 库存）。"""
    cfg = db.get_sms_internal_config()
    p_key = (cfg.get("sms_provider") or "").strip().lower()
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_providers import (
        OPENAI_SMS_COUNTRIES,
        SMS_COUNTRY_NAMES_CN,
        create_sms_provider,
        get_provider_class,
        uses_cdk_pool,
    )
    if uses_cdk_pool(p_key):
        return {"ok": True, "countries": [], "openai_sms_safe": []}
    try:
        if not get_provider_class(p_key).uses_country:
            return {"ok": True, "countries": [], "openai_sms_safe": []}
    except Exception:
        pass
    if not cfg.get("sms_api_key"):
        raise HTTPException(400, "未配置 sms_api_key")
    try:
        provider = create_sms_provider(cfg["sms_provider"], cfg)
        rows = provider.get_top_countries(service=cfg.get("sms_service") or "dr")
        for r in rows:
            cid = str(r.get("country"))
            r["openai_sms_safe"] = cid in OPENAI_SMS_COUNTRIES
            r["name_cn"] = SMS_COUNTRY_NAMES_CN.get(cid, "未知")
        return {"ok": True, "countries": rows[:30], "openai_sms_safe": list(OPENAI_SMS_COUNTRIES)}
    except Exception as e:
        raise HTTPException(500, f"查询失败: {e}")


@app.get("/api/settings/sms/all_countries")
def api_sms_all_countries(provider: str = ""):
    """返回平台全量国家（静态字典 + 实时库存）。有货的排前面，没货的也显示，方便对照网页点选。"""
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_providers import SMS_COUNTRY_NAMES_CN, OPENAI_SMS_COUNTRIES, create_sms_provider

    cfg = db.get_sms_internal_config()
    if provider:
        cfg["sms_provider"] = provider

    live_map: dict = {}
    if cfg.get("sms_api_key"):
        try:
            p = create_sms_provider(cfg["sms_provider"], cfg)
            if hasattr(p, "get_top_countries"):
                for r in p.get_top_countries(service=cfg.get("sms_service") or "dr") or []:
                    cid = str(r.get("country") or "").strip()
                    if cid:
                        live_map[cid] = r
        except Exception as e:
            logger.warning(f"拉取接码国家实时库存失败: {e}")
            live_map = {}

    seen = set()
    countries = []
    for cid, name in SMS_COUNTRY_NAMES_CN.items():
        live = live_map.get(cid) or {}
        seen.add(cid)
        countries.append({
            "id": cid,
            "name_cn": name,
            "openai_sms_safe": cid in OPENAI_SMS_COUNTRIES,
            "price": live.get("price"),
            "count": live.get("count"),
        })
    for cid, live in live_map.items():
        if cid in seen:
            continue
        countries.append({
            "id": cid,
            "name_cn": SMS_COUNTRY_NAMES_CN.get(cid, f"国家{cid}"),
            "openai_sms_safe": cid in OPENAI_SMS_COUNTRIES,
            "price": live.get("price"),
            "count": live.get("count"),
        })

    def _sort_key(c):
        cid = str(c.get("id") or "")
        try:
            nid = int(cid)
        except (TypeError, ValueError):
            nid = 9999
        count = 0
        try:
            count = int(c.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if cid == "52":
            return (0, 0, nid)
        if count > 0:
            return (1, -count, nid)
        return (2, 0, nid)

    countries.sort(key=_sort_key)
    return {
        "ok": True,
        "countries": countries,
        "openai_sms_safe": list(OPENAI_SMS_COUNTRIES),
        "source": "merged" if live_map else "static",
    }


@app.get("/api/settings/sms/price_tiers")
def api_sms_country_price_tiers(country: str = "6", service: str = "dr", provider: str = ""):
    """实时查询指定国家和业务的所有可用金额档位和库存（如 0.008 $ (1.2万件)）。"""
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_providers import create_sms_provider, get_provider_class

    cfg = db.get_sms_internal_config()
    p_key = (provider or cfg.get("sms_provider") or "smsbower").strip().lower()
    try:
        cls = get_provider_class(p_key)
    except Exception:
        return {"ok": True, "tiers": []}
    if not cls.uses_price_tiers or not cfg.get("sms_api_key"):
        return {"ok": True, "tiers": []}

    try:
        p = create_sms_provider(p_key, cfg)
        if hasattr(p, "get_country_price_tiers"):
            tiers = p.get_country_price_tiers(country=country, service=service)
            return {"ok": True, "tiers": tiers}
    except Exception as e:
        logger.warning(f"查询金额档位失败: {e}")
    return {"ok": True, "tiers": []}


# ──────────────────────── 自动导出 (CPA / SUB2API) ────────────────────────


class SaveExportConfigReq(BaseModel):
    # CPA
    cpa_enabled: Optional[str] = None       # "0" / "1"
    cpa_url: Optional[str] = None
    cpa_mgmt_key: Optional[str] = None      # 传 '***' 表示不修改
    cpa_timeout: Optional[str] = None
    # SUB2API
    sub2api_enabled: Optional[str] = None
    sub2api_url: Optional[str] = None
    sub2api_api_key: Optional[str] = None   # '***' 不修改
    sub2api_group_ids: Optional[str] = None  # 逗号分隔，例 "2" 或 "1,2,3"
    sub2api_timeout: Optional[str] = None


@app.get("/api/settings/export")
def api_get_export_config():
    return {"ok": True, "config": db.get_export_config()}


@app.post("/api/settings/export")
def api_save_export_config(req: SaveExportConfigReq):
    db.save_export_config(req.model_dump(exclude_none=True))
    return {"ok": True, "config": db.get_export_config()}


class SavePowSlotsReq(BaseModel):
    slots: int = Field(..., ge=1, le=16, description="PoW 算力槽位数（1-16）")


@app.get("/api/settings/pow_slots")
def api_get_pow_slots():
    """当前 PoW 算力槽位（sentinel 并发碰撞的 node 进程数上限）。"""
    import sentinel_quickjs
    return {"ok": True, "slots": sentinel_quickjs.get_pow_slots()}


@app.post("/api/settings/pow_slots")
def api_save_pow_slots(req: SavePowSlotsReq):
    """保存并立即生效 PoW 算力槽位（存 settings 表，重启后仍生效）。"""
    import sentinel_quickjs
    applied = sentinel_quickjs.set_pow_slots(req.slots)
    db.set_setting("sentinel_pow_slots", str(applied))
    return {"ok": True, "slots": applied}


class ProxyBlacklistReq(BaseModel):
    proxy: str = Field(..., description="代理串或模板（与代理池条目一致即可，内部归一化）")
    country: str = Field("", description="国家码=只拉黑该出口组合；空/*=整模板拉黑")
    on: bool = Field(..., description="true=拉黑 false=取消拉黑")
    reason: str = Field("", description="手动拉黑原因（可选）")


@app.get("/api/proxy_health")
def api_proxy_health():
    """代理健康度清单：每个代理注册的号数 / 验死数 / 是否拉黑。"""
    return {"ok": True, "items": db.list_proxy_health()}


@app.get("/api/proxy_health/overview")
def api_proxy_health_overview():
    """健康度总览面板：汇总统计 + 问题代理榜 + 最近死亡号动态。"""
    return {"ok": True, **db.proxy_health_overview()}


@app.post("/api/proxy_health/blacklist")
def api_proxy_blacklist(req: ProxyBlacklistReq):
    """手动拉黑 / 取消拉黑。整模板拉黑后 auto_loop 立即跳过该代理条目；
    单国家拉黑后注册选国家时自动换国。"""
    db.set_proxy_blacklist(req.proxy, req.country, req.on, req.reason)
    return {"ok": True, "items": db.list_proxy_health()}


class TestExportReq(BaseModel):
    target: str = Field(..., description="cpa 或 sub2api")


@app.post("/api/settings/export/test")
def api_test_export(req: TestExportReq):
    """测试 CPA / SUB2API 连通性。"""
    from . import exporter
    cfg = db.get_export_internal_config()
    target = (req.target or "").strip().lower()
    try:
        if target == "cpa":
            return exporter.test_cpa(cfg["cpa"])
        if target == "sub2api":
            return exporter.test_sub2api(cfg["sub2api"])
        raise HTTPException(400, f"未知 target: {target}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"测试失败: {e}")


class ManualExportReq(BaseModel):
    email: str = Field(..., description="要导出的已注册账号邮箱")
    targets: list[str] = Field(default_factory=lambda: ["cpa", "sub2api"],
                                description="选择导出目标：cpa / sub2api")


@app.post("/api/registered/export_to_panel")
def api_manual_export_to_panel(req: ManualExportReq):
    """对一个已注册账号手动触发到面板的导出。

    targets 里选 cpa / sub2api 之一或全部。即使总开关未启用，本接口也会执行
    （只要 URL/密钥 等基础配置已填）。
    """
    from . import exporter
    cred = db.get_registered(req.email)
    if not cred:
        raise HTTPException(404, f"未找到已注册账号: {req.email}")

    cfg = db.get_export_internal_config()
    out = {"email": req.email, "cpa": None, "sub2api": None}
    targets = {t.strip().lower() for t in (req.targets or []) if t}

    if "cpa" in targets:
        cpa_cfg = dict(cfg["cpa"])
        cpa_cfg["enabled"] = True  # 手动触发：强制启用
        try:
            out["cpa"] = exporter.export_to_cpa(cred, cpa_cfg)
        except Exception as e:
            out["cpa"] = {"ok": False, "error": str(e)}
    if "sub2api" in targets:
        sub2api_cfg = dict(cfg["sub2api"])
        sub2api_cfg["enabled"] = True
        try:
            out["sub2api"] = exporter.export_to_sub2api(cred, sub2api_cfg)
        except Exception as e:
            out["sub2api"] = {"ok": False, "error": str(e)}

    return {"ok": True, **out}


class UpdateCredReq(BaseModel):
    email: str = Field(..., description="要修改的已注册账号邮箱")
    # None = 该字段不动；空串 = 主动清空。前端不填的字段就别传。
    password: Optional[str] = Field(None, description="新密码，None=不修改")
    totp_secret: Optional[str] = Field(None, description="新 TOTP secret，None=不修改")


@app.post("/api/registered/update_credentials")
def api_update_credentials(req: UpdateCredReq):
    """手动修正已注册账号的密码 / TOTP secret。

    ⚠️ 只改本地库，不会同步到 OpenAI。用途是把外部已知凭证补进来或修正记录。

    改完的值会被登录流程直接用上（registrar 的 account_callback 走
    db.get_registered，不区分数据来源），所以 totp_secret 必须过 base32
    校验 —— 脏值存进去要等真登录时才炸，那时根本看不出是手填填错的。
    """
    email = (req.email or "").strip().lower()
    if not email:
        raise HTTPException(400, "email 不能为空")
    if req.password is None and req.totp_secret is None:
        raise HTTPException(400, "没有要修改的字段")
    try:
        ok = db.update_registered_manual(
            email, password=req.password, totp_secret=req.totp_secret
        )
    except ValueError as e:
        # 校验失败：把具体原因带给前端，别让用户猜哪里填错了
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, f"未找到已注册账号: {email}")

    changed = [n for n, v in (("密码", req.password), ("TOTP secret", req.totp_secret))
               if v is not None]
    logger.info(f"[registered] 手动修改凭证 email={email} 字段={'+'.join(changed)}")
    return {"ok": True, "email": email, "changed": changed}


# ──────────────────────── 2FA TOTP 动态码 & 邮箱 OTP 抓取 & 补密补2FA ────────────────────────


@app.get("/api/registered/{email}/totp")
def api_get_account_totp(email: str):
    """获取指定账号的当前实时 2FA (TOTP) 动态验证码及倒计时。"""
    from .two_factor import totp_now, hotp
    row = db.get_registered(email)
    if not row:
        raise HTTPException(404, "未找到该账号")
    secret = (row.get("totp_secret") or "").strip()
    if not secret:
        raise HTTPException(400, "该账号未绑定 2FA (无 TOTP Secret)")

    now_ts = int(time.time())
    period = 30
    remaining = period - (now_ts % period)
    curr_counter = now_ts // period
    try:
        code = totp_now(secret)
        next_code = hotp(secret, curr_counter + 1)
    except Exception as e:
        raise HTTPException(400, f"计算 TOTP 失败: {e}")

    return {
        "ok": True,
        "email": row["email"],
        "totp_secret": secret,
        "code": code,
        "next_code": next_code,
        "remaining_seconds": remaining,
        "period": period,
    }


class FetchMailOtpReq(BaseModel):
    timeout: int = 15
    # 可选：直接传入 4 段式凭证或单项凭证进行即时查询/自动修复号池
    raw_line: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    refresh_token: Optional[str] = None


@app.post("/api/registered/{email}/fetch_otp")
def api_fetch_mail_otp(email: str, req: Optional[FetchMailOtpReq] = None):
    """从邮箱渠道实时抓取/检索该邮箱最新的邮件和 6 位 OTP 验证码。"""
    from mail_providers import create_mail_provider, extract_otp, parse_import_line
    email_clean = (email or "").strip().lower()
    if not email_clean:
        raise HTTPException(400, "email 不能为空")

    settings = db.get_mail_settings()
    account_row = db.get_account(email_clean)
    registered_row = db.get_registered(email_clean) or {}

    # 若前端传入了补充凭证，即时写入号池并装载
    if req and (req.raw_line or req.refresh_token or req.password):
        try:
            if req.raw_line and "----" in req.raw_line:
                parsed = parse_import_line(req.raw_line)
                if parsed and parsed.get("email") == email_clean:
                    account_row = parsed
                    db.import_accounts(req.raw_line, kind=parsed.get("kind", "outlook"))
            else:
                account_row = account_row or {"email": email_clean, "kind": "outlook"}
                if req.password:
                    account_row["password"] = req.password
                if req.client_id:
                    account_row["client_id"] = req.client_id
                if req.refresh_token:
                    account_row["refresh_token"] = req.refresh_token
        except Exception as e:
            logger.warning(f"[fetch_otp] 动态解析补充凭证异常: {e}")

    # 若号池中未找到，尝试从已注册账号的 extra.mail_oauth 或 remail_recycle_pool 中恢复
    if not account_row and registered_row.get("extra"):
        saved_oauth = registered_row["extra"].get("mail_oauth")
        if isinstance(saved_oauth, dict):
            if saved_oauth.get("kind") == "remail" or saved_oauth.get("service_token") or saved_oauth.get("pickup_url"):
                account_row = {
                    "email": email_clean,
                    "service_token": saved_oauth.get("service_token", ""),
                    "pickup_url": saved_oauth.get("pickup_url", ""),
                    "order_no": saved_oauth.get("order_no", ""),
                    "project_id": saved_oauth.get("project_id", 2),
                    "email_suffix": saved_oauth.get("email_suffix", "outlook.com"),
                    "service_mode": saved_oauth.get("service_mode", "purchase"),
                    "kind": "remail",
                }
            elif saved_oauth.get("refresh_token") or saved_oauth.get("password"):
                account_row = {
                    "email": email_clean,
                    "password": saved_oauth.get("password", ""),
                    "client_id": saved_oauth.get("client_id", ""),
                    "refresh_token": saved_oauth.get("refresh_token", ""),
                    "kind": saved_oauth.get("kind", "outlook"),
                }

    # 兜底：检查 remail_recycle_pool
    if not account_row:
        try:
            cur_pool = db._conn().execute(
                "SELECT service_token, order_no, project_id, email_suffix, service_mode FROM remail_recycle_pool WHERE email=? AND service_token IS NOT NULL AND service_token != '' ORDER BY id DESC LIMIT 1",
                (email_clean,),
            ).fetchone()
            if cur_pool and cur_pool["service_token"]:
                account_row = {
                    "email": email_clean,
                    "service_token": cur_pool["service_token"],
                    "order_no": cur_pool.get("order_no", ""),
                    "project_id": cur_pool.get("project_id", 2),
                    "email_suffix": cur_pool.get("email_suffix", "outlook.com"),
                    "service_mode": cur_pool.get("service_mode", "purchase"),
                    "kind": "remail",
                }
        except Exception:
            pass

    # 确定邮箱类型
    kind = (
        (account_row.get("kind") if account_row else None)
        or registered_row.get("kind")
        or ""
    ).strip().lower()

    if not kind or kind not in ("remail", "outlook", "cf_temp", "icloud_relay"):
        if any(dom in email_clean for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
            kind = "outlook"
        elif any(dom in email_clean for dom in ("@icloud.", "@me.", "@mac.")):
            def_source = (db.get_setting("mail_source", "") or "").strip().lower()
            kind = "remail" if def_source == "remail" else "icloud_relay"
        else:
            kind = (db.get_setting("mail_source", "") or "cf_temp").strip().lower()

    # 若是 Outlook 邮箱，但无任何凭证，绝对不能静默降级为 cf_temp（会导致前端标签显示 cf_temp 且永远查不到邮件）
    if kind == "outlook" and (not account_row or (not account_row.get("refresh_token") and not account_row.get("password"))):
        return {
            "ok": False,
            "email": email_clean,
            "provider": "outlook",
            "otp": None,
            "found": False,
            "messages": [],
            "error": "未在号池或记录中找到该 Outlook 邮箱的微软 OAuth 凭证(client_id/refresh_token)或密码。请在下方补充 4 段式凭证或前往「号池管理」重新导入。",
        }

    try:
        provider = create_mail_provider(kind, settings, account_row)
    except Exception as e:
        if kind in ("outlook", "remail"):
            return {
                "ok": False,
                "email": email_clean,
                "provider": kind,
                "otp": None,
                "found": False,
                "messages": [],
                "error": f"初始化 {kind} 邮箱 Provider 异常: {e}",
            }
        provider = create_mail_provider("cf_temp", settings)

    recent_mails = []
    otp_code = None
    t0 = time.time()
    used_protocol = "remail_pickup" if kind == "remail" else ("graph" if getattr(account_row, "get", lambda k: "")("client_id") and getattr(account_row, "get", lambda k: "")("refresh_token") else "imap")

    try:
        if hasattr(provider, "_get_mails"):
            if getattr(provider, "client_id", None) and getattr(provider, "refresh_token", None):
                used_protocol = "graph"
            raw_mails = provider._get_mails(email_clean)
            for m in raw_mails:
                raw_text = str(m.get("content") or m.get("raw") or m.get("text") or m.get("html") or "")
                subject_text = str(m.get("subject") or "(无主题)")
                from_text = str(m.get("from") or m.get("sender") or m.get("source") or "OpenAI")
                # 优先使用 Remail / Provider 已解析好的 otp 字段或 verificationCode，兜底使用全局提取
                c = (
                    str(m.get("otp") or "").strip()
                    or str(m.get("verificationCode") or m.get("code") or "").strip()
                    or extract_otp(raw_text)
                    or extract_otp(subject_text)
                )
                if c and not (c.isdigit() and len(c) == 6):
                    c = extract_otp(c) or ""

                # 优先提取来自 OpenAI / ChatGPT 的最新 6 位验证码
                if c and not otp_code and c.isdigit() and len(c) == 6:
                    otp_code = c

                clean_snippet = re.sub(r"<[^>]+>", " ", raw_text)
                clean_snippet = re.sub(r"\s+", " ", clean_snippet).strip()
                recent_mails.append({
                    "id": str(m.get("id") or ""),
                    "subject": subject_text,
                    "from": from_text,
                    "date": str(m.get("date") or m.get("created_at") or m.get("date_str") or ""),
                    "otp": c if (c and c.isdigit() and len(c) == 6) else "",
                    "snippet": clean_snippet[:350],
                    "content": raw_text,
                })
        elif hasattr(provider, "_load"):
            raw_mails = provider._load()
            for m in raw_mails:
                body_text = str(m.get("body") or m.get("raw") or "")
                subject_text = str(m.get("subject") or "(无主题)")
                from_text = str(m.get("sender") or "OpenAI")
                c = m.get("otp") or extract_otp(body_text) or extract_otp(subject_text)
                if c and not otp_code:
                    otp_code = c
                clean_snippet = re.sub(r"<[^>]+>", " ", body_text)
                clean_snippet = re.sub(r"\s+", " ", clean_snippet).strip()
                recent_mails.append({
                    "id": str(m.get("uid") or ""),
                    "subject": subject_text,
                    "from": from_text,
                    "date": str(m.get("date_str") or m.get("date") or ""),
                    "otp": c or "",
                    "snippet": clean_snippet[:350],
                    "content": body_text,
                })
    except Exception as e:
        logger.warning(f"[fetch_otp] 拉取邮件列表异常: {e}")

    elapsed_s = round(time.time() - t0, 2)

    return {
        "ok": True,
        "email": email_clean,
        "provider": provider.kind,
        "protocol": used_protocol,
        "otp": otp_code,
        "found": bool(otp_code),
        "messages": recent_mails,
        "count": len(recent_mails),
        "elapsed_s": elapsed_s,
    }


class Bind2FAReq(BaseModel):
    proxy: Optional[str] = None


@app.post("/api/registered/{email}/bind_2fa")
def api_bind_account_2fa(email: str, req: Optional[Bind2FAReq] = None):
    """为指定账号补绑 2FA。打 OpenAI 官方 API 激活 TOTP 并落库。支持 Token 自适应刷新。"""
    from .two_factor import bind_totp_2fa_adaptive, totp_now
    email_clean = (email or "").strip().lower()
    row = db.get_registered(email_clean)
    if not row:
        raise HTTPException(404, "未找到该账号")

    if (row.get("totp_secret") or "").strip():
        secret = row["totp_secret"].strip()
        return {
            "ok": True,
            "already_bound": True,
            "totp_secret": secret,
            "code": totp_now(secret),
            "message": "该账号已在数据库中登记 2FA",
        }

    proxy_str = (req.proxy if req else "") or db.get_setting("proxy", "")
    try:
        res = bind_totp_2fa_adaptive(row, proxy=proxy_str)
    except Exception as e:
        raise HTTPException(500, f"OpenAI 官方 2FA 绑定失败: {e}")

    secret = res.get("secret", "")
    if not secret:
        raise HTTPException(500, res.get("message") or "绑定 2FA 失败")

    return {
        "ok": True,
        "email": email_clean,
        "totp_secret": secret,
        "code": totp_now(secret),
        "mfa_enabled": res.get("mfa_enabled", True),
        "message": "OpenAI 官方 2FA 绑定成功 (mfa_enabled: true) 并已持久化落库",
    }


class SetPasswordReq(BaseModel):
    password: Optional[str] = None
    official_reset: Optional[bool] = True
    proxy: Optional[str] = None


@app.post("/api/registered/{email}/set_password")
def api_set_account_password(email: str, req: Optional[SetPasswordReq] = None):
    """为指定账号设置/补设密码。支持官方全自动重置设密 (默认) 与本地快速修改。"""
    from .two_factor import generate_random_password
    from .official_password import official_set_account_password

    email_clean = (email or "").strip().lower()
    row = db.get_registered(email_clean)
    if not row:
        raise HTTPException(404, "未找到该账号")

    new_pw = (req.password if req and req.password else "").strip()
    if not new_pw:
        new_pw = generate_random_password(16)

    official = True if req is None or req.official_reset is None else bool(req.official_reset)
    proxy_str = (req.proxy if req else "") or db.get_setting("proxy", "")

    if official:
        try:
            res = official_set_account_password(email_clean, new_password=new_pw, proxy=proxy_str)
            return res
        except Exception as e:
            logger.warning(f"[set_password] 官方全自动重设密码失败: {e}")
            raise HTTPException(500, f"OpenAI 官方全自动设置密码失败: {e}")
    else:
        db.update_registered_manual(email_clean, password=new_pw)
        logger.info(f"[registered] 账号 {email_clean} 密码已修改并落库: {new_pw}")
        return {
            "ok": True,
            "email": email_clean,
            "password": new_pw,
            "official_applied": False,
            "message": "密码已成功保存在本地数据库中",
        }


class BulkRepairReq(BaseModel):
    emails: list[str]
    official_reset: Optional[bool] = True
    proxy: Optional[str] = None


@app.post("/api/registered/bulk_bind_2fa")
def api_bulk_bind_2fa(req: BulkRepairReq):
    """批量为选中账号打 OpenAI 官方接口补绑 2FA。"""
    from .two_factor import bind_totp_2fa_adaptive
    emails = [e.strip().lower() for e in req.emails if e.strip()]
    if not emails:
        raise HTTPException(400, "请提供账号列表")

    success_list = []
    fail_list = []
    already_list = []

    for em in emails:
        row = db.get_registered(em)
        if not row:
            fail_list.append({"email": em, "error": "账号不存在"})
            continue
        if (row.get("totp_secret") or "").strip():
            already_list.append(em)
            continue
        try:
            res = bind_totp_2fa_adaptive(row, proxy=req.proxy or "")
            sec = res.get("secret")
            if sec:
                success_list.append(em)
            else:
                fail_list.append({"email": em, "error": res.get("message") or "绑定失败"})
        except Exception as e:
            fail_list.append({"email": em, "error": str(e)})

    return {
        "ok": True,
        "success_count": len(success_list),
        "already_count": len(already_list),
        "fail_count": len(fail_list),
        "success_emails": success_list,
        "fails": fail_list,
    }


@app.post("/api/registered/bulk_set_password")
def api_bulk_set_password(req: BulkRepairReq):
    """批量为账号设置/补设密码。支持官方全自动重置设密 (默认) 或本地批量生成。"""
    from .two_factor import generate_random_password
    from .official_password import official_set_account_password

    emails = [e.strip().lower() for e in req.emails if e.strip()]
    if not emails:
        raise HTTPException(400, "请提供账号列表")

    official = True if req.official_reset is None else bool(req.official_reset)
    proxy_str = req.proxy or db.get_setting("proxy", "")

    updated = []
    failed = []

    for em in emails:
        row = db.get_registered(em)
        if not row:
            continue
        new_pw = generate_random_password(16)
        if official:
            try:
                official_set_account_password(em, new_password=new_pw, proxy=proxy_str)
                updated.append(em)
            except Exception as e:
                failed.append({"email": em, "error": str(e)})
        else:
            db.update_registered_manual(em, password=new_pw)
            updated.append(em)

    return {
        "ok": True,
        "updated_count": len(updated),
        "fail_count": len(failed),
        "updated_emails": updated,
        "fails": failed,
        "official_applied": official,
    }


# ──────────────────────── Plus 试用检查 ────────────────────────


class CheckPlusReq(BaseModel):
    emails: list[str] = Field(..., description="要检查的邮箱列表")
    proxy: str = Field("", description="查询代理，留空直连")


# 封号在 401/403 响应体里的措辞。OpenAI 不止一种写法，全部小写后子串匹配。
# 新措辞加在这里即可；日志会打出未匹配的 401/403 原文方便补充。
_DEACTIVATED_MARKERS = (
    "account_deactivated",
    "accountdeactivated",
    "deactivated",
    "has been deactivated",
    "disabled",
    "suspended",
    "banned",
    "violat",          # violating / violation of our policies
    "potential abuse",
    "terminated",
)


def _body_text(resp) -> str:
    """安全取响应体文本，任何异常都不许打断检测循环。"""
    try:
        return (resp.text or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _looks_deactivated(body: str) -> bool:
    return any(m in body.lower() for m in _DEACTIVATED_MARKERS)


@app.post("/api/registered/check_plus")
def api_check_plus(req: CheckPlusReq):
    """用 access_token 查询账号的 Plus 试用状态。"""
    from http_client import create_http_session

    log = logging.getLogger("webui")
    url = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    # 走和注册流程同一个 create_http_session，不再自己拼 proxies dict。
    # 它负责两件这里以前漏掉的事：
    #   1) socks5:// -> socks5h://，DNS 交给代理端解析。用本地 DNS 打
    #      chatgpt.com 经常握手失败，这是「填了 SOCKS5 就检测不出来」的真正原因。
    #   2) trust_env=False + 显式空代理，代理留空时是真直连，
    #      不会被系统 HTTP_PROXY/HTTPS_PROXY 悄悄接管。
    proxy = req.proxy.strip()
    try:
        sess = create_http_session(proxy=proxy or None, impersonate="chrome110")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"创建 HTTP 会话失败: {e}")

    note = ""

    def _check(access_token: str, account_id: str = "", device_id: str = ""):
        """打一次检测请求。

        ⚠️ 这里**不再自动降级直连**。原来的行为是：代理第一次报错就永久切直连，
        后面所有号都用主人的真实 IP 去打 chatgpt.com 的账号接口，而提示只是
        结果末尾一句小字。2026-08-10 实测踩到：主人改了代理池密码，这页却还在
        用 localStorage 里的旧代理 → curl:(97) 鉴权被拒 → 静默直连。
        检测失败重试一次就好，不值得拿真实 IP 换。

        请求头按 chatgpt.com 前端真实发的补齐（Origin/Referer/ChatGPT-Account-ID/
        OAI-Device-Id）。以前只发 Authorization，缺 Origin/Referer 属于典型的
        非浏览器特征，容易被风控挑出来；account_id 从 access_token 的 JWT 里解，
        不额外请求。
        """
        nonlocal note
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": ua,
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
        }
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id
        if device_id:
            headers["OAI-Device-Id"] = device_id
        try:
            return sess.get(url, headers=headers, timeout=15)
        except Exception as e:  # noqa: BLE001
            if proxy and not note:
                # 把 curl 的错误码带出来：(97)=SOCKS5 鉴权被拒，(7)=连不上，
                # 笼统一句「代理连不通」会让人以为是网络抖动，其实是密码/配额问题。
                msg = str(e)
                if "(97)" in msg or "rejected by the SOCKS5" in msg:
                    note = "代理认证被拒（SOCKS5 (97)）—— 检查代理账号密码/配额是否已变更"
                elif "(7)" in msg:
                    note = "代理连不上（curl (7)）—— 检查代理地址端口是否可达"
                else:
                    note = f"代理请求失败（{type(e).__name__}）—— 已保持代理，未改直连"
                log.warning(f"[check_plus] {note}: {msg[:140]}")
            raise

    results = {}
    for email in req.emails:
        cred = db.get_registered(email)
        if not cred:
            results[email] = {"status": "not_found", "label": "未找到"}
            continue
        at = (cred.get("access_token") or "").strip()
        if not at:
            results[email] = {"status": "no_at", "label": "无AT"}
            continue
        # account_id 直接从 AT 的 JWT payload 解（实测 12/12 都带），不发额外请求。
        auth_claims = _get_auth(_decode_jwt_payload(at))
        account_id = str(
            auth_claims.get("chatgpt_account_id") or auth_claims.get("account_id") or ""
        ).strip()
        # device_id 库里普遍是空的（注册时没落盘），按邮箱派生一个稳定 UUID：
        # 同一个号每次检测都是同一个 device，比每次随机更像正常客户端。
        device_id = (cred.get("device_id") or "").strip() or str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"dango-check-plus:{email}")
        )
        try:
            resp = _check(at, account_id, device_id)
        except Exception as e:  # noqa: BLE001
            results[email] = {"status": "error", "label": "网络失败"}
            log.warning(f"[check_plus] {email} 请求失败: {str(e)[:140]}")
            continue
        if resp.status_code in (401, 403):
            # 401/403 的**响应体必须看**。以前这里只看状态码就贴「凭证失效」，
            # 结果是封号号 100% 显示成凭证失效：账号被封时 access_token 会被一起
            # 吊销 → 请求在这里就 401 了 → 永远走不到下面 200 分支的 is_deactivated
            # 判据。2026-08-10 实测某个被封号：JWT exp 还有 239 小时、
            # 13:53 检测还是 plus_eligible，之后被封 → 同一个 token 直接 401。
            #
            # 未过期却失效 = 被吊销，而 OpenAI 会在响应体里写明原因，
            # 所以按响应体内容区分「封号」和「单纯的凭证过期/轮换」。
            body = _body_text(resp)
            if _looks_deactivated(body):
                results[email] = {"status": "banned", "label": "封号"}
                log.info(f"[check_plus] {email} 判定封号 (HTTP {resp.status_code}): {body[:200]}")
                continue
            if resp.status_code == 401:
                results[email] = {"status": "token_invalid", "label": "凭证失效"}
                # 日志留原文：万一是没覆盖到的封号措辞，主人看一眼就能告诉我补进去。
                log.info(f"[check_plus] {email} 401 响应体: {body[:200]}")
                continue
            results[email] = {"status": "error", "label": f"HTTP {resp.status_code}"}
            log.info(f"[check_plus] {email} 403 响应体: {body[:200]}")
            continue
        if resp.status_code != 200:
            results[email] = {"status": "error", "label": f"HTTP {resp.status_code}"}
            continue
        try:
            data = resp.json() or {}
        except Exception:  # noqa: BLE001
            results[email] = {"status": "error", "label": "响应非 JSON"}
            continue

        from .plus_check import parse_account_plan
        results[email] = parse_account_plan(data, resp.text or "")

    try:
        sess.close()
    except Exception:  # noqa: BLE001
        pass

    checked_at = time.time()
    for email, info in results.items():
        # not_found / no_at / error 不写库：它们不是「检测结论」而是**没检测成**
        # （号不在库里、没凭证、代理挂了），写进去号就从 unchecked 过滤器里消失，
        # 看着像已经检测过。修好后重点一次即可。
        #
        # token_invalid **要写**（2026-08-10 改）。原先不写的理由是「凭证问题不是
        # 账号问题，换新凭证后该重查」，但实测下来：AT 没过期却 401 = 被吊销，
        # 大概率就是封号（2026-08-10 实测那个号即是）。不写库的实际后果是这号
        # 一直挂着上次的 plus_eligible，列表上显示「可领Plus试用」——比标成凭证
        # 失效误导得多。写库后 unchecked 过滤器会跳过它，正是想要的：它已经有结论了。
        if info["status"] not in ("not_found", "no_at", "error"):
            db.update_plus_check(email, {**info, "checked_at": checked_at})

    return {"ok": True, "results": results, "note": note}


# ──────────────────────── Plus 状态检测 (异步多 Worker 任务流) ────────────────────────


class StartPlusCheckReq(BaseModel):
    emails: list[str] = Field(..., description="要检测的账号邮箱列表")
    proxies: Optional[str] = Field("", description="检测代理池（每行一个代理；留空则直连）")
    proxy: Optional[str] = Field("", description="检测单个代理或选定代理")
    proxy_country: Optional[str] = Field("", description="代理目标国家代码，如 VN, BR, DE 等")
    workers: int = Field(5, ge=1, le=20, description="并发 worker 线程数")
    timeout: float = Field(20.0, description="单账号请求超时秒数")


def _safe_get_plus(q, timeout: float = 2.0):
    try:
        return q.get(timeout=timeout)
    except Exception as e:
        if type(e).__name__ == "Empty":
            return "__TIMEOUT__"
        return None


@app.post("/api/registered/plus_check/start")
def api_plus_check_start(req: StartPlusCheckReq):
    """启动 Plus 状态多 Worker 并发检测任务，返回 task_id 用于订阅 SSE。"""
    from . import plus_check

    emails = [e.strip().lower() for e in (req.emails or []) if e and e.strip()]
    if not emails:
        raise HTTPException(400, "请提供要检测的账号邮箱列表")

    proxies = []
    if req.proxies:
        for line in str(req.proxies or "").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                proxies.append(line)
    elif req.proxy:
        p = req.proxy.strip()
        if p:
            proxies.append(p)

    config = {
        "proxies": proxies,
        "proxy_country": (req.proxy_country or "").strip().upper(),
        "workers": max(1, min(20, req.workers)),
        "timeout": float(req.timeout or 20.0),
    }
    try:
        task_id = plus_check.start(emails, config)
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info(f"[plus_check] 任务 {task_id} 启动: {len(emails)} 个账号, workers={config['workers']}, country={config['proxy_country']}")
    return {"ok": True, "task_id": task_id, "taskId": task_id, "total": len(emails)}


@app.post("/api/registered/plus_check/{task_id}/stop")
def api_plus_check_stop(task_id: str):
    """停止指定的 Plus 状态检测任务。"""
    from . import plus_check

    active = plus_check.stop(task_id)
    return {"ok": True, "task_id": task_id, "active": active}


@app.get("/api/registered/plus_check/{task_id}/stream")
async def api_plus_check_stream(task_id: str, request: Request):
    """SSE：实时推流 Plus 状态检测进度、快照与日志。"""
    from . import plus_check

    snap = plus_check.snapshot(task_id)
    if snap is None:
        raise HTTPException(404, "task_id 不存在或已结束")

    q = plus_check.get_queue(task_id)

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            yield f"event: init\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await loop.run_in_executor(None, _safe_get_plus, q, 2.0)
                if msg is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg == "__TIMEOUT__":
                    yield ": ping\n\n"
                    continue
                if msg.get("kind") == "end":
                    yield "event: end\ndata: {}\n\n"
                    break
                kind = msg.get("kind", "progress")
                yield f"event: {kind}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/registered/plus_check/{task_id}/log")
def api_plus_check_log(task_id: str, email: str = ""):
    """获取指定任务中特定账号的详细检测日志。"""
    from . import plus_check

    task = plus_check.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")
    email = email.strip().lower()
    item = task.items.get(email)
    if not item:
        return {"ok": True, "email": email, "lines": ["未找到该账号的检测日志"]}
    return {"ok": True, "email": email, "lines": item.get("logs", []), "status": item.get("status")}


# ──────────────────────── 账号批量验活 (Token 验活 & 套餐验活) ────────────────────────


class StartHealthCheckReq(BaseModel):
    emails: list[str] = Field(..., description="要验活的账号邮箱列表")
    mode: Optional[str] = Field("token", description="验活模式: token (Token 状态验活) 或 plan (套餐订阅探测)")
    proxies: Optional[str] = Field("", description="代理池（每行一个）")
    proxy: Optional[str] = Field("", description="单个代理")
    proxy_country: Optional[str] = Field("", description="代理出口国家")
    workers: Optional[int] = Field(5, ge=1, le=10, description="并发线程数")
    timeout: Optional[float] = Field(20.0, description="请求超时秒数")


@app.post("/api/registered/health_check/start")
def api_health_check_start(req: StartHealthCheckReq):
    """启动账号批量验活任务 (Token 状态验活 或 套餐订阅探测)。"""
    from . import health_check_service

    emails = [e.strip().lower() for e in (req.emails or []) if e and e.strip()]
    if not emails:
        raise HTTPException(400, "请先提供要验活的账号列表")

    proxies = []
    if req.proxies:
        for line in str(req.proxies or "").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                proxies.append(line)
    elif req.proxy:
        p = req.proxy.strip()
        if p:
            proxies.append(p)

    cfg = {
        "mode": (req.mode or "token").strip().lower(),
        "proxies": proxies,
        "proxy_country": (req.proxy_country or "").strip().upper(),
        "workers": max(1, min(10, req.workers or 5)),
        "timeout": float(req.timeout or 20.0),
    }

    try:
        task_id = health_check_service.start_health_check(emails, cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))

    logger.info(f"[health_check] 验活任务 {task_id} 启动: {len(emails)} 个账号, mode={cfg['mode']}, workers={cfg['workers']}")
    return {"ok": True, "task_id": task_id, "taskId": task_id, "total": len(emails), "mode": cfg["mode"]}


@app.post("/api/registered/health_check/{task_id}/stop")
def api_health_check_stop(task_id: str):
    """停止指定的批量验活任务。"""
    from . import health_check_service

    active = health_check_service.stop_health_check(task_id)
    return {"ok": True, "task_id": task_id, "active": active}


@app.get("/api/registered/health_check/{task_id}/stream")
async def api_health_check_stream(task_id: str, request: Request):
    """SSE：实时推流批量验活进度与日志。"""
    from . import health_check_service

    task = health_check_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "task_id 不存在或已结束")

    q = task.queue

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            init_snap = {
                "items": task.items,
                "stats": task.stats,
                "mode": task.mode,
                "total": len(task.items),
                "done_count": task.done_count,
            }
            yield f"event: init\ndata: {json.dumps(init_snap, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await loop.run_in_executor(None, _safe_get_plus, q, 2.0)
                if msg is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg == "__TIMEOUT__":
                    yield ": ping\n\n"
                    continue
                if msg.get("kind") == "end":
                    yield f"event: end\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    break
                kind = msg.get("kind", "progress")
                yield f"event: {kind}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/registered/health_check/{task_id}/log")
def api_health_check_log(task_id: str, email: str = ""):
    """获取指定验活任务中特定账号的详细日志。"""
    from . import health_check_service

    task = health_check_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")
    email = email.strip().lower()
    item = task.items.get(email)
    if not item:
        return {"ok": True, "email": email, "lines": ["未找到该账号的验活日志"]}
    return {"ok": True, "email": email, "lines": item.get("logs", []), "status": item.get("status")}


# ──────────────────────── OAICS 资格检测 ────────────────────────


class StartOACheckReq(BaseModel):
    emails: list[str] = Field(..., description="要检测的账号邮箱列表")
    proxies: str = Field("", description="接码代理池（每行一个；支持 sticky 代理）")
    workers: int = Field(1, ge=1, le=20, description="并发 worker 数")
    rounds: int = Field(1, ge=1, le=20, description="每号探测轮数（命中 oaics_ 提前结束）")
    billing_country: str = Field("DE", description="账单国家")
    currency: str = Field("EUR", description="币种")
    proxy_country: str = Field("BR", description="代理出口国家")
    with_promo: bool = Field(False, description="创建 checkout 时附加 plus-1-month-free 促销")
    skip_proxy_check: bool = Field(False, description="跳过 Cloudflare trace 出口国家校验（更快）")
    timeout: float = Field(30.0, description="单次请求超时秒数")


def _safe_get_oa(q, timeout: float = 2.0):
    try:
        return q.get(timeout=timeout)
    except Exception as e:
        if type(e).__name__ == "Empty":
            return "__TIMEOUT__"
        return None


@app.post("/api/registered/oa_check/start")
def api_oa_check_start(req: StartOACheckReq):
    """对勾选的账号启动 OAICS 资格检测，返回 task_id（订阅 SSE 看进度）。"""
    from . import oa_check

    emails = [e.strip().lower() for e in (req.emails or []) if e and e.strip()]
    if not emails:
        raise HTTPException(400, "请先勾选要检测的账号")

    proxies = []
    for line in str(req.proxies or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            proxies.append(line)
    if not proxies:
        raise HTTPException(400, "请先粘贴接码代理池（每行一个代理）")

    config = {
        "proxies": proxies,
        "workers": max(1, min(20, req.workers)),
        "rounds": max(1, min(20, req.rounds)),
        "billing_country": (req.billing_country or "DE").upper().strip(),
        "currency": (req.currency or "EUR").upper().strip(),
        "proxy_country": (req.proxy_country or "BR").upper().strip(),
        "with_promo": req.with_promo,
        "skip_proxy_check": req.skip_proxy_check,
        "timeout": float(req.timeout or 30.0),
    }
    try:
        task_id = oa_check.start(emails, config)
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info(f"[oa_check] 任务 {task_id} 启动: {len(emails)} 个账号, "
                f"workers={config['workers']}, rounds={config['rounds']}")
    return {"ok": True, "task_id": task_id, "taskId": task_id, "total": len(emails)}


@app.post("/api/registered/oa_check/{task_id}/stop")
def api_oa_check_stop(task_id: str):
    """停止指定的 OAICS 资格检测任务。"""
    from . import oa_check

    active = oa_check.stop(task_id)
    return {"ok": True, "task_id": task_id, "active": active}


@app.get("/api/registered/oa_check/{task_id}/stream")
async def api_oa_check_stream(task_id: str, request: Request):
    """SSE：先推全量快照，再实时推 progress / log / end 事件。"""
    from . import oa_check

    snap = oa_check.snapshot(task_id)
    if snap is None:
        raise HTTPException(404, "task_id 不存在或已结束")

    q = oa_check.get_queue(task_id)

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            # 断线重连 / 迟到订阅：先把当前全量状态推一次
            yield f"event: init\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await loop.run_in_executor(None, _safe_get_oa, q, 2.0)
                if msg is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg == "__TIMEOUT__":
                    yield ": ping\n\n"
                    continue
                if msg.get("kind") == "end":
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg.get("kind") == "log":
                    yield f"event: log\ndata: {json.dumps({'line': msg.get('line', '')}, ensure_ascii=False)}\n\n"
                else:
                    yield f"event: progress\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/registered/oa_check/{task_id}/log")
def api_oa_check_log(task_id: str, email: str = ""):
    """获取指定 OAICS 任务中特定账号的详细检测日志。"""
    from . import oa_check

    task = oa_check.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")
    email = email.strip().lower()
    item = task.items.get(email)
    if not item:
        return {"ok": True, "email": email, "lines": ["未找到该账号的检测日志"]}
    return {"ok": True, "email": email, "lines": item.get("logs", []), "status": item.get("status")}


# ──────────────────────── OAuth 导出 (Codex OAuth / CPA / Sub2API) ────────────────────────


class StartOAuthExportReq(BaseModel):
    emails: list[str] = Field(..., description="要执行 OAuth 导出的账号邮箱列表")
    proxies: str = Field("", description="代理池（每行一个）")
    proxy: str = Field("", description="单个代理")
    proxy_country: str = Field("RANDOM_HOT", description="代理目标国家")
    workers: int = Field(5, ge=1, le=20, description="并发 worker 数")
    timeout: float = Field(45.0, description="单账号超时秒数")
    # SMS 接码配置扩展
    sms_enabled: bool = Field(False, description="是否启用自动 SMS 接码")
    sms_provider: Optional[str] = Field("smsbower", description="接码服务平台 kind")
    sms_api_key: Optional[str] = Field("", description="接码平台 API Key / CDK 卡密（留空使用系统全局配置）")
    sms_cdk_url: Optional[str] = Field("https://ndk.cc.cd", description="CDK 平台接口基地址")
    sms_country: Optional[str] = Field("52", description="接码国家ID，默认52泰国")
    sms_max_price: Optional[str] = Field("", description="最高单价限制")
    sms_provider_ids: Optional[str] = Field("", description="指定供应商ID(如3237)")
    sms_except_provider_ids: Optional[str] = Field("", description="排除的供应商ID(如3327)")
    sms_max_attempts: int = Field(3, ge=1, le=10, description="最多换号尝试次数")
    sms_timeout: int = Field(80, ge=20, le=300, description="单号等待短信超时秒数")


def _safe_get_oauth_export(q, timeout: float = 2.0):
    try:
        return q.get(timeout=timeout)
    except Exception as e:
        if type(e).__name__ == "Empty":
            return "__TIMEOUT__"
        return None


@app.post("/api/registered/oauth_export/start")
def api_oauth_export_start(req: StartOAuthExportReq):
    """启动 OAuth 导出多 Worker 并发任务，返回 task_id 用于订阅 SSE。"""
    from . import oauth_export

    emails = [e.strip().lower() for e in (req.emails or []) if e and e.strip()]
    if not emails:
        raise HTTPException(400, "请提供要导出的账号邮箱列表")

    proxies = []
    if req.proxies:
        for line in str(req.proxies or "").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                proxies.append(line)
    elif req.proxy:
        p = req.proxy.strip()
        if p:
            proxies.append(p)

    # 接码配置组装
    sms_api_key = (req.sms_api_key or "").strip()
    global_sms = db.get_sms_internal_config()
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_providers import canonicalize_kind, uses_cdk_pool
    sms_provider_kind = canonicalize_kind(req.sms_provider or "smsbower") or "smsbower"
    is_cdk_mode = uses_cdk_pool(sms_provider_kind)
    if is_cdk_mode:
        # CDK 模式下，若未单独指定卡密，则置空以严格走数据库 CDK 号池自动调度，切勿混用普通接码平台的 API Key！
        if not sms_api_key or sms_api_key == "***" or (len(sms_api_key) == 32 and "-" not in sms_api_key and not sms_api_key.upper().startswith("SMS")):
            sms_api_key = ""
    else:
        if not sms_api_key or sms_api_key == "***":
            sms_api_key = global_sms.get("sms_api_key") or ""
    sms_cdk_url = (req.sms_cdk_url or "").strip() or global_sms.get("sms_cdk_url") or "https://ndk.cc.cd"

    sms_config = {
        "sms_enabled": req.sms_enabled,
        "sms_provider": sms_provider_kind,
        "sms_api_key": sms_api_key,
        "sms_cdk_url": sms_cdk_url,
        "sms_country": (req.sms_country or "52").strip(),
        "sms_max_price": (req.sms_max_price or "").strip(),
        "sms_provider_ids": (req.sms_provider_ids or "").strip(),
        "sms_except_provider_ids": (req.sms_except_provider_ids or "").strip(),
        "sms_max_attempts": max(1, min(10, req.sms_max_attempts)),
        "sms_timeout": max(20, min(300, req.sms_timeout)),
    }

    config = {
        "proxies": proxies,
        "proxy_country": (req.proxy_country or "").strip().upper(),
        "workers": max(1, min(20, req.workers)),
        "timeout": float(req.timeout or 45.0),
        "sms_config": sms_config,
    }
    try:
        task_id = oauth_export.start(emails, config)
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info(f"[oauth_export] 任务 {task_id} 启动: {len(emails)} 个账号, workers={config['workers']}, sms_enabled={req.sms_enabled}")
    return {"ok": True, "task_id": task_id, "taskId": task_id, "total": len(emails)}


class RetryOAuthExportReq(BaseModel):
    emails: Optional[list[str]] = None
    proxy: Optional[str] = None
    proxies: Optional[str] = None
    proxy_country: Optional[str] = None
    workers: Optional[int] = None
    timeout: Optional[int] = None
    sms_enabled: Optional[bool] = None
    sms_provider: Optional[str] = None
    sms_api_key: Optional[str] = None
    sms_cdk_url: Optional[str] = None
    sms_country: Optional[str] = None
    sms_max_price: Optional[str] = None
    sms_provider_ids: Optional[str] = None
    sms_except_provider_ids: Optional[str] = None
    sms_max_attempts: Optional[int] = None
    sms_timeout: Optional[int] = None


@app.post("/api/registered/oauth_export/{task_id}/retry")
def api_oauth_export_retry(task_id: str, req: Optional[RetryOAuthExportReq] = None):
    """重新授权失败或指定的账号。"""
    from . import oauth_export

    emails = req.emails if req else None
    new_cfg = {}
    if req:
        if req.proxy is not None:
            new_cfg["proxy"] = req.proxy
        if req.proxies is not None:
            new_cfg["proxies"] = [p.strip() for p in str(req.proxies).splitlines() if p.strip() and not p.startswith("#")]
        if req.proxy_country is not None:
            new_cfg["proxy_country"] = req.proxy_country
        if req.workers is not None:
            new_cfg["workers"] = req.workers
        if req.timeout is not None:
            new_cfg["timeout"] = req.timeout

        sms_up = {}
        if req.sms_enabled is not None: sms_up["sms_enabled"] = req.sms_enabled
        if req.sms_provider is not None: sms_up["sms_provider"] = req.sms_provider
        if req.sms_api_key is not None: sms_up["sms_api_key"] = req.sms_api_key
        if req.sms_cdk_url is not None: sms_up["sms_cdk_url"] = req.sms_cdk_url
        if req.sms_country is not None: sms_up["sms_country"] = req.sms_country
        if req.sms_max_price is not None: sms_up["sms_max_price"] = req.sms_max_price
        if req.sms_provider_ids is not None: sms_up["sms_provider_ids"] = req.sms_provider_ids
        if req.sms_except_provider_ids is not None: sms_up["sms_except_provider_ids"] = req.sms_except_provider_ids
        if req.sms_max_attempts is not None: sms_up["sms_max_attempts"] = req.sms_max_attempts
        if req.sms_timeout is not None: sms_up["sms_timeout"] = req.sms_timeout
        if sms_up:
            new_cfg["sms_config"] = sms_up

    try:
        res = oauth_export.retry(task_id, emails=emails, new_config=new_cfg or None)
        return res
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/registered/oauth_export/{task_id}/stop")
def api_oauth_export_stop(task_id: str):
    """停止指定的 OAuth 导出任务。"""
    from . import oauth_export

    active = oauth_export.stop(task_id)
    return {"ok": True, "task_id": task_id, "active": active}


@app.get("/api/registered/oauth_export/{task_id}/stream")
async def api_oauth_export_stream(task_id: str, request: Request):
    """SSE：实时推流 OAuth 导出进度与日志。"""
    from . import oauth_export

    snap = oauth_export.snapshot(task_id)
    if snap is None:
        raise HTTPException(404, "task_id 不存在或已结束")

    q = oauth_export.get_queue(task_id)

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            yield f"event: init\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await loop.run_in_executor(None, _safe_get_oauth_export, q, 2.0)
                if msg is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg == "__TIMEOUT__":
                    yield ": ping\n\n"
                    continue
                if msg.get("kind") == "end":
                    yield f"event: end\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    break
                kind = msg.get("kind", "progress")
                yield f"event: {kind}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/registered/oauth_export/{task_id}/log")
def api_oauth_export_log(task_id: str, email: str = ""):
    """获取指定任务中特定账号的详细导出日志。"""
    from . import oauth_export

    lines = oauth_export.get_logs(task_id, email)
    return {"ok": True, "email": email, "lines": lines}


@app.post("/api/registered/oauth_export/{task_id}/download_cpa")
@app.get("/api/registered/oauth_export/{task_id}/download_cpa")
def api_oauth_export_download_cpa(task_id: str, emails: str = ""):
    """下载任务中成功账号的 CPA JSON 凭证。"""
    from . import oauth_export
    from datetime import datetime, timezone

    email_list = [e.strip().lower() for e in emails.split(",") if e.strip()] if emails else None
    cpa_list = oauth_export.export_cpa_bundle(task_id, email_list)
    if not cpa_list and email_list:
        cpa_list = []
        for em in email_list:
            row = db.get_registered(em)
            if row and (row.get("access_token") or row.get("refresh_token")):
                cpa_list.append({
                    "type": "codex",
                    "email": em,
                    "access_token": row.get("access_token") or "",
                    "refresh_token": row.get("refresh_token") or "",
                    "id_token": row.get("id_token") or "",
                    "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
    if not cpa_list:
        raise HTTPException(404, "没有可供下载的 CPA 凭证数据")

    payload = cpa_list if len(cpa_list) > 1 else cpa_list[0]
    filename = f"cpa-oauth-{task_id}.json" if len(cpa_list) > 1 else f"codex-{cpa_list[0]['email']}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/registered/oauth_export/{task_id}/download_sub2")
@app.get("/api/registered/oauth_export/{task_id}/download_sub2")
def api_oauth_export_download_sub2(task_id: str, emails: str = ""):
    """下载任务中成功账号的 Sub2API 聚合 JSON 数据。"""
    from . import oauth_export
    from datetime import datetime, timezone

    email_list = [e.strip().lower() for e in emails.split(",") if e.strip()] if emails else None
    sub2_payload = oauth_export.export_sub2_bundle(task_id, email_list)
    if not sub2_payload.get("accounts") and email_list:
        cpa_list = []
        for em in email_list:
            row = db.get_registered(em)
            if row and (row.get("access_token") or row.get("refresh_token")):
                cpa_list.append({
                    "type": "codex",
                    "email": em,
                    "access_token": row.get("access_token") or "",
                    "refresh_token": row.get("refresh_token") or "",
                    "id_token": row.get("id_token") or "",
                    "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
        sub2_payload = oauth_export.build_sub2api_payload(cpa_list)

    if not sub2_payload.get("accounts"):
        raise HTTPException(404, "没有可供导出的 Sub2API 数据")

    filename = f"sub2api-oauth-{task_id}.json"
    return Response(
        content=json.dumps(sub2_payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/registered/oauth_export/features")
def api_oauth_export_features(limit: int = 100, outcome: str = ""):
    """最近一次次 OAuth 授权尝试的特征明细（成功/失败都在）。"""
    return {"ok": True, "rows": db.list_oauth_attempt_features(limit=limit, outcome=outcome)}


@app.get("/api/registered/oauth_export/feature_weights")
def api_oauth_export_feature_weights(min_n: int = 1):
    """按代理国/指纹/接码国家等组合统计成功率，给后续加权选路用。"""
    return {"ok": True, **db.get_oauth_feature_weights(min_n=min_n)}


# ──────────────────────── Token 重新获取与刷新 (Token Refresh Studio) ────────────────────────


class StartTokenRefreshReq(BaseModel):
    emails: list[str] = Field(..., description="要刷新/重获Token的账号邮箱列表")
    proxies: str = Field("", description="接码代理池（每行一个）")
    proxy: str = Field("", description="单个代理")
    proxy_country: str = Field("RANDOM_HOT", description="代理目标国家")
    workers: int = Field(5, ge=1, le=20, description="并发 worker 数")
    timeout: float = Field(45.0, description="单账号超时秒数")
    force_full_login: bool = Field(False, description="是否强制全流程 OAuth 重新登录（不走 RT 快速置换）")
    # SMS 接码配置扩展
    sms_enabled: bool = Field(False, description="是否启用自动 SMS 接码")
    sms_provider: Optional[str] = Field("smsbower", description="接码服务平台 (smsbower / herosms)")
    sms_api_key: Optional[str] = Field("", description="接码平台 API Key（留空使用系统全局配置）")
    sms_country: Optional[str] = Field("52", description="接码国家ID，默认52泰国")
    sms_max_price: Optional[str] = Field("", description="最高单价限制")
    sms_max_attempts: int = Field(3, ge=1, le=10, description="最多换号尝试次数")
    sms_timeout: int = Field(80, ge=20, le=300, description="单号等待短信超时秒数")


def _safe_get_token_refresh(q, timeout: float = 2.0):
    try:
        return q.get(timeout=timeout)
    except Exception as e:
        if type(e).__name__ == "Empty":
            return "__TIMEOUT__"
        return None


@app.post("/api/registered/token_refresh/start")
def api_token_refresh_start(req: StartTokenRefreshReq):
    """启动 Token 刷新/重获多 Worker 并发任务，返回 task_id 用于订阅 SSE。"""
    from . import token_refresh_service

    emails = [e.strip().lower() for e in (req.emails or []) if e and e.strip()]
    if not emails:
        raise HTTPException(400, "请提供要刷新 Token 的账号邮箱列表")

    sms_api_key = (req.sms_api_key or "").strip()
    if not sms_api_key or sms_api_key == "***":
        global_sms = db.get_sms_internal_config()
        sms_api_key = global_sms.get("sms_api_key") or ""

    try:
        task_id = token_refresh_service.start_token_refresh_task(
            emails=emails,
            proxies=req.proxies,
            proxy=req.proxy,
            proxy_country=req.proxy_country,
            workers=req.workers,
            timeout=int(req.timeout or 45),
            force_full_login=req.force_full_login,
            sms_enabled=req.sms_enabled,
            sms_provider=(req.sms_provider or "smsbower").strip().lower(),
            sms_api_key=sms_api_key,
            sms_country=(req.sms_country or "52").strip(),
            sms_max_price=(req.sms_max_price or "").strip(),
            sms_max_attempts=req.sms_max_attempts,
            sms_timeout=req.sms_timeout,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    logger.info(f"[token_refresh] 任务 {task_id} 启动: {len(emails)} 个账号, workers={req.workers}")
    return {"ok": True, "task_id": task_id, "taskId": task_id, "total": len(emails)}


@app.post("/api/registered/token_refresh/{task_id}/stop")
def api_token_refresh_stop(task_id: str):
    """停止指定的 Token 刷新任务。"""
    from . import token_refresh_service
    active = token_refresh_service.stop_token_refresh_task(task_id)
    return {"ok": True, "task_id": task_id, "active": active}


@app.get("/api/registered/token_refresh/{task_id}/stream")
async def api_token_refresh_stream(task_id: str, request: Request):
    """SSE 实时推送 Token 刷新任务进度。"""
    from . import token_refresh_service
    task = token_refresh_service.get_token_refresh_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")

    async def event_gen():
        loop = asyncio.get_event_loop()
        yield f"event: init\ndata: {json.dumps({'task_id': task_id, 'total': len(task.items), 'items': task.items}, ensure_ascii=False)}\n\n"
        while True:
            if await request.is_disconnected():
                break
            msg = await loop.run_in_executor(None, _safe_get_token_refresh, task.queue)
            if msg is None:
                break
            if msg == "__TIMEOUT__":
                yield ": keep-alive\n\n"
                continue
            kind = msg.get("kind") or "progress"
            data_str = json.dumps(msg, ensure_ascii=False)
            if kind == "end":
                yield f"event: end\ndata: {data_str}\n\n"
                break
            elif kind == "log":
                yield f"event: log\ndata: {data_str}\n\n"
            else:
                yield f"event: progress\ndata: {data_str}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/registered/token_refresh/{task_id}/log")
def api_token_refresh_log(task_id: str, email: str):
    """查询单个账号在 Token 刷新任务中的日志。"""
    from . import token_refresh_service
    lines = token_refresh_service.get_token_refresh_log(task_id, email)
    return {"ok": True, "email": email, "lines": lines}


@app.get("/api/registered/token_refresh/{task_id}/download")
def api_token_refresh_download(task_id: str, format: str = "txt"):
    """下载 Token 刷新任务的凭证文件 (txt / cpa / sub2api / json)。"""
    from . import token_refresh_service
    task = token_refresh_service.get_token_refresh_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")

    fmt = format.lower().strip()
    if fmt == "txt":
        content = token_refresh_service.export_refreshed_tokens_text(task_id)
        if not content:
            raise HTTPException(404, "没有刷新成功的账号可供下载")
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="refreshed_tokens_{task_id}.txt"'},
        )
    elif fmt == "cpa":
        data = token_refresh_service.export_refreshed_tokens_cpa_json(task_id)
        if not data:
            raise HTTPException(404, "没有 CPA 格式凭证可供下载")
        return Response(
            content=json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="cpa_refreshed_{task_id}.json"'},
        )
    elif fmt == "sub2api":
        data = token_refresh_service.export_refreshed_tokens_sub2api_json(task_id)
        if not data.get("accounts"):
            raise HTTPException(404, "没有 Sub2API 格式凭证可供下载")
        return Response(
            content=json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="sub2api_refreshed_{task_id}.json"'},
        )
    else:
        items_dict = {email: it.get("result") for email, it in task.items.items() if it.get("result")}
        return Response(
            content=json.dumps(items_dict, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="refreshed_all_{task_id}.json"'},
        )


# ──────────────────────── Plus 试用提链 (Extract Link) ────────────────────────


class ExtractConfigReq(BaseModel):
    extract_link_api_base: Optional[str] = ""
    extract_link_cdk: Optional[str] = ""
    extract_link_type: Optional[str] = "pix"
    extract_link_workers: Optional[str] = "3"


@app.get("/api/extract/config")
def api_extract_get_config():
    """获取 Plus 提链全局配置。"""
    return {"ok": True, "config": db.get_extract_config()}


@app.post("/api/extract/config")
def api_extract_save_config(req: ExtractConfigReq):
    """保存 Plus 提链全局配置。"""
    db.save_extract_config(req.model_dump(exclude_unset=True))
    return {"ok": True, "config": db.get_extract_config()}


@app.get("/api/extract/cdk")
def api_extract_query_cdk(api_base: str = "", cdk: str = ""):
    """实时查询提链 CDK 剩余次数与额度。"""
    from . import extract_link_service

    internal = db.get_extract_internal_config()
    target_base = api_base.strip() or internal.get("extract_link_api_base", "")
    target_cdk = cdk.strip() if (cdk and cdk != "***") else internal.get("extract_link_cdk", "")
    if not target_base:
        raise HTTPException(400, "尚未配置提链服务 API 地址")
    if not target_cdk:
        raise HTTPException(400, "尚未配置提链 CDK")
    try:
        res = extract_link_service.query_cdk(target_base, target_cdk)
        return {"ok": True, "data": res}
    except Exception as e:
        raise HTTPException(400, str(e))


class StartExtractReq(BaseModel):
    emails: list[str] = Field(..., description="要提链的账号邮箱列表")
    api_base: Optional[str] = Field("", description="提链服务 API 地址")
    cdk: Optional[str] = Field("", description="提链 CDK")
    link_type: Optional[str] = Field("pix", description="渠道类型 (pix/upi/stripe等)")
    workers: int = Field(3, ge=1, le=20, description="并发 worker 线程数")


@app.post("/api/extract/start")
def api_extract_start(req: StartExtractReq):
    """启动 Plus 批量提链任务。"""
    from . import extract_link_service

    emails = [e.strip().lower() for e in (req.emails or []) if e and e.strip()]
    if not emails:
        raise HTTPException(400, "请提供要提链的账号邮箱列表")

    internal = db.get_extract_internal_config()
    cfg = {
        "api_base": (req.api_base or "").strip() or internal.get("extract_link_api_base", ""),
        "cdk": (req.cdk or "").strip() if (req.cdk and req.cdk != "***") else internal.get("extract_link_cdk", ""),
        "link_type": (req.link_type or "").strip().lower() or internal.get("extract_link_type", "pix"),
        "workers": req.workers or int(internal.get("extract_link_workers") or 3),
    }

    try:
        task_id = extract_link_service.start(emails, cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))

    logger.info(f"[extract] 提链任务 {task_id} 启动: {len(emails)} 个账号, link_type={cfg['link_type']}, workers={cfg['workers']}")
    return {"ok": True, "task_id": task_id, "taskId": task_id, "total": len(emails)}


@app.post("/api/extract/{task_id}/stop")
def api_extract_stop(task_id: str):
    """停止指定的 Plus 提链任务。"""
    from . import extract_link_service

    active = extract_link_service.stop(task_id)
    return {"ok": True, "task_id": task_id, "active": active}


@app.get("/api/extract/{task_id}/stream")
async def api_extract_stream(task_id: str, request: Request):
    """SSE：实时推流 Plus 提链进度、状态与日志。"""
    from . import extract_link_service

    snap = extract_link_service.snapshot(task_id)
    if snap is None:
        raise HTTPException(404, "task_id 不存在或已结束")

    q = extract_link_service.get_queue(task_id)

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            yield f"event: init\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await loop.run_in_executor(None, _safe_get_plus, q, 2.0)
                if msg is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg == "__TIMEOUT__":
                    yield ": ping\n\n"
                    continue
                if msg.get("kind") == "end":
                    yield f"event: end\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    break
                kind = msg.get("kind", "progress")
                yield f"event: {kind}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/extract/{task_id}/log")
def api_extract_log(task_id: str, email: str = ""):
    """获取指定提链任务中特定账号的详细日志。"""
    from . import extract_link_service

    lines = extract_link_service.get_logs(task_id, email)
    return {"ok": True, "email": email, "lines": lines}


@app.get("/api/extract/export_links")
def api_extract_export_links(
    task_id: str = "",
    emails: str = "",
    export_format: str = "url_only",
):
    """导出提链成功的链接数据 (url_only / email_url / json / csv)。"""
    from . import extract_link_service

    email_list = [e.strip().lower() for e in emails.split(",") if e.strip()] if emails else []
    records = []

    if task_id:
        snap = extract_link_service.snapshot(task_id)
        if snap and "items" in snap:
            for em, it in snap["items"].items():
                if (not email_list or em in email_list) and it.get("link_url"):
                    records.append({
                        "email": em,
                        "link_type": it.get("link_type", ""),
                        "link_url": it.get("link_url", ""),
                        "status": it.get("status", ""),
                    })

    if not records:
        con = db._conn()
        placeholders = ",".join("?" * len(email_list)) if email_list else ""
        query = "SELECT email, extra_json FROM registered WHERE extra_json LIKE '%\"extract_link\"%'"
        params = []
        if email_list:
            query += f" AND email IN ({placeholders})"
            params = email_list
        rows = con.execute(query, params).fetchall()
        for r in rows:
            em = r["email"]
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            ext = extra.get("extract_link") or {}
            if ext.get("link_url"):
                records.append({
                    "email": em,
                    "link_type": ext.get("link_type", ""),
                    "link_url": ext.get("link_url", ""),
                    "status": ext.get("status", ""),
                })

    if not records:
        raise HTTPException(404, "没有找到提链成功的链接记录")

    if export_format == "json":
        return Response(
            content=json.dumps(records, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="extracted-links.json"'},
        )
    elif export_format == "email_url":
        lines = [f"{r['email']}----{r['link_url']}" for r in records]
        return Response(
            content="\n".join(lines),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="extracted-links.txt"'},
        )
    elif export_format == "csv":
        lines = ["email,link_type,status,link_url"]
        for r in records:
            lines.append(f'"{r["email"]}","{r["link_type"]}","{r["status"]}","{r["link_url"]}"')
        return Response(
            content="\n".join(lines),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="extracted-links.csv"'},
        )
    else:  # url_only
        lines = [r["link_url"] for r in records]
        return Response(
            content="\n".join(lines),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="extracted-links.txt"'},
        )


# ──────────────────────── 本地原生多渠道提炼与资格检测 (无需外部CDK/API) ────────────────────────


class StartNativeExtractTaskReq(BaseModel):
    emails: list[str] = Field(..., description="要提炼的账号邮箱列表")
    channel: str = Field("paypal", description="提炼渠道类型 (gcash_check/oaics_check/gcash/pix/paypal/ideal/upi/kakao/momo/twint/blik/hosted)")
    exit_country: Optional[str] = Field("", description="出口代理国家 (BR/US/NL/VN等)")
    billing_country: Optional[str] = Field("", description="账单国家 (PH/BR/DE/NL/IN等)")
    currency: Optional[str] = Field("", description="币种 (PHP/BRL/EUR/USD等)")
    workers: int = Field(3, ge=1, le=20, description="并发 worker 线程数")
    retries: int = Field(3, ge=1, le=10, description="每号尝试次数")
    allow_fallback: bool = Field(False, description="允许账单回退")
    proxy_pool: Optional[str] = Field("", description="指定代理池")
    # 一条龙代付配置扩展 (同 IP 同环境)
    auto_pay: Optional[bool] = Field(False, description="是否自动接力执行 PayPal 协议代付开通 Plus")
    pay_phone: Optional[str] = Field("", description="全局默认代付手机号")
    account_phones: Optional[dict[str, str]] = Field(None, description="每个账号单独指定的手机号字典 {email: phone}")
    pay_flow_mode: Optional[str] = Field("elevation", description="代付协议模式: elevation / standard")
    sms_provider_name: Optional[str] = Field("", description="接码平台")
    sms_api_key: Optional[str] = Field("", description="接码平台 Key")
    sms_country: Optional[str] = Field("52", description="接码国家")


@app.post("/api/extract/task/start")
def api_extract_task_start(req: StartNativeExtractTaskReq):
    """启动本地原生渠道提炼任务台。"""
    from . import extract_engine

    emails = [e.strip().lower() for e in (req.emails or []) if e and e.strip()]
    if not emails:
        raise HTTPException(400, "请提供要提链的账号邮箱列表")

    # 如果没传代理池，从设置或 proxy_seeds 加载
    pool_str = req.proxy_pool or db.get_setting("proxy_pool", "") or db.get_setting("proxy_seeds", "")

    task_config = {
        "channel": req.channel,
        "exit_country": req.exit_country,
        "billing_country": req.billing_country,
        "currency": req.currency,
        "workers": req.workers,
        "retries": req.retries,
        "allow_fallback": req.allow_fallback,
        "proxy_pool": pool_str,
        "auto_pay": bool(req.auto_pay),
        "pay_phone": req.pay_phone or "",
        "account_phones": req.account_phones or {},
        "pay_flow_mode": req.pay_flow_mode or "elevation",
        "sms_provider_name": req.sms_provider_name or "",
        "sms_api_key": req.sms_api_key or "",
        "sms_country": req.sms_country or "52",
    }

    try:
        task_id = extract_engine.start_extract_job(emails, task_config)
    except ValueError as e:
        raise HTTPException(400, str(e))

    logger.info(f"[native_extract] 任务 {task_id} 启动: channel={req.channel}, total={len(emails)}, workers={req.workers}, auto_pay={req.auto_pay}")
    return {"ok": True, "task_id": task_id, "taskId": task_id, "total": len(emails), "channel": req.channel}


@app.post("/api/extract/task/{task_id}/stop")
def api_extract_task_stop(task_id: str):
    """停止指定的本地原生提炼任务。"""
    from . import extract_engine

    active = extract_engine.stop_extract_job(task_id)
    return {"ok": True, "task_id": task_id, "active": active}


@app.get("/api/extract/task/{task_id}/stream")
async def api_extract_task_stream(task_id: str, request: Request):
    """SSE：实时推流本地原生提炼进度与状态。"""
    from . import extract_engine

    snap = extract_engine.get_task_snapshot(task_id)
    if snap is None:
        raise HTTPException(404, "task_id 不存在或已结束")

    q = extract_engine.get_task_queue(task_id)

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            yield f"event: init\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await loop.run_in_executor(None, _safe_get_plus, q, 2.0)
                if msg is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg == "__TIMEOUT__":
                    yield ": ping\n\n"
                    continue
                if msg.get("kind") == "end":
                    yield f"event: end\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    break
                kind = msg.get("kind", "progress")
                yield f"event: {kind}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/extract/task/{task_id}/log")
def api_extract_task_log(task_id: str, email: str = ""):
    """获取指定本地提炼任务中特定账号的详细日志。"""
    from . import extract_engine

    lines = extract_engine.get_task_logs(task_id, email)
    return {"ok": True, "email": email, "lines": lines}


class RetryExtractTaskReq(BaseModel):
    emails: Optional[list[str]] = None


@app.post("/api/extract/task/{task_id}/retry")
def api_extract_task_retry(task_id: str, req: Optional[RetryExtractTaskReq] = None):
    """重试提炼任务中失败或指定的账号。"""
    from . import extract_engine

    emails = req.emails if req else None
    try:
        res = extract_engine.retry_extract_job(task_id, emails)
        return res
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"重试提炼失败: {e}")


class SubmitExtractTaskInputReq(BaseModel):
    email: str = Field(..., description="账号邮箱")
    value: str = Field(..., description="6位短信验证码或新手机号")


@app.post("/api/extract/task/{task_id}/input")
def api_extract_task_input(task_id: str, req: SubmitExtractTaskInputReq):
    """向一条龙提炼代付任务提交 2FA 短信验证码或新手机号。"""
    from . import extract_engine

    try:
        extract_engine.submit_extract_input(task_id, req.email, req.value)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))




# ──────────────────────── PayPal 协议支付 (自动代付开通) ────────────────────────


class StartPayPalPayReq(BaseModel):
    items: list[dict] = Field(..., description="要支付的列表，每项含 email, ba_token 等")
    country: Optional[str] = Field("BR", description="买家国家代码，如 BR, US, GB 等")
    flow_mode: Optional[str] = Field("elevation", description="协议模式: elevation (身份提升) 或 standard (原版)")
    workers: Optional[int] = Field(2, ge=1, le=10, description="并发线程数")
    proxy_pool: Optional[str] = Field("", description="代理池")


@app.post("/api/paypal-pay/task/start")
def api_paypal_pay_task_start(req: StartPayPalPayReq):
    """启动 PayPal 协议代付任务。"""
    from . import paypal_pay_engine

    if not req.items:
        raise HTTPException(400, "请提供要执行协议支付的列表")

    pool_str = req.proxy_pool or db.get_setting("proxy_pool", "") or db.get_setting("proxy_seeds", "")
    task_id = paypal_pay_engine.start_paypal_pay_task(
        items=req.items,
        workers=req.workers or 2,
        country=req.country or "BR",
        flow_mode=req.flow_mode or "elevation",
        proxy_pool=pool_str,
    )
    return {"ok": True, "task_id": task_id, "taskId": task_id, "total": len(req.items)}


@app.post("/api/paypal-pay/task/{task_id}/stop")
def api_paypal_pay_task_stop(task_id: str):
    """停止指定的 PayPal 协议代付任务。"""
    from . import paypal_pay_engine

    active = paypal_pay_engine.stop_paypal_pay_task(task_id)
    return {"ok": True, "task_id": task_id, "active": active}


@app.get("/api/paypal-pay/task/{task_id}/stream")
async def api_paypal_pay_task_stream(task_id: str, request: Request):
    """SSE：实时推流 PayPal 协议代付进度与状态。"""
    from . import paypal_pay_engine

    task = paypal_pay_engine.get_paypal_pay_task(task_id)
    if not task:
        raise HTTPException(404, "task_id 不存在或已结束")

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            init_snap = {
                "items": task.items,
                "logs": task.logs,
                "country": task.country,
                "flow_mode": task.flow_mode,
            }
            yield f"event: init\ndata: {json.dumps(init_snap, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await loop.run_in_executor(None, _safe_get_plus, task.queue, 2.0)
                if msg is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg == "__TIMEOUT__":
                    yield ": ping\n\n"
                    continue
                if msg.get("kind") == "end":
                    yield f"event: end\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    break
                kind = msg.get("kind", "progress")
                yield f"event: {kind}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/paypal-pay/task/{task_id}/log")
def api_paypal_pay_task_log(task_id: str, key: str = ""):
    """获取 PayPal 协议代付中特定账号/Token 的详细日志。"""
    from . import paypal_pay_engine

    task = paypal_pay_engine.get_paypal_pay_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    lines = task.item_logs.get(key, []) if key else task.logs
    return {"ok": True, "key": key, "lines": lines}


class SubmitPayPalPayInputReq(BaseModel):
    key: str = Field(..., description="任务对应账号/Token key")
    value: str = Field(..., description="6位短信验证码或新手机号")


@app.post("/api/paypal-pay/task/{task_id}/input")
def api_paypal_pay_task_input(task_id: str, req: SubmitPayPalPayInputReq):
    """向等待中的 PayPal 协议任务提交短信验证码或新手机号。"""
    from . import paypal_pay_engine

    task = paypal_pay_engine.get_paypal_pay_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    try:
        task.submit_input(req.key, req.value)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))


# ──────────────────────── 账号安全加固任务台 (批量补密码 & 批量补2FA) ────────────────────────


class StartSecurityTaskReq(BaseModel):
    action: str = Field("password", description="任务类型: password / 2fa")
    emails: list[str] = Field(..., description="要处理的账号邮箱列表")
    workers: int = Field(3, ge=1, le=10, description="并发 worker 数")
    timeout: int = Field(60, ge=10, le=180, description="单账号超时秒数")
    official_reset: bool = Field(True, description="是否走官方服务端全自动生效 (密码模式)")
    proxy: Optional[str] = Field("", description="指定代理")
    proxies: Optional[str] = Field("", description="代理池")
    proxy_country: Optional[str] = Field("", description="代理目标国家代码 (如 BR, JP, US, RANDOM_HOT 等)")


def _safe_get_sec_task(q, timeout: float = 2.0):
    try:
        return q.get(timeout=timeout)
    except Exception as e:
        if type(e).__name__ == "Empty":
            return "__TIMEOUT__"
        return None


@app.post("/api/registered/security_task/start")
def api_security_task_start(req: StartSecurityTaskReq):
    """启动安全加固批量任务 (批量补设密码 / 批量补绑 2FA)。"""
    from . import security_task_service

    emails = [e.strip().lower() for e in (req.emails or []) if e and e.strip()]
    if not emails:
        raise HTTPException(400, "请提供目标账号邮箱列表")

    proxies = []
    if req.proxies and req.proxies.strip():
        proxies = [p.strip() for p in req.proxies.split("\n") if p.strip()]
    elif req.proxy and req.proxy.strip():
        proxies = [req.proxy.strip()]

    cfg = {
        "workers": req.workers,
        "timeout": req.timeout,
        "official_reset": req.official_reset,
        "proxies": proxies,
        "proxy_country": req.proxy_country or "",
    }

    try:
        res = security_task_service.start_security_task(action=req.action, emails=emails, config=cfg)
        return res
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/registered/security_task/{task_id}/stop")
def api_security_task_stop(task_id: str):
    """停止指定的安全加固任务。"""
    from . import security_task_service
    try:
        return security_task_service.stop_security_task(task_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/registered/security_task/{task_id}/retry")
def api_security_task_retry(task_id: str, req: Optional[dict] = None):
    """重试失败账号或指定账号。"""
    from . import security_task_service
    emails = req.get("emails") if isinstance(req, dict) else None
    try:
        return security_task_service.retry_security_task(task_id, emails=emails)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/registered/security_task/{task_id}/stream")
async def api_security_task_stream(task_id: str, request: Request):
    """SSE 实时推送安全加固任务进度与日志。"""
    from . import security_task_service
    task = security_task_service.get_security_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")

    async def event_gen():
        loop = asyncio.get_event_loop()
        init_data = {
            "task_id": task_id,
            "action": task.action,
            "total": len(task.items),
            "items": task.items,
            "stats": task.stats,
        }
        yield f"event: init\ndata: {json.dumps(init_data, ensure_ascii=False)}\n\n"
        while True:
            if await request.is_disconnected():
                break
            msg = await loop.run_in_executor(None, _safe_get_sec_task, task.queue, 2.0)
            if msg is None:
                break
            if msg == "__TIMEOUT__":
                yield ": keep-alive\n\n"
                continue
            kind = msg.get("kind") or "progress"
            data_str = json.dumps(msg, ensure_ascii=False)
            if kind == "done":
                yield f"event: done\ndata: {data_str}\n\n"
                break
            elif kind == "log":
                yield f"event: log\ndata: {data_str}\n\n"
            else:
                yield f"event: progress\ndata: {data_str}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/registered/security_task/{task_id}/log")
def api_security_task_log(task_id: str, email: str = ""):
    """获取安全加固任务中单个账号的完整日志。"""
    from . import security_task_service
    task = security_task_service.get_security_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")
    email = email.strip().lower()
    item = task.items.get(email)
    if not item:
        return {"ok": True, "email": email, "lines": ["未找到该账号的执行日志"]}
    return {"ok": True, "email": email, "lines": item.get("logs", []), "status": item.get("status")}


# ──────────────────────── 账号活跃保温与保鲜 (Account Warming Daemon) ────────────────────────


class StartWarmingReq(BaseModel):
    emails: list[str] = Field(..., description="要保温的账号邮箱列表")
    proxies: str = Field("", description="代理池（每行一个）")
    proxy: str = Field("", description="单个代理")
    proxy_country: str = Field("", description="目标代理国家")
    workers: int = Field(5, ge=1, le=20, description="并发 worker 数")


@app.post("/api/registered/warm/start")
def api_warm_start(req: StartWarmingReq):
    """启动 GPT 账号批量保温与保鲜任务。"""
    from . import account_warmer
    try:
        task_id = account_warmer.start_warming_task(
            emails=req.emails,
            config={
                "proxies": req.proxies,
                "proxy": req.proxy,
                "proxy_country": req.proxy_country,
                "workers": req.workers,
            },
        )
        return {"ok": True, "task_id": task_id, "total": len(req.emails)}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/registered/warm/{task_id}/stop")
def api_warm_stop(task_id: str):
    """停止指定的保温任务。"""
    from . import account_warmer
    ok = account_warmer.stop_warming_task(task_id)
    return {"ok": ok, "task_id": task_id}


@app.get("/api/registered/warm/{task_id}/stream")
async def api_warm_stream(task_id: str, request: Request):
    """SSE 实时推送账号保温进度与日志。"""
    from . import account_warmer
    task = account_warmer.get_warming_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")

    async def event_gen():
        loop = asyncio.get_event_loop()
        init_data = {
            "task_id": task_id,
            "total": len(task.items),
            "items": task.items,
            "stats": task.stats,
        }
        yield f"event: init\ndata: {json.dumps(init_data, ensure_ascii=False)}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await loop.run_in_executor(None, _safe_get_sec_task, task.log_queue, 2.0)
            except Exception:
                msg = None
            if msg is None:
                break
            if msg == "__TIMEOUT__":
                yield ": keep-alive\n\n"
                continue
            m_type = msg.get("type") or "progress"
            data_str = json.dumps(msg, ensure_ascii=False)
            if m_type == "end":
                yield f"event: done\ndata: {data_str}\n\n"
                break
            elif m_type == "log":
                yield f"event: log\ndata: {data_str}\n\n"
            else:
                yield f"event: progress\ndata: {data_str}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/registered/warm/{task_id}/log")
def api_warm_log(task_id: str, email: str = ""):
    """获取保温任务中单个账号的完整日志。"""
    from . import account_warmer
    task = account_warmer.get_warming_task(task_id)
    if not task:
        raise HTTPException(404, "任务未找到")
    email = email.strip().lower()
    item = task.items.get(email)
    logs = task.email_logs.get(email, [])
    return {"ok": True, "email": email, "lines": logs, "status": item.get("status") if item else "unknown"}


# ──────────────────────── PoW 预计算池与代理健康监控 API ────────────────────────


@app.get("/api/sentinel_pool/stats")
def api_sentinel_pool_stats():
    """获取 PoW Sentinel Token 预计算池状态。"""
    from .sentinel_pool import get_sentinel_pool
    return {"ok": True, **get_sentinel_pool().get_stats()}


class SentinelPoolConfigReq(BaseModel):
    enabled: Optional[bool] = None
    target_size: Optional[int] = None


@app.post("/api/sentinel_pool/config")
def api_sentinel_pool_config(req: SentinelPoolConfigReq):
    """动态调整 Sentinel 预计算池配置。"""
    from .sentinel_pool import get_sentinel_pool
    pool = get_sentinel_pool()
    if req.enabled is not None:
        pool.set_enabled(req.enabled)
    if req.target_size is not None:
        pool.set_target_size(req.target_size)
    return {"ok": True, **pool.get_stats()}


@app.get("/api/proxy_health/summary")
def api_proxy_health_summary():
    """获取代理出口 IP 智能评级、失败记忆与冷冻期全景报告。"""
    from .proxy_health import get_proxy_health_manager
    return {"ok": True, **get_proxy_health_manager().get_summary()}


# ──────────────────────── auto-loop ────────────────────────


class AutoLoopStartReq(BaseModel):
    """跟 RegisterReq 复用同样的字段，auto-loop 内部传给每个 run。"""
    model_config = {"extra": "allow"}

    mail_source: Optional[str] = Field(None, description="指定邮箱渠道: cf_temp / outlook / icloud_relay 等")
    want_access_token: bool = True
    want_session_token: bool = True
    want_refresh_token: bool = True
    proxy: str = ""              # 单代理（concurrency=1 + 无代理池时用）
    proxy_pool: str = ""         # 多代理池（每行一个）；优先于 proxy
    proxy_country: str = ""      # 目标代理国家，如 BR, DE, GB 等
    concurrency: int = 1         # 并发 worker 数（1-20）
    otp_timeout: int = 10
    allow_existing_login: bool = True
    cool_down_seconds: float = 3.0  # 每个 worker 跑完后冷却（防风控）
    target_count: int = 0        # 目标成功数（0=不限量，达标自动停止）
    circuit_break_threshold: int = 3  # 连续网络错误暂停阈值（0=关闭）
    want_password: bool = True   # 是否自动设置强登录密码（默认开）
    want_2fa: bool = False


@app.post("/api/auto/start")
def api_auto_start(req: AutoLoopStartReq):
    res = AUTO_LOOP.start(req.model_dump())
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "启动失败"))
    return res


@app.post("/api/auto/pause")
def api_auto_pause():
    res = AUTO_LOOP.pause()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "暂停失败"))
    return res


@app.post("/api/auto/resume")
def api_auto_resume():
    res = AUTO_LOOP.resume()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "恢复失败"))
    return res


@app.post("/api/auto/stop")
def api_auto_stop():
    res = AUTO_LOOP.stop()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "停止失败"))
    return res


@app.get("/api/auto/status")
def api_auto_status():
    return {"ok": True, **AUTO_LOOP.status()}


@app.get("/api/auto/stream")
async def api_auto_stream(request: Request):
    """SSE 推送 auto-loop 状态变化 + run_started / run_finished 事件。"""
    q = AUTO_LOOP.subscribe()

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            while True:
                if await request.is_disconnected():
                    break
                # 阻塞拿消息，但每 30s 心跳
                try:
                    msg = await loop.run_in_executor(None, lambda: q.get(timeout=30))
                except Exception:
                    yield ": heartbeat\n\n"
                    continue
                if msg is None:
                    break
                kind = msg.get("kind", "state")
                data = msg.get("data", {})
                yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            AUTO_LOOP.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ──────────────────────── 静态资源 ────────────────────────


@app.get("/")
def root():
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


class NoCacheStaticFiles(StaticFiles):
    """确保前端重新打包后，浏览器不缓存旧 JS/CSS 代码，即时加载最新构建产物。"""

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webui.app:app", host="127.0.0.1", port=8765, reload=False)
