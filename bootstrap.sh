#!/usr/bin/env bash
# PKG MUTANT SHOP - self-bootstrapping launcher (Linux)
# Ensures Python 3 is present, then starts the app.
# The companion is stdlib-only Python for core function; pystray+Pillow are
# optional (enable a system-tray icon) and can be installed with:
#   pip install --user pystray Pillow
set -e
DIR="$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  echo "Using $(python3 --version)"
else
  echo "python3 not found - attempting automatic install..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y python3
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm python
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y python3
  else
    echo "Could not detect a package manager. Install Python 3.8+ manually, then rerun."
    exit 1
  fi
fi

exec "$DIR/start.sh"
