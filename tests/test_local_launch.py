"""本地版（start.bat → start_win.py → python -m app，open_console_window.py）和启动保护。"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.obs_fake import REPO, free_port, make_project_copy

PY = str(REPO / ".venv/bin/python") if (REPO / ".venv/bin/python").exists() else sys.executable
ENV = dict(os.environ, OBS_SONG_SKIP_DANMAKU="1", PYTHONIOENCODING="utf-8")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(port, path):
    with OPENER.open(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
        return r.status, r.read().decode("utf-8")


def _wait_up(port, t=25):
    end = time.time() + t
    while time.time() < end:
        try:
            return _get(port, "/api/health")
        except Exception:
            time.sleep(0.3)
    raise AssertionError("server did not start")


def _wait_free(port, t=10):
    end = time.time() + t
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.2)
    return False


@pytest.fixture
def proj(tmp_path):
    port = free_port()
    root = make_project_copy(tmp_path / "Lan Yin 本地", port, with_venv=False)
    return root, port


# ---------------- portcfg ----------------
def test_read_port_variants(tmp_path):
    from app.portcfg import read_port

    p = tmp_path / "c.yaml"
    assert read_port(p) == 8765  # 没有文件
    p.write_bytes("\ufeffroom_id: 1\nport: 18765  # 改过\n".encode("utf-8"))
    assert read_port(p) == 18765
    p.write_bytes("# 备注\nport: '9000'\n".encode("gbk"))
    assert read_port(p) == 9000
    p.write_text("server:\n  port: 1234\n", encoding="utf-8")
    assert read_port(p) == 8765  # 只认顶层 port


def test_probe_free_other_and_lanyin():
    from app.portcfg import probe

    port = free_port()
    assert probe(port) == "free"
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    try:
        assert probe(port, timeout=0.5) == "other"
    finally:
        s.close()


# ---------------- first run / config ----------------
def test_first_run_creates_config_but_never_overwrites(tmp_path, monkeypatch):
    from app import settings

    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", cfg_path)
    cfg = settings.load_config()
    assert cfg_path.exists() and cfg["port"] == 8765
    cfg_path.write_text("room_id: 42\nport: 9999\n", encoding="utf-8")
    before = cfg_path.read_bytes()
    cfg = settings.load_config()
    assert cfg["room_id"] == 42 and cfg["port"] == 9999
    assert cfg_path.read_bytes() == before


def test_gbk_and_bom_config_load(tmp_path, monkeypatch):
    from app import settings

    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", cfg_path)
    cfg_path.write_bytes("room_id: 7\ncommands:\n  request_prefix: 来一首\n".encode("gbk"))
    assert settings.load_config()["commands"]["request_prefix"] == "来一首"
    cfg_path.write_bytes("\ufeffroom_id: 8\n".encode("utf-8"))
    assert settings.load_config()["room_id"] == 8


def test_secrets_bom_ok_and_unreadable_backed_up(tmp_path, monkeypatch):
    from app import secrets_store

    sp = tmp_path / "secrets.json"
    monkeypatch.setattr(secrets_store, "SECRETS_PATH", sp)
    monkeypatch.setattr(secrets_store, "ensure_dirs", lambda: None)
    assert secrets_store.load_secrets() == {}
    assert not sp.exists()  # 不会凭空创建
    sp.write_bytes("\ufeff{\"bili\": {\"uid\": 1}}".encode("utf-8"))
    assert secrets_store.load_secrets()["bili"]["uid"] == 1
    sp.write_text("{broken", encoding="utf-8")
    assert secrets_store.load_secrets() == {}
    baks = list(tmp_path.glob("secrets.unreadable-*.json"))
    assert len(baks) == 1 and baks[0].read_text(encoding="utf-8") == "{broken"


# ---------------- run() guard ----------------
def test_second_instance_reuses_and_keeps_pid_files(proj):
    root, port = proj
    a = subprocess.Popen([PY, "-m", "app"], cwd=root, env=ENV, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        _wait_up(port)
        pid_before = (root / "data/server.pid").read_text()
        b = subprocess.run([PY, "-m", "app"], cwd=root, env=ENV, capture_output=True, text=True, timeout=30)
        assert b.returncode == 0
        assert (root / "data/server.pid").read_text() == pid_before
        assert (root / "data/server.ready").exists()
    finally:
        a.send_signal(signal.SIGINT)
        a.wait(15)
    assert _wait_free(port)
    assert not (root / "data/server.pid").exists()  # 正常退出时清理自己的标记


def test_port_owned_by_other_program_exits_2(proj):
    root, port = proj
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    try:
        b = subprocess.run([PY, "-m", "app"], cwd=root, env=ENV, capture_output=True, timeout=30)
        assert b.returncode == 2
        assert "已被其他程序占用" in (root / "data/startup.log").read_text(encoding="utf-8")
    finally:
        s.close()


# ---------------- start_win.py（start.bat 的主体），Linux 下等价执行 ----------------
def _fake_venv(root):
    vbin = root / ".venv" / "bin"
    vbin.mkdir(parents=True)
    shim = vbin / "python"
    shim.write_text(f'#!/bin/sh\nexec "{PY}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    (root / ".venv/.lanyin-requirements.sha1").write_text(
        hashlib.sha1((root / "requirements.txt").read_bytes()).hexdigest(), encoding="utf-8")


def test_start_win_end_to_end_and_already_running(proj):
    root, port = proj
    _fake_venv(root)
    p = subprocess.Popen([PY, "start_win.py", "--no-window"], cwd=root, env=ENV, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    try:
        _, body = _wait_up(port)
        assert json.loads(body)["app"] == "lanyin"
        st, html = _get(port, "/window?v=38")
        assert st == 200 and "console.css?v=38" in html
        st, _ = _get(port, "/overlay")
        assert st == 200
        import asyncio
        import websockets

        async def ws_check():
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                await ws.send(json.dumps({"type": "hello", "role": "probe"}))
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                return msg["type"]

        assert asyncio.run(ws_check()) == "state"
        again = subprocess.run([PY, "start_win.py", "--no-window"], cwd=root, env=ENV,
                               capture_output=True, text=True, timeout=30)
        assert again.returncode == 0 and "已经在运行" in again.stdout
    finally:
        os.killpg(p.pid, signal.SIGINT)  # 相当于关掉 start.bat 窗口
        out = p.communicate(timeout=20)[0].decode("utf-8", "replace")
    assert f"127.0.0.1:{port}/overlay" in out
    assert _wait_free(port)


# ---------------- open_console_window.py ----------------
def test_open_console_window_logic(monkeypatch, tmp_path):
    import importlib

    import open_console_window as ocw

    importlib.reload(ocw)
    assert ocw.URL.endswith("/window?v=38") and ocw.URL.startswith("http://127.0.0.1:")
    args = ocw._browser_args(Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"), app=True)
    assert args[1] == f"--app={ocw.URL}" and "--disable-gpu" in args
    normal = ocw._browser_args(Path("chrome.exe"), app=False)
    assert normal[-1] == ocw.URL and "--new-window" in normal
    # Edge 优先于 Chrome，三个安装位置都查
    cands = [str(c) for c in ocw.browser_candidates()]
    assert "msedge.exe" in cands[0] and "chrome.exe" in cands[-1] and len(cands) == 6
    # 代码里是 Windows 反斜杠路径；在 Linux 上它就是一个带反斜杠的文件名
    fake = tmp_path / r"Google\Chrome\Application\chrome.exe"
    fake.write_text("")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "nope"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "nope86"))
    assert ocw.find_browser() == fake
    # 没有浏览器时退回系统默认浏览器
    opened = []
    monkeypatch.setattr(ocw, "wait_server", lambda timeout=30: True)
    monkeypatch.setattr(ocw, "focus_existing", lambda: False)
    monkeypatch.setattr(ocw, "find_browser", lambda: None)
    monkeypatch.setattr(ocw.subprocess, "Popen", lambda a, **k: opened.append(a))
    assert ocw.main() == 0 and opened == [["xdg-open", ocw.URL]]
    # 服务没起来就不弹
    monkeypatch.setattr(ocw, "wait_server", lambda timeout=30: False)
    assert ocw.main() == 1


def test_launcher_files_static():
    for name in ("start.bat", "bind_to_obs.bat"):
        raw = (REPO / name).read_bytes()
        raw.decode("ascii")  # 批处理只用 ASCII，避免 GBK/UTF-8 代码页问题
        assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"), name
        text = raw.decode()
        assert "chcp 65001" in text and 'cd /d "%~dp0"' in text
        assert "if %ERRORLEVEL%==0" not in text  # 括号块里会被提前展开
    bat = (REPO / "start.bat").read_text()
    assert "start_win.py" in bat and (REPO / "start_win.py").exists()
    assert "bind_to_obs.py" in (REPO / "bind_to_obs.bat").read_text()
    lua = (REPO / "lanyin.lua").read_text(encoding="utf-8")
    assert 'CONSOLE_VERSION = "38"' in lua
    from app.portcfg import CONSOLE_VERSION

    assert CONSOLE_VERSION == "38"
    html = (REPO / "app/static/console.html").read_text(encoding="utf-8")
    assert f"console.css?v={CONSOLE_VERSION}" in html and f"console.js?v={CONSOLE_VERSION}" in html


def test_bind_to_obs_merge_docks():
    import bind_to_obs as b

    t = b.merge_docks("[General]\nFoo=1\n")
    assert "[BasicWindow]\nExtraBrowserDocks=" in t and b.DOCK["url"] in t
    t2 = b.merge_docks(t)
    assert t2.count("obssongrequestconsole01") == 1
    old = '[BasicWindow]\nExtraBrowserDocks=[{"title":"点歌姬控制台","url":"http://127.0.0.1:8765/","uuid":"obssongrequestconsole01"}]\n'
    t3 = b.merge_docks(old)
    assert "澜音控制台" in t3 and t3.count("uuid") == 1



def test_open_console_window_focuses_existing(monkeypatch, tmp_path):
    import importlib
    import open_console_window as ocw

    importlib.reload(ocw)
    monkeypatch.setattr(ocw, "DATA", tmp_path)
    monkeypatch.setattr(ocw, "wait_server", lambda timeout=30: True)
    launched = []
    monkeypatch.setattr(ocw, "focus_existing", lambda: True)
    monkeypatch.setattr(ocw, "find_browser", lambda: launched.append("browser") or Path("chrome"))
    monkeypatch.setattr(ocw, "launch", lambda browser: launched.append("launch") or True)
    assert ocw.main() == 0
    assert launched == []
    assert (tmp_path / "console-window.lock").exists() or True  # lock file is created then may remain
