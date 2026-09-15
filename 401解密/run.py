"""401 解密独立服务。双击 启动.bat 或: python run.py"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fix401.app")

app = FastAPI(title="401解密", version="1.0")
STATIC = ROOT / "static"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


class ProxyReq(BaseModel):
    proxy: str = ""


class StartReq(BaseModel):
    accounts: str = Field("", description="邮箱----密码----2FA 多行，或 JSON")
    proxy: str = ""
    workers: int = 2
    timeout: int = 45
    force_full_login: bool = True


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/proxy/check")
def api_proxy_check(req: ProxyReq):
    try:
        return engine.check_user_proxy(req.proxy)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/start")
def api_start(req: StartReq):
    try:
        accounts = engine.parse_account_text(req.accounts)
    except Exception as e:
        raise HTTPException(400, f"账号文本解析失败: {e}")
    if not accounts:
        raise HTTPException(400, "请粘贴 邮箱----密码----2FA")
    try:
        return engine.start(
            accounts,
            proxy_text=req.proxy,
            workers=req.workers,
            timeout=req.timeout,
            force_full_login=req.force_full_login,
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/snapshot/{task_id}")
def api_snapshot(task_id: str):
    task = engine.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task.snapshot()


@app.get("/api/log/{task_id}/{email}")
def api_log(task_id: str, email: str):
    task = engine.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    it = task.items.get(email.strip().lower())
    return {"email": email, "lines": (it or {}).get("logs") or []}


@app.post("/api/stop/{task_id}")
def api_stop(task_id: str):
    try:
        return engine.stop(task_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/stream/{task_id}")
def api_stream(task_id: str):
    task = engine.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    def gen():
        yield f"data: {json.dumps({'kind': 'hello', 'task_id': task_id}, ensure_ascii=False)}\n\n"
        while True:
            try:
                ev = task.queue.get(timeout=1.0)
            except Exception:
                if task.finished_at:
                    break
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev.get("kind") == "end":
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/download/{kind}/{task_id}")
def api_download(kind: str, task_id: str):
    if kind not in ("cpa", "sub2"):
        raise HTTPException(400, "kind 只能是 cpa 或 sub2")
    docs = engine.collect_exports(task_id, "cpa" if kind == "cpa" else "sub2")
    if not docs:
        raise HTTPException(404, "还没有成功的导出")
    body = json.dumps(docs if len(docs) > 1 else docs[0], ensure_ascii=False, indent=2)
    name = f"{kind}-{len(docs)}.json"
    from fastapi.responses import Response
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def main():
    parser = argparse.ArgumentParser(description="401 解密")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8891)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    import uvicorn
    logger.info("401解密 打开 %s", url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
