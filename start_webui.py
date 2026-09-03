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
    """使用原生底层 socket 探测目标端口是否真正处于占用/不可绑定状态。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.3)
        s.bind((host, port))
        s.close()
        return False
    except OSError:
        return True


def get_pids_by_port(port: int) -> set[int]:
    """通过原生 netstat 秒级精准获取占用指定本地端口的服务进程 PID（耗时<50ms，杜绝一切外部卡死）。"""
    pids = set()
    current_pid = os.getpid()
    target_token = f":{port}"

    try:
        res = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=3,
        )
        for line in res.stdout.splitlines():
            line_str = line.strip()
            # 必须是 TCP 或 UDP 行
            if not line_str.startswith("TCP") and not line_str.startswith("UDP"):
                continue
            if target_token not in line_str:
                continue
            parts = line_str.split()
            # 协议 本地地址 远程地址 [状态] PID
            if len(parts) >= 4 and parts[-1].isdigit():
                local_addr = parts[1]
                # 只有作为服务端监听或绑定的本地地址才属于 WebUI 服务（排除作为客户端发起连接的浏览器等应用）
                if local_addr.endswith(target_token):
                    p = int(parts[-1])
                    if p > 0 and p != current_pid:
                        pids.add(p)
    except Exception:
        pass

    return pids


def kill_process_tree(pid: int) -> bool:
    """结合底层系统 API (os.kill) 与 taskkill /F /T 双保险瞬时终结进程及其子进程。"""
    if pid <= 0 or pid == os.getpid():
        return False
    # 1. 优先调用系统底层 API 直接终止
    try:
        os.kill(pid, getattr(signal, "SIGTERM", 15))
    except Exception:
        pass
    # 2. 调用 Windows taskkill 连带清理子孙进程
    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            pass
    return True


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

    # 2. 毫秒级探测真正占用本地服务端口的进程 PID
    detected_pids = get_pids_by_port(port)
    pids_to_kill.update(detected_pids)

    # 3. 统一强制终结
    for pid in pids_to_kill:
        if kill_process_tree(pid):
            killed += 1
            print(f"  [-] 已终结服务进程 PID: {pid}")

    # 4. 关键：等待 Windows 操作系统内核完全释放套接字 (彻底根除异步释放导致的 WinError 10048)
    if is_port_in_use(port):
        for _ in range(10):  # 最多轮询等待 2 秒
            time.sleep(0.2)
            if not is_port_in_use(port):
                break
        else:
            # 兜底：如果端口依然被占，再次尝试通过 netstat 精准查杀一次
            more_pids = get_pids_by_port(port)
            for p in more_pids:
                kill_process_tree(p)
                killed += 1
                print(f"  [-] 兜底终结残留进程 PID: {p}")
            time.sleep(0.3)

    if not is_port_in_use(port):
        if killed > 0:
            print(f"  [OK] WebUI 服务已成功关闭（已释放端口 {port}）！\n")
        else:
            print(f"  [OK] 端口 {port} 当前空闲可用。\n")
        return True
    else:
        print(f"  [!] 警告：端口 {port} 仍处于内核释放倒计时或被其他程序占用，请稍候再试。\n")
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
        print(f"[*] 检测到端口 {args.port} 仍有残留占用，正在进行最终查杀与释放...")
        for p in get_pids_by_port(args.port):
            kill_process_tree(p)
        for _ in range(10):
            time.sleep(0.2)
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
