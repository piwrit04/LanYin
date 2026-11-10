# -*- coding: utf-8 -*-
"""Windows bootstrap for LanYin（本地版 start.bat 和 OBS 脚本第一次安装都走这里）。"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from app.portcfg import base_url, probe, read_port  # noqa: E402  (stdlib only)

IS_WIN = os.name == "nt"
VENV_DIR = ROOT / ".venv"
VENV_PY = VENV_DIR / ("Scripts/python.exe" if IS_WIN else "bin/python")
VENV_PYW = VENV_DIR / "Scripts" / "pythonw.exe"
REQ = ROOT / "requirements.txt"
REQ_MARK = VENV_DIR / ".lanyin-requirements.sha1"
HIDDEN = "--hidden" in sys.argv
NO_WINDOW = "--no-window" in sys.argv or HIDDEN  # 隐藏模式由 OBS 脚本负责弹窗
CREATE_NO_WINDOW = 0x08000000
RUNTIME_MODULES = "import fastapi, uvicorn, httpx, yaml, websockets"


def _print(msg: str) -> None:
    if HIDDEN:
        log_path = ROOT / "data" / "startup.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(msg, flush=True)


def _child_kwargs() -> dict:
    kw: dict = {"cwd": str(ROOT)}
    if IS_WIN and HIDDEN:
        # pythonw 没有控制台；不加这个标志，pip / 服务进程会各自弹出黑窗口
        kw["creationflags"] = CREATE_NO_WINDOW
        kw["stdin"] = subprocess.DEVNULL
    return kw


def _run(cmd: list[str]) -> int:
    return subprocess.run(cmd, **_child_kwargs()).returncode


def _req_hash() -> str:
    try:
        return hashlib.sha1(REQ.read_bytes()).hexdigest()
    except OSError:
        return ""


def _deps_ok() -> bool:
    return _run([str(VENV_PY), "-c", RUNTIME_MODULES]) == 0


def ensure_env() -> bool:
    if not VENV_PY.exists():
        _print("正在创建运行环境（只需第一次）...")
        if _run([sys.executable, "-m", "venv", str(VENV_DIR)]) != 0 or not VENV_PY.exists():
            _print("创建运行环境失败。请确认安装的是 python.org 的 Python 3.11 或更高版本。")
            return False
    want = _req_hash()
    try:
        have = REQ_MARK.read_text(encoding="utf-8").strip()
    except OSError:
        have = ""
    if want and have == want and _deps_ok():
        return True  # 依赖没变，跳过 pip，断网也能启动
    _print("正在安装/检查依赖（第一次需要联网，可能要一两分钟）...")
    _run([str(VENV_PY), "-m", "pip", "install", "-q", "--disable-pip-version-check", "--upgrade", "pip"])
    rc = _run([str(VENV_PY), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(REQ)])
    if rc == 0:
        try:
            REQ_MARK.write_text(want, encoding="utf-8")
        except OSError:
            pass
        return True
    if _deps_ok():
        _print("依赖更新失败（可能没联网），先用已装好的版本继续启动。")
        return True
    _print("依赖安装失败。请检查网络后重试。")
    return False


def open_console_window() -> None:
    script = ROOT / "open_console_window.py"
    if not script.exists():
        return
    exe = VENV_PYW if (IS_WIN and VENV_PYW.exists()) else VENV_PY
    kw: dict = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL, "close_fds": True}
    if IS_WIN:
        kw["creationflags"] = CREATE_NO_WINDOW | 0x00000200  # + CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen([str(exe), str(script)], **kw)
    except Exception as exc:
        _print(f"弹出控制台窗口失败：{exc}，请用浏览器打开 {base_url()}/")


def main() -> int:
    os.chdir(ROOT)
    port = read_port()
    base = base_url(port)

    status = probe(port)
    if status == "lanyin":
        _print(f"澜音已经在运行（可能是 OBS 脚本或另一个窗口启动的），端口 {port}。")
        _print(f"控制台：{base}/")
        if not NO_WINDOW:
            open_console_window()
            time.sleep(4)
        return 0
    if status == "other":
        _print(f"端口 {port} 已被其他程序占用，澜音无法启动。")
        _print(f"请用记事本打开 config.yaml，把 port: {port} 改成别的数字（例如 18765），再重新启动。")
        return 2

    if not ensure_env():
        return 1

    _print("")
    _print("==============================================")
    _print("  澜音已启动")
    _print(f"  控制台：{base}/")
    _print(f"  覆盖层：{base}/overlay")
    _print(f"  播放器：{base}/player")
    _print("  关闭本窗口即可停止" if not HIDDEN else "  关闭 OBS 时会自动停止")
    _print("==============================================")
    _print("")
    if not NO_WINDOW:
        open_console_window()  # 它会自己等服务就绪（最多 30 秒）再弹出
    server_exe = VENV_PYW if (HIDDEN and IS_WIN and VENV_PYW.exists()) else VENV_PY
    return subprocess.call([str(server_exe), "-m", "app"], **_child_kwargs())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        _print(f"启动失败：{exc}")
        raise SystemExit(1)
