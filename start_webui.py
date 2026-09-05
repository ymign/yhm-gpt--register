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
_WEBUI_CMDLINE_MARKERS = (
    "start_webui.py",
    "webui.app:app",
    "webui.app",
)


def _self_pids() -> set[int]:
    pids = {os.getpid()}
    try:
        ppid = os.getppid()
        if ppid > 0:
            pids.add(ppid)
    except Exception:
        pass
    return pids


def is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    """探测是否有进程在该端口上真正接受连接（比 SO_REUSEADDR bind 探测更准）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.4)
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """端口是否仍被占用：有监听进程，或本机无法重新 bind。"""
    if get_listening_pids(port) or is_port_listening(port, host):
        return True
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Windows 上 SO_REUSEADDR 允许绑到仍在用的端口，探测时不要开它
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        try:
            s.close()
        except Exception:
            pass


def get_listening_pids(port: int) -> set[int]:
    """获取真正正在监听该端口的 PID（忽略 TIME_WAIT）。"""
    pids: set[int] = set()
    skip = _self_pids()
    target_token = f":{port}"
    listen_marks = ("LISTENING", "LISTEN", "正在侦听", "侦听")

    try:
        res = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=5,
        )
        for line in res.stdout.splitlines():
            line_str = line.strip()
            if not line_str.upper().startswith("TCP"):
                continue
            if target_token not in line_str:
                continue
            if not any(m in line_str.upper() or m in line_str for m in listen_marks):
                continue
            parts = line_str.split()
            if len(parts) < 4 or not parts[-1].isdigit():
                continue
            local_addr = parts[1]
            if local_addr.endswith(target_token) or local_addr.endswith(f"]:{port}"):
                p = int(parts[-1])
                if p > 0 and p not in skip:
                    pids.add(p)
    except Exception:
        pass

    if sys.platform.startswith("win"):
        try:
            ps = (
                f"Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
                f"-ErrorAction SilentlyContinue | "
                f"Select-Object -ExpandProperty OwningProcess"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                errors="ignore",
                timeout=8,
            )
            for tok in (res.stdout or "").split():
                if tok.isdigit():
                    p = int(tok)
                    if p > 0 and p not in skip:
                        pids.add(p)
        except Exception:
            pass

    return pids


def get_webui_python_pids() -> set[int]:
    """找出命令行里带着本项目 WebUI 启动标记的 python 进程。"""
    pids: set[int] = set()
    skip = _self_pids()
    if not sys.platform.startswith("win"):
        try:
            res = subprocess.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True,
                text=True,
                errors="ignore",
                timeout=5,
            )
            for line in (res.stdout or "").splitlines()[1:]:
                parts = line.strip().split(None, 1)
                if len(parts) < 2 or not parts[0].isdigit():
                    continue
                pid = int(parts[0])
                cmd = parts[1].lower()
                if pid in skip:
                    continue
                if any(m.lower() in cmd for m in _WEBUI_CMDLINE_MARKERS):
                    pids.add(pid)
        except Exception:
            pass
        return pids

    ps = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' OR Name='pythonw.exe' OR Name='uvicorn.exe'\" "
        "-ErrorAction SilentlyContinue | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=10,
        )
        raw = (res.stdout or "").strip()
        if not raw:
            return pids
        import json
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        for row in data or []:
            pid = int(row.get("ProcessId") or 0)
            cmd = str(row.get("CommandLine") or "").lower()
            if pid <= 0 or pid in skip:
                continue
            if any(m.lower() in cmd for m in _WEBUI_CMDLINE_MARKERS):
                pids.add(pid)
    except Exception:
        pass
    return pids


def is_pid_alive(pid: int) -> bool:
    """检查指定 PID 是否仍在运行。Windows 不能用 os.kill(pid, 0)，会误判已死。"""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_QUERY_INFORMATION = 0x0400
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return int(code.value) == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def kill_process_tree(pid: int) -> bool:
    """强制结束指定进程及其子进程，并等到进程真正退出。"""
    if pid <= 0 or pid in _self_pids():
        return False

    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                errors="ignore",
                timeout=8,
            )
        except Exception:
            pass
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue",
                ],
                capture_output=True,
                text=True,
                errors="ignore",
                timeout=8,
            )
        except Exception:
            pass
    else:
        try:
            os.kill(pid, getattr(signal, "SIGTERM", 15))
        except Exception:
            pass
        time.sleep(0.2)
        if is_pid_alive(pid):
            try:
                os.kill(pid, getattr(signal, "SIGKILL", 9))
            except Exception:
                pass

    for _ in range(25):
        if not is_pid_alive(pid):
            return True
        time.sleep(0.2)
    return not is_pid_alive(pid)


def collect_webui_pids(port: int) -> set[int]:
    """汇总所有应被关闭的 WebUI 相关 PID：pid 文件 + 端口监听 + python 命令行。"""
    pids: set[int] = set()
    skip = _self_pids()
    pid_file = ROOT / "webui.pid"
    if pid_file.exists():
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                p = int(raw)
                if p > 0 and p not in skip:
                    pids.add(p)
        except Exception:
            pass
    pids.update(get_listening_pids(port))
    pids.update(get_webui_python_pids())
    return {p for p in pids if p not in skip}


def stop_webui(port: int = 8765) -> bool:
    """彻底查杀指定端口的 WebUI 进程，并等到端口可重新绑定。"""
    print(f"\n[*] 正在检查并关闭 WebUI 服务 (端口 {port})...")
    killed = 0
    last_pids: set[int] = set()

    for round_i in range(1, 6):
        pids_to_kill = collect_webui_pids(port)
        last_pids = set(pids_to_kill)
        if not pids_to_kill and not is_port_in_use(port, "127.0.0.1"):
            break

        if pids_to_kill:
            print(f"  [*] 第 {round_i} 轮结束进程: {sorted(pids_to_kill)}")
        for pid in sorted(pids_to_kill):
            print(f"  [-] 正在强制结束 PID {pid} ...")
            if kill_process_tree(pid):
                killed += 1
                print(f"  [OK] 已结束 PID {pid}")
            else:
                print(f"  [!] PID {pid} 结束失败，继续重试")

        pid_file = ROOT / "webui.pid"
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass

        time.sleep(0.4)
        if not collect_webui_pids(port) and not is_port_listening(port):
            break

    remaining = collect_webui_pids(port)
    if remaining or is_port_listening(port):
        print(f"  [!] 警告：端口 {port} 仍被占用 (PID: {sorted(remaining or last_pids)})，请检查系统。\n")
        return False

    if killed > 0:
        print(f"  [OK] WebUI 服务已关闭，端口 {port} 已释放。\n")
    else:
        print(f"  [OK] WebUI 服务未运行，端口 {port} 空闲可用。\n")
    return True


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

    # 启动前先关掉旧实例，确保可以重新占用端口
    stop_webui(args.port)
    if collect_webui_pids(args.port) or is_port_listening(args.port, args.host):
        print(f"[*] 端口 {args.port} 仍未释放，继续强制结束残留进程...")
        for _ in range(10):
            for p in collect_webui_pids(args.port):
                kill_process_tree(p)
            time.sleep(0.4)
            if not collect_webui_pids(args.port) and not is_port_listening(args.port, args.host):
                break
        leftover = collect_webui_pids(args.port)
        if leftover or is_port_listening(args.port, args.host):
            print(f"\n[!] 错误：端口 {args.port} 被占用且无法释放 (PID: {sorted(leftover)})。")
            print("    可先运行 一键关闭.bat，或以管理员身份再试；或换端口：")
            print(f"    python start_webui.py --port 8766\n")
            return
        print(f"  [OK] 端口 {args.port} 已释放，继续启动。\n")

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
    except Exception as e:
        print(f"\n[!] WebUI 启动失败: {e}")
        if "10048" in str(e) or "address already in use" in str(e).lower():
            print(f"    端口 {args.port} 冲突，请尝试指定其他端口启动：")
            print(f"    python start_webui.py --port 8766\n")
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
