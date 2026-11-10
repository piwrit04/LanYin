"""在 Linux 上跑 lanyin.lua 的假 OBS + 假 kernel32（LuaJIT via lupa）。仅测试用。

- obslua：记录创建的来源、场景项、属性、定时器、前端回调
- ffi/kernel32：CreateProcessW → subprocess.Popen；OpenProcess/GetExitCodeProcess → 进程是否存活；
  TerminateProcess → SIGKILL（和 Windows 一样不给进程清理的机会）
- 路径：脚本里拼的是 Windows 反斜杠路径，这里在 io.open / os.remove 里换回 /
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from lupa import luajit21

REPO = Path(__file__).resolve().parents[1]

PRELUDE = r"""
local py = ...
local function fix(p) if type(p) == "string" then return (string.gsub(p, "\\", "/")) end return p end
local _open, _remove = io.open, os.remove
io.open = function(p, m) return _open(fix(p), m) end
os.remove = function(p) return _remove(fix(p)) end

-- ---------- fake ffi ----------
local NULL = setmetatable({}, {__tostring = function() return "NULL" end})
local ffi = {NULL = NULL}
function ffi.cdef() end
function ffi.sizeof() return 104 end
function ffi.new(t, n)
  if t == "DWORD[1]" then return {[0] = 0} end
  return {}
end
function ffi.copy(dst, src, nbytes)
  for i = 0, nbytes / 2 - 1 do dst[i] = src[i] end
