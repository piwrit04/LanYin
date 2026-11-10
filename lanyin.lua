obs = obslua

SOURCE_OVERLAY = "澜音覆盖层"
SOURCE_AUDIO = "澜音音频"
OLD_SOURCE_OVERLAY = "点歌姬覆盖层"
OLD_SOURCE_AUDIO = "点歌姬音频"
CONSOLE_URL = "http://127.0.0.1:8765/"
OVERLAY_URL = "http://127.0.0.1:8765/overlay"
PLAYER_URL = "http://127.0.0.1:8765/player"

auto_start = true
auto_stop = true
auto_sources = false
auto_popup = true
loaded = false
poll_count = 0
launching_pid = nil

ffi = nil
kernel32 = nil

function try_load_ffi()
  local ok, lib = pcall(require, "ffi")
  if not ok then
    return false
  end
  ffi = lib
  ffi.cdef[[
    typedef unsigned long DWORD;
    typedef int BOOL;
    typedef void* HANDLE;
    typedef const wchar_t *LPCWSTR;
    typedef wchar_t *LPWSTR;
    typedef void *LPVOID;
    typedef unsigned short WORD;

    typedef struct {
      DWORD cb;
      LPWSTR lpReserved;
      LPWSTR lpDesktop;
      LPWSTR lpTitle;
      DWORD dwX, dwY, dwXSize, dwYSize;
      DWORD dwXCountChars, dwYCountChars;
      DWORD dwFillAttribute;
      DWORD dwFlags;
      WORD wShowWindow;
      WORD cbReserved2;
      LPVOID lpReserved2;
      HANDLE hStdInput;
      HANDLE hStdOutput;
      HANDLE hStdError;
    } STARTUPINFOW;

    typedef struct {
      HANDLE hProcess;
      HANDLE hThread;
      DWORD dwProcessId;
      DWORD dwThreadId;
    } PROCESS_INFORMATION;

    int MultiByteToWideChar(unsigned int, DWORD, const char*, int, wchar_t*, int);
    BOOL CreateProcessW(LPCWSTR, LPWSTR, LPVOID, LPVOID, BOOL, DWORD, LPVOID, LPCWSTR, STARTUPINFOW*, PROCESS_INFORMATION*);
    BOOL CloseHandle(HANDLE);
    HANDLE OpenProcess(DWORD, BOOL, DWORD);
    BOOL TerminateProcess(HANDLE, unsigned int);
    BOOL GetExitCodeProcess(HANDLE, DWORD*);
    DWORD GetLastError();
  ]]
  kernel32 = ffi.load("kernel32")
  return true
end

try_load_ffi()

function script_description()
  return [[<h2>澜音</h2>
<p>打开 OBS 后会在后台启动澜音，不用再点 start.bat，也不用再开浏览器。</p>
<p>控制台请点下面的「弹出独立控制台窗口」，会打开一个单独的大窗口，不用挤在 OBS 边缘。</p>
<p>原来那个叫「点歌姬控制台」的停靠窗口如果还在，可以在 <b>视图 → 停靠窗口</b> 里取消勾选。关掉 OBS 后重新运行 bind_to_obs.bat，标题会改成「澜音控制台」。</p>
<p>覆盖层和音频<b>不会</b>自动加到场景里。需要时点下面的「把覆盖层和音频加到当前场景」。如果场景里已有旧的「点歌姬覆盖层」「点歌姬音频」，加载时会自动改名为「澜音覆盖层」「澜音音频」。</p>]]
end

function script_defaults(settings)
  obs.obs_data_set_default_bool(settings, "auto_start", true)
  obs.obs_data_set_default_bool(settings, "auto_stop", true)
  obs.obs_data_set_default_bool(settings, "auto_sources", false)
  obs.obs_data_set_default_bool(settings, "auto_popup", true)
end

function script_update(settings)
  auto_start = obs.obs_data_get_bool(settings, "auto_start")
  auto_stop = obs.obs_data_get_bool(settings, "auto_stop")
  auto_sources = obs.obs_data_get_bool(settings, "auto_sources")
  auto_popup = obs.obs_data_get_bool(settings, "auto_popup")
end

