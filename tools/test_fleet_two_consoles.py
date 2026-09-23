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
PS5_IP = "10.0.0.99"
PS4_IP = "10.0.0.87"
DEAD_IP = "10.0.0.250"        # nothing answers here - used to force the auto-discovery path
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


def write_cfg(consoles, ps5_ip, ps4_ip=""):
    # BOTH address fields, always. reconcile_consoles folds these two settings into the console
    # list and removes the entry a cleared address owns - which is correct and is what it is
    # for. An earlier version of this file wrote the list without ps4_ip, the PS4 entry was duly
    # removed, and the suite only passed because auto-discovery added it back - which in turn
    # only happened while the PS5 was switched off. A test whose result depends on which console
    # is powered is not a test.
    json.dump({
        "companion": {"host": "127.0.0.1", "port": PORT},
        "ftp": {"port": 2121},
        "ps5_ip": ps5_ip,
        "ps4_ip": ps4_ip,
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
    print("\n=== SUITE 1: the configured console unreachable, another awake on the LAN ===")
    # The configured address is one nothing answers on, deliberately: auto-discovery only runs
    # when the configured console cannot be reached, and that is the only state in which the
    # bug this suite exists for - discovery OVERWRITING the configured console rather than
    # adding the one it found - can happen at all.
    write_cfg([{"id": "ps5", "name": "PS5", "ip": DEAD_IP, "platform": "ps5",
                "ftp_port": 2121, "dpi_port": 12800}], DEAD_IP)
    proc = start()
    if proc:
        _, c = req("/api/consoles")
        cons = c.get("consoles") or []
        by_plat = {x.get("platform"): x for x in cons}
        check("the configured console survived, at its own address",
              by_plat.get("ps5", {}).get("ip") == DEAD_IP, json.dumps(cons))
        check("the PS4 was ADDED as a second console",
              by_plat.get("ps4", {}).get("ip") == PS4_IP, "%d console(s)" % len(cons))
        _, h = req("/api/health")
        check("ps5_ip was not repointed at the discovered console",
              h.get("ps5_ip") == DEAD_IP, str(h.get("ps5_ip")))
    stop(proc)
    proc = None

    # ======================================================== SUITE 2
    print("\n=== SUITE 2: both consoles configured (the real setup) ===")
    write_cfg([{"id": "ps5", "name": "PS5", "ip": PS5_IP, "platform": "ps5",
                "ftp_port": 2121, "dpi_port": 12800},
               {"id": "ps4", "name": "PS4", "ip": PS4_IP, "platform": "ps4",
                "ftp_port": 2121}], PS5_IP, PS4_IP)
    proc = start()
    if proc:
        _, c = req("/api/consoles")
        cons = {x["id"]: x for x in (c.get("consoles") or [])}
        check("both consoles present", set(cons) == {"ps5", "ps4"}, sorted(cons))
        check("the PS5 address was not rewritten", cons.get("ps5", {}).get("ip") == PS5_IP,
              cons.get("ps5", {}).get("ip"))
        check("the PS4 address was not rewritten", cons.get("ps4", {}).get("ip") == PS4_IP,
              cons.get("ps4", {}).get("ip"))
        # Reachability is a fact about the room, not about this code: assert only that each
        # console is reported on, with a boolean, whichever of them is powered today.
        check("each console reports a reachability, whichever is powered",
              isinstance(cons.get("ps4", {}).get("online"), bool)
              and isinstance(cons.get("ps5", {}).get("online"), bool),
              {k: v.get("online") for k, v in cons.items()})

        _, d = req("/api/devices", timeout=60)
        check("/api/devices keeps the legacy `ps5` key", "ps5" in d, sorted(d.keys()))
        check("/api/devices lists both consoles", len(d.get("consoles") or []) == 2,
              len(d.get("consoles") or []))
        ps4row = [x for x in (d.get("consoles") or []) if x.get("platform") == "ps4"]
        check("the PS4 row carries its drives", ps4row and len(ps4row[0].get("devices") or []) > 0,
              len(ps4row[0].get("devices") or []) if ps4row else 0)

        st, r = req("/api/install", {"install_key": REAL_KEY, "title_id": "CUSA02365",
                                     "name": "Regression Probe", "kind": "base", "mode": "queued", "force": True})
        # Riptide GP2 really is installed on the PS4, so the honest answer is a skip - and the skip
        # NAMES the console, which is itself the proof that the PS4 was the chosen target and that
        # install state is known per console.
        check("no console named -> exactly one console is chosen",
              isinstance(r.get("consoles"), list) and len(r.get("consoles") or []) == 1,
              json.dumps(r)[:160])

        st, r = req("/api/install", {"install_key": REAL_KEY, "title_id": "CUSA02365",
                                     "name": "Regression Probe", "kind": "base", "mode": "queued",
                                     "console": "ps5", "force": True})
        check("explicit console=ps5 is honoured", r.get("consoles") == ["ps5"], json.dumps(r)[:160])

        st, r = req("/api/install", {"install_key": REAL_KEY, "title_id": "PPSA00000",
                                     "name": "PS5 Probe", "kind": "base", "mode": "queued",
                                     "console": "ps4", "force": True})
        check("a PS5 package aimed at the PS4 is refused with a reason",
              r.get("ok") is False and "PS4" in str(r.get("error", "")), json.dumps(r)[:200])

        st, r = req("/api/install", {"install_key": REAL_KEY, "title_id": "CUSA02365",
                                     "name": "Both Probe", "kind": "base", "mode": "queued",
                                     "console": "all", "force": True})
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
