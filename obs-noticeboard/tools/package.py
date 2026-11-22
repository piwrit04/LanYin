#!/usr/bin/env python3
"""Package an installed local build; version comes from the source header."""
from pathlib import Path
import re
import sys
import zipfile
root = Path(__file__).resolve().parents[1]
match = re.search(r'#define NB_VERSION "([^"]+)"', (root / 'src/nb-version.h').read_text())
if not match:
    raise SystemExit('Missing NB_VERSION')
version = match.group(1)
dist = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'dist'
out = Path(sys.argv[2]) if len(sys.argv) > 2 else root / f'obs-noticeboard-{version}-windows-x64.zip'
required = [Path('obs-plugins/64bit/obs-noticeboard.dll'), Path('data/obs-plugins/obs-noticeboard/noticeboard.effect'), Path('data/obs-plugins/obs-noticeboard/locale/zh-CN.ini'), Path('data/obs-plugins/obs-noticeboard/locale/en-US.ini')]
if not all((dist / p).is_file() for p in required):
    raise SystemExit('Run cmake --install first; expected plugin DLL/effect/locales are missing')
# Deliberately allowlist runtime inputs: never package OBS itself or local logs.
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in required:
        z.write(dist / p, p.as_posix())
    for name in ['install.cmd', 'uninstall.cmd']:
        z.write(root / 'tools' / name, name)
    for name in ['README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md']:
        z.write(root / name, name)
    for p in sorted((root / 'docs').rglob('*')):
        if p.is_file() and p.suffix.lower() in {'.md', '.png'}:
            z.write(p, p.relative_to(root).as_posix())
print(out)
