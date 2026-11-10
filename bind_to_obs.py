# -*- coding: utf-8 -*-
"""把澜音绑定到 OBS：写入控制台停靠窗口。请在 OBS 关闭时运行。"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

DOCK = {
    "title": "澜音控制台",
    "url": "http://127.0.0.1:8765/",
    "uuid": "obssongrequestconsole01",
}
SCRIPT_NAME = "lanyin.lua"


def _print(msg: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(msg, flush=True)


def _obs_running() -> bool:
    if os.name != "nt":
        return False
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq obs64.exe"],
            capture_output=True,
            text=True,
            errors="replace",
        ).stdout.lower()
        if "obs64.exe" in out:
            return True
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq obs.exe"],
            capture_output=True,
            text=True,
            errors="replace",
        ).stdout.lower()
        return "obs.exe" in out
    except Exception:
        return False


def _user_ini() -> Path:
    appdata = os.environ.get("APPDATA") or ""
    return Path(appdata) / "obs-studio" / "user.ini"


def _read_text(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gbk"):
        try:
            return data.decode(enc), enc
        except Exception:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8"


def merge_docks(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = (match.group(1) or "").strip()
        try:
            arr = json.loads(raw) if raw else []
        except Exception:
            arr = []
        if not isinstance(arr, list):
            arr = []
        already = False
        for item in arr:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if item.get("uuid") == DOCK["uuid"] or url.rstrip("/") == DOCK["url"].rstrip("/"):
                item["title"] = DOCK["title"]
                item["url"] = DOCK["url"]
                item["uuid"] = DOCK["uuid"]
                already = True
                break
        if not already:
            arr.append(dict(DOCK))
        return "ExtraBrowserDocks=" + json.dumps(arr, ensure_ascii=False, separators=(",", ":"))

    if re.search(r"^ExtraBrowserDocks=", text, flags=re.M):
        return re.sub(r"^ExtraBrowserDocks=(.*)$", repl, text, count=1, flags=re.M)
    if re.search(r"^\[BasicWindow\]", text, flags=re.M):
        return re.sub(
            r"^\[BasicWindow\]\s*$",
            "[BasicWindow]\nExtraBrowserDocks=" + json.dumps([DOCK], ensure_ascii=False, separators=(",", ":")),
            text,
            count=1,
            flags=re.M,
        )
    return text.rstrip() + "\n[BasicWindow]\nExtraBrowserDocks=" + json.dumps([DOCK], ensure_ascii=False, separators=(",", ":")) + "\n"


def main() -> int:
    root = Path(__file__).resolve().parent
    lua = root / SCRIPT_NAME
    _print("这个工具会把澜音嵌进 OBS。")
    _print("以后只要打开 OBS 就能用，不用再点 start.bat，也不用再开浏览器。")
    _print("")
    if not lua.exists():
        _print(f"找不到脚本文件：{lua}")
        return 1
    ini = _user_ini()
    if not ini.exists():
        _print(f"没有找到 OBS 配置（预期在 {ini}）。请先单独打开一次 OBS 再关掉，然后重新运行本工具。")
        return 1
    if _obs_running():
        _print("检测到 OBS 正在运行。请先关掉 OBS，关掉后我会自动继续。")
        while _obs_running():
            time.sleep(1)
        # 等 OBS 把配置写完
        time.sleep(1.5)
        _print("已检测到 OBS 关闭，开始写入。")
    text, enc = _read_text(ini)
    backup = ini.with_suffix(".ini.bak-songbot")
    try:
        if not backup.exists():
            backup.write_bytes(ini.read_bytes())
    except Exception:
        pass
    new_text = merge_docks(text)
    ini.write_text(new_text, encoding="utf-8")
    _print("已经写好「澜音控制台」停靠窗口。")
    _print("")
    _print("请再打开 OBS，然后按下面做一次（以后不用再做）：")
    _print("  1. 顶部菜单点「工具」→「脚本」")
    _print("  2. 点左下角的 + 号")
    _print(f"  3. 选中这个文件：")
    _print(f"     {lua}")
    _print("")
    _print("脚本加载后，会自动在后台启动澜音。覆盖层和音频需要时再在脚本窗口点「把覆盖层和音频加到当前场景」。")
    _print("控制台在 OBS 里：「视图」→「停靠窗口」→「澜音控制台」。")
    _print("如果当时没看到这个窗口，关掉 OBS 再开一次就会出现。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        _print(f"绑定失败：{exc}")
        raise SystemExit(1)
