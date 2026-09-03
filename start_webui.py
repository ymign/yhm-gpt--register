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
import signal
import socket
import subprocess
import sys
import time
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


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """使用带 SO_REUSEADDR 的原生底层 socket 探测目标端口是否真正处于不可绑定状态。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(0.3)
        s.bind((host, port))
        s.close()
        return False
    except OSError:
        return True


def get_listening_pids(port: int) -> set[int]:
    """通过原生 netstat 秒级精准获取真正正在监听该端口的服务端进程 PID（自动忽略已断开的 TIME_WAIT 连接）。"""
    pids = set()
    current_pid = os.getpid()
    target_token = f":{port}"

    try:
        res = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=3,
        )
        for line in res.stdout.splitlines():
            line_str = line.strip()
            line_upper = line_str.upper()
            if not line_str.startswith("TCP"):
                continue
            if target_token not in line_str:
                continue
            # 必须处于真正监听状态，彻底杜绝把已经断开处于 TIME_WAIT/CLOSE_WAIT 的连接误判为服务进程
            if "LISTENING" not in line_upper and "正在侦听" not in line_str:
                continue
            parts = line_str.split()
            if len(parts) >= 4 and parts[-1].isdigit():
                local_addr = parts[1]
                if local_addr.endswith(target_token):
                    p = int(parts[-1])
                    if p > 0 and p != current_pid:
                        pids.add(p)
    except Exception:
        pass

    return pids


def kill_process_tree(pid: int) -> bool:
    """终结指定进程及其子进程树。"""
    if pid <= 0 or pid == os.getpid():
        return False
    if sys.platform.startswith("win"):
        try:
            res = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return res.returncode == 0 or "SUCCESS" in (res.stdout or "").upper()
        except Exception:
            return False
    else:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", 9))
            return True
        except Exception:
            return False


def stop_webui(port: int = 8765) -> bool:
    """彻底查杀指定端口的服务进程，并同步等待操作系统内核完全释放套接字。"""
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

    # 2. 毫秒级探测真正正在监听本地服务端口的进程 PID
    listening_pids = get_listening_pids(port)
    pids_to_kill.update(listening_pids)

    # 3. 统一强制终结
    for pid in pids_to_kill:
        if kill_process_tree(pid):
            killed += 1
            print(f"  [-] 已终结服务进程 PID: {pid}")

    # 4. 等待 Windows 操作系统内核释放套接字
    if is_port_in_use(port):
        for _ in range(8):
            time.sleep(0.15)
            if not is_port_in_use(port):
                break

    # 5. 校验结果：只要已无任何监听进程且端口可复用，即代表成功关闭
    remaining_listeners = get_listening_pids(port)
    if not remaining_listeners and not is_port_in_use(port):
        if killed > 0:
            print(f"  [OK] WebUI 服务已成功关闭（端口 {port} 已释放）！\n")
        else:
            print(f"  [OK] WebUI 服务未运行，端口 {port} 空闲可用。\n")
        return True
    else:
        if remaining_listeners:
            for p in remaining_listeners:
                kill_process_tree(p)
            print(f"  [OK] WebUI 服务已强制关闭（终结残留 PID: {list(remaining_listeners)}）！\n")
            return True
        print(f"  [!] 警告：端口 {port} 仍被其他程序占用，请检查系统。\n")
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

    # 启动前先清理一次可能残留的同端口旧服务并确保端口完全就绪
    stop_webui(args.port)
    if is_port_in_use(args.port, args.host):
        print(f"[*] 检测到端口 {args.port} 仍有残留监听，正在进行最终查杀与释放...")
        for p in get_listening_pids(args.port):
            kill_process_tree(p)
        for _ in range(8):
            time.sleep(0.15)
            if not is_port_in_use(args.port, args.host):
                break
        else:
            print(f"\n[!] 错误：端口 {args.port} 被其他程序占用且无法释放。")
            print(f"    请检查是否有其他软件占用了该端口，或尝试指定其他端口启动：")
            print(f"    python start_webui.py --port 8766\n")
            return

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
