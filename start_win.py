# -*- coding: utf-8 -*-
"""Windows bootstrap for LanYin."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
REQ = ROOT / "requirements.txt"
HIDDEN = "--hidden" in sys.argv


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


def _run(cmd: list[str], **kwargs) -> None:
    result = subprocess.run(cmd, cwd=str(ROOT), **kwargs)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> int:
    os.chdir(ROOT)

    if not VENV_PY.exists():
        _print("正在创建运行环境（只需第一次）...")
        _run([sys.executable, "-m", "venv", str(ROOT / ".venv")])

    _print("正在检查依赖...")
    _run([str(VENV_PY), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    _run([str(VENV_PY), "-m", "pip", "install", "-q", "-r", str(REQ)])

    _print("")
    _print("==============================================")
    _print("  澜音已启动")
    _print("  控制台：http://127.0.0.1:8765/")
    _print("  覆盖层：http://127.0.0.1:8765/overlay")
    _print("  播放器：http://127.0.0.1:8765/player")
    _print("  关闭本窗口即可停止")
    _print("==============================================")
    _print("")
    return subprocess.call([str(VENV_PY), "-m", "app"], cwd=str(ROOT))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        _print(f"启动失败：{exc}")
        raise SystemExit(1)
