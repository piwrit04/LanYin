# -*- coding: utf-8 -*-
"""Open a standalone, properly sized console window (Edge/Chrome app mode)."""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.portcfg import base_url, http_get, window_url  # noqa: E402  (stdlib only)

DATA = ROOT / "data"
PROFILE = DATA / "console-window-profile"
# 端口跟 config.yaml 走（默认 8765），版本号跟 console.html 的 ?v= 与 CONSOLE_VERSION 保持一致
URL = window_url()
TITLE = "澜音控制台"
WIDTH, HEIGHT = 1320, 900
HEALTH = base_url() + "/api/health"

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _log(msg: str) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with (DATA / "console-window.log").open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def wait_server(timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with http_get(HEALTH, timeout=1) as resp:
                if getattr(resp, "status", 200) == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def browser_candidates() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA") or ""
    pf = os.environ.get("PROGRAMFILES") or r"C:\Program Files"
    pf86 = os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
    return [
        Path(pf86) / r"Microsoft\Edge\Application\msedge.exe",
        Path(pf) / r"Microsoft\Edge\Application\msedge.exe",
        Path(local) / r"Microsoft\Edge\Application\msedge.exe",
        Path(pf) / r"Google\Chrome\Application\chrome.exe",
        Path(pf86) / r"Google\Chrome\Application\chrome.exe",
        Path(local) / r"Google\Chrome\Application\chrome.exe",
    ]


def find_browser() -> Path | None:
    for path in browser_candidates():
        if path.is_file():
            return path
    return None


def focus_existing() -> bool:
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):  # type: ignore[misc]
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if TITLE in (buf.value or ""):
                found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        return False
    hwnd = found[0]
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP


def _browser_args(browser: Path, *, app: bool) -> list[str]:
    # OBS puts this process in a Windows job. Chrome then fails to start its
    # GPU/sandbox child (error_code=18) and exits with "GPU process isn't usable."
    # These flags keep rendering in-process so the window can stay up.
    args = [
        str(browser),
        f"--window-size={WIDTH},{HEIGHT}",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--no-sandbox",
        "--in-process-gpu",
        "--disable-gpu",
        "--disable-gpu-sandbox",
    ]
    if app:
        args.insert(1, f"--app={URL}")
    else:
        args.append("--new-window")
        args.append(URL)
    return args


def _kill_stale_profile_processes() -> None:
    if os.name != "nt":
        return
    marker = "console-window-profile"
    ps = (
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Name -match 'chrome|msedge' -and $_.CommandLine -and "
        f"($_.CommandLine -like '*{marker}*') }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        cwd=str(ROOT),
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )


def _clear_stale_locks() -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile"):
        path = PROFILE / name
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except OSError:
            pass
    for folder in ("GPUPersistentCache", "ShaderCache"):
        cache = PROFILE / folder
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)


def _popen(args: list[str]) -> subprocess.Popen:
    DATA.mkdir(parents=True, exist_ok=True)
    log_path = DATA / "console-window-browser.log"
    logf = open(log_path, "ab")
    kwargs: dict = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": logf,
        "stderr": logf,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _creation_flags()
    else:
        kwargs["start_new_session"] = True
        kwargs["close_fds"] = True
    return subprocess.Popen(args, **kwargs)


def _shell_execute(args: list[str]) -> bool:
    """Start via the Windows shell, which is more likely to escape OBS's job."""
    if os.name != "nt":
        return False
    import ctypes

    params = subprocess.list2cmdline(args[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "open", args[0], params, str(ROOT), 1
    )
    _log(f"ShellExecute 返回 {rc}")
    return rc > 32


def _start_via_bat(args: list[str]) -> bool:
    """Write a short launcher and open it with the shell (same path that already works for URLs)."""
    if os.name != "nt":
        return False
    DATA.mkdir(parents=True, exist_ok=True)
    bat = DATA / "open_console_window.cmd"
    lines = [
        "@echo off",
        "start \"\" " + subprocess.list2cmdline(args),
    ]
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="gbk", errors="replace")
    os.startfile(str(bat))  # noqa: S606
    _log(f"已通过启动脚本打开：{bat}")
    return True


def _window_stayed(seconds: float = 3.0) -> bool:
    deadline = time.time() + seconds
    seen = False
    while time.time() < deadline:
        if focus_existing():
            seen = True
            time.sleep(0.4)
            if focus_existing():
                return True
        time.sleep(0.25)
    return seen and focus_existing()


def launch(browser: Path) -> bool:
    PROFILE.mkdir(parents=True, exist_ok=True)
    try:
        _kill_stale_profile_processes()
    except Exception as exc:
        _log(f"清理旧窗口进程时出错：{exc}")
    _clear_stale_locks()

    app_args = _browser_args(browser, app=True)
    if _shell_execute(app_args) and _window_stayed():
        _log(f"独立窗口已打开：{browser}")
        return True

    _log("窗口没有稳住，改用启动脚本再试一次")
    try:
        _kill_stale_profile_processes()
    except Exception:
        pass
    _clear_stale_locks()
    if _start_via_bat(app_args) and _window_stayed(4.0):
        _log("已通过启动脚本打开独立窗口")
        return True

    _log("应用窗口仍未稳住，再试一次普通窗口")
    try:
        _kill_stale_profile_processes()
    except Exception:
        pass
    _clear_stale_locks()
    normal_args = _browser_args(browser, app=False)
    proc = _popen(normal_args)
    time.sleep(3.0)
    if proc.poll() is None:
        _log("已用普通浏览器窗口打开控制台")
        return True
    _log(f"普通窗口进程已退出，代码 {proc.poll()}")
    return False



@contextlib.contextmanager
def _launch_lock():
    """同一时刻只允许一个弹窗进程启动，避免自动启动连开两个窗口。"""
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "console-window.lock"
    fh = open(path, "a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            if fh.read(1) == b"":
                fh.write(b"\0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            locked = True
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        try:
            if locked and os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            elif locked:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass


def main() -> int:
    if not wait_server():
        _log("澜音还没启动，无法弹出控制台窗口")
        return 1
    with _launch_lock():
        if focus_existing():
            _log("已把现有控制台窗口放到前面")
            return 0
        browser = find_browser()
        if browser is None:
            _log("没有找到 Edge 或 Chrome，改用系统默认浏览器打开")
            if os.name == "nt":
                os.startfile(URL)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", URL], close_fds=True)
            return 0
        if launch(browser):
            return 0
        _log("无法稳住独立窗口，改用系统默认浏览器打开")
        if os.name == "nt":
            os.startfile(URL)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", URL], close_fds=True)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        _log(f"弹出窗口失败：{exc}")
        raise SystemExit(1)
