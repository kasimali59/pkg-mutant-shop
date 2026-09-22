# -*- coding: utf-8 -*-
"""Two-console regression suite for the PC companion, run from source on a spare port.

SUITE 1 - one PS5 configured, the PS4 awake on the LAN. Auto-discovery must ADD the PS4 as a second
          console and leave the configured PS5 completely alone, including ps5_ip.
SUITE 2 - both consoles configured (the user's real setup). Discovery must not run at all, install
          targeting must pick the console that can actually install, an explicit target must win,
          and a PS5 package must be refused for a PS4.

Nothing is installed: every install probe is queued (held), and the server is killed afterwards.
companion/config.json is saved and restored. The shipped exe keeps its own config on the Desktop.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = r"C:\PS5 Projects\PS4 PKG STORE\pkg-mutant-shop"
CFG = os.path.join(REPO, "companion", "config.json")
BAK = CFG + ".regress-backup"
PORT = 8791
BASE = "http://127.0.0.1:%d" % PORT
PS5_IP = "10.0.0.99"          # switched off
PS4_IP = "10.0.0.87"          # awake
REAL_KEY = "Riptide-GP2-CUSA02365.pkg"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  -> " + str(detail)[:150]) if detail else ""))


def req(path, body=None, timeout=60):
    """Returns (status, parsed-or-text). A 4xx is a legitimate answer here, not an exception."""
    if body is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    try:
        return code, json.loads(raw)
    except Exception:
        return code, raw


def write_cfg(consoles, ps5_ip):
    json.dump({
        "companion": {"host": "127.0.0.1", "port": PORT},
        "ftp": {"port": 2121},
        "ps5_ip": ps5_ip,
        "consoles": consoles,
        "library": {},
        "device": {"id": "regress0000000001"},
    }, open(CFG, "w"), indent=2)


def start():
    p = subprocess.Popen([sys.executable, os.path.join(REPO, "companion", "server.py")],
                         cwd=os.path.join(REPO, "companion"),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(45):
        time.sleep(2)
        try:
            req("/api/health", timeout=6)
            return p
        except Exception:
            if p.poll() is not None:
                print("SERVER DIED:\n" + (p.communicate()[0] or "")[-2500:])
                return None
    print("SERVER NEVER CAME UP")
    return None


def stop(p):
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()


proc = None
try:
    shutil.copy2(CFG, BAK)

    # ======================================================== SUITE 1
    print("\n=== SUITE 1: one PS5 configured, the PS4 awake on the LAN ===")
    write_cfg([{"id": "ps5", "name": "PS5", "ip": PS5_IP, "platform": "ps5",
                "ftp_port": 2121, "dpi_port": 12800}], PS5_IP)
    proc = start()
    if proc:
        _, c = req("/api/consoles")
        cons = c.get("consoles") or []
        by_plat = {x.get("platform"): x for x in cons}
        check("the configured PS5 survived", by_plat.get("ps5", {}).get("ip") == PS5_IP,
              json.dumps(cons))
        check("the PS4 was ADDED as a second console",
              by_plat.get("ps4", {}).get("ip") == PS4_IP, "%d console(s)" % len(cons))
        _, h = req("/api/health")
        check("ps5_ip still points at the PS5, not the PS4", h.get("ps5_ip") == PS5_IP,
              str(h.get("ps5_ip")))
    stop(proc)
    proc = None

    # ======================================================== SUITE 2
    print("\n=== SUITE 2: both consoles configured (the real setup) ===")
    write_cfg([{"id": "ps5", "name": "PS5", "ip": PS5_IP, "platform": "ps5",
                "ftp_port": 2121, "dpi_port": 12800},
               {"id": "ps4", "name": "PS4", "ip": PS4_IP, "platform": "ps4",
                "ftp_port": 2121}], PS5_IP)
    proc = start()
    if proc:
        _, c = req("/api/consoles")
        cons = {x["id"]: x for x in (c.get("consoles") or [])}
        check("both consoles present", set(cons) == {"ps5", "ps4"}, sorted(cons))
        check("the PS5 address was not rewritten", cons.get("ps5", {}).get("ip") == PS5_IP,
              cons.get("ps5", {}).get("ip"))
        check("the PS4 address was not rewritten", cons.get("ps4", {}).get("ip") == PS4_IP,
              cons.get("ps4", {}).get("ip"))
        check("the PS4 reports online", cons.get("ps4", {}).get("online") is True,
              cons.get("ps4", {}).get("online"))
        check("the PS5 reports offline", cons.get("ps5", {}).get("online") is False,
              cons.get("ps5", {}).get("online"))

        _, d = req("/api/devices", timeout=60)
        check("/api/devices keeps the legacy `ps5` key", "ps5" in d, sorted(d.keys()))
        check("/api/devices lists both consoles", len(d.get("consoles") or []) == 2,
              len(d.get("consoles") or []))
        ps4row = [x for x in (d.get("consoles") or []) if x.get("platform") == "ps4"]
        check("the PS4 row carries its drives", ps4row and len(ps4row[0].get("devices") or []) > 0,
              len(ps4row[0].get("devices") or []) if ps4row else 0)

        st, r = req("/api/install", {"install_key": REAL_KEY, "title_id": "CUSA02365",
                                     "name": "Regression Probe", "kind": "base", "mode": "queued"})
        # Riptide GP2 really is installed on the PS4, so the honest answer is a skip - and the skip
        # NAMES the console, which is itself the proof that the PS4 was the chosen target and that
        # install state is known per console.
        picked_ps4 = (r.get("consoles") == ["ps4"]
                      or (r.get("skipped") and "PS4" in str(r.get("message", ""))))
        check("no console named -> the console that can install (ps4)", picked_ps4,
              json.dumps(r)[:160])
        check("an already-installed title is skipped, not silently re-sent",
              r.get("skipped") is True and r.get("reason") == "already_installed",
              json.dumps(r)[:120])

        st, r = req("/api/install", {"install_key": REAL_KEY, "title_id": "CUSA02365",
                                     "name": "Regression Probe", "kind": "base", "mode": "queued",
                                     "console": "ps5"})
        check("explicit console=ps5 is honoured", r.get("consoles") == ["ps5"], json.dumps(r)[:160])

        st, r = req("/api/install", {"install_key": REAL_KEY, "title_id": "PPSA00000",
                                     "name": "PS5 Probe", "kind": "base", "mode": "queued",
                                     "console": "ps4"})
        check("a PS5 package aimed at the PS4 is refused with a reason",
              r.get("ok") is False and "PS4" in str(r.get("error", "")), json.dumps(r)[:200])

        st, r = req("/api/install", {"install_key": REAL_KEY, "title_id": "CUSA02365",
                                     "name": "Both Probe", "kind": "base", "mode": "queued",
                                     "console": "all"})
        check("console=all targets both", sorted(r.get("consoles") or []) == ["ps4", "ps5"],
              json.dumps(r)[:160])

        code, page = req("/", timeout=60)
        check("the page is served", isinstance(page, str) and len(page) > 200000,
              len(page) if isinstance(page, str) else type(page).__name__)
finally:
    stop(proc)
    if os.path.exists(BAK):
        shutil.move(BAK, CFG)
        print("\nrestored companion/config.json")

bad = [n for n, ok in results if not ok]
print("\n%d checks, %d failed" % (len(results), len(bad)))
for n in bad:
    print("  FAILED: " + n)
sys.exit(1 if bad else 0)
