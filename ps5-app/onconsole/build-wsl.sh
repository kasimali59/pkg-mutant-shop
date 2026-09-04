#!/usr/bin/env bash
# Build the on-console HTTP server (serves the shop UI + local API from the PS5 itself).
# Reuses the SDK + user-space clang set up by ps5-app/payload/build-wsl.sh. NO sudo.
# Usage (WSL):  bash build-wsl.sh  [PORT]
set -e
PORT="${1:-8710}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SDK=~/sdk/ps5-payload-sdk
export PS5_PAYLOAD_SDK="$SDK"
export LD_LIBRARY_PATH="$HOME/clang18/usr/lib/llvm-18/lib:$HOME/clang18/usr/lib/x86_64-linux-gnu"

if [ ! -x "$SDK/bin/prospero-clang" ]; then
  echo "SDK not found — run ps5-app/payload/build-wsl.sh once first (it fetches the SDK + clang)." >&2
  exit 1
fi

# THE SPAWNED INSTALLER FIRST. payload_bundle.h .incbin's payloads/pms-installer.elf straight into
# this ELF, so building the shop without rebuilding the installer ships a new shop carrying an old
# installer - and nothing anywhere says so. It was built by hand until 3.31.0, and it drifted.
bash "$HERE/build-installer-wsl.sh"

# Embed the current web/ UI into the ELF (self-extracted to WEB_ROOT on boot) so loading the ELF updates
# the PS5 UI with no separate push. Regenerate the bundle header from web/ first.
# Keep the UI's version in step with the shipping one: it used to carry a stale hardcoded
# number that flashed on every load before /api/health answered.
if command -v python3 >/dev/null 2>&1; then
  python3 "$HERE/../../tools/stamp_version.py" || echo "warn: could not stamp the UI version"
fi

# A page whose script will not parse blanks the app on every device while the server
# keeps answering normally - so refuse to embed one.
if command -v python3 >/dev/null 2>&1; then
  python3 "$HERE/../../tools/check_web.py" || { echo "ABORT: the UI script is broken - not building." >&2; exit 1; }
fi

if command -v python3 >/dev/null 2>&1; then
  python3 "$HERE/gen_web_bundle.py" "$HERE/../../web" "$HERE/web_bundle.h" || { echo "ABORT: could not regenerate web_bundle.h - refusing to ship a console UI that is older than web/index.html." >&2; exit 1; }
  python3 "$HERE/gen_cheat_bundle.py" "$HERE/../../assets/cheats" "$HERE/cheat_bundle.h" "$HERE/cheats.pack" || echo "warn: cheat bundle not regenerated"
else
  echo "warn: python3 not found in WSL — using the existing web_bundle.h (regenerate it on the PC)."
fi

# Link set mirrors the known-working reference implementation: the installer appears to need the
# wider system-library context, not AppInstUtil alone.
"$SDK/bin/prospero-clang" -Wall -g -DPORT="$PORT" \
  -o "$HERE/PKG-MUTANT-SHOP.elf" "$HERE/server.c" "$HERE/jb.c" \
  -lkernel_sys -lSceNotification -lSceUserService -lSceSystemService \
  -lSceAppInstUtil -lScePad -lSceSsl -lSceHttp -lSceRegMgr
echo "BUILT: $HERE/PKG-MUTANT-SHOP.elf"
file "$HERE/PKG-MUTANT-SHOP.elf"