end
local function wstr(buf)
  if buf == nil then return nil end
  local t, i = {}, 0
  while buf[i] ~= nil and buf[i] ~= 0 do t[#t + 1] = string.char(buf[i]); i = i + 1 end
  return table.concat(t)
end
local k32 = {}
function k32.MultiByteToWideChar(cp, fl, s, n, buf, cap)
  if buf == nil then return #s + 1 end
  for i = 1, #s do buf[i - 1] = string.byte(s, i) end
  buf[#s] = 0
  return #s + 1
end
function k32.CreateProcessW(app, cmd, a, b, inherit, flags, env, cwd, si, pi)
  local pid = py.spawn(wstr(app), wstr(cmd), wstr(cwd), flags)
  if pid == 0 then return 0 end
  pi.dwProcessId = pid
  return 1
end
function k32.OpenProcess(access, inh, pid)
  if py.alive(pid) then return {pid = pid} end
  return NULL
end
function k32.GetExitCodeProcess(h, code) code[0] = py.alive(h.pid) and 259 or 0; return 1 end
function k32.TerminateProcess(h, c) py.kill(h.pid); return 1 end
function k32.CloseHandle() return 1 end
function k32.GetLastError() return 2 end
function ffi.load(name) return k32 end
package.loaded.ffi = ffi

-- ---------- fake obslua ----------
local obs = {}
obslua = obs
obs.LOG_INFO = 300
obs.OBS_FRONTEND_EVENT_FINISHED_LOADING = 26
obs.OBS_FRONTEND_EVENT_EXIT = 17
obs.OBS_FRONTEND_EVENT_SCRIPTING_SHUTDOWN = 33
function obs.script_log(level, msg) py.log(msg) end
function script_path() return py.root .. "/" end

function obs.obs_data_create() return {_v = {}, _d = {}} end
function obs.obs_data_release() end
function obs.obs_data_set_default_bool(d, k, v) d._d[k] = v end
function obs.obs_data_set_bool(d, k, v) d._v[k] = v end
function obs.obs_data_get_bool(d, k) local v = d._v[k]; if v == nil then v = d._d[k] end; return v == true end
function obs.obs_data_set_string(d, k, v) d._v[k] = v end
function obs.obs_data_get_string(d, k) return d._v[k] or "" end
function obs.obs_data_set_int(d, k, v) d._v[k] = v end

function obs.obs_properties_create() return {items = {}} end
function obs.obs_properties_add_bool(p, name, label) table.insert(p.items, {kind = "bool", name = name, label = label}) end
function obs.obs_properties_add_button(p, name, label, cb) table.insert(p.items, {kind = "button", name = name, label = label, cb = cb}) end

FAKE = {callbacks = {}, timers = {}, sources = {}, scene = {name = "场景", items = {}}, created = {}, updated = {}}
function obs.obs_frontend_add_event_callback(cb) FAKE.callbacks[cb] = true end
function obs.obs_frontend_remove_event_callback(cb) FAKE.callbacks[cb] = nil end
function obs.timer_add(cb, ms) FAKE.timers[cb] = ms end
function obs.timer_remove(cb) FAKE.timers[cb] = nil end
function FAKE.fire(ev) for cb in pairs(FAKE.callbacks) do cb(ev) end end
function FAKE.tick() local n = 0; for cb in pairs(FAKE.timers) do cb(); n = n + 1 end; return n end
function FAKE.timer_count() local n = 0; for _ in pairs(FAKE.timers) do n = n + 1 end; return n end
function FAKE.cb_count() local n = 0; for _ in pairs(FAKE.callbacks) do n = n + 1 end; return n end

function obs.obs_get_source_by_name(name) return FAKE.sources[name] end
function obs.obs_source_release() end
function obs.obs_source_set_name(src, name)
  FAKE.sources[src.name] = nil; src.name = name; FAKE.sources[name] = src
end
function obs.obs_source_create(kind, name, settings, hotkeys)
  local s = {kind = kind, name = name, settings = {_v = {}}}
  for k, v in pairs(settings._v) do s.settings._v[k] = v end
  FAKE.sources[name] = s
  table.insert(FAKE.created, s)
  return s
end
function obs.obs_source_get_settings(src) return src.settings end
function obs.obs_source_update(src, settings) table.insert(FAKE.updated, src.name) end
function obs.obs_frontend_get_current_scene() return FAKE.scene end
function obs.obs_scene_from_source(src) return src end
function obs.obs_scene_find_source(scene, name)
  for _, it in ipairs(scene.items) do if it.source.name == name then return it end end
  return nil
end
function obs.obs_scene_add(scene, src)
  local it = {source = src, visible = true}
  table.insert(scene.items, it)
  return it
end
function obs.obs_sceneitem_set_visible(it, v) it.visible = v end
function obs.vec2() return {x = 0, y = 0} end
function obs.obs_sceneitem_set_pos(it, pos) it.pos = {x = pos.x, y = pos.y} end
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_project_copy(dest: Path, port: int, with_venv: bool = True) -> Path:
    def ignore(d, names):
        return {n for n in names if n in {".venv", ".git", "data", "__pycache__", ".pytest_cache"}}

    shutil.copytree(REPO, dest, ignore=ignore)
    (dest / "data").mkdir()
    cfg = (REPO / "config.yaml").read_text(encoding="utf-8")
    import re

    cfg = re.sub(r"^port:.*$", f"port: {port}", cfg, flags=re.M)
    (dest / "config.yaml").write_text(cfg, encoding="utf-8")
    if with_venv:
        scripts = dest / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        real = REPO / ".venv" / "bin" / "python"
        for name in ("pythonw.exe", "python.exe"):
            shim = scripts / name
            shim.write_text(f'#!/bin/sh\nexec "{real}" "$@"\n', encoding="utf-8")
            shim.chmod(0o755)
    return dest


class FakeObs:
    def __init__(self, root: Path, run_popup: bool = False):
        self.root = root
        self.logs: list[str] = []
        self.spawned: list[dict] = []
        self.procs: dict[int, subprocess.Popen] = {}
        self.run_popup = run_popup
        self.lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        api = self.lua.table_from({
            "root": str(root),
            "spawn": self._spawn,
            "alive": self._alive,
            "kill": self._kill,
            "log": self.logs.append,
        })
        self.lua.execute(PRELUDE, api)
        src = (REPO / "lanyin.lua").read_text(encoding="utf-8")
        chunk = self.lua.eval("function(s) local f, e = loadstring(s, 'lanyin.lua'); if not f then error(e) end; return f end")(src)
        chunk()
        self.g = self.lua.globals()
        self.fake = self.g.FAKE

    # ---- kernel32 backends ----
    def _spawn(self, app, cmd, cwd, flags):
        rec = {"app": app, "cmd": cmd, "cwd": cwd, "flags": int(flags)}
        self.spawned.append(rec)
        argv = shlex.split(cmd.replace("\\", "/"))
        exe = argv[0]
        if app is None and shutil.which(exe) is None:
            return 0  # PATH 里没有：和 Windows 一样失败
        if any("open_console_window.py" in a for a in argv) and not self.run_popup:
            return 999999  # 只记录，不真去开浏览器
        env = dict(os.environ, OBS_SONG_SKIP_DANMAKU="1")
        p = subprocess.Popen(argv, cwd=cwd.replace("\\", "/"), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.procs[p.pid] = p
        return p.pid

    def _alive(self, pid):
        pid = int(pid)
        p = self.procs.get(pid)
        if p is not None:
            return p.poll() is None
        try:
            st = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            return st != "Z"
        except Exception:
            return False

    def _kill(self, pid):
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass
        p = self.procs.get(int(pid))
        if p is not None:
            try:
                p.wait(5)
            except Exception:
                pass

    # ---- helpers ----
    def settings(self, **vals):
        d = self.g.obslua.obs_data_create()
        self.g.script_defaults(d)
        for k, v in vals.items():
            d["_v"][k] = v
        return d

    def wait_ready(self, timeout=25.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.fake.timer_count() == 0:
                return bool(self.g.server_is_up())
            self.fake.tick()
            time.sleep(0.4)
        return False

    def cleanup(self):
        for p in self.procs.values():
            if p.poll() is None:
                p.kill()
                try:
                    p.wait(5)
                except Exception:
                    pass
