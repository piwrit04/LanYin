obs = obslua

SOURCE_OVERLAY = "澜音覆盖层"
SOURCE_AUDIO = "澜音音频"
OLD_SOURCE_OVERLAY = "点歌姬覆盖层"
OLD_SOURCE_AUDIO = "点歌姬音频"
CONSOLE_VERSION = "35"
DEFAULT_PORT = 8765

auto_start = true
auto_stop = true
auto_popup = true
loaded = false
poll_count = 0
launching_pid = nil
owned_pending = false

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

-- 端口跟 config.yaml 走（默认 8765）。用户按 README 改了端口，覆盖层/音频地址也跟着变。
function read_port()
  local f = io.open(path_join(project_root(), "config.yaml"), "r")
  if not f then
    return DEFAULT_PORT
  end
  local text = f:read("*a") or ""
  f:close()
  for line in string.gmatch(text .. "\n", "([^\n]*)\n") do
    local n = string.match(line, "^port%s*:%s*['\"]?(%d+)")
    if n then
      n = tonumber(n)
      if n and n > 0 and n < 65536 then
        return n
      end
    end
  end
  return DEFAULT_PORT
end

function base_url()
  return "http://127.0.0.1:" .. tostring(read_port())
end

function overlay_url()
  return base_url() .. "/overlay"
end

function player_url()
  return base_url() .. "/player"
end

function console_url()
  return base_url() .. "/window?v=" .. CONSOLE_VERSION
end

function script_defaults(settings)
  obs.obs_data_set_default_bool(settings, "auto_start", true)
  obs.obs_data_set_default_bool(settings, "auto_stop", true)
  obs.obs_data_set_default_bool(settings, "auto_popup", true)
end

-- 不再有「自动加到场景」开关：旧版本保存过的 auto_sources=true 也不会再生效，
-- 覆盖层和音频只在点按钮时才加。
function script_update(settings)
  auto_start = obs.obs_data_get_bool(settings, "auto_start")
  auto_stop = obs.obs_data_get_bool(settings, "auto_stop")
  auto_popup = obs.obs_data_get_bool(settings, "auto_popup")
end

function script_properties()
  local props = obs.obs_properties_create()
  obs.obs_properties_add_bool(props, "auto_start", "打开 OBS 时自动启动澜音")
  obs.obs_properties_add_bool(props, "auto_stop", "关闭 OBS 时自动退出澜音")
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
  elseif event == obs.OBS_FRONTEND_EVENT_EXIT or event == obs.OBS_FRONTEND_EVENT_SCRIPTING_SHUTDOWN then
    if auto_stop then
      -- 只停 OBS 自己启动的那个；用 start.bat 单独开的本地版不受影响
      stop_owned_server()
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
  -- 不带路径的程序名（如 pythonw）要让 CreateProcessW 按 PATH 搜索：
  -- lpApplicationName 必须传 NULL，否则它只在当前目录找、也不会补 .exe，必然失败。
  local bare = not string.find(exe, "[\\/]")
  local wexe = nil
  if not bare then
    wexe = to_wide(exe)
  end
  local wcmd = to_wide(cmdline)
  local wcwd = to_wide(cwd)
  if (wexe == nil and not bare) or wcmd == nil or wcwd == nil then
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
    file_log("启动失败（" .. exe .. "），错误码 " .. tostring(kernel32.GetLastError()))
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
  file_log("正在弹出独立控制台窗口：" .. console_url())
  start_hidden(exe, '"' .. script .. '"', root)
end

function start_server()
  if server_is_up() then
    file_log("澜音已经在运行（" .. base_url() .. "）")
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
  local candidates = {}
  if file_exists(pythonw) then
    candidates = {pythonw}
  elseif file_exists(python) then
    candidates = {python}
  end
  local args = "-m app"
  if #candidates == 0 then
    -- 第一次运行：后台建环境、装依赖，不弹黑窗口
    candidates = system_pythonw_candidates()
    args = '"' .. start_win .. '" --hidden'
    file_log("还没有运行环境，正在第一次安装，可能需要一两分钟")
  end
  local pid = nil
  for i = 1, #candidates do
    file_log("正在后台启动澜音：" .. candidates[i] .. " " .. args)
    pid = start_hidden(candidates[i], args, root)
    if pid ~= nil then
      break
    end
  end
  if pid == nil then
    file_log("启动失败。可先双击 start.bat 看是否有报错。")
    return
  end
  launching_pid = pid
  write_pid_file("launch.pid", pid)
  owned_pending = true
  poll_count = 0
  obs.timer_remove(poll_ready)
  obs.timer_add(poll_ready, 800)