function script_properties()
  local props = obs.obs_properties_create()
  obs.obs_properties_add_bool(props, "auto_start", "打开 OBS 时自动启动澜音")
  obs.obs_properties_add_bool(props, "auto_stop", "关闭 OBS 时自动退出澜音")
  obs.obs_properties_add_bool(props, "auto_sources", "启动时自动把覆盖层和音频加到当前场景")
  obs.obs_properties_add_bool(props, "auto_popup", "启动后自动弹出独立控制台窗口")
  obs.obs_properties_add_button(props, "btn_start", "立即启动澜音", on_start_clicked)
  obs.obs_properties_add_button(props, "btn_popout", "弹出独立控制台窗口", on_popout_clicked)
  obs.obs_properties_add_button(props, "btn_sources", "把覆盖层和音频加到当前场景", on_sources_clicked)
  obs.obs_properties_add_button(props, "btn_stop", "停止澜音", on_stop_clicked)
  return props
end

function script_load(settings)
  script_update(settings)
  loaded = true
  obs.obs_frontend_add_event_callback(on_frontend_event)
  migrate_legacy_sources()
  if auto_start then
    start_server()
  end
end

function script_unload()
  loaded = false
  obs.timer_remove(poll_ready)
  obs.obs_frontend_remove_event_callback(on_frontend_event)
end

function on_frontend_event(event)
  if event == obs.OBS_FRONTEND_EVENT_FINISHED_LOADING then
    migrate_legacy_sources()
    if auto_start then
      start_server()
    end
    if auto_sources then
      ensure_sources()
    end
  elseif event == obs.OBS_FRONTEND_EVENT_EXIT or event == obs.OBS_FRONTEND_EVENT_SCRIPTING_SHUTDOWN then
    if auto_stop then
      stop_server()
    end
  end
end

function on_start_clicked(props, prop)
  start_server()
  return true
end

function on_popout_clicked(props, prop)
  open_console_window()
  return true
end

function on_sources_clicked(props, prop)
  ensure_sources()
  return true
end

function on_stop_clicked(props, prop)
  stop_server()
  return true
end

function project_root()
  local p = script_path() or ""
  p = string.gsub(p, "\\", "/")
  p = string.gsub(p, "/+$", "")
  if string.match(string.lower(p), "%.lua$") then
    p = string.match(p, "^(.*)/[^/]+$") or p
  end
  return string.gsub(p, "/", "\\")
end

function path_join(a, b)
  return string.gsub(a, "\\+$", "") .. "\\" .. b
end

function file_exists(path)
  local f = io.open(path, "rb")
  if f then
    f:close()
    return true
  end
  return false
end

function file_log(msg)
  local f = io.open(path_join(project_root(), "data\\obs-script.log"), "a")
  if f then
    f:write(os.date("%Y-%m-%d %H:%M:%S") .. " " .. msg .. "\n")
    f:close()
  end
  obs.script_log(obs.LOG_INFO, msg)
end

function read_pid_file(name)
  local f = io.open(path_join(project_root(), "data\\" .. name), "r")
  if not f then
    return nil
  end
  local pid = (f:read("*l") or ""):match("%d+")
  f:close()
  return pid and tonumber(pid) or nil
end

function write_pid_file(name, pid)
  local f = io.open(path_join(project_root(), "data\\" .. name), "w")
  if f then
    f:write(tostring(pid or ""))
    f:close()
  end
end

function to_wide(str)
  if ffi == nil or str == nil then
    return nil
  end
  local n = kernel32.MultiByteToWideChar(65001, 0, str, -1, nil, 0)
  if n <= 0 then
    return nil
  end
  local buf = ffi.new("wchar_t[?]", n)
  kernel32.MultiByteToWideChar(65001, 0, str, -1, buf, n)
  return buf
end

function process_is_running(pid)
  if ffi == nil or pid == nil or pid <= 0 then
    return false
  end
  local PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
  local STILL_ACTIVE = 259
  local h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
  if h == nil or h == ffi.NULL then
    return false
  end
  local code = ffi.new("DWORD[1]")
  local ok = kernel32.GetExitCodeProcess(h, code)
  kernel32.CloseHandle(h)
  return ok ~= 0 and tonumber(code[0]) == STILL_ACTIVE
end

