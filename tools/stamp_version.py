"""Stamp the real version into web/index.html before it is bundled.

The UI carried a hardcoded APP_VERSION used until /api/health answers, so every load
flashed whatever number was last typed there (1.1.1) before snapping to the true one.
Both artifacts bundle web/ directly, so stamping the file once covers the EXE and the ELF.

Single source of truth: companion/server.py VERSION.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "companion", "server.py")
SHOP_C = os.path.join(ROOT, "ps5-app", "onconsole", "server.c")
INDEX = os.path.join(ROOT, "web", "index.html")


def read_version():
    with open(SERVER, encoding="utf-8") as f:
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit("could not find VERSION in companion/server.py")
    return m.group(1)


def elf_version():
    try:
        with open(SHOP_C, "rb") as f:
            m = re.search(rb'#define\s+SHOP_VERSION\s+"([^"]+)"', f.read())
        return m.group(1).decode() if m else None
    except OSError:
        return None


def main():
    ver = read_version()
    elf = elf_version()
    if elf and elf != ver:
        # STAMP it, do not warn about it. The two artifacts must ship the same number, and a
        # warning in the middle of a long build is not a mechanism - it scrolls past. Today the
        # console reported 3.39.0 while the companion said 3.40.0, and the only way to tell which
        # build was actually on the console was the compile timestamp in /api/health.
        raw = open(SHOP_C, "rb").read()
        new_raw, k = re.subn(rb'(#define\s+SHOP_VERSION\s+")[^"]+(")',
                             lambda m: m.group(1) + ver.encode() + m.group(2), raw, count=1)
        if k != 1:
            raise SystemExit("SHOP_VERSION not found in ps5-app/onconsole/server.c")
        open(SHOP_C, "wb").write(new_raw)
        print("stamped server.c -> SHOP_VERSION=%s (was %s)" % (ver, elf))

    raw = open(INDEX, "rb").read()
    text = raw.decode("utf-8")
    new, n = re.subn(r'var APP_VERSION="[^"]*";',
                     'var APP_VERSION="%s";' % ver, text, count=1)
    if n != 1:
        raise SystemExit("APP_VERSION not found in web/index.html")
    if new != text:
        open(INDEX, "wb").write(new.encode("utf-8"))
        print("stamped web/index.html -> APP_VERSION=%s" % ver)
    else:
        print("web/index.html already at %s" % ver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
