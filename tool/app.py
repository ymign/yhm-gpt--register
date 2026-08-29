"""app.py — GPT 账号密码2FA管理与Sub2API转换助手主服务
=========================================================
开箱即用独立 Web 服务，支持 SQLite 持久化、账号列表管理、批量/单个 OAuth 授权、
OpenAI 官方免邮箱 Sudo 改密落库、实时日志弹窗、代理池配置保存与 Sub2API JSON 多格式导出。
"""
from __future__ import annotations

import logging
import os
import random
import socket
import string
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# 保证能直接从当前目录或父目录导入
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from core import db
from core.exporter import (
    render_lines_at_only,
    render_lines_email_at,
    render_lines_email_pwd,
    render_lines_email_pwd_2fa,
    render_lines_rt_only,
    render_session_json,
    render_sub2api_json,
)
from core.task_engine import ENGINE, parse_account_line
from core.totp import get_totp_remaining_seconds, get_totp_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("server")

app = FastAPI(title="GPT 账号密码2FA管理与Sub2API转换助手", docs_url=None, redoc_url=None)

STATIC_DIR = CURRENT_DIR / "static"


def _generate_random_password(length: int = 16) -> str:
    """生成符合 OpenAI 强度的随机密码。"""
    lower = random.choice(string.ascii_lowercase)
    upper = random.choice(string.ascii_uppercase)
    digit = random.choice(string.digits)
    punct = random.choice("@#$%^&*!_+=")
    all_chars = string.ascii_letters + string.digits + "@#$%^&*!_+="
    rest = [random.choice(all_chars) for _ in range(length - 4)]
    combo = list(lower + upper + digit + punct + "".join(rest))
    random.shuffle(combo)
    return "".join(combo)


# ──────────────────────── Pydantic 请求模型 ────────────────────────


class ImportAccountsReq(BaseModel):
    text: str = Field(..., description="批量账号文本（每行一个：邮箱----密码----2FA）")


class StartTaskReq(BaseModel):
    emails: Optional[list[str]] = Field(None, description="指定执行的邮箱列表（为空则执行 text 中的文本）")
    text: Optional[str] = Field("", description="批量账号文本")
    new_password_mode: str = Field("keep", description="keep/random/custom/prefix/inline")
    custom_password: str = Field("", description="自定义统一新密码")
    password_prefix: str = Field("Gpt@", description="新密码前缀")
    concurrency: int = Field(5, ge=1, le=50, description="并发线程数")
    cooldown: float = Field(1.0, ge=0, le=60, description="单线程冷却时间（秒）")
    proxy: str = Field("", description="代理池（多行）")
    timeout: int = Field(55, ge=10, le=180, description="单号超时秒数")


class UpdatePasswordReq(BaseModel):
    email: Optional[str] = Field(None, description="单个邮箱")
    emails: Optional[list[str]] = Field(None, description="批量邮箱列表")
    mode: str = Field("custom", description="custom / random / prefix")
    new_password: Optional[str] = Field("", description="单个新密码")
    custom_password: Optional[str] = Field("", description="批量统一新密码")
    password_prefix: Optional[str] = Field("Gpt@", description="新密码前缀")


class DeleteAccountsReq(BaseModel):
    emails: list[str] = Field(..., description="待删除邮箱列表")


class SettingsReq(BaseModel):
    settings: dict[str, Any] = Field(..., description="系统配置字典")


class ExportReq(BaseModel):
    format: str = Field(..., description="sub2api / session / email_pwd_2fa / email_pwd / email_at / at / rt / failed")
    emails: Optional[list[str]] = Field(None, description="指定导出的邮箱列表（为空则按当前筛选导出）")
    only_success: bool = Field(True, description="是否仅导出成功账号")


class CalcTotpReq(BaseModel):
    secret: str = Field(..., description="2FA Base32 密钥")


# ──────────────────────── 账号管理 REST API ────────────────────────