function start_hidden(exe, args, cwd)
  if ffi == nil then
    file_log("无法在无窗口模式下启动（缺少 ffi）")
    return nil
  end
  local cmdline = '"' .. exe .. '" ' .. args
  local wexe = to_wide(exe)
  local wcmd = to_wide(cmdline)
  local wcwd = to_wide(cwd)
  if wexe == nil or wcmd == nil or wcwd == nil then
    file_log("路径转换失败")
    return nil
  end
  -- CreateProcessW may write to the command-line buffer
  local cmd_len = 0
  while wcmd[cmd_len] ~= 0 do
    cmd_len = cmd_len + 1
  end
  local cmdbuf = ffi.new("wchar_t[?]", cmd_len + 1)
  ffi.copy(cmdbuf, wcmd, (cmd_len + 1) * 2)

  local si = ffi.new("STARTUPINFOW")
  si.cb = ffi.sizeof(si)
  si.dwFlags = 1
  si.wShowWindow = 0
  local pi = ffi.new("PROCESS_INFORMATION")
  local CREATE_NO_WINDOW = 0x08000000
  local CREATE_NEW_PROCESS_GROUP = 0x00000200
  local CREATE_BREAKAWAY_FROM_JOB = 0x01000000
  local flags = CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP + CREATE_BREAKAWAY_FROM_JOB
  local ok = kernel32.CreateProcessW(wexe, cmdbuf, nil, nil, 0, flags, nil, wcwd, si, pi)
  if ok == 0 then
    file_log("启动失败，错误码 " .. tostring(kernel32.GetLastError()))
    return nil
  end
  local pid = tonumber(pi.dwProcessId)
  kernel32.CloseHandle(pi.hThread)
  kernel32.CloseHandle(pi.hProcess)
  return pid
end

function kill_pid(pid)
  if ffi == nil or pid == nil or pid <= 0 then
    return false
  end
  local PROCESS_TERMINATE = 0x0001
  local h = kernel32.OpenProcess(PROCESS_TERMINATE, 0, pid)
  if h == nil or h == ffi.NULL then
    return false
  end
  kernel32.TerminateProcess(h, 1)
  kernel32.CloseHandle(h)
  return true
end

function server_is_up()
  if not file_exists(path_join(project_root(), "data\\server.ready")) then
    return false
  end
  local pid = read_pid_file("server.pid")
  if pid == nil then
    return false
  end
  if ffi ~= nil then
    return process_is_running(pid)
  end
  return true
end

function already_starting()
  if launching_pid and process_is_running(launching_pid) then
    return true
  end
  local pid = read_pid_file("launch.pid")
  if pid and process_is_running(pid) then
    launching_pid = pid
    return true
  end
  return false
end

function open_console_window()
  local root = project_root()
  local script = path_join(root, "open_console_window.py")
  if not file_exists(script) then
    file_log("找不到 open_console_window.py，无法弹出窗口")
    return
  end
  local pythonw = path_join(root, ".venv\\Scripts\\pythonw.exe")
  local python = path_join(root, ".venv\\Scripts\\python.exe")
  local exe
  if file_exists(pythonw) then
    exe = pythonw
  elseif file_exists(python) then
    exe = python
  else
    exe = "pythonw"
  end
  file_log("正在弹出独立控制台窗口")
  start_hidden(exe, '"' .. script .. '"', root)
end

function start_server()
  if server_is_up() then
    file_log("澜音已经在运行")
    refresh_browser_sources()
    if auto_popup then
      open_console_window()
    end
    return
  end
  if already_starting() then
    file_log("澜音正在启动，请稍候")
    poll_count = 0
    obs.timer_remove(poll_ready)
    obs.timer_add(poll_ready, 800)
    return
  end
  local root = project_root()
  local pythonw = path_join(root, ".venv\\Scripts\\pythonw.exe")
  local python = path_join(root, ".venv\\Scripts\\python.exe")
  local start_win = path_join(root, "start_win.py")
  local exe, args
  if file_exists(pythonw) then
    exe = pythonw
    args = "-m app"
  elseif file_exists(python) then
    exe = python
    args = "-m app"
  else
    -- First run: install env in the background, no console window
    local sys_pythonw = "pythonw"
    local sys_python = "python"
    if file_exists("C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\pythonw.exe") then
      sys_pythonw = "C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\pythonw.exe"
    end
    exe = sys_pythonw
    args = '"' .. start_win .. '" --hidden'
    file_log("还没有运行环境，正在第一次安装，可能需要一两分钟")
  end
  file_log("正在后台启动澜音：" .. exe .. " " .. args)
  local pid = start_hidden(exe, args, root)
  if pid == nil then
    file_log("启动失败。可先双击 start.bat 看是否有报错。")
    return
  end
  launching_pid = pid
  write_pid_file("launch.pid", pid)
  poll_count = 0
  obs.timer_remove(poll_ready)
  obs.timer_add(poll_ready, 800)
end

function poll_ready()
  poll_count = poll_count + 1
  if server_is_up() then
    file_log("澜音已启动")
    refresh_browser_sources()
    if auto_sources then
      ensure_sources()
    end
    if auto_popup then
      open_console_window()
    end
    obs.timer_remove(poll_ready)
    return
  end
  if launching_pid and not process_is_running(launching_pid) and not server_is_up() then
    file_log("澜音启动失败，进程已退出。可先双击 start.bat 看是否有报错。")
    obs.timer_remove(poll_ready)
    return
  end
  if poll_count >= 75 then
    file_log("澜音启动超时。可再点一次「立即启动澜音」，或先双击 start.bat 看是否有报错。")
    obs.timer_remove(poll_ready)
  end
