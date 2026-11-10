"""OBS 脚本版（lanyin.lua）端到端：假 obslua + 假 kernel32，真起服务进程。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pytest.importorskip("lupa")
if sys.platform != "linux":
    pytest.skip("harness uses /proc", allow_module_level=True)

from tests.obs_fake import REPO, FakeObs, free_port, make_project_copy  # noqa: E402


def _health(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
        return json.loads(r.read())


def _port_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _wait_free(port, t=8):
    end = time.time() + t
    while time.time() < end:
        if _port_free(port):
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def proj(tmp_path):
    port = free_port()
    root = make_project_copy(tmp_path / "LanYin 澜音", port)  # 带空格和中文的路径
    return root, port


def _start_external(root):
    env = dict(os.environ, OBS_SONG_SKIP_DANMAKU="1")
    return subprocess.Popen([str(REPO / ".venv/bin/python"), "-m", "app"], cwd=root, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def test_properties_have_no_auto_add_and_have_buttons(proj):
    root, port = proj
    obs = FakeObs(root)
    props = obs.g.script_properties()
    items = [(props["items"][i]["kind"], props["items"][i]["name"]) for i in range(1, len(props["items"]) + 1)]
    names = [n for _, n in items]
    assert "auto_sources" not in names
    assert {"btn_start", "btn_popout", "btn_sources", "btn_stop"} <= set(names)
    assert obs.g.console_url() == f"http://127.0.0.1:{port}/window?v=35"


def test_load_starts_server_no_sources_and_exit_stops_it(proj):
    root, port = proj
    obs = FakeObs(root)
    try:
        # 旧版本存过 auto_sources=true 也不能再自动加
        obs.g.script_load(obs.settings(auto_popup=False, auto_sources=True))
        assert obs.wait_ready(), obs.logs
        assert _health(port)["app"] == "lanyin"
        obs.fake.fire(obs.g.obslua.OBS_FRONTEND_EVENT_FINISHED_LOADING)
        time.sleep(0.3)
        assert len(obs.fake.created) == 0
        assert len(obs.fake.scene["items"]) == 0
        server_pid = int((root / "data/server.pid").read_text())
        assert (root / "data/obs-owned.pid").read_text().strip() == str(server_pid)
        # 窗口模式标志：无窗口 + 新进程组 + 脱离 OBS 作业
        assert obs.spawned[0]["flags"] == 0x08000000 + 0x200 + 0x01000000

        # 再点一次「立即启动」= 复用，不再起新进程
        n = len(obs.spawned)
        obs.g.on_start_clicked(None, None)
        assert len(obs.spawned) == n

        obs.fake.fire(obs.g.obslua.OBS_FRONTEND_EVENT_EXIT)
        assert _wait_free(port)
        assert not obs._alive(server_pid)
        assert not (root / "data/server.ready").exists()
        assert not (root / "data/obs-owned.pid").exists()
        obs.g.script_unload()
        assert obs.fake.timer_count() == 0 and obs.fake.cb_count() == 0
    finally:
        obs.cleanup()


def test_manual_add_sources_urls_and_audio_visible(proj):
    root, port = proj
    obs = FakeObs(root)
    obs.g.script_load(obs.settings(auto_start=False))
    obs.g.on_sources_clicked(None, None)
    obs.g.on_sources_clicked(None, None)  # 再点不重复
    created = {obs.fake.created[i]["name"]: obs.fake.created[i] for i in range(1, len(obs.fake.created) + 1)}
    assert set(created) == {"澜音覆盖层", "澜音音频"}
    ov = created["澜音覆盖层"]["settings"]["_v"]
    au = created["澜音音频"]["settings"]["_v"]
    assert created["澜音覆盖层"]["kind"] == "browser_source"
    assert ov["url"] == f"http://127.0.0.1:{port}/overlay" and ov["width"] == 1920 and ov["height"] == 1080
    assert ov["reroute_audio"] is False
    assert au["url"] == f"http://127.0.0.1:{port}/player" and au["reroute_audio"] is True
    items = obs.fake.scene["items"]
    assert len(items) == 2
    audio_item = [items[i] for i in (1, 2) if items[i]["source"]["name"] == "澜音音频"][0]
    assert audio_item["visible"] is True  # 隐藏的来源不进混音
    assert audio_item["pos"]["x"] == -2000


def test_refresh_updates_old_port_and_renames_legacy(proj):
    root, port = proj
    obs = FakeObs(root)
    lua_src = obs.lua.eval("function(n, u) local d = obslua.obs_data_create(); obslua.obs_data_set_string(d, 'url', u); return obslua.obs_source_create('browser_source', n, d, nil) end")
    lua_src("点歌姬覆盖层", "http://127.0.0.1:8765/overlay?mode=bar")
    lua_src("澜音音频", "http://127.0.0.1:8765/player")
    obs.g.script_load(obs.settings(auto_start=False))
    assert obs.fake.sources["点歌姬覆盖层"] is None
    obs.g.refresh_browser_sources()
    assert obs.fake.sources["澜音覆盖层"]["settings"]["_v"]["url"] == f"http://127.0.0.1:{port}/overlay?mode=bar"
    assert obs.fake.sources["澜音音频"]["settings"]["_v"]["url"] == f"http://127.0.0.1:{port}/player"


def test_external_server_reused_and_not_stopped_on_obs_exit(proj):
    root, port = proj
    ext = _start_external(root)
    obs = FakeObs(root)
    try:
        end = time.time() + 20
        while time.time() < end and not (root / "data/server.ready").exists():
            time.sleep(0.2)
        obs.g.script_load(obs.settings(auto_popup=False))
        assert len(obs.spawned) == 0  # 复用，没有再起
        obs.fake.fire(obs.g.obslua.OBS_FRONTEND_EVENT_EXIT)
        time.sleep(0.5)
        assert ext.poll() is None, "OBS 关闭时不该停掉 start.bat 启动的澜音"
        obs.g.on_stop_clicked(None, None)  # 手动「停止澜音」照样能停
        ext.wait(8)
        assert _wait_free(port)
    finally:
        if ext.poll() is None:
            ext.kill()
        obs.cleanup()


def test_port_taken_by_other_program_fails_cleanly(proj):
    root, port = proj
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    obs = FakeObs(root)
    try:
        obs.g.script_load(obs.settings(auto_popup=False))
        assert not obs.wait_ready(15)
        assert any("启动失败" in m for m in obs.logs), obs.logs
        assert obs.fake.timer_count() == 0
        log = (root / "data/startup.log").read_text(encoding="utf-8")
        assert "已被其他程序占用" in log
    finally:
        blocker.close()
        obs.cleanup()


def test_first_run_without_venv_tries_path_pythonw(tmp_path):
    root = make_project_copy(tmp_path / "p", free_port(), with_venv=False)
    obs = FakeObs(root)
    obs.g.script_load(obs.settings(auto_popup=False))
    bare = [s for s in obs.spawned if s["app"] is None]
    assert [s["cmd"].split()[0].strip('"') for s in bare][:2] == ["pythonw", "pyw"]
    assert all("start_win.py" in s["cmd"] and "--hidden" in s["cmd"] for s in obs.spawned)
    assert any("启动失败" in m for m in obs.logs)