@app.get("/api/accounts")
def api_list_accounts(
    keyword: str = Query("", description="搜索关键字"),
    status: str = Query("", description="状态筛选"),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """查询持久化数据库中的账号列表。"""
    accounts, total = db.list_accounts(keyword=keyword, status=status, limit=limit, offset=offset)

    # 动态为每个账号计算当前 2FA 动态码
    for acc in accounts:
        sec = acc.get("totp_secret")
        if sec:
            acc["current_totp"] = get_totp_token(sec)
        else:
            acc["current_totp"] = ""

    return {
        "ok": True,
        "data": {
            "total": total,
            "items": accounts,
            "totp_remaining_seconds": get_totp_remaining_seconds(),
        },
    }


@app.get("/api/accounts/detail")
def api_account_detail(email: str = Query(...)):
    """获取单个账号的完整信息及详细时序日志。"""
    acc = db.get_account_by_email(email)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if acc.get("totp_secret"):
        acc["current_totp"] = get_totp_token(acc["totp_secret"])
    return {"ok": True, "data": acc}


@app.post("/api/accounts/import")
def api_import_accounts(req: ImportAccountsReq):
    """仅导入账号文本入库（不立即执行）。"""
    lines = [l.strip() for l in req.text.splitlines() if l.strip()]
    parsed_list = []
    seen = set()
    for line in lines:
        item = parse_account_line(line)
        if item and item["email"] not in seen:
            parsed_list.append(item)
            seen.add(item["email"])

    if not parsed_list:
        raise HTTPException(400, "未解析到有效的账号（需包含邮箱与密码）")

    inserted, updated = db.import_accounts(parsed_list)
    return {
        "ok": True,
        "data": {
            "total_parsed": len(parsed_list),
            "inserted": inserted,
            "updated": updated,
        },
    }


@app.post("/api/accounts/update_password")
def api_update_password(req: UpdatePasswordReq):
    """单账号或批量启动 OpenAI 官方免邮箱改密流程（Sudo 重认证改密并在官方正式生效）。"""
    proxy_pool = db.get_setting("proxy_pool", "")
    timeout = int(db.get_setting("timeout") or 55)

    if req.email:
        # 单账号官方改密
        email = req.email.strip().lower()
        new_pwd = (req.new_password or "").strip()
        if not new_pwd:
            if req.mode == "random":
                new_pwd = _generate_random_password(16)
            elif req.mode == "prefix":
                new_pwd = f"{req.password_prefix or 'Gpt@'}{_generate_random_password(10)}"
            else:
                raise HTTPException(400, "新密码不能为空")

        # 启动官方改密 Worker 任务
        ENGINE.start_selected(
            emails=[email],
            options={
                "new_password_mode": "custom",
                "custom_password": new_pwd,
                "concurrency": 1,
                "timeout": timeout,
                "proxy_pool": proxy_pool,
            },
        )
        return {
            "ok": True,
            "message": f"已为 {email} 启动官方免邮箱改密流程（目标新密码: {new_pwd}），请在列表中查看实时进度与日志",
            "new_password": new_pwd,
        }

    elif req.emails:
        # 批量官方改密
        concurrency = int(db.get_setting("concurrency") or 5)
        ENGINE.start_selected(
            emails=req.emails,
            options={
                "new_password_mode": req.mode,
                "custom_password": req.custom_password,
                "password_prefix": req.password_prefix,
                "concurrency": concurrency,
                "timeout": timeout,
                "proxy_pool": proxy_pool,
            },
        )
        return {
            "ok": True,
            "message": f"已为 {len(req.emails)} 个账号启动批量官方免邮箱改密任务！",
        }
    else:
        raise HTTPException(400, "请指定待修改密码的账号邮箱")


@app.get("/api/accounts/generate_password")
def api_gen_random_password(prefix: str = "", length: int = 16):
    """辅助生成强随机密码。"""
    if prefix:
        pwd = f"{prefix}{_generate_random_password(10)}"
    else:
        pwd = _generate_random_password(length)
    return {"ok": True, "password": pwd}


@app.post("/api/accounts/delete")
def api_delete_accounts(req: DeleteAccountsReq):
    """从数据库中批量删除账号。"""
    cnt = db.delete_accounts_by_emails(req.emails)
    return {"ok": True, "data": {"deleted": cnt}}


@app.post("/api/accounts/clear")
def api_clear_accounts():
    """清空全部账号数据。"""
    cnt = db.clear_all_accounts()
    return {"ok": True, "data": {"deleted": cnt}}


# ──────────────────────── 系统配置与代理池 ────────────────────────


@app.get("/api/settings")
def api_get_settings():
    """获取已持久化的代理池与默认配置。"""
    return {"ok": True, "data": db.get_all_settings()}


@app.post("/api/settings")
def api_save_settings(req: SettingsReq):
    """保存系统配置与代理池。"""
    db.save_settings_dict(req.settings)
    return {"ok": True, "message": "配置已成功保存到数据库"}


# ──────────────────────── 批量任务调度 ────────────────────────


@app.get("/api/task/status")
def api_task_status():
    """获取当前并发引擎状态与进度。"""
    return {"ok": True, "data": ENGINE.get_status()}


@app.post("/api/task/start")
def api_task_start(req: StartTaskReq):
    """启动批量任务（支持选定已有账号或导入新文本）。"""
    options = {
        "new_password_mode": req.new_password_mode,
        "custom_password": req.custom_password,
        "password_prefix": req.password_prefix,
        "concurrency": req.concurrency,
        "cooldown": req.cooldown,
        "proxy_pool": req.proxy,
        "timeout": req.timeout,
    }
    try:
        if req.emails:
            data = ENGINE.start_selected(emails=req.emails, options=options)
        elif req.text:
            data = ENGINE.start_from_text(text=req.text, options=options)
        else:
            raise ValueError("请指定待执行的账号列表或输入账号文本")

        return {"ok": True, "data": data}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/task/pause")
def api_task_pause():
    ENGINE.pause()
    return {"ok": True, "data": ENGINE.get_status()}


@app.post("/api/task/resume")
def api_task_resume():
    ENGINE.resume()
    return {"ok": True, "data": ENGINE.get_status()}


@app.post("/api/task/stop")
def api_task_stop():
    ENGINE.stop()
    return {"ok": True, "data": ENGINE.get_status()}


# ──────────────────────── 2FA 辅助计算与多格式导出 ────────────────────────


@app.post("/api/totp/calc")
def api_calc_totp(req: CalcTotpReq):
    """辅助工具：计算单条 2FA 动态码与剩余有效秒数。"""
    code = get_totp_token(req.secret)
    rem = get_totp_remaining_seconds()
    if not code:
        raise HTTPException(400, "无效的 2FA Secret 密钥")
    return {"ok": True, "code": code, "remaining_seconds": rem}


@app.post("/api/export")
def api_export(req: ExportReq):
    """从 SQLite 数据库中按指定格式渲染并下载导出文件。"""
    target_accounts = []
    if req.emails:
        for em in req.emails:
            acc = db.get_account_by_email(em)
            if acc:
                target_accounts.append(acc)
    else:
        accounts, _ = db.list_accounts(limit=5000)
        target_accounts = accounts

    if req.only_success and req.format.lower() not in ("failed", "email_pwd_2fa", "email_pwd"):
        items = [a for a in target_accounts if (a.get("access_token") or a.get("refresh_token") or a.get("status") == "success")]
    else:
        items = target_accounts

    fmt = req.format.lower()
    filename = "export.txt"
    content = ""
    media_type = "text/plain; charset=utf-8"

    if fmt == "sub2api":
        filename = "sub2api_accounts.json"
        content = render_sub2api_json(items)
        media_type = "application/json; charset=utf-8"
    elif fmt == "session":
        filename = "chatgpt_sessions.json"
        content = render_session_json(items)
        media_type = "application/json; charset=utf-8"
    elif fmt == "email_pwd_2fa":
        filename = "账号密码2FA_已改密.txt"
        content = render_lines_email_pwd_2fa(items)
    elif fmt == "email_pwd":
        filename = "账号密码_已改密.txt"
        content = render_lines_email_pwd(items)
    elif fmt == "email_at":
        filename = "邮箱AT.txt"
        content = render_lines_email_at(items)
    elif fmt == "at":
        filename = "AT.txt"
        content = render_lines_at_only(items)
    elif fmt == "rt":
        filename = "RT.txt"
        content = render_lines_rt_only(items)
    elif fmt == "failed":
        filename = "失败账号列表.txt"
        failed_list = [a for a in target_accounts if a.get("status") == "failed" or a.get("error")]
        lines = []
        for f in failed_list:
            lines.append(f"{f.get('email')}----{f.get('password')}----{f.get('totp_secret')}  # 失败: {f.get('error')}")
        content = "\n".join(lines)
    else:
        raise HTTPException(400, f"未知导出格式: {fmt}")

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"},
    )


# ──────────────────────── 静态页面与入口 ────────────────────────


@app.get("/", response_class=HTMLResponse)
def index_page():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return index_file.read_text(encoding="utf-8")
    return "<h3>Web 页面未找到，请检查 static/index.html 是否存在</h3>"


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def find_free_port(start_port: int = 8899) -> int:
    """寻找本地可用端口。"""
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def main():
    import uvicorn

    port = find_free_port(8899)
    url = f"http://127.0.0.1:{port}"
    print("=" * 66)
    print(" 🚀 GPT 账号密码2FA管理与Sub2API转换助手正在启动...")
    print(f" 🌐 浏览器访问地址: {url}")
    print("=" * 66)

    def _open_browser():
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
