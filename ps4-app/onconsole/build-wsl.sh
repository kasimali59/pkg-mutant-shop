#!/usr/bin/env bash
# Build the PS4 on-console payload (the PS4 half of PKG MUTANT SHOP).
#
# This is the sibling of ps5-app/onconsole/build-wsl.sh and deliberately runs THE SAME GATES: the
# two payloads share one web UI, and a UI that will not parse, a language missing a key, or a
# message off house style must stop both builds, not just one. It does NOT touch the PS5 tree.
#
# One-time setup this performs by itself if needed: clone the ps4-payload-dev SDK and apply
# sdk-goldhen.patch.py to it. That patch is what makes a payload actually RUN under GoldHEN - see
# the comments in the patch for exactly why, and never skip it.
#
# Usage (WSL):  bash build-wsl.sh [PORT]
set -e
PORT="${1:-8710}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

SDK="$HOME/sdk/ps4-payload-sdk"
SDK_SRC="$HOME/sdk/ps4-payload-sdk.tmp"
export PS4_PAYLOAD_SDK="$SDK"
export PATH="$HOME/bin:$PATH"

# The SDK's makefiles ask llvm-config for --bindir. This box has clang-18 unpacked without
# llvm-config, so provide the one answer it wants rather than installing a toolchain.
if ! command -v llvm-config >/dev/null 2>&1; then
  mkdir -p "$HOME/bin"
  CLANGBIN="$HOME/clang18/usr/lib/llvm-18/bin"
  printf '#!/bin/sh\ncase "$1" in\n  --bindir) echo "%s" ;;\n  --version) echo "18.1.3" ;;\n  *) echo "" ;;\nesac\n' "$CLANGBIN" > "$HOME/bin/llvm-config"
  chmod +x "$HOME/bin/llvm-config"
fi

if [ ! -d "$SDK_SRC" ]; then
  echo "== fetching the ps4-payload-dev SDK (one time)"
  mkdir -p "$HOME/sdk"
  git clone --depth 1 https://github.com/ps4-payload-dev/sdk.git "$SDK_SRC"
fi

echo "== patching the SDK crt for GoldHEN (idempotent)"
python3 "$HERE/sdk-goldhen.patch.py" "$SDK_SRC"

if [ ! -x "$SDK/bin/orbis-clang" ] || [ "$SDK_SRC/crt/crt.c" -nt "$SDK/target/lib/crt1.o" ]; then
  echo "== building/installing the SDK"
  ( cd "$SDK_SRC" && make DESTDIR="$SDK" install >/dev/null )
fi

TOOLS="$HERE/../../tools"
WEB="$HERE/../../web"

# THE SHARED GATES. Both payloads embed the same web/ directory, so a broken UI must stop this
# build too - it is the single point of failure the PS5 side already learned to gate.
python3 "$TOOLS/check_web.py"      || { echo "ABORT: the UI script is broken - not building." >&2; exit 1; }
python3 "$TOOLS/i18n_report.py" --check || { echo "ABORT: a language is missing keys the app uses." >&2; exit 1; }
python3 "$TOOLS/message_report.py" --check || { echo "ABORT: a user-facing message is off house style." >&2; exit 1; }

# Embed the current UI. Same generator the PS5 build uses, writing OUR copy of the bundle.
python3 "$HERE/../../ps5-app/onconsole/gen_web_bundle.py" "$WEB" "$HERE/web_bundle.h" \
  || { echo "ABORT: could not regenerate web_bundle.h." >&2; exit 1; }

# Keep our copy of the SQLite reader honest against the PS5 original.
python3 "$TOOLS/ps4_sync_sqmini.py" --check \
  || { echo "ABORT: sqmini.h has drifted from ps5-app/onconsole/server.c - re-run tools/ps4_sync_sqmini.py" >&2; exit 1; }

# THE DASHBOARD APP THIS ELF CARRIES. tile_bundle.h .incbin's it, so a missing package is a link
# error with no explanation; say so here instead. Build order is the package first, then this - and
# it is one-way on purpose: the package must never carry this ELF back (see tile_bundle.h).
TILE_PKG="$HERE/../tile-pkg/IV0000-PKGM00001_00-PKGMUTANTSHOP001.pkg"
if [ ! -f "$TILE_PKG" ]; then
  echo "ABORT: the PS4 dashboard app is not built - run ps4-app/tile-pkg/build-wsl.sh first." >&2
  exit 1
fi

echo "== syntax check"
"$SDK/bin/orbis-clang" -Wall -DPORT="$PORT" -fsyntax-only -Werror=implicit-function-declaration \
  "$HERE/server_ps4.c" || { echo "ABORT: server_ps4.c does not compile." >&2; exit 1; }

echo "== link"
"$SDK/bin/orbis-clang" -Wall -g -DPORT="$PORT" \
  -o "$HERE/PKG-MUTANT-SHOP-PS4.elf" "$HERE/server_ps4.c" \
  -lSceAppInstUtil -lSceUserService -lSceSystemService

echo "BUILT: $HERE/PKG-MUTANT-SHOP-PS4.elf"
ls -l "$HERE/PKG-MUTANT-SHOP-PS4.elf"
file "$HERE/PKG-MUTANT-SHOP-PS4.elf"
