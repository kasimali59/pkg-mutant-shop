#!/usr/bin/env python3
"""
PKG MUTANT SHOP - doctor / test kit
===================================
One command to check everything is wired before (and during) the first real install.

    python doctor.py                     # run all diagnostics
    python doctor.py --install CUSA08692 # do a REAL install of that title and watch live progress
                                         # (companion server must be running in another window)

Direct checks need nothing running. The --install test drives the companion API.
"""
import json
import socket
import sys
import time
import urllib.request
import urllib.error

import pkg_meta
from server import load_config, lan_ip, Library

G, Y, R, X = "\033[92m", "\033[93m", "\033[91m", "\033[0m"
OK, WARN, BAD = G + "  OK " + X, Y + " WARN" + X, R + " FAIL" + X


def line(status, label, detail=""):
    print(" [%s] %-30s %s" % (status, label, detail))


def tcp(ip, port, timeout=1.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def api(base, path, method="GET", body=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def diagnostics(cfg):
    print("\n" + "=" * 60)
    print(" PKG MUTANT SHOP - doctor")
    print("=" * 60)

    ip = cfg.get("ps5_ip")
    dpi = cfg["dpi"]["port_v2"]
    ftp = cfg.get("ftp", {}).get("port", 2121)
    cport = cfg["companion"]["port"]

    print("\n- Config")
    line("OK" if ip else "WARN", "PS5 IP", ip or "(not set — edit config.json)")
    line("OK", "DPI v2 port", str(dpi))
    line("OK", "FTP port", str(ftp))
    line("OK", "This PC (LAN)", lan_ip())

    print("\n- Companion")
    base = "http://127.0.0.1:%d" % cport
    server_up = False
    try:
        h = api(base, "/api/health")
        server_up = True
        line("OK", "server running", "v%s on :%d" % (h.get("version"), cport))
    except Exception:
        line("WARN", "server running", "not detected — run: python server.py")

    print("\n- Console reachability (%s)" % (ip or "?"))
    if ip:
        dpi_ok = tcp(ip, dpi)
        line("OK" if dpi_ok else "FAIL", "DPI v2 :%d" % dpi,
             "reachable" if dpi_ok else "no route — is a DPI v2 host running? (Elf Arsenal / ps5-dpi-v2 / etaHEN)")
        # Try both known FTP ports, not just the configured one. etaHEN serves FTP on 1337 and
        # leaves 2121 closed, so a doctor that only probed the config said "FTP: no route" on a
        # perfectly healthy console.
        live_ftp = next((p for p in dict.fromkeys((ftp, 1337, 2121)) if tcp(ip, p)), 0)
        line("OK" if live_ftp else "WARN", "FTP :%d" % (live_ftp or ftp),
             ("reachable" + ("" if live_ftp == ftp else "  (config says :%d)" % ftp))
             if live_ftp else "no route on 2121 or 1337 — needed for install-detection")
    else:
        line("WARN", "console", "set ps5_ip first")

    print("\n- Library (real param.sfo parse)")
    lib = Library(cfg)
    lib.scan()
    if lib.is_empty:
        line("WARN", "titles", "0 found — point library.local_paths at your PKG folder")
    else:
        line("OK", "titles", "%d game(s)" % len(lib.games))
        for g in lib.games[:12]:
            tag = "%s %s" % (g["platform"], g["region"])
            extras = "  +%du +%ddlc" % (len(g["updates"]), len(g["dlc"])) if (g["updates"] or g["dlc"]) else ""
            print("        %-11s %-34s %s%s" % (g["title_id"] or "-", g["name"][:34], tag, extras))

    print("\n- Install path the PS5 will use")
    line("OK", "companion file host", "http://%s:%d/library/<file>" % (lan_ip(), cport))
    print("\nNext: python doctor.py --install <TITLE_ID>   (with the server running)\n")
    return server_up


def do_install(cfg, title_id):
    base = "http://127.0.0.1:%d" % cfg["companion"]["port"]
    try:
        lib = api(base, "/api/library")
    except Exception:
        print(R + "Companion not running. Start it: python server.py" + X)
        return
    game = next((g for g in lib["games"] if g.get("title_id") == title_id), None)
    if not game:
        print(R + "Title %s not in library." % title_id + X)
        print("Available:", ", ".join(g.get("title_id") or "-" for g in lib["games"]))
        return
    if not game["base"] or not game["base"][0].get("install_key"):
        print(R + "No installable base file with a real key for %s (demo entry?)." % title_id + X)
        return

    item = game["base"][0]
    print("\nInstalling: %s  (%s)" % (game["name"], title_id))
    r = api(base, "/api/install", "POST", {
        "install_key": item["install_key"], "title_id": title_id,
        "name": game["name"], "kind": "base", "drive": "internal"})
    if r.get("error"):
        print(R + "Install rejected: %s" % r["error"] + X)
        return
    job = r["ids"][0]
    print("job %s  source %s  url %s\n" % (job, r.get("source"), r["url"]))

    last = None
    while True:
        q = api(base, "/api/queue")
        t = next((x for x in q["tasks"] if x["id"] == job), None)
        if not t:
            break
        bar = int((t.get("pct", 0)) / 4)
        stamp = "%-12s [%-25s] %3d%%  %s" % (t["state"], "#" * bar, t.get("pct", 0), t.get("msg", ""))
        if stamp != last:
            print("  " + stamp)
            last = stamp
        if t["state"] in ("playable", "error", "canceled", "submitted"):
            break
        time.sleep(1)
    print("\nDone.\n")


if __name__ == "__main__":
    cfg = load_config()
    if len(sys.argv) >= 3 and sys.argv[1] == "--install":
        do_install(cfg, sys.argv[2].upper())
    else:
        diagnostics(cfg)
