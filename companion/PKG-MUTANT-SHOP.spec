# -*- mode: python ; coding: utf-8 -*-
#
# THE UI GATE AND THE VERSION STAMP RUN HERE, not just in the ELF build.
#
# Both artifacts embed web/index.html. build-wsl.sh runs tools/check_web.py and
# tools/stamp_version.py before it bundles the UI into the ELF; NOTHING ran them before the exe
# bundled it. So building only the exe could ship a UI whose script does not parse - which blanks
# the app on every device while the server keeps answering 200 - or a stale APP_VERSION that makes
# the page lie about which build the user is looking at.
#
# It happened to be safe until now only because the ELF was always built first by hand. Ordering is
# not a safeguard; this is.
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(SPEC))
_ROOT = os.path.dirname(_HERE)


def _gate(script, fatal):
    path = os.path.join(_ROOT, "tools", script)
    if not os.path.exists(path):
        print("spec: %s missing - skipping" % script)
        return
    rc = subprocess.call([sys.executable, path], cwd=_ROOT)
    if rc != 0:
        if fatal:
            raise SystemExit("ABORT: %s failed (rc=%d) - not building an exe with a broken UI."
                             % (script, rc))
        print("spec: warning - %s returned %d" % (script, rc))


_gate("stamp_version.py", False)   # keep APP_VERSION in step with companion/server.py VERSION
_gate("check_web.py", True)        # refuse to package a UI whose script will not parse


a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=[('..\\web', 'web')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PKG-MUTANT-SHOP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['..\\web\\assets\\app.ico'],
)