end

function stop_server()
  obs.timer_remove(poll_ready)
  local pid = read_pid_file("server.pid")
  if pid and kill_pid(pid) then
    file_log("已停止澜音")
  end
  local lp = read_pid_file("launch.pid")
  if lp and lp ~= pid then
    kill_pid(lp)
  end
  launching_pid = nil
  -- also clear ready flags if still present
  local ready = path_join(project_root(), "data\\server.ready")
  os.remove(ready)
end

function source_exists(name)
  local src = obs.obs_get_source_by_name(name)
  if src ~= nil then
    obs.obs_source_release(src)
    return true
  end
  return false
end

function rename_legacy_source(old_name, new_name)
  if old_name == new_name then
    return
  end
  if source_exists(new_name) then
    return
  end
  local src = obs.obs_get_source_by_name(old_name)
  if src ~= nil then
    obs.obs_source_set_name(src, new_name)
    obs.obs_source_release(src)
    file_log("已将「" .. old_name .. "」重命名为「" .. new_name .. "」")
  end
end

function migrate_legacy_sources()
  rename_legacy_source(OLD_SOURCE_OVERLAY, SOURCE_OVERLAY)
  rename_legacy_source(OLD_SOURCE_AUDIO, SOURCE_AUDIO)
end

function current_scene()
  local source = obs.obs_frontend_get_current_scene()
  if source == nil then
    return nil, nil
  end
  local scene = obs.obs_scene_from_source(source)
  return source, scene
end

function scene_has_source(scene, name)
  local item = obs.obs_scene_find_source(scene, name)
  return item ~= nil
end

function add_browser_source(name, url, width, height, audio)
  if source_exists(name) then
    return obs.obs_get_source_by_name(name)
  end
  local settings = obs.obs_data_create()
  obs.obs_data_set_string(settings, "url", url)
  obs.obs_data_set_int(settings, "width", width)
  obs.obs_data_set_int(settings, "height", height)
  obs.obs_data_set_int(settings, "fps", 30)
  obs.obs_data_set_bool(settings, "reroute_audio", audio)
  obs.obs_data_set_bool(settings, "shutdown", false)
  obs.obs_data_set_bool(settings, "restart_when_active", false)
  local src = obs.obs_source_create("browser_source", name, settings, nil)
  obs.obs_data_release(settings)
  return src
end

function ensure_sources()
  migrate_legacy_sources()
  local scene_source, scene = current_scene()
  if scene == nil then
    file_log("还没有当前场景，稍后再试「把覆盖层和音频加到当前场景」")
    return
  end

  local overlay = add_browser_source(SOURCE_OVERLAY, OVERLAY_URL, 1920, 1080, false)
  if overlay ~= nil then
    if not scene_has_source(scene, SOURCE_OVERLAY) then
      obs.obs_scene_add(scene, overlay)
      file_log("已在当前场景加入「澜音覆盖层」")
    end
    obs.obs_source_release(overlay)
  end

  local audio = add_browser_source(SOURCE_AUDIO, PLAYER_URL, 320, 180, true)
  if audio ~= nil then
    if not scene_has_source(scene, SOURCE_AUDIO) then
      local item = obs.obs_scene_add(scene, audio)
      if item ~= nil then
        obs.obs_sceneitem_set_visible(item, false)
        local pos = obs.vec2()
        pos.x = -2000
        pos.y = -2000
        obs.obs_sceneitem_set_pos(item, pos)
      end
      file_log("已在当前场景加入隐藏的「澜音音频」（请在混音器里调音量）")
    end
    obs.obs_source_release(audio)
  end

  obs.obs_source_release(scene_source)
  refresh_browser_sources()
end

function refresh_browser_sources()
  migrate_legacy_sources()
  local names = {SOURCE_OVERLAY, SOURCE_AUDIO}
  for i = 1, #names do
    local src = obs.obs_get_source_by_name(names[i])
    if src ~= nil then
      local settings = obs.obs_source_get_settings(src)
      local url = obs.obs_data_get_string(settings, "url")
      obs.obs_data_set_string(settings, "url", url)
      obs.obs_source_update(src, settings)
      obs.obs_data_release(settings)
      obs.obs_source_release(src)
    end
  end
end
