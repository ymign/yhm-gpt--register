#!/usr/bin/env python3
"""WebUI 一键启动与关闭管理脚本：装依赖 → 跑 uvicorn / 查杀残留。

用法：
    python start_webui.py             # 默认 127.0.0.1:8765
    python start_webui.py --port 9000 # 自定义端口
    python start_webui.py --stop      # 关闭 8765 端口服务
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

# Windows 控制台 GBK 编码兼容：强制 UTF-8 输出
if sys.platform.startswith("win"):
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent


def stop_webui(port: int = 8765) -> bool:
    """彻底查杀指定端口的服务进程。"""
    print(f"\n[*] 正在检查并关闭 WebUI 服务 (端口 {port})...")
    killed = 0
    pids_to_kill = set()

    # 1. 优先读取记录的 PID 文件
    pid_file = ROOT / "webui.pid"
    if pid_file.exists():
        try:
            pid = pid_file.read_text(encoding="utf-8").strip()
            if pid.isdigit() and int(pid) > 0:
                pids_to_kill.add(int(pid))
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass

    # 2. 从 netstat 扫描端口占用（兼容量英文 LISTENING 与中文 正在侦听）
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            errors="ignore",
        ).stdout
        target_token = f":{port}"
        for line in out.splitlines():
            line_upper = line.upper()
            if target_token in line and ("LISTENING" in line_upper or "正在侦听" in line):
                parts = line.strip().split()
                if len(parts) >= 5:
                    pid_str = parts[-1]
                    if pid_str.isdigit() and int(pid_str) > 0:
                        pids_to_kill.add(int(pid_str))
    except Exception as exc:
        print(f"  [!] netstat 检查异常: {exc}")

    # 3. 统一强制树状查杀
    current_pid = os.getpid()
    for pid in pids_to_kill:
        if pid == current_pid:
            continue
        try:
            res = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0 or "SUCCESS" in (res.stdout or "").upper():
                killed += 1
                print(f"  [-] 已终结服务进程 PID: {pid}")
        except Exception:
            pass

    if killed > 0:
        print(f"  [OK] WebUI 服务已成功关闭！\n")
        return True
    else:
        print(f"  [i] 未检测到运行中的 WebUI 服务（端口 {port} 未被占用）。\n")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="监听端口 (默认 8765)")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--reload", action="store_true", help="开发模式 (代码改动自动重启)")
    ap.add_argument("--stop", action="store_true", help="关闭当前运行的 WebUI 服务")
    args = ap.parse_args()

    if args.stop:
        stop_webui(args.port)
        return

    # 启动前先清理一次可能残留的同端口旧服务
    stop_webui(args.port)

    # 确保依赖装了
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("[!] 缺少依赖，正在安装 fastapi / uvicorn ...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "fastapi", "uvicorn[standard]", "pydantic>=2",
        ])
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401

    sys.path.insert(0, str(ROOT))
    import uvicorn

    url = f"http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{args.port}/"
    print(f"===================================================")
    print(f"  [*] 团子喵 WebUI 控制台已启动")
    print(f"  [*] 浏览器访问: {url}")
    print(f"===================================================\n")

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    pid_file = ROOT / "webui.pid"
    try:
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass

    try:
        uvicorn.run(
            "webui.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass

    print(f"\n===================================================")
    print(f"  [!] WebUI 服务已停止运行。")
    print(f"===================================================\n")


if __name__ == "__main__":
    main()