end

function system_pythonw_candidates()
  local list = {}
  local seen = {}
  local function add(p)
    if p and not seen[p] then
      seen[p] = true
      table.insert(list, p)
    end
  end
  local bases = {}
  local la = os.getenv("LOCALAPPDATA")
  if la and la ~= "" then
    table.insert(bases, la .. "\\Programs\\Python")
  end
  local pf = os.getenv("ProgramFiles")
  if pf and pf ~= "" then
    table.insert(bases, pf)
  end
  for _, b in ipairs(bases) do
    for _, v in ipairs({"314", "313", "312", "311"}) do
      local p = b .. "\\Python" .. v .. "\\pythonw.exe"
      if file_exists(p) then
        add(p)
      end
    end
  end
  add("pythonw")  -- 勾选了 Add Python to PATH
  add("pyw")      -- Python 启动器
  return list
end

-- 记录「这个澜音是 OBS 启动的」，脚本重载后也认得，关 OBS 时只停自己的。
function mark_owned()
  local pid = read_pid_file("server.pid")
  if pid then
    write_pid_file("obs-owned.pid", pid)
  end
end

function stop_owned_server()
  local owned = read_pid_file("obs-owned.pid")
  local pid = read_pid_file("server.pid")
  if owned and pid and owned == pid then
    stop_server()
  elseif owned_pending then
    stop_server()  -- 还在启动中就关 OBS：停掉自己拉起来的进程
  else
    file_log("澜音不是 OBS 启动的（可能是 start.bat），关闭 OBS 时不去停它")
  end
end

function poll_ready()
  poll_count = poll_count + 1
  if server_is_up() then
    file_log("澜音已启动")
    if owned_pending then
      mark_owned()
      owned_pending = false
    end
    refresh_browser_sources()
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
  owned_pending = false
  -- also clear ready flags if still present
  local ready = path_join(project_root(), "data\\server.ready")
  os.remove(ready)
  os.remove(path_join(project_root(), "data\\obs-owned.pid"))
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

  local overlay = add_browser_source(SOURCE_OVERLAY, overlay_url(), 1920, 1080, false)
  if overlay ~= nil then
    if not scene_has_source(scene, SOURCE_OVERLAY) then
      obs.obs_scene_add(scene, overlay)
      file_log("已在当前场景加入「澜音覆盖层」")
    end
    obs.obs_source_release(overlay)
  end

  local audio = add_browser_source(SOURCE_AUDIO, player_url(), 320, 180, true)
  if audio ~= nil then
    if not scene_has_source(scene, SOURCE_AUDIO) then
      local item = obs.obs_scene_add(scene, audio)
      if item ~= nil then
        -- 不能把它设成不可见：OBS 里隐藏的来源不会进混音，直播就没声音了。
        -- 保持可见，只挪到画布外面，观众看不到。
        local pos = obs.vec2()
        pos.x = -2000
        pos.y = -2000
        obs.obs_sceneitem_set_pos(item, pos)
      end
      file_log("已在当前场景加入「澜音音频」（放在画布外，观众看不到；请在混音器里调音量）")
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
      -- 改过端口后，把旧的 127.0.0.1:端口 地址换成当前端口
      local want = (names[i] == SOURCE_OVERLAY) and overlay_url() or player_url()
      local path = string.match(url or "", "^http://127%.0%.0%.1:%d+(/.*)$") or string.match(url or "", "^http://localhost:%d+(/.*)$")
      if path then
        local base_path = string.match(want, "^http://[^/]+(/[^?]*)")
        if string.sub(path, 1, #base_path) == base_path then
          url = base_url() .. path
        end
      end
      obs.obs_data_set_string(settings, "url", url)
      obs.obs_source_update(src, settings)
      obs.obs_data_release(settings)
      obs.obs_source_release(src)
    end
  end
end
