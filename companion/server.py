#!/usr/bin/env python3
"""
PKG MUTANT SHOP - companion server  (v1.1.0 - full in-app install via DPI v2 + live download/install progress)
============================================
Stdlib only (Python 3.8+). No pip installs, no CDNs, no external services.

v0.4 adds: multi-console fleet + "send to all", parallel queue with per-console serialization,
unified REAL progress (local byte-count OR polling a remote companion's /api/served), SHA-256
integrity, per-title manifests.

Honest markers:  # REAL works now   # ON-DEVICE real code, console confirms it
"""

import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import sqlite3
import tempfile
import uuid
import platform
import gzip
import threading
import time
import ftplib
import shutil
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, quote, parse_qs

import pkg_meta
import sources as source_engine

VERSION = "3.60.0"

if getattr(sys, "frozen", False):          # PyInstaller one-file .exe
    HERE = os.path.dirname(sys.executable)
    WEB_DIR = os.path.join(sys._MEIPASS, "web")
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    WEB_DIR = os.path.normpath(os.path.join(HERE, "..", "web"))
# The cheat + patch library we ship. Frozen: unpacked beside web/ inside the one-file exe.
# From source: assets/cheats in the repo. It is pushed to the console on demand, never embedded
# in the ELF - see PKG-MUTANT-SHOP.spec for why.
if getattr(sys, "frozen", False):
    CHEATS_DIR = os.path.join(sys._MEIPASS, "cheats")
else:
    CHEATS_DIR = os.path.normpath(os.path.join(HERE, "..", "assets", "cheats"))
CHEAT_SUBDIRS = ("json", "mc4", "shn", "patches", "xml", "xml_orbis", "xml_prospero")
CONSOLE_CHEAT_ROOT = "/data/pkg-mutant-shop/cheats"

# ---------------------------------------------------------------------------------------------
# [audit 17] A LOG, because the shipped exe had none.
#
# Every diagnostic in this file is a print(), and the exe is built --noconsole: stdout is a null
# device. So when an install fails with "the console rejected this PKG", the daemon's real reply -
# the SCE error code, which FTP port answered, whether a reload was even attempted - was printed
# and discarded. The user was left with a one-line UI message and no way to find out anything else.
#
# print() is wrapped rather than replaced at every call site: ~200 existing prints become useful
# immediately, and stdout still works when running from source.
# ---------------------------------------------------------------------------------------------
LOG_PATH = os.path.join(HERE, "pms.log")
LOG_MAX_BYTES = 2 * 1024 * 1024
_log_lock = threading.Lock()
_log_fh = None


def _log_open():
    """Open the log, rotating one generation if it has grown past the cap. Never raises: a logging
    problem must not be able to stop the app it is logging for."""
    global _log_fh
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            old = LOG_PATH + ".1"
            try:
                if os.path.exists(old):
                    os.remove(old)
                os.replace(LOG_PATH, old)
            except OSError:
                pass
        _log_fh = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
        _log_fh.write("\n==== PKG MUTANT SHOP %s started %s ====\n"
                      % (VERSION, time.strftime("%Y-%m-%d %H:%M:%S")))
        _log_fh.flush()
    except Exception:
        _log_fh = None


_real_print = print


def print(*args, **kwargs):                       # noqa: A001 - deliberate shadow
    """print() that also lands in pms.log, with a timestamp."""
    try:
        _real_print(*args, **kwargs)
    except Exception:
        pass                                       # no console attached (frozen, --noconsole)
    if _log_fh is None:
        return
    try:
        line = kwargs.get("sep", " ").join(str(a) for a in args)
        with _log_lock:
            _log_fh.write("%s  %s\n" % (time.strftime("%H:%M:%S"), line))
            _log_fh.flush()                        # a crash must not swallow the last lines
    except Exception:
        pass


CACHE_DIR = os.path.join(HERE, ".cache")
ICON_DIR = os.path.join(CACHE_DIR, "icons")
# Card-sized covers. The originals are 512x512 PNGs averaging ~280 KB, which every browser has
# to decode to a full 1.0 MB of RGBA - for a tile drawn about 220 px wide. On the PS5 that is
# the whole performance story: thirty visible cards meant thirty megabytes of decoded bitmap.
# A 320 px WebP costs roughly 15-25 KB and a quarter of the decode, and is indistinguishable at
# card size. Originals are kept untouched for the detail panel.
THUMB_DIR = os.path.join(CACHE_DIR, "thumbs")
HASH_CACHE_PATH = os.path.join(CACHE_DIR, "hashes.json")
CONFIG_PATH = os.path.join(HERE, "config.json")
EXAMPLE_PATH = os.path.join(HERE, "config.example.json")
INSTALLED_PATH = os.path.join(HERE, "installed.json")

# The one true library root. Created automatically if missing so a fresh install just works.
LIBRARY_ROOT = r"C:\Mutant Games"
LIBRARY_LAYOUT = ("PS4", "PS5")

DEFAULT_CONFIG = {
    "ps5_ip": "192.168.1.50",
    "consoles": [],
    # OUR engine is the only install lane: the companion serves the pkg, the on-console ELF
    # streams it to console storage and registers it itself. Nothing third-party is involved, and
    # it never enters the console's BGFT download queue, so a stalled system job cannot wedge it.
    # The section keeps its old "dpi" name only so an existing config.json still parses.
    # `pldmgr_port` is the one key still read - it is how the
    # console spawns our installer. The rest (mode, host, port_v2, port_v1, port_ezremote,
    # auto_reload, live_probe) described a third-party install daemon and are gone; anything left
    # in an existing config.json is simply ignored.
    "dpi": {"pldmgr_port": 8084},
    "ftp": {"port": 2121},
    "console": {"app_db_path": "/system_data/priv/mms/app.db", "notify_port": 9099},
    # PS5 backups land in /mnt/<drive>/homebrew; ShadowMount scans and mounts them itself.
    # port 10101, NOT 9021: ShadowMountPlus announces its own listener in its log
    # ("[API] HTTP/JSON ready: http://127.0.0.1:10101/api/v1"). 9021 is elfldr, the ELF loader,
    # which is always running - so probing it reported ShadowMount "active" no matter what.
    # It binds loopback-only, so only the console itself can answer this; we just report it.
    "shadowmount": {"port": 10101, "scan_path": "/data/homebrew", "scan_paths_by_drive": {}},
    "cheats": {"root": "/data/cheatrunner"},
    "install": {"ftp_dest": "/data/pkg"},
    "library": {"local_paths": [LIBRARY_ROOT], "sources_file": "sources.json", "serve_local_over_http": True},
    "sources": [],
    "queue": {"max_parallel": 2, "allow_parallel_per_console": False},
    "companion": {"host": "0.0.0.0", "port": 8710},
    "drives": ["internal", "ext0", "ext1", "usb0", "usb1", "usb2", "usb3", "usb4", "usb5", "usb6", "usb7"],
    "maintenance": {"auto_rescan": True, "rescan_interval_sec": 10, "rescan_settle_sec": 3,
                    "temp_sweep_age_sec": 3600},
    "performance": {"serve_chunk_bytes": 1048576},
    "integrity": {"enabled": True, "verify_before_install": True, "on_local_corrupt": "warn"},
    "federation": {"enabled": False, "name": "", "peers": []},
    "content_ownership_ack": False,
}


def install_error_text(rc):
    """Turn an SCE install error code into a sentence. IDENTICAL by design to install_error_text()
    in ps5-app/onconsole/server.c and installer_probe.c - change one, change all three.

    `rc` may be an int or a "0x80B2116F" string; anything unparseable still produces a true
    sentence, which is what stops this table from needing to be exhaustive.
    """
    try:
        code = int(str(rc), 16) if isinstance(rc, str) else int(rc or 0)
    except (TypeError, ValueError):
        code = 0
    if code == 0x80B2116F:
        return ("The console turned the request down before it started - reload PKG MUTANT SHOP "
                "on the PS5 from Payload Manager and try again")
    if code == 0x80B22404:
        return "The console could not fetch the file - check this PC is still sharing it"
    if code == 0x80B21104:
        # No bgft row is created at all for this one. Measured with 20.1 GB free and an 85.29 GB
        # package whose own integrity check passed. It used to fall into the generic 0x80B2xxxx
        # bucket below, which tells the user to download the package again - on an 85 GB file, the
        # one action that cannot possibly help.
        return ("The console would not start this install - it is usually not enough free space "
                "on the drive it installs to")
    if code == 0x80F00003:
        return ("The console does not recognise this file as a package - it may be a backup, "
                "not a PKG")
    if (code & 0xFFFF0000) == 0x80A40000:
        return ("The console's own game database refused it - rebuild the database from Safe Mode, "
                "then try again")
    if (code & 0xFFFF0000) == 0x80B20000:
        return ("The console's installer refused it - the package may be incomplete, so download "
                "it again")
    return "The console refused it - nothing was installed"


class ShopHTTPError(Exception):
    """The console answered, with an HTTP error status. It is REACHABLE - do not tell the user to
    reload it. `payload` is whatever JSON it sent, which normally carries its own sentence."""

    def __init__(self, status, payload, body, url):
        self.status = status
        self.payload = payload or {}
        self.body = body
        self.url = url
        Exception.__init__(self, "%s -> HTTP %d: %s" % (url, status, (body or "")[:180]))

    def sentence(self, fallback):
        """What the console itself said, if it said anything useful."""
        return self.payload.get("message") or self.payload.get("error") or fallback


def engine_words(cfg):
    """(what to call the install engine, what to do when it is not answering).

    There used to be two answers here because there were two lanes. Kept as a function so the
    wording lives in one place, but it can no longer disagree with itself.
    """
    return ("our own install engine",
            "Load PKG MUTANT SHOP on the PS5 from Payload Manager, then press Start queue.")


def drive_label(d):
    return {"internal": "Internal M.2", "usb0": "USB0", "usb1": "USB1", "ext0": "EXT0",
            "ext1": "EXT1"}.get(d, d.upper())


def _first_bridge(srv):
    return srv.fleet.bridge(srv.fleet.ids()[0]) if srv.fleet.consoles else None


_TID_NAME_RE = re.compile(r"^(?:CUSA|PPSA|PLAS|NPXS|NPUB|NPEB)\d{4,5}$", re.I)


def is_placeholder_name(name, tid=None):
    """True when `name` is really a title id (or nothing) rather than something to show a person.

    build_library() falls back to the title id for a console-installed PS4 game this machine knows
    nothing else about - the PS5's app.db has no row for those. That fallback is fine as a last
    resort and wrong the moment anything better turns up, so every place that could supply a real
    name asks this first."""
    s = str(name or "").strip()
    if not s:
        return True
    if tid and s.upper() == str(tid).strip().upper():
        return True
    return bool(_TID_NAME_RE.match(s))


def build_library(srv):
    """PC-folder library (installable) merged with the console's installed games (real, from app.db)."""
    games = [dict(g) for g in srv.library.games]
    by_tid = {g["title_id"]: g for g in games if g.get("title_id")}
    bridge = _first_bridge(srv)
    apps = bridge.console_apps() if bridge else None
    if apps:
        for a in apps:
            if a["platform"] not in ("PS4", "PS5"):
                continue                       # skip system apps in the game library
            g = by_tid.get(a["title_id"])
            csize = a.get("size") or 0         # REAL installed size (tbl_contentinfo.size == AppInfoJson #_size)
            if g:                              # already in PC folder → enrich; keep the pkg file size as primary
                g["on_console"] = True
                g["installed_drive"] = a["drive"]
                g["installed_version"] = a.get("app_ver") or ""   # lets the UI hide applied patches
                g["console_size"] = csize
                g["backup_path"] = a.get("backup_path")
                g["region"] = (g.get("region") if g.get("region") not in (None, "", "—") else a.get("region")) or "—"
                if a.get("source") == "backup":
                    g["size"] = a["size"]
                    g["format"] = a.get("format") or g.get("format")
                g["size_known"] = True
            else:                              # installed on console (not in the PC folder) → real console data
                games.append({"title_id": a["title_id"], "content_id": a.get("content_id", ""),
                              "name": a["name"], "platform": a["platform"],
                              "region": a.get("region", "—"), "lane": "installed", "on_console": True,
                              "installed_drive": a["drive"], "installed_version": a.get("app_ver") or "",
                              "size": csize, "console_size": csize,
                              "size_known": csize > 0,
                              "format": a.get("format") or (a["platform"] + " app"),
                              "backup_path": a.get("backup_path"), "source": a.get("source"),
                              # A backup has a real container behind it, so it can be moved to
                              # another drive. "Move to another drive" is shown only for a title
                              # with `movable` AND a path, so without these the one control that
                              # puts a game on a different drive was hidden for precisely the
                              # games that have a file to move. Kept in step with the console's
                              # own library output.
                              "movable": bool(a.get("backup_path")),
                              "local_path": a.get("backup_path") or None,
                              "runs_in_place": bool(a.get("backup_path")),
                              "cover_seed": a["title_id"], "has_icon": bool(a.get("icon_path")),
                              "base": [], "updates": [], "dlc": [], "cheats": []})

    # Packages sitting on a USB stick plugged into the PS5. The console app finds these on
    # its own; without this the same stick would appear ONLY when the PC was switched off,
    # which is exactly the kind of inconsistency that makes the app feel unreliable.
    # Matched by title id so a stick copy never duplicates a title already listed.
    # Only worth asking when the console answered a moment ago. This call allows 20s for a
    # console that is genuinely busy scanning sticks, which is right when it is there and
    # completely wrong when it is not — it was the bulk of the ~25s the library took with the
    # PS5 off. `apps is not None` is the same reachability signal reported below.
    if bridge and apps is not None:
        try:
            for cg in (bridge.console_usb_packages() or []):
                tid = cg.get("title_id")
                if tid and tid in by_tid:
                    if not by_tid[tid].get("base"):
                        by_tid[tid]["base"] = cg.get("base") or []
                    continue
                games.append(cg)
                if tid:
                    by_tid[tid] = cg
        except Exception:
            pass

    games.sort(key=lambda g: g["name"].lower())
    return {"empty": len(games) == 0, "count": len(games),
            "console_reachable": apps is not None, "games": games}


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" % (n, unit)) if unit in ("B", "KB") else ("%.2f %s" % (n, unit))
        n /= 1024.0
    return "%.2f TB" % n


def device_identity(cfg):
    """Stable identity for this PC. Written to config once and reused forever: a hostname
    changes and is not unique, so peers need something they can key on to recognise the same
    machine after a restart, a rename, or a DHCP address change."""
    dev = cfg.setdefault("device", {})
    if not dev.get("id"):
        dev["id"] = uuid.uuid4().hex[:16]
        try:
            save_config(cfg)
        except Exception:
            pass                                   # a read-only config still works, just not sticky
    if not dev.get("name"):
        dev["name"] = socket.gethostname()
    return dev


def _library_capacity(srv):
    """Free/total of the drive this PC's library actually lives on.

    Every PC already knew this and never sent it, so a PC tile could only ever show a game count
    while a console drive showed free space - the same row of tiles saying two different things."""
    paths = (srv.cfg.get("library", {}) or {}).get("local_paths") or []
    for p in paths:
        try:
            import shutil
            du = shutil.disk_usage(p if os.path.isdir(p) else os.path.dirname(p) or p)
            return {"free": du.free, "total": du.total}
        except Exception:
            continue
    return {"free": None, "total": None}


def _library_counts(srv):
    ps4 = ps5 = 0
    nbytes = 0
    for g in srv.library.games:
        if (g.get("platform") or "") == "PS5":
            ps5 += 1
        elif (g.get("platform") or "") == "PS4":
            ps4 += 1
        # How much this PC is actually holding. Peers report it too, so every machine can be shown
        # on the storage bar with a real size instead of just a title count.
        for kind in ("base", "updates", "dlc"):
            for it in (g.get(kind) or []):
                nbytes += int(it.get("size") or 0)
    return {"ps4": ps4, "ps5": ps5, "total": len(srv.library.games), "bytes": nbytes}


def discover_peers(cfg, timeout=0.35, port=None):
    """Find other PKG MUTANT SHOP companions on this /24 by probing the companion port.
    Same approach as the PS5 scan: parallel connects, short timeout, then identify each
    responder so we never merge a library from something that is not one of ours."""
    port = port or cfg.get("companion", {}).get("port", 8710)
    ip = lan_ip()
    if "." not in ip:
        return []
    me = device_identity(cfg).get("id")
    base = ip.rsplit(".", 1)[0]
    found, lock = [], threading.Lock()

    def probe(i):
        host = "%s.%d" % (base, i)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except OSError:
            return
        # Generous on purpose. The port answering in milliseconds says nothing about how long the
        # peer takes to describe itself — it builds a library listing to do it, and an older build
        # may still probe its console first. 2.5s was under that: every scan saw the open port,
        # timed out identifying it, and threw the peer away. That alone is why PCs never found
        # each other unless the PS5 was up to introduce them.
        info = http_get_json("http://%s:%d/api/federation" % (host, port), timeout=8.0)
        # Only ours, and never ourselves (the same box answers on several addresses).
        if not info or not info.get("id") or info.get("id") == me:
            return
        info["lan_ip"] = info.get("lan_ip") or host
        info["url"] = "http://%s:%d" % (host, port)
        with lock:
            found.append(info)

    threads = [threading.Thread(target=probe, args=(i,), daemon=True) for i in range(1, 255)]
    for t in threads:
        t.start()
    # One deadline for the whole sweep, not four seconds per thread. Waiting per-thread meant a
    # slow-to-identify peer could still be mid-answer when its turn came and be lost anyway.
    deadline = time.time() + 12.0
    for t in threads:
        t.join(timeout=max(0.05, deadline - time.time()))
    return sorted(found, key=lambda x: x.get("name") or x.get("url"))


class PeerRegistry:
    """Known companions, refreshed in the background so the library merge never blocks on a
    LAN scan. Peers are remembered by device id, so a peer that changes address is still the
    same peer rather than a duplicate entry."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.peers = {}                 # id -> info
        self.last_scan = 0
        self.scanning = False
        self.lock = threading.Lock()

    def known(self):
        with self.lock:
            return list(self.peers.values())

    def _merge(self, infos):
        with self.lock:
            for info in infos:
                pid = info.get("id")
                if not pid:
                    continue
                prev = self.peers.get(pid, {})
                info["first_seen"] = prev.get("first_seen") or now_ms()
                info["last_seen"] = now_ms()
                info["online"] = True
                self.peers[pid] = info
            self.last_scan = now_ms()

    def scan(self):
        infos = discover_peers(self.cfg)
        self._merge(infos)
        me = device_identity(self.cfg).get("id")
        for info in infos:                 # and whoever they can see, so the set converges
            self._adopt(info, me)
        return self.known()

    def scan_async(self, min_interval_ms=60000):
        with self.lock:
            if self.scanning or (now_ms() - self.last_scan) < min_interval_ms:
                return
            self.scanning = True

        def run():
            try:
                self.scan()
            except Exception:
                pass
            finally:
                with self.lock:
                    self.scanning = False

        threading.Thread(target=run, daemon=True).start()

    def refresh_online(self):
        """Re-check the peers we already know, without a full /24 sweep."""
        me = device_identity(self.cfg).get("id")
        for pid, info in list(self.peers.items()):
            url = info.get("url")
            if not url:
                continue
            # Same budget as discovery: a peer describing itself is not a ping, and cutting it
            # short here would quietly mark a perfectly healthy machine as offline.
            fresh = http_get_json(url.rstrip("/") + "/api/federation", timeout=8.0)
            with self.lock:
                if fresh and fresh.get("id"):
                    fresh["url"] = url
                    fresh["first_seen"] = info.get("first_seen")
                    fresh["last_seen"] = now_ms()
                    fresh["online"] = True
                    self.peers[fresh["id"]] = fresh
                else:
                    self.peers[pid]["online"] = False
            self._adopt(fresh, me)
        return self.known()

    def _adopt(self, info, me):
        """Take on the PCs a peer can see. Every companion advertises `known_pcs`, so one machine
        finding another is enough for the whole set to converge — no repeated /24 sweeps, and it
        keeps working when the PS5 (which used to do the introducing) is off."""
        if not info:
            return
        port = self.cfg.get("companion", {}).get("port", 8710)
        mine = lan_ip()
        for kp in (info.get("known_pcs") or []):
            ip = kp.get("lan_ip")
            if not ip or ip == mine:
                continue
            with self.lock:
                if any((p.get("lan_ip") == ip) for p in self.peers.values()):
                    continue
            got = http_get_json("http://%s:%d/api/federation" % (ip, int(kp.get("port") or port)),
                                timeout=8.0)
            if got and got.get("id") and got.get("id") != me:
                got["lan_ip"] = got.get("lan_ip") or ip
                got["url"] = "http://%s:%d" % (ip, int(kp.get("port") or port))
                self._merge([got])

    def keepalive(self, srv=None):
        """Keep the peer list true without anyone opening the app.

        Discovery used to happen only as a side effect of a request, so two PCs sitting idle never
        found each other at all. A full sweep is expensive, so it runs rarely; re-checking the
        machines we already know is cheap, so it runs often."""
        def loop():
            first = True
            while True:
                try:
                    if first or (now_ms() - self.last_scan) > 300000:   # full sweep at most every 5 min
                        self.scan()
                        first = False
                    else:
                        self.refresh_online()
                except Exception:
                    pass
                time.sleep(20)

        threading.Thread(target=loop, daemon=True).start()


def _with_peer_sources(srv, doc):
    """Fold the other PCs into the source list. They really ARE sources - the console can pull a
    game straight from them - and counting only download mirrors is why the header said
    "1 source" however many machines were connected."""
    try:
        reg = getattr(srv, "peers", None)
        peers = reg.known() if reg is not None else []
        srcs = list(doc.get("sources") or [])
        for p in peers:
            if not p.get("online"):
                continue
            c = p.get("counts") or {}
            srcs.append({"name": p.get("name") or p.get("lan_ip"), "ok": True, "kind": "peer",
                         "latency_ms": None, "titles": c.get("total", p.get("count", 0))})
        doc["sources"] = srcs
        doc["peers"] = [{"name": p.get("name"), "online": bool(p.get("online")),
                         "lan_ip": p.get("lan_ip")} for p in peers]
    except Exception:
        pass
    return doc


def _known_pc_addrs(srv):
    """Compact address list of the other companions we know about, for peer-of-peer discovery."""
    out = []
    try:
        reg = getattr(srv, "peers", None)
        for p in (reg.known() if reg is not None else []):
            ip = p.get("lan_ip")
            if not ip:
                url = p.get("url") or ""
                ip = url.split("//")[-1].split(":")[0] if "//" in url else ""
            if not ip:
                continue
            prt = p.get("companion_port") or srv.cfg["companion"]["port"]
            out.append({"lan_ip": ip, "port": int(prt), "online": bool(p.get("online"))})
    except Exception:
        pass
    return out


def federation_self(srv):
    """What this companion advertises to federation peers: its name + a compact list of its installable
    library, so another companion can present one merged library. [B9]"""
    dev = device_identity(srv.cfg)
    name = srv.cfg.get("federation", {}).get("name") or dev.get("name") or socket.gethostname()
    port = srv.cfg["companion"]["port"]
    games = []
    for g in srv.library.games:
        # Every installable file this PC holds for the title, tagged with what it is. Sending
        # only the base is what made add-ons invisible from other machines.
        items = []
        for kind, lst in (("base", g.get("base")), ("update", g.get("updates")), ("dlc", g.get("dlc"))):
            for it in (lst or []):
                if not it.get("install_key"):
                    continue
                items.append({"install_key": it["install_key"], "kind": it.get("kind") or kind,
                              "version": it.get("version"), "size": it.get("size", 0),
                              "file": it.get("file"), "content_id": it.get("content_id", ""),
                              # container type per file, so a backup stays identifiable as one
                              "format": it.get("format") or g.get("format"),
                              "parts": it.get("parts"), "multi_part": it.get("multi_part", False)})
        if not items:
            continue
        base = (g.get("base") or [{}])[0]
        games.append({"title_id": g.get("title_id"), "name": g.get("name"), "size": g.get("size"),
                      "platform": g.get("platform"), "region": g.get("region"),
                      "update_only": bool(g.get("update_only")),
                      # kept for older peers that only understand a single key
                      "install_key": base.get("install_key") or items[0]["install_key"],
                      "version": base.get("version"), "items": items,
                      # A ShadowMount backup (.ffpfs / .ffpfsc / .ffpkg …) is not a PKG: it mounts
                      # instead of installing, and its container type is the whole point. Neither
                      # travelled, so a backup held on another PC arrived looking like a package
                      # with no format at all — which is why its panel read "—" everywhere.
                      "format": g.get("format"),
                      # Cover art lives on whichever PC holds the game. Tell peers it exists and
                      # where to get it, or every other device falls back to drawing initials.
                      "has_icon": bool(g.get("has_icon")),
                      "thumb_url": ("http://%s:%d/thumb/%s.webp" % (lan_ip(), port, g.get("title_id")))
                                   if g.get("title_id") else "",
                      "icon_url": ("http://%s:%d/icon/%s.png" % (lan_ip(), port, g.get("title_id")))
                                  if (g.get("has_icon") and g.get("title_id")) else None})
    con = _first_bridge(srv)
    return {"id": dev.get("id"), "name": name, "hostname": socket.gethostname(),
            "os": platform.system(), "app": "PKG MUTANT SHOP", "version": VERSION,
            "lan_ip": lan_ip(), "companion_port": port,
            "url": "http://%s:%d" % (lan_ip(), port),
            "lan_url": "http://%s:%d/library/" % (lan_ip(), port),
            "console": {"ip": con.ip if con else None,
                        # read-only: this reply is how other PCs identify us, so it must
                        # never wait on the console. Health refreshes the value.
                        "online": bool(con and con.pldmgr.alive(probe=False))} if con else None,
            "counts": _library_counts(srv), "count": len(games), "games": games,
            # So another machine can draw this PC's tile with the same four facts as its own.
            "storage": _library_capacity(srv),
            # Who else we can see. The console reads this and registers them itself, so one PC
            # finding the PS5 is enough for every PC to be found - no subnet sweep on the console.
            "known_pcs": _known_pc_addrs(srv)}


def build_federated_library(srv):
    """Local library merged with every peer companion's library, deduped by title id.

    A title can live on more than one PC, so each entry carries `hosts` — who has a copy and
    where to fetch it from. Peer-only titles are still installable: the console downloads
    straight from that PC, so nothing is proxied through this one.

    Peers come from two places: ones discovered on the LAN, and ones written into the config
    by hand. Discovery means a second PC only has to run the app to join."""
    result = build_library(srv)
    games = result["games"]
    by_tid = {g.get("title_id"): g for g in games if g.get("title_id")}
    me = device_identity(srv.cfg)

    # Everything local is hosted by THIS pc — say so, so the UI can show provenance
    # consistently instead of only labelling the remote ones.
    for g in games:
        if g.get("base") and any(b.get("install_key") for b in g["base"]):
            g.setdefault("hosts", []).append({"pc": me.get("name"), "id": me.get("id"),
                                              "local": True, "url": None})
            g.setdefault("source_pc", me.get("name"))
            g.setdefault("source_id", me.get("id"))

    fed = srv.cfg.get("federation", {})
    peers = []
    reg = getattr(srv, "peers", None)
    if reg is not None:
        reg.scan_async()                       # keeps itself fresh without blocking this request
        peers.extend(reg.known())
    seen_urls = {p.get("url") for p in peers if p.get("url")}
    for peer in fed.get("peers", []):          # hand-configured peers still work
        purl = peer if isinstance(peer, str) else (peer.get("url") if isinstance(peer, dict) else None)
        if not purl or purl in seen_urls:
            continue
        info = http_get_json(purl.rstrip("/") + "/api/federation", timeout=2.5)
        if info:
            info["url"] = purl
            info["online"] = True
            peers.append(info)
        else:
            peers.append({"url": purl, "name": purl, "online": False, "games": [], "count": 0})

    peers_status = []
    for info in peers:
        purl = info.get("url") or ""
        peers_status.append({"url": purl, "id": info.get("id"), "name": info.get("name") or purl,
                             "online": bool(info.get("online")), "count": info.get("count", 0),
                             "counts": info.get("counts"), "lan_ip": info.get("lan_ip"),
                             "os": info.get("os"), "version": info.get("version"),
                             "last_seen": info.get("last_seen")})
        if not info.get("online"):
            continue
        base_url = info.get("lan_url") or (purl.rstrip("/") + "/library/")
        for pg in info.get("games", []):
            tid = pg.get("title_id")
            host = {"pc": info.get("name"), "id": info.get("id"), "local": False,
                    "url": base_url + (pg.get("install_key") or "")}
            # Older peers send one key; newer ones send every file with its kind.
            pitems = pg.get("items") or [{"install_key": pg.get("install_key"), "kind": "base",
                                          "version": pg.get("version"), "size": pg.get("size", 0),
                                          "file": pg.get("name")}]

            def _mk(it):
                return {"install_key": it.get("install_key"), "file": it.get("file") or pg.get("name"),
                        "size": it.get("size", 0), "kind": it.get("kind") or "base",
                        "version": it.get("version"), "content_id": it.get("content_id", ""),
                        "format": it.get("format") or pg.get("format"),
                        "parts": it.get("parts"), "multi_part": it.get("multi_part", False),
                        "peer_url": base_url + (it.get("install_key") or "")}

            g = by_tid.get(tid) if tid else None
            if g:
                # Same title on another PC. Record the extra source and fold in any file this
                # machine does not have — that is how a peer's DLC reaches the game's panel.
                if not any(h.get("id") == host["id"] for h in g.get("hosts", [])):
                    g.setdefault("hosts", []).append(host)
                for it in pitems:
                    if not it.get("install_key"):
                        continue
                    bucket = {"base": "base", "update": "updates", "dlc": "dlc"}.get(it.get("kind") or "base", "base")
                    have = {x.get("install_key") for x in (g.get(bucket) or [])}
                    if it["install_key"] not in have:
                        g.setdefault(bucket, []).append(_mk(it))
                if g.get("base"):
                    g["update_only"] = False
                # A TITLE ID IS NOT A NAME. This title can already be here as a console-only
                # entry whose name fell back to the id (build_library, via server.py "name": tid)
                # because the PS5's app.db carries no row for an installed PS4 game. The peer that
                # holds the package knows what it is actually called - pg["name"] is the game name,
                # the filename lives in items[].file - and we are about to borrow its cover anyway.
                # One direction only: a placeholder is upgraded, a real name is never overwritten.
                if is_placeholder_name(g.get("name"), tid) and not is_placeholder_name(pg.get("name"), tid):
                    g["name"] = pg["name"]
                if not g.get("has_icon") and pg.get("icon_url"):
                    g["has_icon"] = True                 # borrow the peer's artwork
                    g["icon_url"] = pg["icon_url"]
                    if pg.get("thumb_url"):
                        g["thumb_url"] = pg["thumb_url"]   # and its card-sized copy
                continue

            buckets = {"base": [], "updates": [], "dlc": []}
            for it in pitems:
                if not it.get("install_key"):
                    continue
                buckets[{"base": "base", "update": "updates", "dlc": "dlc"}.get(it.get("kind") or "base", "base")].append(_mk(it))
            # Carry the container type and version the owner reported. Falling back to the file
            # extension matters: a peer on an older build advertises no format at all, and the
            # filename is proof enough — so backups from a PC that has not been updated still
            # identify themselves correctly.
            pfmt = (pg.get("format") or (pitems[0].get("format") if pitems else None)
                    or fmt_from_filename(pg.get("name"))
                    or (fmt_from_filename(pitems[0].get("file")) if pitems else None))
            # A backup is a backup wherever it happens to sit: it mounts, so it gets the mount lane
            # and with it the drive picker, exactly as a local one does. The peer-mount lane fetches
            # it here first; every container in MOUNT_EXTS behaves the same way.
            pbackup = any(is_backup_item(i, pg) for i in pitems) if pitems else is_backup_item({}, pg)
            ng = {"title_id": tid, "name": pg.get("name"), "platform": pg.get("platform", "PS4"),
                  "region": pg.get("region") or "—", "size": pg.get("size", 0),
                  "lane": "mount" if pbackup else "install",
                  "format": pfmt, "version": pg.get("version"),
                  "size_known": bool(pg.get("size")),
                  "cover_seed": tid or pg.get("name"),
                  "has_icon": bool(pg.get("icon_url")), "icon_url": pg.get("icon_url"),
                  # The owner serves its own art: never rewrite this to point at us.
                  "thumb_url": pg.get("thumb_url") or "",
                  "source_pc": info.get("name"), "source_id": info.get("id"), "remote": True,
                  "update_only": (not buckets["base"]) or bool(pg.get("update_only")),
                  "base": buckets["base"], "updates": buckets["updates"], "dlc": buckets["dlc"],
                  "cheats": [], "hosts": [host]}
            games.append(ng)
            if tid:
                by_tid[tid] = ng

    # CARD ART: point every local title at its card-sized thumbnail.
    #
    # This is the single most expensive omission in the whole app, and it was invisible because
    # each half looked right on its own. /api/federation has always advertised thumb_url (it is
    # built ~140 lines above), and the console's own /api/library sets it on 112 of 113 titles.
    # But the PS5 does not read either of those: the page is served from the console, then
    # resolveApi() upgrades API to a PC companion, so every cover the console paints comes from
    # THIS endpoint - and this endpoint never set the field. iconUrl() in the UI prefers
    # thumb_url, finds none, falls through icon_url, and lands on the full-size PNG.
    #
    # Measured across a 10-title sample: 415,866 bytes average per cover instead of 26,409 - a
    # 15.7x difference on the wire - and 1,048,576 bytes of decoded RGBA per cover instead of
    # 409,600, held for every card in the DOM at once. On the first screen alone that is roughly
    # 20 MB of PNG and ~55 MB of bitmap. The thumbnails already existed on this very host.
    #
    # Only ever fill in a blank: a peer-hosted title already carries its owner's URL above, and
    # rewriting that to point here would serve art we do not have.
    _port = int((srv.cfg.get("companion") or {}).get("port") or 8710)
    _ip = lan_ip()
    for g in games:
        if g.get("thumb_url") or not g.get("title_id") or not g.get("has_icon"):
            continue
        g["thumb_url"] = "http://%s:%d/thumb/%s.webp" % (_ip, _port, g["title_id"])

    games.sort(key=lambda g: (g.get("name") or "").lower())
    result["games"] = games
    result["count"] = len(games)
    result["peers"] = peers_status
    result["device"] = {"id": me.get("id"), "name": me.get("name")}
    return result


def _drive_of_backup(path):
    """Which console drive a container actually sits on, from its own path. app.db cannot answer
    this - it records where the TITLE is registered, not where the file is."""
    p = str(path or "")
    m = re.match(r"^/mnt/(ext[0-2]|usb[0-7])/", p)
    if m:
        return m.group(1)
    if p.startswith("/data/"):
        return "internal"
    return ""


def build_storage(srv, apps, devices=None, console_online=True):
    """What is actually holding games right now — console drives AND the PCs.

    Only live, populated places are listed. Every configured drive used to be emitted whenever the
    console was unreachable, which filled the bar with a row of identical "0 games" tiles that said
    nothing: eleven slots for storage that either was not there or held nothing. A drive earns its
    place by having something on it.

    The PCs belong here for the same reason the drives do — they are where games live, and the
    console installs straight from them. Each is listed with its real title count and size.

    `console_online` is what decides whether the console drives appear at all, and it is NOT the
    same question as "do we have an app list". console_apps() keeps serving its last good list for
    the lifetime of the process, which is correct for the library — browsing your games with the
    PS5 off is the point of it — but a storage bar is a live readout of what is plugged in. Drawing
    it from a remembered app.db left four tiles up with no capacity behind them, all reading
    "space unknown", and with the capacity map empty the loc:2 fold below could not run either, so
    the M.2 came back as two tiles. A drive is shown when the console is answering, and not before.
    """
    drives = []
    if apps is not None and console_online:
        # REAL capacity, straight from the console's statvfs. Without it a tile can only show a
        # used figure with nothing to judge it against - which is how "3352 GB" sat unquestioned on
        # a 2 TB drive.
        cap = {}
        for d in (devices or []):
            if d.get("detected") and d.get("total"):
                cap[str(d.get("id"))] = {"label": d.get("label") or str(d.get("id")),
                                         "free": d.get("free"), "total": d.get("total")}
        used, cnt, label = {}, {}, {}

        def _add(key, nice, size):
            used[key] = used.get(key, 0) + int(size or 0)
            cnt[key] = cnt.get(key, 0) + 1
            label.setdefault(key, nice)

        for a in apps:
            if a["platform"] not in ("PS4", "PS5"):
                continue
            # A backup lives wherever its container is, which app.db does not know. Only fall back
            # to app.db's location code when there is no container to look at.
            key = _drive_of_backup(a.get("backup_path"))
            if key:
                _add(key, (cap.get(key) or {}).get("label") or key.upper(), a.get("size"))
            elif a.get("location"):
                loc = a["location"]
                _add("loc:" + loc, Ps5Bridge.LOC_LABEL.get(loc, "Storage " + loc), a.get("size"))

        # FOLD THE app.db BUCKETS INTO THE REAL DRIVES THEY NAME. A title with a container is
        # bucketed by where that container actually is; one without falls back to app.db's location
        # code. Both can describe the SAME physical drive - which is why the M.2 appeared twice,
        # once as "ext1" with its 33 backups and once as "loc:2" with its 6 packages.
        #   location "0" -> the internal drive
        #   location "2" -> whichever extended drive is actually present
        _ext = next((i for i in ("ext1", "ext0", "ext2") if i in cap), None)
        for src, dst in (("loc:0", "internal"), ("loc:2", _ext)):
            if src in used and dst and dst in cap:
                used[dst] = used.get(dst, 0) + used.pop(src)
                cnt[dst] = cnt.get(dst, 0) + cnt.pop(src)
                label.setdefault(dst, (cap.get(dst) or {}).get("label") or dst)

        for key in sorted(used):
            if not cnt.get(key):
                continue
            c = cap.get(key) or {}
            if not c and key == "loc:0":
                c = cap.get("internal") or {}
            # A DRIVE EARNS ITS TILE BY BEING PLUGGED IN. These buckets are built from app.db and
            # from container paths, and neither notices hardware leaving: pull a stick and app.db
            # still lists its titles; remove the M.2 and rows still say location "2", which the
            # fold above then has no real drive to merge into. Either way the bucket has no
            # capacity behind it and used to render as a tile reading "space unknown" - or, in the
            # second case, as a second tile for a drive that already had one. `cap` holds only what
            # the console just reported as detected, so this is the whole test.
            if not c:
                continue
            # THE FILESYSTEM OVER app.db. `used[key]` is the sum of each title's app.db size, and
            # app.db's size is metadata - it claimed 790.9 GB of games on a 673.9 GB drive. When the
            # console has given us a real statvfs pair, total-free is what is actually occupied.
            real = used[key]
            if c.get("total") and c.get("free") is not None:
                real = max(0, int(c["total"]) - int(c["free"]))
            drives.append({"id": key, "label": label.get(key, key), "used": real,
                           "games_bytes": used[key],
                           "free": c.get("free"), "total": c.get("total"),
                           "count": cnt[key], "kind": "console"})

    # This PC, then every peer that is online and actually holding something.
    me = device_identity(srv.cfg)
    mine = _library_counts(srv)
    if mine.get("total"):
        _capme = _library_capacity(srv)
        drives.append({"id": "pc:" + str(me.get("id")), "label": me.get("name") or "This PC",
                       "used": mine.get("bytes") or None,
                       "free": _capme.get("free"), "total": _capme.get("total"),
                       "count": mine.get("total", 0), "kind": "pc", "local": True})
    reg = getattr(srv, "peers", None)
    for p in (reg.known() if reg is not None else []):
        if not p.get("online"):
            continue
        c = p.get("counts") or {}
        n = c.get("total", p.get("count", 0)) or 0
        if not n:
            continue
        # A peer on an older build reports no byte total. It still lists every file it holds, so
        # add them up here rather than showing that machine as an empty tile.
        nb = c.get("bytes")
        if not nb:
            nb = sum(int(it.get("size") or 0)
                     for g in (p.get("games") or []) for it in (g.get("items") or []))
        # A peer that advertises its library drive gets the same four facts as every other tile.
        # An older peer sends no "storage" block; its tile simply has no capacity to show, rather
        # than the whole row of tiles each showing a different thing.
        _st = p.get("storage") or {}
        drives.append({"id": "pc:" + str(p.get("id")), "label": p.get("name") or p.get("lan_ip"),
                       "used": nb or None,
                       "free": _st.get("free"), "total": _st.get("total"),
                       "count": n, "kind": "pc", "local": False, "lan_ip": p.get("lan_ip")})

    return {"reachable": apps is not None and console_online, "drives": drives}


def _identifies_as_console(host, port=8710, timeout=1.6):
    """Does this host say IT IS a console? Returns True / False / None (could not ask).

    An open port cannot answer this. A peer PC running the companion listens on the same 8710 and
    speaks the same API; the only difference is what it says about itself. Our on-console ELF sets
    on_console true and server "on-console"; the companion does not."""
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (host, port), timeout=timeout) as r:
            h = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(h, dict):
        return None
    return bool(h.get("on_console")) or h.get("server") == "on-console"


def discover_ps5(cfg):
    """Scan the local /24 for a PS5. Only hosts that identify themselves as a console qualify."""
    ip = lan_ip()
    if "." not in ip:
        return []
    base = ip.rsplit(".", 1)[0]
    # Both FTP ports, not just the configured one: a console running etaHEN serves FTP on 1337 and
    # 2121 is closed, so scanning only the configured port found the console solely by its DPI port
    # - and missed it completely whenever the install host happened to be down.
    ports = tuple(dict.fromkeys(
        (8710, 8084, cfg.get("ftp", {}).get("port", 2121), 2121)))
    found, lock = [], threading.Lock()

    def probe(i):
        host = "%s.%d" % (base, i)
        openp = []
        for p in ports:
            try:
                with socket.create_connection((host, p), timeout=0.35):
                    openp.append(p)
            except OSError:
                pass
        if not openp:
            return
        # ASK, do not assume. Without this a peer PC running the companion was indistinguishable
        # from the console and could be adopted as one - and then written to config.json.
        says = _identifies_as_console(host) if 8710 in openp else None
        if says is False:
            return                      # it told us it is not a console. Believe it.
        # If it would not answer, fall back to a fingerprint only a PS5 has: Payload Manager on
        # 8084. A peer PC runs the companion on 8710 and nothing on 8084, so requiring 8084 keeps
        # a slow-to-answer peer from being mistaken for a console that is merely busy.
        if says is None and 8084 not in openp:
            return
        with lock:
            found.append({"ip": host, "ports": openp, "confirmed": bool(says)})

    threads = [threading.Thread(target=probe, args=(i,)) for i in range(1, 255)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.0)
    # A confirmed console always outranks one we merely could not rule out, so the arbitrary
    # lowest-address tiebreak can no longer put a PC ahead of the real console.
    return sorted(found, key=lambda x: (0 if x.get("confirmed") else 1, x["ip"]))

# States in which a worker thread is actively driving the task. "reloading" belongs here: the
# worker is inside ensure_dpi_ready()/recover_dpi() for up to 75s, and leaving it out let _claim()
# start another job past max_parallel while that one was still in flight.
RUNNING = {"claimed", "verifying", "submitting", "transferring", "promoting", "reloading"}
# Anything that installs ON TOP of a title that is already there. These cannot be verified the way
# a base game is: the parent is already registered and its app.pkg is already full size, so both of
# those checks pass before the add-on has done anything at all.
ADDON_KINDS = {"update", "patch", "dlc", "backport"}


def _ver_key(v):
    """"01.04" -> (1, 4) so versions compare numerically. Empty/odd input -> ()."""
    parts = re.findall(r"\d+", str(v or ""))
    return tuple(int(x) for x in parts) if parts else ()
# bgft.db tbl_downloads.status values that mean the transfer+install finished.
# 1036 = a full title install, 1026 = a patch/update install. Anything else is in flight or parked.
BGFT_DONE = {1026, 1036}
# QUEUED-BUT-DEAD. status 1000 with length 0 and nothing transferred is a job the console accepted
# and never started - typically submitted when the target drive was full. It does not progress, it
# does not fail, and it blocks everything behind it. Treating it as "already installing" is what
# turned one stuck download into every install being refused before it was tried.
def bgft_row_is_dead(row):
    """Is this bgft row a job that was accepted and never started?

    row is (status, transferred, length, ...) as install_job_row() returns it. Status 1000 means
    queued - which covers both "about to start" and "will never start". The pair that separates
    them is length 0 AND transferred 0: a job the console has actually taken up knows how big it
    is. Seven of these, queued while the drive was full, blocked every install on this console."""
    try:
        status, transferred, length = int(row[0]), int(row[1] or 0), int(row[2] or 0)
    except Exception:
        return False
    return status == 1000 and transferred == 0 and length == 0
# Terminal statuses that are NOT success. 1021 is what a rejected package leaves behind - observed
# on this console as `status=1021 err=0x80B21104`. Used to end the confirm wait on evidence rather
# than on a stopwatch; see [11] in _run.
BGFT_FAILED = {1021, 1022, 1023}
# ShadowMount backups are MOUNTED, not installed - they never appear in bgft.db at all, which is
# why the install-confirm logic below must not be pointed at them. .ffpfsc = compressed PFS container.
# Every container ShadowMount can mount, plus raw images. The scan walks ALL subfolders.
MOUNT_EXTS = (".ffpfsc", ".ffpkg", ".ffpfs", ".exfat", ".ffpfsx", ".fpkg", ".iso", ".img")


class _Cancelled(Exception):
    pass


def _mkdirs_list(path):
    parts, cur, out = [p for p in path.split("/") if p], "", []
    for p in parts:
        cur += "/" + p
        out.append(cur)
    return out


# --------------------------------------------------------------------------- #
# config / net helpers                                                         #
# --------------------------------------------------------------------------- #
# Keys that are subscripted directly somewhere in this file (cfg["companion"]["port"] and
# friends). If an override replaces one of these with null or a scalar, startup dies with a
# TypeError before anything is logged - and in the frozen exe that is a window that closes with no
# message at all. POST /api/config deep-merges an arbitrary unvalidated body, so this is reachable
# from a settings-panel bug or a hand-crafted request, not only from a hand edit.
_CONFIG_DICT_SECTIONS = ("dpi", "ftp", "console", "shadowmount", "cheats", "install", "library",
                         "queue", "companion", "maintenance", "performance", "integrity",
                         "federation", "device")


def coerce_config(cfg):
    """Put back any top-level section that an override turned into null or a scalar.

    Deliberately shallow and quiet: this exists so a bad value cannot stop the app from starting,
    not to validate the whole config. It says what it repaired, then carries on.
    """
    defaults = DEFAULT_CONFIG
    for k in _CONFIG_DICT_SECTIONS:
        if not isinstance(cfg.get(k), dict):
            was = cfg.get(k)
            cfg[k] = json.loads(json.dumps(defaults.get(k, {})))
            print("[cfg] section %r was %r, not an object - using defaults for it" % (k, was))
    for k in ("consoles", "sources", "drives"):
        if not isinstance(cfg.get(k), list):
            cfg[k] = json.loads(json.dumps(defaults.get(k, [])))
            print("[cfg] %r was not a list - using the default" % k)
    try:
        cfg["companion"]["port"] = int(cfg["companion"].get("port") or 8710)
    except (TypeError, ValueError):
        print("[cfg] companion.port was not a number - using 8710")
        cfg["companion"]["port"] = 8710
    return cfg


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _adopt_library_root(cfg):
    """Guarantee the canonical library root exists and is scanned.

    Users drop games in C:\Mutant Games\PS4 (or PS5); the app must find them with no setup. We
    create the tree if missing and make sure the root is in local_paths. Any previously configured
    folder is KEPT (so an existing library keeps working) but the canonical root always wins first
    place."""
    lib = cfg.setdefault("library", {})
    paths = [p for p in (lib.get("local_paths") or []) if p]
    norm = os.path.normcase(os.path.normpath(LIBRARY_ROOT))
    if not any(os.path.normcase(os.path.normpath(p)) == norm for p in paths):
        paths.insert(0, LIBRARY_ROOT)
    lib["local_paths"] = paths
    try:
        ensure_library_tree(LIBRARY_ROOT)
    except Exception:
        pass
    return cfg


# Set when config.json exists but could not be parsed. While this is true, saving is REFUSED:
# see save_config(). It is module state rather than a flag on the dict because the dict is copied,
# deep-merged and handed around, and the one thing that must not be lost is "we are running on
# defaults because we could not read the user's file".
_CONFIG_UNREADABLE = None


def load_config():
    global _CONFIG_UNREADABLE
    _CONFIG_UNREADABLE = None
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for path in (EXAMPLE_PATH, CONFIG_PATH):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k in list(data):
                    if k.startswith("_"):
                        data.pop(k)
                cfg = deep_merge(cfg, data)
            except Exception as e:
                print("[cfg] could not read %s: %s" % (path, e))
                if path == CONFIG_PATH:
                    # A truncated config.json (power cut, editor crash) used to be silently
                    # replaced by defaults - and then, because the defaults point at 192.168.1.50,
                    # main()'s auto-discover branch called save_config() and WROTE THE DEFAULTS
                    # OVER THE USER'S FILE. Their library paths, sources and console list were
                    # gone, and nothing had said a word.
                    #
                    # Keep a copy of what could not be read, and refuse to save until someone
                    # deliberately overrides. Running on defaults for one session is recoverable;
                    # overwriting the only copy is not.
                    _CONFIG_UNREADABLE = str(e)
                    try:
                        keep = "%s.unreadable-%s" % (CONFIG_PATH, time.strftime("%Y%m%d-%H%M%S"))
                        shutil.copy2(CONFIG_PATH, keep)
                        print("[cfg] kept a copy of the unreadable config at %s" % keep)
                    except Exception as ce:
                        print("[cfg] could not preserve the unreadable config: %r" % (ce,))
                    print("[cfg] RUNNING ON DEFAULTS and refusing to save - fix or delete %s"
                          % CONFIG_PATH)
    return coerce_config(cfg)


def _atomic_write_json(path, obj, indent=None):
    """Write JSON via a temp file + os.replace() so a crash mid-write can never leave a truncated/corrupt
    file (config, hash cache, installed state). Returns True on success. [audit L3]"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".pms_tmp_", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("[atomic-write] %s: %s" % (path, e))
        if tmp and os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
        return False


def save_config(cfg, force=False):
    """Persist the config. Returns True when something was written.

    Refuses while the on-disk config could not be parsed, because writing then means replacing a
    file we could not read with defaults we invented. `force=True` is the deliberate override.
    """
    if _CONFIG_UNREADABLE and not force:
        print("[cfg] NOT saving: %s could not be read this session (%s). "
              "Fix or delete it first — saving now would overwrite it with defaults."
              % (CONFIG_PATH, _CONFIG_UNREADABLE))
        return False
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return bool(_atomic_write_json(CONFIG_PATH, clean, indent=2))


def _port_open(host, port, timeout=0.4):
    """Is a TCP port accepting right now? Cheap and non-blocking-ish.

    A refused connect returns immediately; only a filtered or black-holed port costs the full timeout.
    Used to avoid paying an HTTP timeout to talk to a service that plainly is not there.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def local_ips():
    """Every non-loopback IPv4 this PC has — one per interface (Wi-Fi + Ethernet, etc.). [B11]"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    li = lan_ip()
    if li and not li.startswith("127."):
        ips.add(li)
    return sorted(ips)


def companion_ip_for(target_ip):
    """Pick the local IP on the same /24 as the console so a dual-homed PC (Wi-Fi + Ethernet) hands the
    console a pull URL it can actually reach. Falls back to lan_ip() when nothing matches — so single-NIC
    behavior is byte-identical to before. [B11]"""
    if not target_ip:
        return lan_ip()
    tp = target_ip.rsplit(".", 1)[0]
    for ip in local_ips():
        if ip.rsplit(".", 1)[0] == tp:
            return ip
    return lan_ip()


def assemble_sources(cfg):
    implicit = {"name": "companion-lan", "type": "lan",
                "base_url": "http://%s:%d/library/" % (lan_ip(), cfg["companion"]["port"]),
                "priority": 10, "companion": True}
    return [implicit] + list(cfg.get("sources", []))


# PS5 install destinations the picker can offer (real per-device placement works today for the MOUNT lane;
# DPI destination targeting stays gated behind dpi.device_key). [B6]
PS5_DEVICES = [
    {"id": "internal", "label": "Internal SSD"},
    {"id": "ext0", "label": "Extended (ext0)"},
    {"id": "ext1", "label": "Extended (ext1)"},
] + [{"id": "usb%d" % i, "label": "USB%d" % i} for i in range(8)]


def enumerate_pc_drives():
    """PC-side storage roots with free/total, so the UI can offer a place to build the Games tree. [B6]"""
    import shutil
    out = []
    if sys.platform == "win32":
        import string
        for letter in string.ascii_uppercase:
            root = "%s:\\" % letter
            if os.path.isdir(root):
                try:
                    du = shutil.disk_usage(root)
                    out.append({"id": "%s:" % letter, "label": "Drive %s:" % letter, "path": root,
                                "kind": "pc", "free": du.free, "total": du.total})
                except OSError:
                    pass
    else:
        for root in ("/", os.path.expanduser("~")):
            try:
                du = shutil.disk_usage(root)
                out.append({"id": root, "label": root, "path": root, "kind": "pc",
                            "free": du.free, "total": du.total})
            except OSError:
                pass
    return out


def drive_id_for_label(label):
    """Map a console-reported storage label back to the drive id the queue uses."""
    l = (label or "").strip().lower()
    if "internal" in l:
        return "internal"
    m = re.search(r"(usb\d)", l)
    if m:
        return m.group(1)
    if "extended" in l or "ext" in l:
        return "ext1"
    return l or "internal"


def mount_dest_for_drive(cfg, drive):
    """Where a PS5 backup must land for ShadowMount to pick it up, for the chosen drive.

    Verified over FTP on-device: backups live in /mnt/<drive>/homebrew (e.g. /mnt/ext1/homebrew,
    /mnt/usb0/homebrew ... /mnt/usb7/homebrew). ShadowMount scans those folders itself and mounts
    the title once the file is fully written, so our job is only to put it in the right place.
    'internal' has no /mnt entry and uses ShadowMount's own internal scan path."""
    sm = cfg.get("shadowmount", {})
    d = (drive or "").strip().lower()
    explicit = (sm.get("scan_paths_by_drive") or {}).get(d)
    if explicit:
        return explicit
    if d in ("ext0", "ext1", "ext2") or re.match(r"^usb[0-7]$", d):
        return "/mnt/%s/homebrew" % d
    if d in ("", "internal", "shadowmount", "auto"):
        return sm.get("scan_path", "/data/homebrew")
    return "/mnt/%s/homebrew" % d          # any future drive id follows the same shape


def ensure_library_tree(root, layout=LIBRARY_LAYOUT):
    """Create <root>/PS4 and <root>/PS5 so there is one obvious place to drop games.
    PS4 and PS5 live DIRECTLY under the root (no extra Games/ level). The scan walks every
    subfolder, so anything dropped anywhere beneath the root is found automatically."""
    created = []
    base = root
    for p in [base] + [os.path.join(base, s) for s in layout]:
        try:
            if not os.path.isdir(p):
                os.makedirs(p)
                created.append(p)
        except OSError as e:
            return {"ok": False, "error": str(e), "created": created, "root": base}
    return {"ok": True, "root": base, "created": created}


def consoles_from_cfg(cfg):
    raw = list(cfg.get("consoles") or [])
    if not raw and cfg.get("ps5_ip"):
        raw = [{"name": "PS5", "ip": cfg["ps5_ip"]}]
    out = []
    for i, c in enumerate(raw):
        if not c.get("ip"):
            continue
        out.append({"id": c.get("id") or ("ps5-%d" % i), "name": c.get("name") or c["ip"],
                    "ip": c["ip"],
                    "ftp_port": c.get("ftp_port", cfg["ftp"].get("port", 2121))})
    return out


def http_get_json(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def registry_key(registry, raw):
    """Canonicalize a path segment to the registry key. Registry keys are quote(basename);
    a request may arrive quoted (%5B) or unquoted ([). Try both so keys always match."""
    if raw in registry:
        return raw
    q = quote(raw)
    if q in registry:
        return q
    return None


def now_ms():
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- #
# library                                                                      #
# --------------------------------------------------------------------------- #
GAME_FOLDER_MARKERS = ("eboot.bin", "sce_sys", "param.json", "param.sfo", "sce_module")


def is_game_folder(path, name):
    """Is this directory a game stored unpacked, rather than an ordinary folder?

    ShadowMount runs a folder the same way it runs a container, so one belongs in the library like
    any other backup. Recognised by the structure a real dump has — never by name alone, so the
    PS4/PS5 layout folders and stray directories are left alone.
    """
    # A marker directory is part of a game, never a game itself. The real scan prunes the parent
    # before reaching it, but saying so here means the test does not depend on that ordering.
    if str(name or "").lower() in GAME_FOLDER_MARKERS:
        return False
    try:
        entries = set(e.lower() for e in os.listdir(path))
    except OSError:
        return False
    if any(m in entries for m in GAME_FOLDER_MARKERS):
        return True
    # A folder named for a title id that holds the usual dump layout counts too.
    if re.search(r"(CUSA|PPSA)\d{5}", name or "", re.I):
        return any(m in entries for m in GAME_FOLDER_MARKERS)
    return False


def is_backup_item(item, game=None):
    """Is this file a ShadowMount container rather than a PKG?

    Checked three ways because a peer on an older build may state none of them: the kind it was
    filed under, the format either side reported, and finally the filename — which is proof on its
    own. Every container in MOUNT_EXTS counts, so .ffpfs / .exfat / .ffpkg / .iso behave exactly as
    .ffpfsc does rather than only the one format we happened to test with.
    """
    item = item or {}
    k = str(item.get("kind") or "").lower()
    if k == "backup":
        return True
    # An ADD-ON is a PKG. Always. It is never a ShadowMount container, whatever the game it
    # belongs to happens to be stored as.
    #
    # Without this line the checks below consult the GAME's format as a fallback - which is right
    # for the base item and badly wrong for everything else. A PS5 game arrives as a .ffpfsc
    # backup, so once its updates and DLC were correctly grouped onto that game's card, every one
    # of them inherited "ffpfsc" from the parent and was declared a backup. They were then routed
    # down the mount/delegate lane as kind "backup", which cost them their real kind: the install
    # watcher saw kind "base", waited for a full-size app.pkg that a 1 MB DLC can never produce,
    # and after four minutes reported "Console rejected this package - delivered in full, but it
    # won't install (bad dump)". Measured on Borderlands 4's Firehawk's Finery: bgft.db said
    # status 1026, error 0x00000000, and addcont.db held the registration. The install had
    # succeeded completely; only the report was wrong.
    if k in ADDON_KINDS:
        return False
    for f in (item.get("format"), (game or {}).get("format")):
        if f and ("." + str(f).lower().lstrip(".")) in MOUNT_EXTS:
            return True
    for n in (item.get("file"), (game or {}).get("name")):
        if fmt_from_filename(n):
            return True
    return False


def fmt_from_filename(fn):
    """Container type from a filename, or None if it is not a backup container.

    The extension is proof on its own, which is what makes a backup identifiable even when it came
    from a peer that never told us what it was."""
    ext = os.path.splitext(str(fn or ""))[1].lower()
    return ext.lstrip(".") if ext in MOUNT_EXTS else None


def mount_name(fn):
    """Readable title for a backup container, whose only metadata is its filename.

    A PKG carries a param.sfo with the real title, but a ShadowMount backup does not — so the name
    on the card is whatever the file is called. Left alone that reads "Elden Ring - PPSA04610-app",
    with the id and the container's role spelled out twice. Strip the parts that are already shown
    in their own fields (platform tag, title id, version tag, -app/-patch suffix) and keep the name.
    """
    stem = os.path.splitext(fn)[0]
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", stem)              # [PS5], (v1.02), [DLPSGAME.COM]
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"\b(CUSA|PPSA)\d{5}\b", " ", s, flags=re.I)     # the id has its own field
    # Hyphen-attached and at the end only. These markers are written "PPSA04610-app" by the backup
    # tools; matching a bare trailing word instead turned "The Last Game" into "The Last".
    s = re.sub(r"-\s*(app|patch|game|dlc|data)\s*$", "", s, flags=re.I)
    s = " ".join(s.split()).strip(" -._")
    if not s:                                                    # nothing but tags: fall back
        s = " ".join(re.sub(r"[\[\]\(\)_]+", " ", stem).split()).strip(" -.")
    return s or fn


PART_PATTERNS = [
    re.compile(r"[\s._\-\[(]part[\s._\-]*(\d{1,2})\b", re.I),
    re.compile(r"[\s._\-\[(]pt[\s._\-]*(\d{1,2})\b", re.I),
    re.compile(r"[\s._\-\[(](\d{1,2})\s*of\s*(\d{1,2})[\s._\-\])]", re.I),
    re.compile(r"\.part(\d{1,2})\b", re.I),
    re.compile(r"[\s._\-\[(]disc[\s._\-]*(\d{1,2})\b", re.I),
]


def part_info(filename):
    """(part_number, total_or_None, stem_without_the_part_marker) for a split release.

    Returns (None, None, stem) when the name carries no part marker. The stem is what
    groups the pieces together, so "Game Part 1.pkg" and "Game Part 2.pkg" collapse into
    one item while two unrelated packages never do."""
    stem = os.path.splitext(filename)[0]
    for rx in PART_PATTERNS:
        m = rx.search(stem)
        if not m:
            continue
        num = int(m.group(1))
        total = None
        if rx.groups >= 2 and m.lastindex and m.lastindex >= 2:
            try:
                total = int(m.group(2))
            except (TypeError, ValueError):
                total = None
        cleaned = (stem[:m.start()] + " " + stem[m.end():]).strip()
        cleaned = re.sub(r"[\s._\-]+", " ", cleaned).strip(" -._")
        return num, total, cleaned.lower()
    return None, None, re.sub(r"[\s._\-]+", " ", stem).strip(" -._").lower()


def group_parts(items):
    """Collapse split releases into single entries carrying an ordered `parts` list.

    Grouping is per (kind, title_id, stem) so a two-part BASE and a two-part UPDATE of the
    same game stay separate. Anything without a part marker is returned untouched."""
    out, groups = [], {}
    for it in items:
        num, total, stem = part_info(it.get("file") or "")
        if num is None:
            out.append(it)
            continue
        gk = (it.get("kind"), it.get("title_id"), stem)
        g = groups.get(gk)
        if not g:
            g = dict(it)
            g["parts"] = []
            g["multi_part"] = True
            g["parts_expected"] = total
            groups[gk] = g
            out.append(g)
        g["parts"].append({"install_key": it.get("install_key"), "file": it.get("file"),
                           "size": it.get("size", 0), "part": num})
        if total and not g.get("parts_expected"):
            g["parts_expected"] = total
    for g in groups.values():
        g["parts"].sort(key=lambda p: p["part"])
        g["size"] = sum(p.get("size") or 0 for p in g["parts"])
        # the first part is what a single-file install would have used
        g["install_key"] = g["parts"][0]["install_key"]
        g["parts_count"] = len(g["parts"])
        exp = g.get("parts_expected")
        g["parts_missing"] = (max(0, exp - len(g["parts"])) if exp else 0)
        nums = [p["part"] for p in g["parts"]]
        # a gap means a piece is simply not here; say so rather than installing a broken set
        g["parts_gap"] = bool(nums) and (sorted(nums) != list(range(min(nums), min(nums) + len(nums))))
    return out


def filename_fallback(fn):
    """Best-effort metadata when the package itself cannot be read.

    PS4 never really lands here: pkg_meta.parse_pkg() reads its param.sfo and returns a real
    CATEGORY (gd/gp/ac). PS5 ALWAYS lands here, because a PS5 package is a different container
    entirely - magic 7F 46 49 48 (FIH), not PS4's 7F 43 4E 54 (CNT) - and its param.sfo is not readable
    (verified: the SFO magic appears nowhere in the first 4 MB of four different PS5 packages).
    So for PPSA titles this
    function IS the classifier, and defaulting everything to "base" is what gave every PS5 update
    and DLC its own library card named after a content id.

    The filename still carries the truth, in two documented shapes:
        UP1001-PPSA01494_00-FIREHAWKSFINERY0.pkg      content id, title id inline
        UP4321-00-0014691966945618-PPSA07064.pkg      content id, title id appended
        Disney-Epic-Mickey-Rebrushed-v01-000-000-PPSA15656.pkg   an UPDATE (-v<maj>-<min>-<pt>)
    """
    stem = os.path.splitext(fn)[0]
    m = re.search(r"(CUSA\d{5}|PPSA\d{5})", stem, re.IGNORECASE)
    tid = m.group(1).upper() if m else None
    low = fn.lower()
    kind = "update" if any(w in low for w in ("update", "patch")) else ("dlc" if "dlc" in low else "base")
    ver = None
    if tid and tid.startswith("PPSA"):
        # A PS5 game arrives as a ShadowMount backup (.ffpfsc), never as a .pkg, so a PS5 .pkg is
        # an add-on. Which kind is readable from the name: a version triple means an update,
        # anything else is downloadable content.
        vm = re.search(r"[-_]v(\d{1,2})[-.](\d{2,3})[-.](\d{2,3})", stem, re.IGNORECASE)
        if vm:
            kind, ver = "update", "%02d.%s" % (int(vm.group(1)), vm.group(2))
        elif kind == "base":
            kind = "dlc"
    name = re.sub(r"[\[\]\(\)_]+", " ",
                  re.sub(r"(CUSA\d{5}|PPSA\d{5}|\[BASE\]|\[UPDATE\]|\[DLC\])", "", stem, flags=re.I))
    return {"title_id": tid, "name": " ".join(name.split()).strip(" -.") or fn, "kind": kind,
            "version": ver, "region": "—", "content_id": "", "icon": False}


# --------------------------------------------------------------------------- #
# PKG filename normalisation                                                    #
# --------------------------------------------------------------------------- #
# Anything outside this set breaks the install lane. The console's DPI daemon is handed a URL to
# the file, and a name carrying spaces or brackets — "Cars 3 Driven to Win (CUSA07083) -
# [DLPSGAME.COM].pkg" — comes back as a bare "HTTP 500: Error" only AFTER the queue has accepted
# the job. Fixing the name on sight means a bad one never reaches the installer at all.
_PKG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_PKG_TERMINAL_STATES = ("playable", "error", "canceled", "submitted", "installed", "done")


def safe_pkg_filename(meta, fn):
    """The name a PKG should have: no spaces, no brackets, game name and title id preserved.

        Cars 3 Driven to Win (CUSA07083) - [DLPSGAME.COM].pkg
        -> Cars-3-Driven-to-Win-CUSA07083.pkg

    Built from the PKG's OWN param.sfo (title + title id + version) rather than from the old
    filename, so site stamps and release-group tags fall away and the game keeps its real name.
    Falls back to cleaning the filename when a PKG has no readable metadata.

    A base package gets `Name-TITLEID`. Updates and DLC additionally carry their kind and version,
    because they live in the same folder as the base and must not collapse onto its name — the
    library really does hold Castle Crashers at both v1.00 (227 MB base) and v1.04 (10 MB update).
    """
    stem = os.path.splitext(fn)[0]
    meta = meta or {}
    tid = (meta.get("title_id") or "").upper()
    if not tid:
        m = re.search(r"(CUSA\d{5}|PPSA\d{5})", stem, re.I)
        tid = m.group(1).upper() if m else ""

    name = (meta.get("title") or "").strip()
    if not name:
        # No param.sfo: clean the filename instead — drop bracketed junk and the id.
        name = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", stem)
        name = re.sub(r"(CUSA\d{5}|PPSA\d{5})", " ", name, flags=re.I)
        name = re.sub(r"[._]+", " ", name)
    name = _PKG_UNSAFE.sub("-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-._") or "PKG"

    parts = [name]
    if tid:
        parts.append(tid)
    kind = (meta.get("kind") or "").lower()
    if kind and kind != "base":
        parts.append(kind.upper())
        ver = (meta.get("app_ver") or meta.get("version") or "").strip().lstrip("vV")
        if ver:
            parts.append("v" + _PKG_UNSAFE.sub("-", ver))
        if kind == "dlc":
            # Two DLC for one title would otherwise land on the same name.
            cid = (meta.get("content_id") or "").strip()
            tail = cid.rsplit("-", 1)[-1] if "-" in cid else ""
            if tail and tail.upper() != tid:
                parts.append(_PKG_UNSAFE.sub("-", tail))
    return "-".join(p for p in parts if p) + ".pkg"


class Library:
    def __init__(self, cfg):
        self.cfg = cfg
        self.queue = None       # set in main(); lets a rename skip a file a live job is using
        self.file_registry = {}
        self.file_sizes = {}
        self.games = []
        self.is_empty = True
        self.gen = 0            # bumps every scan; the UI polls it to auto-refresh [B4]
        self.last_scan_ms = 0

    def _register(self, abspath, reg=None, sizes=None):
        reg = self.file_registry if reg is None else reg
        sizes = self.file_sizes if sizes is None else sizes
        key = quote(os.path.basename(abspath))
        base, i = key, 1
        while key in reg and reg[key] != abspath:
            key, i = "%s.%d" % (base, i), i + 1
        reg[key] = abspath
        sizes[key] = os.path.getsize(abspath)
        return key

    def _register_dir(self, abspath, reg, sizes):
        """Same as _register for a game stored as a FOLDER, whose size is the sum of its contents.

        Kept separate because getsize() on a directory reports the entry, not the game — a folder
        registered through the file path would claim to be a few kilobytes."""
        key = quote(os.path.basename(abspath))
        base, i = key, 1
        while key in reg and reg[key] != abspath:
            key, i = "%s.%d" % (base, i), i + 1
        reg[key] = abspath
        total = 0
        for dp, _d, fs in os.walk(abspath):
            for f in fs:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        sizes[key] = total
        return key

    def _busy_paths(self):
        """Files a live queue job is reading right now — never rename one of those."""
        busy = set()
        q = self.queue
        if q is None:
            return busy
        try:
            for t in q.snapshot():
                if t.get("state") in _PKG_TERMINAL_STATES:
                    continue
                lp = t.get("local_path")
                if lp:
                    busy.add(os.path.normcase(os.path.abspath(lp)))
        except Exception:
            pass
        return busy

    def normalise_pkg_names(self):
        """Give every PKG in the library a name the installer can fetch.

        Runs at the top of every scan, and the folder watcher calls scan() whenever something
        appears — so a package dropped into the games folder is fixed before it is ever listed,
        let alone queued. Idempotent: a name that is already safe is left completely alone, which
        is also what stops the watcher (which fires on name changes) from looping.

        Deliberately narrow — it touches ONLY `.pkg` files under the configured library folders.
        PS5 backups, anything on the console, and every other system are untouched.
        """
        renamed = []
        busy = self._busy_paths()
        now = time.time()
        for root in self.cfg["library"].get("local_paths", []):
            if not root or not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    if not fn.lower().endswith(".pkg"):
                        continue
                    if not _PKG_UNSAFE.search(os.path.splitext(fn)[0]):
                        continue                      # already safe — do not even open it
                    ap = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(ap)
                    except OSError:
                        continue
                    # Still being written? Leave it; the next scan will pick it up.
                    if now - st.st_mtime < 15:
                        continue
                    if os.path.normcase(os.path.abspath(ap)) in busy:
                        continue
                    try:
                        meta = pkg_meta.parse_pkg(ap)
                    except Exception:
                        meta = None
                    new = safe_pkg_filename(meta, fn)
                    if new == fn:
                        continue
                    target = os.path.join(dirpath, new)
                    if os.path.exists(target):
                        # Never overwrite: a same-named neighbour is a different package.
                        stem, ext = os.path.splitext(new)
                        n = 2
                        while os.path.exists(os.path.join(dirpath, "%s-%d%s" % (stem, n, ext))) and n < 100:
                            n += 1
                        target = os.path.join(dirpath, "%s-%d%s" % (stem, n, ext))
                        if os.path.exists(target):
                            continue
                    try:
                        os.rename(ap, target)
                    except OSError as e:
                        # Locked by another process (a copy still finishing, an antivirus scan).
                        print("[pkgname] skipped %s: %s" % (fn, e))
                        continue
                    renamed.append((fn, os.path.basename(target)))
                    print("[pkgname] %s -> %s" % (fn, os.path.basename(target)))
        return renamed

    def scan(self):
        # A PKG whose name carries spaces or brackets fails the install with a bare "HTTP 500"
        # from the DPI daemon, so names are fixed BEFORE anything is registered — the rest of the
        # app then only ever sees a name that works.
        try:
            self.normalise_pkg_names()
        except Exception as e:
            print("[pkgname] pass skipped: %s" % e)
        # Build into local dicts and swap in at the very end, so a concurrent /library/ request during a
        # rescan never sees a half-built (or momentarily empty) registry and 404s. [B4 atomic scan swap]
        reg, sizes = {}, {}
        by_title, loose, mounts = {}, [], []
        n_parsed = n_fallback = 0
        for root in self.cfg["library"].get("local_paths", []):
            if not root or not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                # A game stored as a FOLDER instead of a container. ShadowMount mounts it the same
                # way, so it is listed the same way. Taken out of the walk once claimed, or its
                # internal files would come back a second time as loose packages.
                claimed = []
                for d in list(_dirs):
                    full = os.path.join(dirpath, d)
                    if not is_game_folder(full, d):
                        continue
                    fkey = self._register_dir(full, reg, sizes)
                    ftid = re.search(r"(CUSA\d{5}|PPSA\d{5})", d, re.I)
                    ftid = ftid.group(1).upper() if ftid else None
                    if re.search(r"\[\s*PS5\s*\]", d, re.I):   fplat = "PS5"
                    elif re.search(r"\[\s*PS4\s*\]", d, re.I): fplat = "PS4"
                    elif ftid:                                 fplat = "PS5" if ftid.startswith("PPSA") else "PS4"
                    else:                                       fplat = "BACKUP"
                    mounts.append({"install_key": fkey, "file": d, "size": sizes.get(fkey, 0),
                                   "kind": "backup", "name": mount_name(d), "title_id": ftid,
                                   "platform": fplat, "format": "folder", "is_dir": True})
                    claimed.append(d)
                if claimed:
                    _dirs[:] = [d for d in _dirs if d not in claimed]
                for fn in files:
                    low = fn.lower()
                    is_pkg = low.endswith(".pkg")
                    is_mount = low.endswith(MOUNT_EXTS)
                    if not (is_pkg or is_mount):
                        continue
                    ap = os.path.join(dirpath, fn)
                    try:
                        size = os.path.getsize(ap)
                    except OSError:
                        continue
                    key = self._register(ap, reg, sizes)
                    if is_mount:      # ShadowMount backup - mount lane, no param.sfo
                        mt = re.search(r"(CUSA\d{5}|PPSA\d{5})", fn, re.I)
                        mtid = mt.group(1).upper() if mt else None
                        # "[PS5] PPSA01885 - Evergate.ffpfsc" -> platform from the tag, else from the id
                        if re.search(r"\[\s*PS5\s*\]", fn, re.I):   mplat = "PS5"
                        elif re.search(r"\[\s*PS4\s*\]", fn, re.I): mplat = "PS4"
                        elif mtid:                                    mplat = "PS5" if mtid.startswith("PPSA") else "PS4"
                        else:                                          mplat = "BACKUP"
                        mounts.append({"install_key": key, "file": fn, "size": size, "kind": "backup",
                                       "name": mount_name(fn), "title_id": mtid, "platform": mplat,
                                       "format": os.path.splitext(fn)[1].lower().lstrip(".")})
                        continue
                    tg = re.search(r"(CUSA\d{5}|PPSA\d{5})", fn, re.I)
                    icon_path = os.path.join(ICON_DIR, (tg.group(1).upper() if tg else key) + ".png")
                    want_icon = None if os.path.exists(icon_path) else icon_path   # don't re-extract if cached
                    meta = pkg_meta.parse_pkg(ap, extract_icon_to=want_icon)       # REAL
                    if meta and meta.get("title_id"):
                        n_parsed += 1
                        info = {"title_id": meta["title_id"], "name": meta["title"] or fn,
                                "kind": meta["kind"], "version": meta["app_ver"] or meta["version"],
                                "region": meta["region"], "content_id": meta["content_id"],
                                "icon": bool(meta.get("icon"))}
                    else:
                        n_fallback += 1
                        info = filename_fallback(fn)
                    item = {"install_key": key, "file": fn, "size": size, "kind": info["kind"],
                            "version": info["version"], "name": info["name"], "title_id": info["title_id"],
                            "content_id": info.get("content_id", "")}
                    if info["title_id"]:
                        by_title.setdefault(info["title_id"], []).append(item)
                    else:
                        loose.append(item)

        games = []
        by_tid_entry = {}                   # tid -> the entry, so a backup can merge into it
        for tid, items in by_title.items():
            items = group_parts(items)          # split releases become one entry with `parts`
            base = next((i for i in items if i["kind"] == "base"), items[0])
            by_tid_entry[tid] = {
                "title_id": tid, "name": base["name"], "lane": "install",
                "region": pkg_meta.region_from_content_id(base.get("content_id", "")),
                "platform": "PS5" if str(tid).startswith("PPSA") else "PS4",
                "size": base["size"], "cover_seed": tid,
                "has_icon": os.path.exists(os.path.join(ICON_DIR, tid + ".png")),
                "update_only": not any(i["kind"] == "base" for i in items),
                "base": [i for i in items if i["kind"] == "base"],
                "updates": sorted([i for i in items if i["kind"] == "update"], key=lambda x: x.get("version") or ""),
                "dlc": [i for i in items if i["kind"] == "dlc"], "cheats": []}
            games.append(by_tid_entry[tid])
        for it in loose:
            games.append({"title_id": None, "name": it["name"], "region": "—", "platform": "PS4",
                          "lane": "install", "size": it["size"], "cover_seed": it["file"], "has_icon": False,
                          "base": [it], "updates": [], "dlc": [], "cheats": []})
        seen_mounts = set()
        for m in mounts:      # ShadowMount backup lane (Game Compressor .ffpfsc etc.)
            mtid = m.get("title_id")
            # The same container reaching us twice (two roots, or a peer echoing it back) used to
            # add a second identical card.
            mkey = (mtid or "", str(m.get("file") or ""))
            if mkey in seen_mounts:
                continue
            seen_mounts.add(mkey)
            # ONE CARD PER TITLE. A PS5 game arrives as a .ffpfsc backup while its updates and DLC
            # arrive as .pkg files, so the two halves used to land in different lists and produce
            # two cards for one game - the real one, plus a second named after a content id holding
            # all its add-ons. Merge them: the backup becomes the base, the packages stay as the
            # updates/dlc they now classify as. Verified before the change that NO PS4 title has
            # this split, so nothing on that path can move.
            ex = by_tid_entry.get(mtid) if mtid else None
            if ex is not None:
                ex["base"] = [dict(m, version=None, content_id="")] +                              [i for i in ex.get("base", []) if i.get("kind") != "backup"]
                ex["name"] = m["name"]                     # the real title, not a content id
                ex["platform"] = m.get("platform") or ex.get("platform")
                ex["lane"] = "mount"                       # the GAME installs by mounting
                ex["format"] = m.get("format")
                ex["size"] = m.get("size") or ex.get("size")
                ex["cover_seed"] = mtid or ex.get("cover_seed")
                ex["update_only"] = False
                if not ex.get("has_icon"):
                    ex["has_icon"] = bool(mtid) and os.path.exists(os.path.join(ICON_DIR, mtid + ".png"))
                continue
            games.append({"title_id": mtid, "name": m["name"], "region": "—",
                          "platform": m.get("platform") or "BACKUP",
                          "lane": "mount", "format": m["format"], "size": m["size"],
                          "cover_seed": mtid or m["file"],
                          "has_icon": bool(mtid) and os.path.exists(os.path.join(ICON_DIR, mtid + ".png")),
                          "base": [dict(m, version=None, content_id="")],
                          "updates": [], "dlc": [], "cheats": []})
        games += load_sources(self.cfg)
        # atomic swap: registry / sizes / games all become visible together [B4]
        self.file_registry, self.file_sizes = reg, sizes
        self.games = sorted(games, key=lambda g: g["name"].lower())
        self.is_empty = len(self.games) == 0      # honest empty state — never fabricated games
        self.gen += 1
        self.last_scan_ms = now_ms()
        print("[lib] %d title(s) (pkg parsed %d, fallback %d, mount %d) gen=%d%s" %
              (len(self.games), n_parsed, n_fallback, len(mounts), self.gen, " [empty]" if self.is_empty else ""))
        return self.games

    def find_game(self, title_id):
        return next((g for g in self.games if g.get("title_id") == title_id), None)


def load_sources(cfg):
    sf = cfg["library"].get("sources_file")
    if not sf:
        return []
    path = sf if os.path.isabs(sf) else os.path.join(HERE, sf)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("games", [])
    except Exception as e:
        print("[sources] %s" % e)
        return []


# --------------------------------------------------------------------------- #
# integrity (SHA-256, cached by path+size+mtime)                               #
# --------------------------------------------------------------------------- #
def load_hashes():
    try:
        with open(HASH_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_hashes(d):
    with _state_lock:
        _atomic_write_json(HASH_CACHE_PATH, d)


def sha256_of(path, cache):
    st = os.stat(path)
    ck = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    if ck in cache:
        return cache[ck]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    cache[ck] = h.hexdigest()
    save_hashes(cache)
    return cache[ck]


def cached_sha(path, cache):
    try:
        st = os.stat(path)
        return cache.get("%s|%d|%d" % (path, st.st_size, int(st.st_mtime)))
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# DPI daemon self-healing (Payload Manager)                                     #
# --------------------------------------------------------------------------- #
class CheatRunner:
    """Client for the CheatRunner service on the PS5 (:9999).

    It owns the actual cheat engine — matching a cheat file to the running game, patching memory,
    and keeping per-title profiles. We drive it rather than reimplement it, and add the one thing
    it cannot know: whether the file matches the version WE know is installed.

    Endpoints (verified live against v0.16):
      GET /api/cheats/find?titleId=            -> best local file + scored candidates
      GET /api/cheats?titleId=                 -> the loaded cheat doc (mods[])
      GET /api/cheats/state?titleId=&debug=1   -> which mods are currently on
      POST /api/cheats/select?titleId=&path=[&force=1]
      GET /api/cheats/toggle?titleId=&index=&on=0|1
      GET /api/cheats/disable-all?titleId=
      GET /api/patches/apply?titleId=&index=|&entryId=[&force=1]
    """
    PORT = 9999

    def __init__(self, ip, port=None, timeout=20):
        self.ip = ip
        self.port = int(port or self.PORT)
        self.timeout = timeout

    def _call(self, path, method="GET", timeout=None):
        url = "http://%s:%d%s" % (self.ip, self.port, path)
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8", "replace") or "{}")
            except Exception:
                return {"ok": False, "error": "http_%s" % e.code}
        except Exception as e:
            return {"ok": False, "error": repr(e), "unreachable": True}

    def alive(self, ttl=15.0, probe=True):
        """Is CheatRunner reachable? Cached BOTH ways for `ttl` seconds.

        The uncached version cost 2.04 s every single call, because :9999 is normally not
        listening and Windows burns the full connect timeout on a refused TCP connect. Measured:
        GET /api/mods/<tid> for a title with no local cheat file took 2.090 s, against 0.059 s for
        a title that has one - the whole delta was this probe. It is also why /api/health went to
        2.1 s once every ten minutes, when running_title()'s negative memo expired and re-probed.

        Caching the FALSE is the point, so it is deliberately keyed off the RESULT and not off an
        exception: _call() swallows every error and returns {"ok": False, "unreachable": True},
        so a try/except memo would never arm.

        A short symmetric TTL beats a long negative memo: someone who starts CheatRunner is
        picked up within `ttl` instead of being told it is offline for the next ten minutes.
        """
        now = time.monotonic()
        if now - getattr(self, "_alive_ts", 0.0) < ttl:
            return getattr(self, "_alive_val", False)
        if not probe:
            return getattr(self, "_alive_val", False)
        v = bool(self._call("/api/health", timeout=1.5).get("ok"))
        self._alive_ts, self._alive_val = now, v
        return v

    def running(self):
        """What game is running right now: {titleId,titleName,appVersion,pid,...} or {}."""
        st = self._call("/api/state", timeout=6)
        r = (st.get("running") or {}) if isinstance(st, dict) else {}
        return r if r.get("running") else {}

    def find(self, tid):
        return self._call("/api/cheats/find?titleId=" + quote(tid))

    def doc(self, tid):
        return self._call("/api/cheats?titleId=" + quote(tid))

    def state(self, tid):
        return self._call("/api/cheats/state?titleId=%s&debug=1" % quote(tid))

    def select(self, tid, path, force=False):
        return self._call("/api/cheats/select?titleId=%s&path=%s%s"
                          % (quote(tid), quote(path), "&force=1" if force else ""), method="POST")

    def toggle(self, tid, index, on):
        return self._call("/api/cheats/toggle?titleId=%s&index=%d&on=%d"
                          % (quote(tid), int(index), 1 if on else 0))

    def disable_all(self, tid):
        return self._call("/api/cheats/disable-all?titleId=" + quote(tid))

    def apply_patch(self, tid, index=None, entry_id=None, force=False):
        idpart = ("&entryId=" + quote(str(entry_id))) if entry_id else ("&index=%d" % int(index or 0))
        return self._call("/api/patches/apply?titleId=%s%s%s"
                          % (quote(tid), idpart, "&force=1" if force else ""), timeout=60)


def _merge_mod(i, m, srow):
    """Combine the cheat document entry with its live engine state."""
    state = (srow.get("state") or "").lower()
    return {
        "index": i,
        "name": m.get("name") or m.get("title") or ("Mod %d" % (i + 1)),
        "type": m.get("type") or "",
        "patches": m.get("patches"),
        "state": state or "off",
        "label": srow.get("label") or ("ON" if state == "on" else "OFF"),
        "on": state == "on",
        "conflict": state == "conflict",
        "can_toggle": bool(srow.get("canToggle", True)),
        "next_on": bool(srow.get("nextOn", state != "on")),
        "conflicts_with": [c.get("name") for c in (srow.get("conflictsWith") or []) if c.get("name")],
    }


def _cheat_format(path):
    """Which of our three cheat formats a file is. .shn/.mc4 are Trainer XML (the latter
    AES-encrypted); the ELF converts both to the same document shape we already parse."""
    low = (path or "").lower()
    for ext in ("shn", "mc4", "json"):
        if low.endswith("." + ext):
            return ext
    return "json"


def cheat_file_version(filename):
    """Pull the version out of a cheat filename: CUSA14409_01.04.json -> "01.04"."""
    m = re.search(r"_(\d{2}\.\d{3}\.\d{3})", filename or "")   # PS5: 01.011.000
    if m:
        return m.group(1)
    m = re.search(r"_(\d{2}\.\d{2})", filename or "")            # PS4: 01.04
    return m.group(1) if m else ""


class PayloadManager:
    """Drives Payload Manager (pldmgr) on the console over LAN — the piece that makes the
    DPI daemon reload itself with ZERO user interaction.

    Why this exists: the DPI v2 daemon WEDGES after a heavy install (POST /api/install stops
    answering and never self-recovers) and its API has exactly ONE endpoint — POST /api/install —
    so there is no in-band reset to call. Reloading the payload is the only real fix.

    Measured on-device (FW 12.70, pldmgr v0.5.0 on :8084):
      * The install daemon is `dpiv2.elf`, a process of its OWN — relaunching elf-arsenal alone
        does NOT replace it (verified: elf-arsenal's relaunch cycled dpi.elf/ftpsrv/nanodns but
        dpiv2 kept its pid and :12800 never dropped). The old instance still owns the port, so a
        fresh one cannot bind. Killing it FIRST is what makes the reload real.
      * kill dpiv2 -> :12800 gone in ~2.5s; loadpayload elf-arsenal -> serving again in ~3.5s
        with a brand-new dpiv2 pid. Full recovery ~6s, unattended.
    """
    PORT = 8084

    def __init__(self, ip, port=None, timeout=25):
        self.ip = ip
        self.port = int(port or self.PORT)
        self.timeout = timeout

    def _get(self, path, timeout=None):
        url = "http://%s:%d%s" % (self.ip, self.port, path)
        with urllib.request.urlopen(url, timeout=timeout or self.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def alive(self, ttl=8.0, probe=True):
        """Is Payload Manager reachable? (auto-recovery is only possible when it is)

        Cached for `ttl` seconds. This is asked on paths that must stay quick — most importantly
        /api/federation, which is how OTHER PCs identify us. With the console off, the bare probe
        cost 3s every time, which pushed our identity reply past the timeout other companions
        allow: we became invisible to them for no reason other than the PS5 being asleep.
        """
        now = time.monotonic()
        if now - getattr(self, "_alive_ts", 0.0) < ttl:
            return getattr(self, "_alive_val", False)
        if not probe:
            return getattr(self, "_alive_val", False)   # answer from memory, never stall a caller
        try:
            v = bool(self._get("/version", timeout=3).strip())
        except Exception:
            v = False
        self._alive_ts, self._alive_val = now, v
        return v

    def processes(self):
        try:
            return json.loads(self._get("/processes_list", timeout=8)).get("processes", [])
        except Exception:
            return []

    def pids_named(self, prefix):
        pre = prefix.lower()
        return [p.get("pid") for p in self.processes()
                if str(p.get("name", "")).lower().startswith(pre) and p.get("pid")]

    def payload_path(self, needle):
        """Full on-console path of a payload whose filename contains `needle` (e.g. 'arsenal')."""
        try:
            paths = json.loads(self._get("/list_payloads", timeout=8)).get("payloads", [])
        except Exception:
            return None
        n = needle.lower()
        for p in paths:
            if n in p.rsplit("/", 1)[-1].lower():
                return p
        return None

    def kill(self, pid):
        try:
            self._get("/process_kill?pid=%d" % int(pid), timeout=10)
            return True
        except Exception:
            return False

    def load(self, path):
        try:
            self._get("/loadpayload:" + quote(path, safe="/:"), timeout=30)
            return True
        except Exception:
            return False


# --------------------------------------------------------------------------- #
# console bridge + fleet                                                        #
# --------------------------------------------------------------------------- #
class Ps5Bridge:
    LOC_LABEL = {"0": "Internal SSD", "2": "Extended Storage"}

    def __init__(self, console, cfg):
        self.c = console
        self.cfg = cfg
        self._apps = None
        self._apps_ts = 0.0
        self._backups = None
        self._backups_ts = 0.0
        self._ps4meta = None       # PS4/CUSA titles proven installed by their own app.pkg
        self._ps4loc = {}          # ...and which root each was found under
        self._ps4meta_ts = 0.0
        self._apps_refreshing = False   # guards the background stale-while-revalidate refresh
        self._pldmgr = None        # Payload Manager client — this is how our installer is spawned
        self._cr = None            # CheatRunner client — mods / cheats / patches
        self._run_cache, self._run_ts = {}, 0.0
        self._addons = None        # content_ids from addcont.db — the only record a DLC leaves
        self._addons_ts = 0.0
        self._ftp_port = None      # sticky: which FTP port answered last. Probe, never assume -
                                   # the port depends on which FTP payload the user runs.
        self._fs_ok, self._fs_ok_at = None, 0.0   # is OUR on-console file API answering?

    # Every read of console state - app.db, bgft.db, addcont.db, the install proof, the cheat sync -
    # goes through here, so which FTP is up decides whether the app can see the console at all.
    # Elf Arsenal's ftpsrv served :2121; etaHEN's own FTP serves :1337. Now that Arsenal is no
    # longer bundled the port depends on what is running, so try the configured one and fall back
    # to the other rather than going blind. The working port is remembered so this costs one
    # connect in the normal case.
    FTP_FALLBACKS = (2121, 1337)

    def _ftp(self, timeout=6):
        first = self._ftp_port or self.c.get("ftp_port") or 2121
        order = [first] + [p for p in self.FTP_FALLBACKS if p != first]
        last = None
        for port in order:
            ftp = ftplib.FTP()
            try:
                ftp.connect(self.ip, port, timeout=timeout)
                ftp.login()
            except Exception as e:
                last = e
                try:
                    ftp.close()
                except Exception:
                    pass
                continue
            if self._ftp_port != port:
                if self._ftp_port is not None:
                    print("[ftp] %s: port %s -> %s" % (self.name, self._ftp_port, port))
                self._ftp_port = port
            return ftp
        self._ftp_port = None          # both gone - re-probe from the configured port next time
        raise last if last else OSError("no FTP port reachable")

    # ---------------- DPI self-healing: probe + automatic reload ----------------
    def mutant_running(self, version=""):
        """Running game as OUR on-console engine sees it (title/pid/base/cheat file).

        Pass the installed version when we know it so the ELF can prefer the exactly
        matching cheat file instead of falling back to "some other version"."""
        q = "/api/cheat/running"
        if version:
            q += "?version=" + quote(str(version))
        try:
            j = self._shop(q, timeout=6)
            return j if j.get("running") else {}
        except Exception:
            return {}

    def console_usb_packages(self):
        """Installable packages the CONSOLE can see on its own removable media.
        Our on-console app already scans /mnt/usb0..7, so ask it rather than
        duplicating the scan here (two scanners would drift apart)."""
        try:
            doc = self._shop("/api/library", timeout=20) or {}
        except Exception:
            return []
        out = []
        for g in (doc.get("games") or []):
            if not str(g.get("source") or "").startswith("usb"):
                continue
            g = dict(g)
            g["lane"] = "install"
            g["on_console"] = False
            out.append(g)
        return out

    def cheat_paths(self):
        """Where the console keeps cheats, and which folders it watches for drop-ins."""
        try:
            return self._shop("/api/cheat/paths", timeout=8)
        except Exception:
            return {}

    def cheat_rescan(self):
        """File anything dropped in since boot, without reloading the payload."""
        try:
            return self._shop("/api/cheat/rescan", timeout=60)
        except Exception:
            return {}

    def mutant_find(self, title_id, version=""):
        """Cheat file OUR engine would use for a title, running or not. This is what keeps
        mod browsing working without CheatRunner."""
        q = "/api/cheat/find?title=" + quote(str(title_id))
        if version:
            q += "&version=" + quote(str(version))
        try:
            j = self._shop(q, timeout=8)
            return j if j.get("ok") else {}
        except Exception:
            return {}

    def mutant_mods(self, cheat_file, pid=None, base=None):
        """Mods plus their LIVE on/off state (needs pid+base, else state is unknown)."""
        q = "/api/cheat/list?file=" + quote(cheat_file)
        if pid:
            q += "&pid=%d" % int(pid)
            if base:
                q += "&base=%s" % (base if isinstance(base, str) else hex(base))
        try:
            return self._shop(q, timeout=25)
        except Exception:
            return {}

    def mutant_apply(self, cheat_file, index, on, pid, base, name="", force=False):
        q = ("/api/cheat/apply?file=%s&mod=%d&on=%d&pid=%d&base=0x%x&name=%s%s"
             % (quote(cheat_file), int(index), 1 if on else 0, int(pid), int(base),
                quote(name[:60]), "&force=1" if force else ""))
        try:
            return self._shop(q, timeout=90)
        except Exception as e:
            return {"ok": False, "error": repr(e)}

    def mutant_patches(self, title_id, version=""):
        """Game patches OUR engine holds for a title (the XML patch library).

        Separate from cheats: a patch is written once rather than toggled, comes from
        <patches>/<TITLEID>.xml, and a title can have patches with no cheat file at all — so this
        is asked for independently of the cheat document.
        """
        q = "/api/patch/list?title=" + quote(str(title_id))
        if version:
            q += "&version=" + quote(str(version))
        try:
            j = self._shop(q, timeout=15)
            return j.get("patches") or []
        except Exception:
            return []

    def mods_action(self, title_id, action, body, timeout=60):
        """Run a mod action (select / toggle / apply / disable-all) through OUR engine.

        The on-console ELF implements all four itself — it resolves the cheat file, gates each
        write on the memory already holding the expected bytes, and raises the on-screen toast.
        Proxying to it keeps ONE implementation of the engine instead of a second, drifting copy
        here; this is also the only path that works when the PC is driving a game on the console.

        Returns the console's reply as-is (including its own honest refusals, e.g.
        `game_not_running`), or None when our engine could not be reached at all.
        """
        try:
            return self._shop_post("/api/mods/%s/%s" % (quote(str(title_id)), action),
                                   body or {}, timeout=timeout)
        except Exception:
            return None

    def running_title(self, ttl=4.0):
        """Title id of the game running on the console right now (cached briefly)."""
        now = time.time()
        if now - getattr(self, "_run_ts", 0) < ttl:
            return getattr(self, "_run_cache", {})
        r = self.mutant_running()          # our engine first
        if r:
            r = {"titleId": r.get("title_id"), "titleName": "", "pid": r.get("pid"),
                 "imageBase": r.get("base"), "running": True}
        else:
            # CheatRunner was removed from this project; our own engine answers in ~15ms. Asking for
            # it anyway cost a refused connection — 2.07s on Windows — on EVERY health check. That
            # pushed /api/health past the 1.5s the console allows when looking for a companion, so
            # the PS5 never found one and fell back to its own API, which can only install packages
            # already on the console. One dead call was breaking installing from a PC entirely.
            # Kept as a fallback for anyone still running it, but only retried occasionally.
            if now - getattr(self, "_cr_fail_ts", 0) < 600:
                r = {}
            else:
                try:
                    r = self.cheats.running()
                except Exception:
                    r = {}
                # Its client swallows every error and returns a dict, so an exception never arrives
                # here — the memo has to key off the RESULT. Nothing back means nothing to ask, and
                # our own engine has already answered the "which game is running" question anyway.
                self._cr_fail_ts = 0.0 if r else now
        self._run_cache, self._run_ts = r, now
        return r


    @property
    def cheats(self):
        if getattr(self, "_cr", None) is None:
            self._cr = CheatRunner(self.ip, self.cfg.get("cheats", {}).get("port", CheatRunner.PORT))
        return self._cr

    @property
    def pldmgr(self):
        if self._pldmgr is None:
            port = self.cfg.get("dpi", {}).get("pldmgr_port", PayloadManager.PORT)
            self._pldmgr = PayloadManager(self.ip, port)
        return self._pldmgr

    @property
    def name(self):
        return self.c["name"]

    @property
    def ip(self):
        return self.c["ip"]

    def ftp_ok(self, timeout=1.2):
        """Is ANY console FTP up? Same 2121/1337 question as _ftp() - probing only the configured
        port reported "FTP down" the moment Arsenal's ftpsrv stopped, while etaHEN's 1337 was
        serving perfectly and the app was happily reading app.db over it."""
        if not self.ip:
            return False
        first = self._ftp_port or self.c.get("ftp_port") or 2121
        for port in [first] + [p for p in self.FTP_FALLBACKS if p != first]:
            try:
                with socket.create_connection((self.ip, port), timeout=timeout):
                    self._ftp_port = port
                    return True
            except OSError:
                continue
        return False

    def notify(self, text):
        """Put a message on the television. Our own HTTP endpoint first; the legacy socket after.

        This used to open a socket to console.notify_port (9099) and nothing else. Nothing listens
        on 9099 - it is not bound by this app or by any payload we ship - so every call quietly
        returned False and no notification was ever sent from the PC.
        """
        if not self.ip:
            return False
        try:
            j = self._shop("/api/notify?text=%s" % quote(str(text).replace("\n", " "), safe=""),
                           timeout=6) or {}
            if j.get("ok"):
                return True
        except Exception:
            pass
        # Legacy path, for a console still running an ELF from before /api/notify existed.
        port = self.cfg.get("console", {}).get("notify_port", 9099)
        try:
            with socket.create_connection((self.ip, port), timeout=2) as s:
                s.sendall(("notify " + str(text).replace("\n", " ") + "\n").encode("utf-8"))
                try:
                    s.recv(16)
                except OSError:
                    pass
            return True
        except OSError:
            return False

    def open_shop(self, url=None):
        """Ask the console to open the shop in its own browser. Returns the console's own answer.

        Returns a dict now, not a bool: whether the browser actually opened and whether the console
        at least put the address on screen are different outcomes and the user deserves to be told
        which one happened. sceSystemServiceLaunchWebBrowser is resolved by name on the console, so
        a firmware that does not export it degrades to the notification rather than to silence.
        """
        if not self.ip:
            return {"ok": False, "error": "no console configured"}
        try:
            q = "/api/open" + ("?url=%s" % quote(str(url), safe="") if url else "")
            j = self._shop(q, timeout=8) or {}
            if j:
                return j
        except Exception:
            pass
        port = self.cfg.get("console", {}).get("notify_port", 9099)
        cmd = ("open " + str(url).replace("\n", " ") if url else "open") + "\n"
        try:
            with socket.create_connection((self.ip, port), timeout=2) as s:
                s.sendall(cmd.encode("utf-8"))
                try:
                    s.recv(16)
                except OSError:
                    pass
            return {"ok": True, "launched": True, "notified": False, "legacy": True}
        except OSError:
            return {"ok": False, "error": "PKG MUTANT SHOP on the PS5 did not answer - "
                                          "load it again from Payload Manager"}

    # ---------------- our own install engine (on-console, no Elf Arsenal) -------------
    def _shop(self, path, timeout=30, data=None):
        """Call the on-console shop server (our ELF). `data` (a dict) makes it a POST.

        An HTTP error status from the console is NOT a failure to reach the console, and the
        difference matters enormously: on 2026-08-25 a 409 "an install is already running" was
        reported as "the console did not answer - load it again from Payload Manager", the ELF was
        reloaded on that advice, and reloading zeroed the very latch that was refusing the
        duplicate. Callers that care can now see the status and the console's own words.
        """
        # Ask the cheap question first. Without this, every proxied call to a console that is
        # switched off waits out its full HTTP timeout.
        if not self.up():
            raise IOError("the console is not answering on the network")
        port = self.cfg.get("console", {}).get("shop_port", 8710)
        url = "http://%s:%d%s" % (self.ip, port, path)
        req = url
        if data is not None:
            req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            try:
                j = json.loads(body)
            except ValueError:
                j = {}
            raise ShopHTTPError(e.code, j, body, url)

    def _shop_text(self, path, timeout=15):
        """GET a PLAIN TEXT endpoint on the console. _shop() json.loads() its answer, which is
        exactly wrong for the install log - the only thing it could ever return is an exception."""
        url = "http://%s:%d%s" % (self.ip, self.cfg.get("console", {}).get("shop_port", 8710), path)
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")

    def _shop_post(self, path, payload, timeout=900):
        """POST to the on-console shop. Installs from a stick can take minutes, so the
        timeout is generous -- the console is doing real work, not hanging."""
        port = self.cfg.get("console", {}).get("shop_port", 8710)
        url = "http://%s:%d%s" % (self.ip, port, path)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def install_local_on_console(self, path, name=""):
        """Install a package that is ALREADY on the console (USB stick, external drive).
        Nothing is transferred, so this bypasses the download/DPI lane entirely."""
        return self._shop_post("/api/install", {"install_key": "local:" + path,
                                                "path": path, "name": name})

    def helper_status(self):
        """Live state of the two helper services, for the settings panel.

        ShadowMount binds loopback-only, so from the PC we can only report it as 'unknown'
        rather than pretending it is down — the on-console shop answers this accurately."""
        def up(port, t=0.35):
            try:
                with socket.create_connection((self.ip, port), timeout=t):
                    return True
            except OSError:
                return False
        smp = self.cfg.get("shadowmount", {}).get("port", 10101)
        # ShadowMount binds LOOPBACK-ONLY on the console, so probing it from the PC can never
        # succeed - it can only burn its full timeout. Measured: this endpoint took 771 ms to
        # return 564 bytes, three sequential 0.7 s probes deep, and the on-console answer below
        # overwrote the ShadowMount value anyway. The UI calls this from updateSrcBar() on every
        # successful library load, so it sat on the boot path for three quarters of a second.
        # Do not probe what cannot answer; run the two that can, side by side.
        res = {}

        def _probe(key, port):
            res[key] = up(port)

        def _probe_ftp():
            # NOT a single-port probe. Which FTP is up depends on which host is running (Arsenal's
            # ftpsrv on 2121, etaHEN's own on 1337), so this asks the same question _ftp() asks and
            # leaves self._ftp_port pointing at whichever answered.
            res["ftp"] = self.ftp_ok(timeout=1.2)

        # Only FTP is worth probing from here. The "install host" probe that used to run beside it
        # asked whether a third-party daemon was on :12800; there is no such daemon, and the port it
        # read was removed from the console dict by the purge - so this function raised KeyError and
        # its caller quietly turned that into "ShadowMount is not running".
        th = threading.Thread(target=_probe_ftp, daemon=True)
        th.start()
        th.join(timeout=2.5)
        # shadowmount starts as None = UNKNOWN, not False. It binds loopback-only, so from the PC
        # the honest answer is "cannot tell from here" - the console's own answer replaces it below
        # whenever it is reachable.
        out = {"shadowmount": None, "shadowmount_port": smp,
               "ftp": res.get("ftp", False),
               "ftp_port": self._ftp_port or self.c.get("ftp_port") or 2121}
        # ShadowMount listens on loopback only, so a LAN probe always says "down". Our on-console
        # shop can see it properly — prefer its answer whenever it is reachable.
        try:
            j = self._shop("/api/helpers", timeout=4)
            if j.get("ok"):
                for k in ("shadowmount", "ftp"):
                    if k in j:
                        out[k] = bool(j[k])
                # The console knows the real port numbers; the PC only has config. ShadowMount in
                # particular moved (see the shadowmount default below) and a stale config here is
                # what put ":9021 - active" under a service that is not on 9021.
                for k in ("shadowmount_port",):
                    if j.get(k):
                        out[k] = int(j[k])
                out["source"] = "console"
        except Exception:
            out["source"] = "lan"
        return out

    def engine_available(self):
        try:
            return bool(self._shop("/api/health", timeout=4).get("on_console"))
        except Exception:
            return False

    def install_pms(self, url, name=""):
        """Hand the PKG to OUR engine: it downloads it and installs it itself.

        No third-party daemon in the path, so there is nothing to wedge — which is what
        made multi-install fragile before. The console pulls over plain HTTP from the
        companion, then registers the package with sceAppInstUtilAppInstallPkg."""
        q = "/api/engine/install-url?url=%s&name=%s" % (quote(url, safe=""), quote(name or "", safe=""))
        try:
            j = self._shop(q, timeout=60)
            return bool(j.get("ok")), j
        except ShopHTTPError as e:
            print("[pms] the console refused with HTTP %d: %s" % (e.status, e.body[:160]))
            return False, {"error": e.sentence("The console refused this request"),
                           "http_status": e.status, "do_not_reload": True}
        except Exception as e:
            return False, {"error": "PKG MUTANT SHOP on the PS5 did not answer - load it again "
                                    "from Payload Manager",
                           "detail": repr(e)[:200],
                           "hint": "is the PKG MUTANT SHOP ELF loaded on the PS5?"}

    def fetch_to(self, url, dest, name, timeout=60, size=0):
        """Ask the console to DOWNLOAD a file into `dest` under exactly `name`, and nothing else.

        This is the PS5 backup lane: our own downloader pulls the container into the drive's
        homebrew folder and ShadowMount mounts it. The console writes "<name>.part" and renames only
        once the last byte is across, because ShadowMount mounts whatever appears in that folder and
        a half-written container mounts as a broken game.

        Returns (ok, info). ok=False with info["unsupported"] means this console is running an ELF
        from before the endpoint existed - the caller should fall back to pushing.
        """
        q = "/api/engine/fetch?url=%s&dest=%s&name=%s" % (
            quote(url, safe=""), quote(dest, safe=""), quote(name, safe=""))
        # Tell it how big this is, so it can refuse a drive without room BEFORE the transfer rather
        # than filling one. The "internal" destination is /data/homebrew on the system partition,
        # which is exactly where a 100 GB backup must never silently land.
        if size:
            q += "&size=%d" % int(size)
        try:
            j = self._shop(q, timeout=timeout) or {}
        except ShopHTTPError as e:
            return False, {"error": e.sentence("The console refused the download"),
                           "http_status": e.status}
        except Exception as e:
            return False, {"error": "PKG MUTANT SHOP on the PS5 did not answer", "detail": repr(e)[:200]}
        if not j:
            # The unknown-/api/ stub answers {} - i.e. this ELF predates /api/engine/fetch.
            return False, {"unsupported": True,
                           "error": "This console is running an older PKG MUTANT SHOP"}
        if not j.get("ok"):
            return False, {"error": j.get("error") or "The console would not start the download"}
        return True, j

    def engine_job(self):
        try:
            return self._shop("/api/engine/job", timeout=20)
        except Exception:
            return None

    def install_spawn(self, url, name="", force=False):
        """OUR OWN base-game lane. No etaHEN anywhere in the call.

        sceAppInstUtilInstallByPackage returns 0x80B2116F when called from a payload injected into
        a hijacked host process - which is what our shop ELF is - and succeeds from a freshly
        SPAWNED process. So the ELF writes the URI to a request file, has Payload Manager spawn
        pms-installer.elf, and that process makes the call. Proven on hardware 2026-08-25:
        Castle Crashers (CUSA14409, absent beforehand) -> bgft 1036, title "PKG MUTANT SHOP".

        Returns (ok, info) with the same shape every other lane uses. `ok` means the console
        ACCEPTED the package and created a BGFT task - completion is still proven from bgft.db,
        exactly as it is for etaHEN.
        """
        # DOUBLE-SUBMIT DEBOUNCE, and nothing more.
        #
        # This used to remember accepted URLs for THIRTY MINUTES and refuse any repeat, which asks
        # the wrong question entirely: "did we send this recently" instead of "is the console busy
        # with it". Deleting a game and reinstalling it inside half an hour was refused, with a
        # message that claimed the console might still be installing something it had finished, and
        # an instruction to use a Force button the UI does not have. Two jobs, permanently held.
        #
        # The real protection - is there a LIVE bgft job for this content - lives in Queue._run(),
        # which already reads that row. What is left here is a two-minute window that exists only to
        # swallow a genuine double-click, and it is cleared the moment a verdict arrives (about two
        # seconds later), so it can never block a deliberate reinstall.
        now = time.time()
        inflight = getattr(self, "_inflight", None)
        if inflight is None:
            inflight = self._inflight = {}
        for k in [k for k, v in inflight.items() if now - v > 120]:
            inflight.pop(k, None)
        if url in inflight and not force:
            print("[spawn] ignoring a repeat of %s sent %ds ago - the first one has not reported "
                  "back yet" % (url.rsplit("/", 1)[-1], int(now - inflight[url])))
            return False, {"error": "That package was handed to the console a moment ago and it "
                                    "has not answered yet. Give it a few seconds.",
                           "host": "pms-spawn", "busy": True, "do_not_reload": True}
        inflight[url] = now

        # One at a time. The console serialises per-console already, but a retry or a stray
        # request could still overlap - and both installs would share one request file.
        #
        # 20 x 1.5s = 30 seconds, not two minutes: the console releases the latch by itself the
        # moment a verdict exists, so anything still set after half a minute is a spawned installer
        # that died without writing one. Clearing that is exactly what spawn_cleanup() is for, and
        # holding the queue behind it helps nobody.
        waited = 0.0
        for _ in range(20):
            st = self.spawn_status()
            if not st.get("busy"):
                break
            time.sleep(1.5)
            waited += 1.5
        else:
            # Only clear a latch that is provably stale. The console releases it by itself once a
            # verdict exists, so one still set with NO result and a short age is guarding a live
            # hand-over - and clearing that is how a second installer gets spawned on top of the
            # first. 600s matches the console's own expiry.
            st = self.spawn_status() or {}
            if st.get("has_result") or int(st.get("busy_for") or 0) >= 600:
                print("[spawn] the lane was still marked busy after %ds but the job is over "
                      "(has_result=%s busy_for=%ss) - clearing it"
                      % (int(waited), st.get("has_result"), st.get("busy_for")))
                self.spawn_cleanup()
            else:
                print("[spawn] the lane is genuinely busy (%ss) - NOT clearing it"
                      % st.get("busy_for"))
                return False, {"error": "An install is still being handed to the console. Let it "
                                        "finish, then start this one.",
                               "host": "pms-spawn", "busy": True, "do_not_reload": True}
        try:
            j = self._shop("/api/engine/install-spawn?uri=%s&name=%s"
                           % (quote(url, safe=""), quote(name or "", safe="")), timeout=90)
        except ShopHTTPError as e:
            # THE CONSOLE ANSWERED. 409 is its busy guard refusing a second install while one is
            # still being handed over - that refusal is protective and must be relayed, never
            # dressed up as an absence. No `queued` key: see the re-drive gate in Queue._run.
            print("[spawn] the console refused with HTTP %d: %s" % (e.status, e.body[:160]))
            return False, {"error": e.sentence("An install is already running on the console - "
                                               "let it finish, then try this one again"),
                           "host": "pms-spawn", "busy": True, "http_status": e.status,
                           "do_not_reload": True}
        except Exception as e:
            return False, {"error": "PKG MUTANT SHOP on the PS5 did not answer - load it again "
                                    "from Payload Manager",
                           "detail": repr(e)[:200], "host": "pms-spawn",
                           "hint": "is the PKG MUTANT SHOP ELF loaded on the PS5?"}
        if not j.get("ok"):
            # This one really is pre-queue: the console answered 200 and said it did not start an
            # installer, so nothing exists to duplicate.
            return False, {"error": j.get("error") or "The console could not start the installer",
                           "host": "pms-spawn", "queued": False,
                           "busy": bool(j.get("busy"))}
        # The spawned process writes its verdict to a file; poll for it rather than guessing.
        deadline = time.time() + 90
        misses = 0                      # consecutive TRANSPORT failures, not 404s
        while time.time() < deadline:
            time.sleep(2)
            try:
                r = self._shop("/api/engine/spawn-result", timeout=15)
                misses = 0
            except ShopHTTPError:
                # 404 = "no result yet", which is the normal case for most of this loop.
                misses = 0
                continue
            except Exception as e:
                # A TRANSPORT failure is different: the console is not answering. Say so, and stop
                # rather than polling a corpse for the rest of the 90 seconds.
                #
                # But the verdict behind _shop() is CACHED for UP_TTL_DOWN seconds and this loop
                # sleeps 2, so without forcing a fresh probe ONE unlucky connect is re-served from
                # cache twice and spends all three strikes in about four seconds - on an install
                # that is running perfectly. The message that produces is the one that invites a
                # mid-install reload, which is exactly how the duplicate-install crash happened.
                if self.up(fresh=True):
                    misses = 0
                    continue
                misses += 1
                if misses == 1:
                    print("[spawn] the console stopped answering mid-install (%s)" % repr(e)[:90])
                if misses >= 3:
                    print("[spawn] giving up after %d transport failures - the console is gone"
                          % misses)
                    return False, {"error": "The console stopped answering while it was installing "
                                            "this package. Check the PS5 before trying again.",
                                   "host": "pms-spawn", "console_gone": True,
                                   "no_verdict": True, "do_not_reload": True}
                continue
            if r.get("rc") is None:
                continue
            # Clear the handover files either way, so the NEXT install in the queue cannot read
            # this one's request or verdict. This is our equivalent of the third-party host's
            # /cleartmp - except there is no daemon to unstick and no port to clear, because each
            # install is a fresh process that has already exited.
            self.spawn_cleanup()
            self._inflight.pop(url, None)      # it answered; the debounce has done its job
            if r.get("ok"):
                return True, {"res": "0", "host": "pms-spawn", "via": "spawned-process",
                              "content_id": r.get("content_id") or "", "rc": r.get("rc")}
            # A refusal here is pre-BGFT: no task was registered, so nothing is half-queued.
            return False, {"error": "%s (%s)" % (install_error_text(r.get("rc")), r.get("rc")),
                           "host": "pms-spawn", "rc": r.get("rc"), "queued": False}
        # NO `queued` KEY. Its absence is load-bearing: `queued is False` is the re-drive gate in
        # Queue._run, and re-driving after a timeout is how one package became several BGFT jobs.
        # We do not know what happened - say so, and stop.
        print("[spawn] no verdict after 90s - NOT re-submitting; the console may still be "
              "installing this package")
        return False, {"error": "The console took this package but never reported back. It may "
                                "still be installing it - check the PS5 before trying again.",
                       "host": "pms-spawn", "no_verdict": True, "do_not_reload": True}

    def spawn_cleanup(self):
        """Remove the spawn lane's handover files and release its busy latch. Never raises."""
        try:
            return self._shop("/api/engine/spawn-cleanup", timeout=15)
        except Exception as e:
            print("[spawn] cleanup skipped: %r" % e)
            return None

    def spawn_status(self):
        """Is a spawned install in flight right now?"""
        try:
            return self._shop("/api/engine/spawn-status", timeout=10) or {}
        except Exception:
            return {}

    def install(self, url, name="", force=False):
        """Install a package on the console with OUR OWN engine. Returns (ok, info).

        This used to dispatch between four lanes. It does not any more - see install_spawn().
        Kept as the single entry point so every caller has one thing to call.

        Historical, for anyone reading old code: 'ezremote' mode = cy33hc's ps5-ezremote-dpi standalone
        payload on TCP 9040: open a socket, send the pkg URL as one line, done. Older 'v2'/'v1'
        modes speak the etaHEN/GoldHEN HTTP+JSON API. Returns (ok, info)."""
        # ONE LANE, and no configuration can select another. There used to be four - two of ours
        # and two third-party protocols - chosen by dpi.mode, whose default here ("ezremote")
        # disagreed with the default in DEFAULT_CONFIG ("v2"), so what an unconfigured PC did was
        # not predictable from reading either one. Both third-party lanes are gone, along with the
        # probe that decided which foreign daemon owned :12800.
        return self.install_spawn(url, name, force=force)

    SMP_CONFIG = "/data/shadowmount/config.ini"

    def register_path(self):
        """Which route will ShadowMount use to REGISTER a newly staged title?

        This is the single setting that decides whether a PS5 backup becomes a working game or a
        tile that crashes, and it is worth stating plainly because it cost a long night to find.

        ShadowMount does two separate things: it MOUNTS the container, then it REGISTERS the title
        with the system. Mounting had always worked. Registering failed every time, with:

            [REG] internal AppInstallTitleDir bridge unavailable
            NOTIFY: Register failed: <game> (<TID>)

        That "internal bridge" is a patch ShadowMount writes into SceShellCore, and on this console
        it is never installed at all — its own startup log says why:

            [SHELLCORE] unexpected prologue: launchApp at 0x1563c660
            [SHELLCORE] lifecycle hooks unavailable; stock behavior kept

        So the direct per-title register route cannot work here. ShadowMount has a second route,
        and its own documentation says this firmware is supposed to be on it already:

            # 1/true/yes/on  -> submit queued installs through sceAppInstUtilAppInstallAll
            # 0/false/no/off -> register each title directly
            # Default: 0 on FW below 12.00, forced to 1 on FW 12.00 and newer

        The console is on 12.70 — but its runtime config dump read `app_install_all=0`, so the
        forcing does not happen in 1.7alpha4 and it used the broken route. Setting the key
        explicitly switched the very same file from "Register failed" to "[REG] Installed", with
        full `appmeta` (which is what gives the PS5 notification the real name and artwork instead
        of "Unknown"). No console restart was involved.

        Returns {"ok", "value", "reason"} and never raises.
        """
        text = self.fs_text(self.SMP_CONFIG, timeout=8)
        if text is None:
            return {"ok": None, "value": None,
                    "reason": "no ShadowMount config at %s" % self.SMP_CONFIG}
        val = None
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            if k.strip() == "app_install_all":
                val = 1 if v.strip().lower() in ("1", "true", "yes", "on") else 0
        if val == 1:
            return {"ok": True, "value": 1, "reason": "batch registration enabled"}
        return {"ok": False, "value": val,
                "reason": "app_install_all is %s — new titles would mount but fail to register"
                          % ("absent" if val is None else "0")}

    def ensure_register_path(self):
        """Put ShadowMount on the register route that works here, rewriting its config if needed.

        Returns 'ok' | 'fixed' | 'unknown'. 'fixed' means the file was changed and ShadowMount has
        to be restarted before it takes effect — it reads the config once, at startup.

        Deliberately conservative: the file is only rewritten after it has been read in full, the
        original text is preserved byte for byte, and the key is appended rather than reformatting
        anything. Everything else in that config is the user's (fakelib excludes, kstuff rules,
        scan paths) and must survive untouched.
        """
        st = self.register_path()
        if st.get("ok") is True:
            return "ok"
        if st.get("ok") is None:
            return "unknown"
        try:
            text = self.fs_text(self.SMP_CONFIG, timeout=8)
            if not text or not text.strip():
                return "unknown"                      # never overwrite a config we failed to read
            out, done = [], False
            for line in text.splitlines(True):
                s = line.strip()
                if not s.startswith("#") and s.split("=", 1)[0].strip() == "app_install_all":
                    if not done:
                        out.append("app_install_all=1\n")
                        done = True
                    continue                          # drop any further/duplicate assignments
                out.append(line)
            if not done:
                out.append(
                    "\n# --- REGISTER PATH (set by PKG MUTANT SHOP) ------------------------------\n"
                    "# FW 12.70. The direct per-title register route needs a SceShellCore patch that\n"
                    "# is never installed on this console (\"unexpected prologue: launchApp\" ->\n"
                    "# \"lifecycle hooks unavailable\"), so it always ended in\n"
                    "# \"[REG] internal AppInstallTitleDir bridge unavailable\" / Register failed.\n"
                    "# This routes registration through sceAppInstUtilAppInstallAll instead, which\n"
                    "# ShadowMount's own docs say is required on FW 12.00 and newer.\n"
                    "app_install_all=1\n")
            new = "".join(out)
            if not self.fs_write(self.SMP_CONFIG, new.encode("utf-8"), timeout=20):
                return "unknown"
            return "fixed"
        except Exception:
            return "unknown"

    def restart_shadowmount(self, wait=45):
        """Reload the ShadowMount payload so a config change takes effect. Only when it is safe.

        Restarting remounts every backup, so it must never happen while a game is running off one.
        Returns 'restarted' | 'busy' | 'failed'.
        """
        try:
            if (self.running_title() or {}).get("title_id"):
                return "busy"
        except Exception:
            pass
        pm = self.pldmgr
        path = pm.payload_path("shadowmount")
        if not path:
            return "failed"
        for pid in pm.pids_named("shadowmountplus"):
            pm.kill(pid)
        time.sleep(5)
        if not pm.load(path):
            return "failed"
        deadline = time.time() + wait
        while time.time() < deadline:
            if pm.pids_named("shadowmountplus"):
                time.sleep(8)              # let it finish its startup scan before we stage anything
                return "restarted"
            time.sleep(3)
        return "failed"

    def title_has_mount_link(self, title_id):
        """Does `/user/app/<TID>/mount.lnk` exist — i.e. is a container actually mounted for it?"""
        if not self.ip or not title_id:
            return False
        # fs_list returns None rather than raising, so the try/finally that used to tear down an
        # FTP connection here has nothing left to guard.
        for r in ("/user/app", "/mnt/ext0/user/app", "/mnt/ext1/user/app", "/mnt/ext2/user/app"):
            rows = self.fs_list("%s/%s" % (r, title_id), timeout=10)
            if not rows:
                continue
            for e in rows:
                if (e.get("name") or "").strip().lower() == "mount.lnk":
                    return True
        return False

    def title_is_mounted(self, title_id):
        """Has ShadowMount finished — mounted AND *registered* the title?

        BOTH are required, and each one alone has already lied to us in production:

          * `mount.lnk` alone — ShadowMount writes the links BEFORE it registers, so a title whose
            register step failed still has one. That reported "ready to play" for Evergate while
            the console had never registered it.

          * app.db alone — a registration outlives its data. Little Nightmares III sat in app.db at
            75 MB of metadata with no container on the console at all: `/user/app/PPSA05144` held
            only `icon0.png` and `sce_sys`, no `mount.lnk`. Waiting on app.db would have passed
            instantly, on a tile that crashes.

        A genuinely working backup has both — verified against all 17 mounted titles, and against
        each of the two failures above. So we require both.
        """
        if not self.ip or not title_id:
            return False
        try:
            if title_id not in (self.installed_titles(force=True) or []):
                return False
            return self.title_has_mount_link(title_id)
        except Exception:
            return False

    def installed_app_pkg(self, title_id):
        """Size of the installed game's `app.pkg` on the console, or None if it is not there.

        This is the only honest proof that a title really installed. app.db registers a title as
        soon as its metadata lands, and `sceAppInstUtilAppInstallPkg` writes metadata WITHOUT the
        data — so "it is in app.db" has been telling us a game was ready when the console had
        nothing to run. A registered title with no `app.pkg` is the broken tile that crashes on
        launch. Verified layout: /user/app/<TID>/app.pkg, or /mnt/ext<n>/user/app/<TID>/app.pkg
        when it lives on extended storage.
        """
        if not self.ip or not title_id:
            return None
        roots = ["/user/app", "/mnt/ext0/user/app", "/mnt/ext1/user/app", "/mnt/ext2/user/app"]
        try:
            for r in roots:
                rows = self.fs_list("%s/%s" % (r, title_id), timeout=10)
                if not rows:
                    continue
                for e in rows:
                    name, size = (e.get("name") or ""), e.get("size")
                    if name.strip().lower() == "app.pkg":
                        try:
                            return int(size)
                        except (TypeError, ValueError):
                            return None
            return None
        except Exception:
            return None

    def install_progress(self, title_id):
        """Live on-console install byte-progress from bgft.db (Background File Transfer):
        returns (transferred_total, length_total) for the title's newest row, or None.
        Best-effort - some install paths don't refresh bgft, so the worker also confirms
        completion via app.db (installed_titles)."""
        if not self.ip or not title_id:
            return None
        # Fetched through _pull_db, which prefers OUR on-console file API and only falls
        # back to the third-party FTP. Unique temp name per pull: the confirm loop, the
        # library scan and the UI can all be here at once, and one thread truncating a file
        # another is mid-read on produces "database disk image is malformed" - which the
        # caller reads as "not installed yet". sweep_temp_dbs() still matches the pms_ prefix.
        tmp = self._pull_db("/system_data/priv/mms/bgft.db")
        if not tmp:
            return None
        try:
            con = sqlite3.connect(tmp)
            row = con.execute(
                "SELECT transferred_total,length_total FROM tbl_downloads "
                "WHERE title_id=? ORDER BY last_updated DESC LIMIT 1", (title_id,)).fetchone()
            con.close()
            if row and row[1]:
                return int(row[0] or 0), int(row[1])
        except sqlite3.Error:
            return None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return None

    # ---- console filesystem: ours first, somebody else's FTP second --------------------------
    #
    # Every console read in this file went through FTP, and FTP here belongs to etaHEN (:1337) or
    # to ftpsrv (:2121) - neither of which we ship. That was 18 call sites of dependency on other
    # people's software for the sake of reading files on a machine where our own payload already
    # runs as root with an HTTP server.
    #
    # So: try our own ELF first, fall back to FTP, and remember which answered. Nothing regresses
    # if the ELF is not loaded - the old path is still there, unchanged, underneath.
    #
    # It is also simply faster. Measured on this console, bgft.db (94,208 bytes), the file the
    # install-confirm loop re-reads every few seconds: HTTP 0.011 s vs FTP 0.079 s - 7.3x - because
    # FTP pays a connect, a login and a data-channel setup for every single fetch.
    FS_TTL = 20.0                       # how long a "the ELF is not answering" verdict is trusted

    UP_TTL_OK = 4.0            # a console that answered is re-checked often - it can be switched off
    UP_TTL_DOWN = 8.0          # a console that did not is given another chance quickly

    def up(self, budget=0.6, fresh=False):
        """Is the console's shop port accepting connections RIGHT NOW? Cached briefly.

        A TCP connect to a machine that is off is refused or times out in milliseconds on a LAN,
        while an HTTP request to the same address waits out its whole timeout. With the PS5 off
        that difference was 10 s on /api/health and 20 s on /api/library - the UI polls health, so
        the entire app sat grey. This only ever skips calls that were going to fail."""
        now = time.time()
        cached, at = getattr(self, "_up", None), getattr(self, "_up_at", 0.0)
        if not fresh and cached is not None and now - at < (self.UP_TTL_OK if cached else self.UP_TTL_DOWN):
            return cached
        if not self.ip:
            return False
        port = self.cfg.get("console", {}).get("shop_port", 8710)
        sock = socket.socket()
        sock.settimeout(budget)
        try:
            ok = sock.connect_ex((self.ip, int(port))) == 0
        except Exception:
            ok = False
        finally:
            try:
                sock.close()
            except Exception:
                pass
        self._up, self._up_at = ok, now
        return ok

    def _fs_http_ok(self):
        """Has our own on-console server answered recently? Cached, so a console without the ELF
        loaded does not pay a probe on every read."""
        now = time.time()
        # A closed port is a definitive no, and costs milliseconds rather than a whole timeout.
        if not self.up():
            return False
        if self._fs_ok_at and now - self._fs_ok_at < self.FS_TTL:
            return self._fs_ok
        return None                     # unknown - just try it

    def _fs_url(self, op, path):
        port = self.cfg.get("console", {}).get("shop_port", 8710)
        return "http://%s:%d/api/fs/%s?path=%s" % (self.ip, port, op, quote(path, safe=""))

    def fs_read(self, path, timeout=60):
        """Bytes of a file on the console, or None. Our ELF first, FTP second."""
        if not self.ip:
            return None
        if self._fs_http_ok() is not False:
            try:
                with urllib.request.urlopen(self._fs_url("read", path), timeout=timeout) as r:
                    data = r.read()
                self._fs_ok, self._fs_ok_at = True, time.time()
                return data
            except urllib.error.HTTPError:
                # The server answered and said no (missing file). That is a real answer about the
                # file, not a transport failure, so do NOT fall through to FTP for it.
                self._fs_ok, self._fs_ok_at = True, time.time()
                return None
            except Exception:
                self._fs_ok, self._fs_ok_at = False, time.time()
        try:
            ftp = self._ftp(8)
            buf = io.BytesIO()
            ftp.retrbinary("RETR " + path, buf.write)
            try:
                ftp.quit()
            except Exception:
                pass
            return buf.getvalue()
        except Exception:
            return None

    def fs_list(self, path, timeout=20):
        """[{name, dir, size, mtime}, ...] for a console directory, or None.

        The FTP fallback parses a LIST listing, which is why the HTTP form exists at all: LIST
        output is a formatted human string with no schema, and every server words it differently.
        """
        if not self.ip:
            return None
        if self._fs_http_ok() is not False:
            try:
                with urllib.request.urlopen(self._fs_url("list", path), timeout=timeout) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                self._fs_ok, self._fs_ok_at = True, time.time()
                if j.get("ok"):
                    return j.get("entries") or []
                return None
            except urllib.error.HTTPError:
                self._fs_ok, self._fs_ok_at = True, time.time()
                return None
            except Exception:
                self._fs_ok, self._fs_ok_at = False, time.time()
        try:
            ftp = self._ftp(8)
            lines = []
            ftp.retrlines("LIST " + path, lines.append)
            try:
                ftp.quit()
            except Exception:
                pass
            out = []
            for ln in lines:
                parts = ln.split(None, 8)
                if len(parts) < 9:
                    continue
                name = parts[8]
                if name in (".", ".."):
                    continue
                try:
                    size = int(parts[4])
                except ValueError:
                    size = 0
                out.append({"name": name, "dir": ln[:1] == "d", "size": size, "mtime": 0})
            return out
        except Exception:
            return None

    def fs_write(self, path, data, timeout=900, size=None):
        """Write to a console file. Our ELF writes to <path>.part and renames, so a broken transfer
        can never be seen as a finished file. Returns True on success.

        `data` is bytes, or a file-like object with `size` given — in which case urllib streams it
        straight off the disk instead of loading it into memory, which is the difference between
        this being usable for a 90 GB game and only for small ones.
        """
        if not self.ip:
            return False
        blen = int(size) if size is not None else len(data)
        if self._fs_http_ok() is not False:
            try:
                req = urllib.request.Request(
                    self._fs_url("write", path), data=data, method="POST",
                    headers={"Content-Type": "application/octet-stream",
                             "Content-Length": str(blen)})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                self._fs_ok, self._fs_ok_at = True, time.time()
                if j.get("ok"):
                    return True
            except _Cancelled:
                # The caller's progress reader stopped the transfer on purpose. Do NOT retry it
                # over FTP, and do NOT mark our own transport dead - neither is broken. The ELF
                # unlinks its .part when the connection drops mid-body, so nothing is left behind.
                raise
            except urllib.error.HTTPError:
                self._fs_ok, self._fs_ok_at = True, time.time()
                return False
            except Exception:
                self._fs_ok, self._fs_ok_at = False, time.time()
        try:
            ftp = self._ftp(15)
            ftp.timeout = timeout
            try:
                ftp.sock.settimeout(timeout)      # a multi-GB push stalls while the console flushes
            except Exception:
                pass
            for d in _mkdirs_list(os.path.dirname(path).replace("\\", "/")):
                try:
                    ftp.mkd(d)
                except Exception:
                    pass
            # .part then rename, matching what the ELF's own write does. Writing straight to the
            # final name means an interrupted transfer leaves a partial container in ShadowMount's
            # scan folder, where it looks exactly like a real game - half a game that mounts, or
            # refuses to, for no visible reason.
            part = path + ".part"
            ftp.storbinary("STOR " + part,
                           io.BytesIO(data) if isinstance(data, bytes) else data,
                           blocksize=262144)
            try:
                ftp.delete(path)                  # a stale copy would block the rename
            except Exception:
                pass
            ftp.rename(part, path)
            try:
                ftp.quit()
            except Exception:
                pass
            return True
        except _Cancelled:
            try:
                ftp.delete(path + ".part")      # never leave a partial in a watched folder
            except Exception:
                pass
            raise
        except Exception:
            return False

    def fs_text(self, path, timeout=20):
        """A console text file as str, or None. Convenience over fs_read - most of these are small
        configs and json records, and every caller was decoding the same way."""
        data = self.fs_read(path, timeout=timeout)
        if data is None:
            return None
        return data.decode("utf-8", "replace")

    def fs_mkdir(self, path, timeout=15):
        """Create a directory on the console (parents included). True on success.

        Our ELF's write path calls mkparents() itself, so this is only needed where a directory has
        to exist independently of any file - the cheat tree, and the mount destination.
        """
        if not self.ip:
            return False
        if self._fs_http_ok() is not False:
            try:
                with urllib.request.urlopen(self._fs_url("mkdir", path), timeout=timeout) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                self._fs_ok, self._fs_ok_at = True, time.time()
                return bool(j.get("ok"))
            except urllib.error.HTTPError:
                self._fs_ok, self._fs_ok_at = True, time.time()
                return False
            except Exception:
                self._fs_ok, self._fs_ok_at = False, time.time()
        try:
            ftp = self._ftp(8)
            for d in _mkdirs_list(path):
                try:
                    ftp.mkd(d)
                except Exception:
                    pass                        # already there
            try:
                ftp.quit()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _pull_db(self, remote):
        """Fetch a console sqlite database to a private temp file. Returns the path, or None.

        Every caller used to open its own FTP connection for this. Now they share one transport
        decision, and all four became independent of etaHEN's FTP at the same time.
        """
        data = self.fs_read(remote, timeout=60)
        if not data:
            return None
        _fd, tmp = tempfile.mkstemp(prefix="pms_db_", suffix=".db")
        try:
            with os.fdopen(_fd, "wb") as f:
                f.write(data)
        except OSError:
            self._drop_tmp(tmp)
            return None
        return tmp

    @staticmethod
    def _drop_tmp(path):
        # None-tolerant on purpose: callers reach here on failure paths where the temp file was
        # never created, and os.remove(None) raises TypeError, which `except OSError` misses.
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def install_job_row(self, title_id):
        """Newest bgft.db row for a title as (status, transferred, length, last_updated).

        `install_progress` deliberately returns only bytes, which cannot distinguish "this title
        has an old completed row from its base install" from "the thing I just submitted landed".
        The status code is what separates them:

            1000  queued / parked        1009  downloading or installing
            1026  a PATCH/update install completed
            1036  a full title install completed

        A base game leaves a 1036 row; an update leaves its own 1026 row. That distinction is the
        only honest proof an ADD-ON installed, because the base game's app.pkg is already on disk
        and trivially satisfies any size check made against the add-on's much smaller package.
        """
        if not self.ip or not title_id:
            return None
        # Fetched through _pull_db, which prefers OUR on-console file API and only falls
        # back to the third-party FTP. Unique temp name per pull: the confirm loop, the
        # library scan and the UI can all be here at once, and one thread truncating a file
        # another is mid-read on produces "database disk image is malformed" - which the
        # caller reads as "not installed yet". sweep_temp_dbs() still matches the pms_ prefix.
        tmp = self._pull_db("/system_data/priv/mms/bgft.db")
        if not tmp:
            return None
        try:
            con = sqlite3.connect(tmp)
            row = con.execute(
                "SELECT status,transferred_total,length_total,last_updated FROM tbl_downloads "
                "WHERE title_id=? ORDER BY last_updated DESC, rowid DESC LIMIT 1",
                (title_id,)).fetchone()
            con.close()
            if row:
                # Column types are not guaranteed here: on this firmware last_updated is a TEXT
                # timestamp, and a numeric column can still come back as text. Coerce what has to be
                # a number, and keep last_updated verbatim — it is only ever compared for equality
                # against the pre-handoff snapshot, never arithmetic.
                def _n(v):
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return 0
                return (_n(row[0]), _n(row[1]), _n(row[2]), row[3])
        except sqlite3.Error:
            return None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return None

    def installed_addons(self, force=False):
        """content_ids of every DLC the console has registered, from addcont.db. None if unreadable.

        This is the only place a DLC is recorded — a DLC does not appear in app.db and does not get
        its own app.pkg, so without this we have no way to know one is already there.
        """
        now = time.time()
        if not force and self._addons is not None and now - self._addons_ts < 30:
            return self._addons
        if not self.ip:
            return None
        # Fetched through _pull_db, which prefers OUR on-console file API and only falls
        # back to the third-party FTP. Unique temp name per pull: the confirm loop, the
        # library scan and the UI can all be here at once, and one thread truncating a file
        # another is mid-read on produces "database disk image is malformed" - which the
        # caller reads as "not installed yet". sweep_temp_dbs() still matches the pms_ prefix.
        tmp = self._pull_db("/system_data/priv/mms/addcont.db")
        if not tmp:
            return None
        try:
            con = sqlite3.connect(tmp)
            got = {str(r[0]) for r in con.execute("SELECT content_id FROM addcont") if r[0]}
            con.close()
        except sqlite3.Error:
            return None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        self._addons, self._addons_ts = got, now
        return got

    def _name_of(self, tid):
        """The console's own name for a title, falling back to the id. Used in messages the user
        has to make a decision about - "Riptide GP2 is already installed" is a question someone can
        answer; "CUSA02365 is already installed" is one they have to go and look up."""
        try:
            for a in (self.console_apps() or []):
                if a.get("title_id") == tid and a.get("name"):
                    return a["name"]
        except Exception:
            pass
        return tid

    def already_installed(self, kind, title_id, version="", content_id="", pkg_bytes=0):
        """Is this exact package already on the console? Returns a reason string, or "".

        Reinstalling something that is already there is never harmless: for a base game the console
        SKIPS the re-pull and keeps the old files (the stale_install trap), and for an add-on it is
        pure wasted transfer. Each kind needs a different proof, because the console records them
        in completely different places:

            base game   app.db registration AND its own app.pkg on disk at full size
            update      app.db's APP_VER already at or past the package's version
            dlc         its content_id present in addcont.db

        Anything we cannot read comes back "" — never block an install on a failed lookup.
        """
        kind = (kind or "").lower()
        tid = (title_id or "").upper()
        if not tid:
            return ""
        try:
            if kind in ("dlc", "backport"):
                have = self.installed_addons()
                cid = (content_id or "").strip()
                if have is not None and cid and cid in have:
                    return "this add-on is already installed on %s" % self.name
                return ""
            if kind in ("update", "patch"):
                want = _ver_key(version)
                if not want:
                    return ""
                for a in (self.console_apps() or []):
                    if str(a.get("title_id", "")).upper() != tid:
                        continue
                    have = _ver_key(a.get("app_ver") or "")
                    if have and have >= want:
                        return ("%s is already at version %s" % (a.get("name") or tid,
                                                                 a.get("app_ver")))
                return ""
            # base game
            if tid not in (self.installed_titles() or []):
                return ""
            got = self.installed_app_pkg(tid)
            if got and (not pkg_bytes or got >= int(pkg_bytes * 0.98)):
                return ("%s is already installed on %s" % (self._name_of(tid), self.name))
        except Exception:
            return ""
        return ""

    # Elf Arsenal and everything it spawns. NOT the generic "payload.elf" - Payload Manager names
    # EVERY loaded payload that, including our own ELF, so killing by that name kills the app
    # itself (learned the hard way). Only ever match specific names.
    # Elf Arsenal and the daemons Arsenal itself spawns. Used both to answer "is Arsenal here?"
    # and to decide what to sweep off the install port.
    #
    # nanodns.elf, ftpsrv.elf and klogsrv were in this tuple and are NOT Arsenal's. They are
    # Payload Manager autoload entries - /data/pldmgr/autoload.txt on this console reads
    # "!5000 / nanodns.elf / ftpsrv.elf / kstuff-lite_v1.10.elf / shadowmountplus.elf" - so they
    # come back on every boot no matter what we do. CHANGELOG.md already recorded that correction
    # once; it was never propagated here, and the consequences were both live:
    #   * "Fix install-port conflicts" stopped ftpsrv.elf and nanodns.elf - the only two matches on
    #     a healthy console - and nothing that can contend for :12800. ftpsrv owns :2121, which is
    #     the FTP this app itself uses to read app.db and bgft.db.
    #   * _arsenal_running() returned True on a console with no Arsenal, so every install ran the
    #     full Arsenal preflight: an FTP fetch of /data/elf-arsenal/config.ini plus two refused
    #     connects to :6969, measured at ~4.1 s of dead time per install.
    # The fight is over :12800. ftpsrv binds 2121, nanodns binds 53/udp, klogsrv binds neither -
    # none of them can ever be an install-port conflict, so removing them loses nothing.
    #
    # NEVER add the bare "payload" here: Payload Manager names EVERY loaded payload payload.elf,
    # our own ELF included, and sweeping by that name has already killed this app once.
    # The Arsenal/conflict process lists lived here. They named elf-arsenal.elf, dpiv2.elf,
    # dpi.elf, tile-autoinst and garlic-savemgr so the app could stop them fighting over the
    # install port. This app has no install port to fight over and does not ship either payload,
    # so deciding which of the user's processes to kill is no longer its business.

    # ---- cheat / patch library -----------------------------------------------------------------
    # The library ships inside the exe (7022 files, 30.7 MB) and is pushed to the console on
    # demand. Deliberately not embedded in the ELF: a payload is mapped into console RAM whole, so
    # carrying 30 MB of idle data every boot costs memory for nothing. Pushing is INCREMENTAL - we
    # list what the console already has and send only the difference - so a second run is a no-op
    # and an interrupted first run simply resumes.
    def cheat_library_status(self):
        """{sub: {"local": n, "console": n|None, "missing": n}} plus totals. Never raises."""
        out, tl, tc, tm, reachable = {}, 0, 0, 0, bool(self.ip)
        try:
            for sub in CHEAT_SUBDIRS:
                ldir = os.path.join(CHEATS_DIR, sub)
                local = set(os.listdir(ldir)) if os.path.isdir(ldir) else set()
                remote = None
                if reachable:
                    rows = self.fs_list("%s/%s" % (CONSOLE_CHEAT_ROOT, sub), timeout=15)
                    # None = the directory does not exist yet, which is "nothing there", not
                    # "console unreachable" - fs_list already tried both transports.
                    remote = {e.get("name") for e in rows} if rows else set()
                miss = len(local - remote) if remote is not None else None
                out[sub] = {"local": len(local), "console": (len(remote) if remote is not None else None),
                            "missing": miss}
                tl += len(local)
                if remote is not None:
                    tc += len(remote); tm += miss
        except Exception:
            reachable = False
        return {"ok": True, "reachable": reachable, "dirs": out, "local_total": tl,
                "console_total": (tc if reachable else None), "missing_total": (tm if reachable else None)}

    def sync_cheat_library(self, log=None, budget=None):
        """Push every cheat file the console does not already have. Returns a summary dict."""
        def say(m):
            if log:
                log(m)
        if not os.path.isdir(CHEATS_DIR):
            # The 48 MB cheat library is embedded in the ELF, not in the exe (it would roughly
            # triple the download for a copy the console already carries). Say that, instead of
            # printing an internal _MEI path that reads like a broken install.
            frozen = getattr(sys, "frozen", False)
            return {"ok": False, "error":
                    ("This build does not carry the cheat library - the PS5 app has all 7022 "
                     "files embedded and writes them to %s itself, so there is nothing to push."
                     % CONSOLE_CHEAT_ROOT) if frozen else
                    ("no bundled cheat library at %s" % CHEATS_DIR)}
        if not self.ip:
            return {"ok": False, "error": "no console configured"}
        sent = failed = skipped = 0
        started = time.time()
        try:
            for d in ("/data/pkg-mutant-shop", CONSOLE_CHEAT_ROOT):
                self.fs_mkdir(d)
            for sub in CHEAT_SUBDIRS:
                ldir = os.path.join(CHEATS_DIR, sub)
                if not os.path.isdir(ldir):
                    continue
                rdir = "%s/%s" % (CONSOLE_CHEAT_ROOT, sub)
                self.fs_mkdir(rdir)
                rows = self.fs_list(rdir, timeout=20)
                have = {e.get("name") for e in rows} if rows else set()
                todo = sorted(set(os.listdir(ldir)) - have)
                skipped += len(have)
                if todo:
                    say("cheats: %s — sending %d file(s)" % (sub, len(todo)))
                for i, name in enumerate(todo):
                    if budget and time.time() - started > budget:
                        say("cheats: time budget reached, will continue next run")
                        return {"ok": True, "sent": sent, "failed": failed, "skipped": skipped,
                                "partial": True, "seconds": round(time.time() - started, 1)}
                    try:
                        lp = os.path.join(ldir, name)
                        with open(lp, "rb") as fh:
                            ok = self.fs_write("%s/%s" % (rdir, name), fh,
                                               timeout=60, size=os.path.getsize(lp))
                        if ok:
                            sent += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
                    if sent and sent % 400 == 0:
                        say("cheats: %d sent…" % sent)
        except Exception as e:
            return {"ok": False, "error": "cheat sync failed (%r)" % (e,),
                    "sent": sent, "failed": failed, "skipped": skipped}
        return {"ok": True, "sent": sent, "failed": failed, "skipped": skipped, "partial": False,
                "seconds": round(time.time() - started, 1)}

    def invalidate_apps(self):
        """Drop the installed-titles cache. Call this the moment something finishes installing:
        the cache is 30s, which is why a freshly applied patch kept reading as still pending."""
        self._apps = None
        self._apps_ts = 0.0

    def console_apps(self, force=False):
        """
        Read installed titles from the console's app.db (tbl_contentinfo, verified PS5 12.70 schema):
        title_id, name, size, install location. Cached 30s. Returns list of dicts, or None if unreachable.
        """
        if not self.ip:
            return None
        if not force and self._apps is not None:
            if time.time() - self._apps_ts < 30:
                return self._apps                       # warm cache → instant
            # stale: serve the stale list NOW, refresh once in the background (never block the request)
            if not self._apps_refreshing:
                self._apps_refreshing = True
                threading.Thread(target=self._bg_refresh_apps, daemon=True).start()
            return self._apps
        # A console that just failed is not going to answer three lines later. Without this memo
        # every call in a single request paid the full FTP timeout again, which is what made the
        # library take ~25s whenever the PS5 was off. `force` still ignores it.
        if not force and time.time() - getattr(self, "_apps_fail_ts", 0) < 10:
            return None
        db_path = self.cfg.get("console", {}).get("app_db_path", "/system_data/priv/mms/app.db")
        # Through _pull_db: our own on-console file API first, the third-party FTP only as a
        # fallback. Unique temp name per pull - the confirm loop, the library scan and the UI can
        # all be here at once, and one thread truncating a file another is mid-read on produces
        # "database disk image is malformed", which the caller reads as "not installed yet".
        tmp = self._pull_db(db_path)
        if not tmp:
            self._apps_fail_ts = time.time()
            return None
        apps = []
        try:
            con = sqlite3.connect(tmp)
            cur = con.cursor()
            try:
                cur.execute("SELECT titleId,contentId,titleName,size,contentLocation,installStatus,"
                            "icon0Info,appFormatType,AppInfoJson FROM tbl_contentinfo")
                rows = cur.fetchall()
            except sqlite3.Error:
                rows = []
            for tid, cid, name, size, loc, st, icon, aft, aij in rows:
                if not tid:
                    continue
                # The version ACTUALLY installed (patches bump it). Without it we cannot tell
                # an applied patch from an available one, and the cheat engine cannot tell
                # whether a cheat file matches the build.
                #
                # PS4 and PS5 do NOT use the same field, which is why every PPSA title used to
                # report no version at all:
                #   PS4 (CUSA) -> "APP_VER"          e.g. 01.04
                #   PS5 (PPSA) -> "CONTENT_VERSION"  e.g. 01.011.000
                # Verified against this console: CONTENT_VERSION matches the cheat filenames
                # exactly (Doom 01.011.000, Hogwarts 01.000.010, Ratchet 01.005.003).
                app_ver = ""
                try:
                    info = json.loads(aij or "{}") or {}
                    app_ver = info.get("APP_VER") or info.get("CONTENT_VERSION") or ""
                except Exception:
                    m = (re.search(r'"APP_VER"\s*:\s*"([^"]+)"', aij or "")
                         or re.search(r'"CONTENT_VERSION"\s*:\s*"([^"]+)"', aij or ""))
                    app_ver = m.group(1) if m else ""
                loc = str(loc)
                plat = "PS5" if tid.startswith("PPSA") else ("PS4" if tid.startswith("CUSA") else "SYS")
                apps.append({"title_id": tid, "content_id": cid or "", "name": name or tid,
                             "size": int(size or 0),   # REAL installed size (app.db size == AppInfoJson #_size)
                             "region": pkg_meta.region_from_content_id(cid or ""),
                             "location": loc, "drive": self.LOC_LABEL.get(loc, "Storage " + loc),
                             "platform": plat, "install_status": st, "app_format_type": aft,
                             "app_ver": app_ver,
                             "icon_path": (icon or "").split("?")[0] or None})
            con.close()
        except sqlite3.Error:
            return None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        # PS4 (CUSA) games the PS5 app.db drops on this jailbreak are still really installed - but
        # only the ones whose game payload is still on the drive. See console_ps4_installed().
        have = {a["title_id"] for a in apps}
        for tid, sz in self.console_ps4_installed(force).items():
            if tid not in have:
                # Say which drive it is really on. Hardcoding internal put every PS4 game on the
                # wrong storage tile the moment the user installed one to the M.2.
                _root = getattr(self, "_ps4loc", {}).get(tid, "")
                loc4 = "2" if _root.startswith("/mnt/") else "0"
                apps.append({"title_id": tid, "name": tid, "size": sz, "location": loc4,
                             "drive": self.LOC_LABEL.get(loc4, "Storage " + loc4),
                             "platform": "PS4", "install_status": 0,
                             "icon_path": "/user/appmeta/%s/icon0.png" % tid})
        # REAL size/format/path from ShadowMount backup files (app.db size is a bogus metadata value)
        backups = self.console_backups() or {}
        for a in apps:
            b = backups.get(a["title_id"])
            if b:
                a["size"], a["backup_path"], a["format"], a["source"] = b["size"], b["path"], b["format"], "backup"
            else:
                a["backup_path"], a["format"], a["source"] = None, None, "installed"
        self._apps, self._apps_ts = apps, time.time()
        self._apps_fail_ts = 0.0            # it answered: stop holding the failure against it
        return apps

    def _bg_refresh_apps(self):
        """Background refresh for stale-while-revalidate. On FTP failure console_apps() returns None
        without touching self._apps, so the stale cache survives until the next successful refresh."""
        try:
            self.console_apps(force=True)
        finally:
            self._apps_refreshing = False

    def console_ps4_installed(self, force=False):
        """PS4 (CUSA) titles that are REALLY installed, as {title_id: bytes}. Cached 60s.

        The PS5's app.db omits PS4 games on this firmware, so it cannot answer this and something
        on the filesystem has to. Two candidates, and only one of them is true:

          /user/appmeta/<CUSA>/icon0.png   ARTWORK. Survives an uninstall and survives "reset
                                           database". Measured here: 55 of these, for 2 installed
                                           games. This is what the check used to use, and it is why
                                           53 titles the console does not have were shown as
                                           installed with Install offering their update.

          /user/app/<CUSA>/app.pkg         THE GAME. Measured here: 31 folders, 29 of them EMPTY,
                                           and exactly 2 with a non-empty app.pkg - CUSA02365 and
                                           CUSA58072, which is precisely what the console itself
                                           reports. The folder alone is not enough; the payload is.

        Its size is worth returning: app.db has no row for these titles, so this is the only real
        figure anyone has for them."""
        if not self.ip:
            return {}
        if not force and self._ps4meta is not None and time.time() - self._ps4meta_ts < 60:
            return self._ps4meta
        # EVERY root a PS4 game can live under - the same four that installed_app_pkg() and
        # ps5-app/onconsole/server.c already scan. Looking only at /user/app made every game on
        # extended storage read as not installed, which matters a great deal now: with the
        # console's Installation Location set to the M.2, that is where new games go.
        roots = ["/user/app", "/mnt/ext0/user/app", "/mnt/ext1/user/app", "/mnt/ext2/user/app"]
        found, locs = {}, {}
        # Through fs_list, so this works without a third-party FTP - and without parsing LIST
        # output, which is a formatted human string every server words differently.
        try:
            for base in roots:
                top = self.fs_list(base, timeout=20)
                if top is None:
                    if base == "/user/app":
                        raise OSError("cannot list %s" % base)
                    continue        # an absent extended root is just a drive that is not plugged in
                cands = [e["name"].upper() for e in top
                         if e.get("dir") and re.fullmatch(r"CUSA\d{5}", e.get("name") or "", re.I)]
                for tid in cands:
                    sub = self.fs_list("%s/%s" % (base, tid), timeout=15)
                    if sub is None:
                        # A FAILED READ IS NOT AN EMPTY FOLDER. Treating them alike dropped the
                        # title from console_apps(), and /api/installed writes that loss straight
                        # into installed.json - where nothing ever puts it back.
                        raise OSError("cannot list %s/%s" % (base, tid))
                    if not sub:
                        continue                   # a genuinely empty folder is a leftover
                    for e in sub:
                        if (not e.get("dir")) and (e.get("name") or "").lower() == "app.pkg":
                            sz = int(e.get("size") or 0)
                            if sz > 0:
                                found[tid] = sz
                                locs[tid] = base
                            break
        except Exception as exc:
            print("[ps4] install scan failed: %r" % (exc,))
            return self._ps4meta or {}
        self._ps4meta, self._ps4meta_ts = found, time.time()
        self._ps4loc = locs
        return found

    def console_backups(self, force=False):
        """Scan the ShadowMount backup folders for real game files (.ffpfsc etc). Map title_id -> real size."""
        if not self.ip:
            return None
        if not force and self._backups is not None and time.time() - self._backups_ts < 30:
            return self._backups
        sm = self.cfg.get("shadowmount", {})
        # Scan EVERY real backup location so .ffpfsc/.ffpfs/etc. are found regardless of config: the
        # configured path(s) + internal + ext0/ext1 + all USB slots. (Backups here live in /mnt/ext1/homebrew.)
        paths = list(sm.get("scan_paths") or [])
        for p in ([sm.get("scan_path"), "/data/homebrew", "/mnt/ext0/homebrew", "/mnt/ext1/homebrew"]
                  + ["/mnt/usb%d/homebrew" % i for i in range(8)]):
            if p and p not in paths:
                paths.append(p)
        result = {}
        any_dir = False
        for base in paths:
            rows = self.fs_list(base, timeout=15)
            if rows is None:
                continue                        # this folder does not exist on this console
            any_dir = True
            for e in rows:
                if e.get("dir"):
                    continue
                try:
                    size = int(e.get("size") or 0)
                except (TypeError, ValueError):
                    continue
                name = e.get("name") or ""
                # Same list the library scanner uses. These two disagreed, so a backup in a
                # format one accepted was invisible to the other.
                if not name.lower().endswith(MOUNT_EXTS):
                    continue
                m = re.search(r"(CUSA\d{5}|PPSA\d{5})", name, re.I)
                if m:
                    result[m.group(1).upper()] = {"size": size, "path": base + "/" + name,
                                                  "format": name.rsplit(".", 1)[-1].lower()}
        if not any_dir:
            return None                     # console unreachable, as opposed to "no backups"
        self._backups, self._backups_ts = result, time.time()
        return result

    def installed_titles(self, force=False):
        apps = self.console_apps(force=force)
        return None if apps is None else sorted(a["title_id"] for a in apps)

    def console_icon(self, title_id):
        """Fetch a game's real icon0.png from the console (cached in ICON_DIR). Returns local path or None."""
        dest = os.path.join(ICON_DIR, title_id + ".png")
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        apps = self.console_apps()
        if not apps:
            return None
        path = next((a.get("icon_path") for a in apps if a["title_id"] == title_id), None)
        if not path:
            return None
        try:
            os.makedirs(ICON_DIR, exist_ok=True)
            data = self.fs_read(path, timeout=20)
            if not data:
                return None
            with open(dest, "wb") as f:
                f.write(data)
            return dest if os.path.getsize(dest) > 0 else None
        except Exception:
            try:
                os.remove(dest)
            except OSError:
                pass
            return None

    def console_cheats(self, title_id):
        """Read mods/patches for a title from the mod library over FTP, parsing REAL mod names. dict|None.
        Reads data files directly (not the Cheat Runner service) — path is configurable (cheats.root)."""
        if not self.ip or not title_id:
            return None
        root = self.cfg.get("cheats", {}).get("root", "/data/cheatrunner")
        tu = title_id.upper()
        def files_in(path):
            # fs_list, not NLST. NLST is the one FTP verb this console answers 502 to on several
            # of these paths, and its output is a bare name list with no way to tell a file from a
            # directory.
            rows = self.fs_list(path, timeout=12)
            if not rows:
                return []
            return sorted({e["name"] for e in rows
                           if not e.get("dir") and (e.get("name") or "").upper().startswith(tu)})

        cheats, patches = [], []
        for base in files_in(root + "/cheats/json"):     # JSON = richest: parse real mod names
            entry = {"file": base, "format": "json", "version": None, "process": None, "mods": [], "credits": []}
            try:
                d = json.loads(self.fs_text("%s/cheats/json/%s" % (root, base), timeout=12) or "{}")
                entry["version"], entry["process"] = d.get("version"), d.get("process")
                entry["credits"] = d.get("credits") or []
                for m in d.get("mods") or []:
                    entry["mods"].append({"name": m.get("name", ""), "type": m.get("type", ""),
                                          "patches": len(m.get("memory") or [])})
            except Exception:
                pass
            cheats.append(entry)
        for sub in ("shn", "mc4"):                        # other formats: listed (parsed later)
            for base in files_in("%s/cheats/%s" % (root, sub)):
                cheats.append({"file": base, "format": sub, "version": None, "process": None, "mods": [], "credits": []})
        for sub, tag in (("xml_orbis", "PS4"), ("xml_prospero", "PS5"), ("xml", "xml")):
            for base in files_in("%s/patches/%s" % (root, sub)):
                patches.append({"file": base, "platform": tag})
        seen = set()
        patches = [p for p in patches if not (p["file"] in seen or seen.add(p["file"]))]
        return {"cheats": cheats, "patches": patches}


class _ProgressReader(object):
    """A read-only file wrapper that reports bytes as they are handed to the socket.

    urllib streams a file-like body in 8 KB blocks when Content-Length is set, so wrapping the file
    is how the mount lane keeps its live percentage without buffering a multi-GB game in memory.
    `on_read` may raise (that is how cancel works) and the exception propagates out of the request.
    """

    def __init__(self, fh, on_read=None):
        self._fh = fh
        self._on_read = on_read
        self.total = 0

    def read(self, size=-1):
        chunk = self._fh.read(size)
        if chunk:
            self.total += len(chunk)
            if self._on_read:
                self._on_read(len(chunk), self.total)
        return chunk

    def __iter__(self):
        return self

    def __next__(self):
        chunk = self.read(65536)
        if not chunk:
            raise StopIteration
        return chunk

    next = __next__


class Fleet:
    def __init__(self, cfg):
        self.cfg = cfg
        self.reload()

    def reload(self):
        self.consoles = consoles_from_cfg(self.cfg)
        self.bridges = {c["id"]: Ps5Bridge(c, self.cfg) for c in self.consoles}

    def bridge(self, cid):
        if cid in self.bridges:
            return self.bridges[cid]
        return next(iter(self.bridges.values()), None)

    def ids(self):
        return [c["id"] for c in self.consoles]

    def status(self):
        # probe all consoles in parallel so /api/consoles isn't N × ping-timeout
        results = {}

        def probe(c):
            results[c["id"]] = dict(c, online=self.bridges[c["id"]].engine_available())

        threads = [threading.Thread(target=probe, args=(c,)) for c in self.consoles]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2)
        return [results.get(c["id"], dict(c, online=False)) for c in self.consoles]


# --------------------------------------------------------------------------- #
# install queue - parallel, per-console serialized, unified real progress      #
# --------------------------------------------------------------------------- #
class Queue:
    def __init__(self, fleet, transfers, cfg):
        self.fleet = fleet
        self.transfers = transfers
        self.cfg = cfg
        self.max_parallel = max(1, cfg["queue"].get("max_parallel", 2))
        self.allow_ppc = bool(cfg["queue"].get("allow_parallel_per_console", False))
        self.tasks = {}
        self.order = []
        self.active = set()          # consoles with an install in flight
        self.lock = threading.Lock()
        self._seq = 0
        for _ in range(self.max_parallel):
            threading.Thread(target=self._worker, daemon=True).start()

    def add(self, task):
        with self.lock:
            self._seq += 1
            tid = "job-%d" % self._seq
            held = bool(task.get("hold"))                      # [B8] add-to-queue vs install-now
            task.update({"id": tid, "state": "held" if held else "queued", "pct": 0,
                         "msg": "Held — press Start" if held else "Queued",
                         "cancel": False, "simulated": False, "ts": now_ms()})
            self.tasks[tid] = task
            self.order.append(tid)
        return tid

    def start_held(self):
        """Release all held tasks into the run queue, SMALLEST game first (smart order); the worker then
        installs them one at a time per console. [B8]"""
        with self.lock:
            held = [self.tasks[i] for i in self.order if self.tasks[i]["state"] == "held"]
            held.sort(key=lambda t: t.get("total") or 0)
            for t in held:
                t["state"], t["msg"] = "queued", "Queued"
            ids = set(t["id"] for t in held)
            self.order = [i for i in self.order if i not in ids] + [t["id"] for t in held]
            return len(held)

    def snapshot(self):
        with self.lock:
            return [self.tasks[i] for i in self.order]

    # Genuinely finished states. A task in one of these has nothing left to stop.
    TERMINAL = ("playable", "error", "canceled")

    def cancel(self, tid):
        """Stop a task. Returns True when something was actually stopped.

        Two things were wrong here. "submitted" was treated as terminal, so pressing x on the one
        state that most needs it - handed to the console, no progress, spins forever - returned
        False and the UI said nothing at all. And a task that had not been claimed yet only got a
        flag, so its row went on reading "Queued" until a worker eventually picked it up; now it is
        marked cancelled straight away. A task that IS running still gets the flag, because its
        lane has to unwind its own transfer.
        """
        with self.lock:
            t = self.tasks.get(tid)
            if not t or t["state"] in self.TERMINAL:
                return False
            t["cancel"] = True
            was = t["state"]
            if was in ("queued", "held", "submitted"):
                t["state"] = "canceled"
                t["msg"] = ("Canceled - the console may still finish what it already accepted"
                            if was == "submitted" else "Canceled")
                t["ts"] = now_ms()
            return True

    def retry(self, tid):
        """Re-queue a failed / canceled / stalled task — e.g. after the user reloads a wedged DPI payload,
        press retry on Dark Souls and it hands off again (small-first ordering keeps it after any running). """
        with self.lock:
            t = self.tasks.get(tid)
            if t and t["state"] in ("error", "canceled", "submitted"):
                t.update(state="queued", msg="Queued (retry)", pct=0, cancel=False, fail_reason=None)
                if tid in self.order:
                    self.order.remove(tid)
                    self.order.append(tid)
                return True
        return False

    @staticmethod
    def _pause_pending(self, console, msg):
        """After a DPI wedge, HOLD every still-pending install for this console so they don't cascade-fail.
        The daemon can't self-recover — the user reloads the payload then presses ▶ Start to release them
        all (small-first). [wedge-pause]"""
        with self.lock:
            for i in self.order:
                t = self.tasks.get(i)
                if t and t.get("lane") == "install" and t.get("console") == console and t["state"] == "queued":
                    t.update(state="held", msg=msg, pct=0)

    def clear_done(self):
        """Drop only what is genuinely FINISHED - which is what the button says.

        It used to keep a whitelist (RUNNING + "queued") and delete everything else, so it also
        deleted every HELD job. Build a queue with "+ Queue" (all held), press Clear finished to
        tidy one failed row, and the whole queue is gone server-side. Worse after a wedge, where
        _pause_pending deliberately holds every pending install: the one button offered to tidy up
        is the one that throws the batch away. "reloading" and "submitted" were in the same
        blast radius, and a reloading job is actively being worked on.
        """
        with self.lock:
            keep = [i for i in self.order if self.tasks[i]["state"] not in self.TERMINAL]
            self.tasks = {i: self.tasks[i] for i in keep}
            self.order = keep

    def dismiss(self, tid):
        """Remove a single FINISHED task from the list. Returns False while it is still running.

        clear_done() drops every finished task at once, which is the wrong granularity for "I want
        this one row gone". Refusing while a task is live is deliberate: the worker still holds a
        reference and goes on calling _set() on it, and a task that vanishes underneath its own
        worker is how a queue starts reporting states for jobs nobody can see.
        """
        with self.lock:
            t = self.tasks.get(tid)
            if not t:
                return True                       # already gone; the caller got what it wanted
            if t["state"] not in self.TERMINAL:
                return False
            self.tasks.pop(tid, None)
            self.order = [i for i in self.order if i != tid]
            return True

    def _finished_installing(self, t):
        """Anything that just landed on the console invalidates what we think is installed."""
        try:
            b = self.fleet.bridge(t.get("console"))
            if b:
                b.invalidate_apps()
            lib = getattr(self, "library", None)
            if lib is not None:
                lib.gen += 1          # nudges the UI to reload without waiting for a poll
        except Exception:
            pass
        # A backup carries no artwork of its own — the console only creates
        # /user/appmeta/<id>/icon0.png once the title is actually there. Now that it is, grab the
        # icon straight away and rescan, so the card gets its real art immediately instead of
        # keeping its initials until something else happens to trigger a fetch. Once cached here it
        # is advertised to every other device as icon_url, exactly as for any other title.
        def _art():
            try:
                tid = t.get("title_id")
                b2 = self.fleet.bridge(t.get("console"))
                if not (tid and b2):
                    return
                for _ in range(10):                 # ShadowMount needs a moment to register it
                    if b2.console_icon(tid):
                        lib2 = getattr(self, "library", None)
                        if lib2 is not None:
                            lib2.scan()             # has_icon is read from disk at scan time
                        return
                    time.sleep(6)
            except Exception:
                pass
        threading.Thread(target=_art, daemon=True).start()

    def _set(self, t, **kw):
        with self.lock:
            t.update(kw)

    def _claim(self):
        with self.lock:
            if sum(1 for t in self.tasks.values() if t["state"] in RUNNING) >= self.max_parallel:
                return None
            for i in self.order:
                t = self.tasks[i]
                if t["state"] != "queued":
                    continue
                cid = t["console"]
                if (not self.allow_ppc) and cid in self.active:
                    continue
                t["state"] = "claimed"
                self.active.add(cid)
                return t
        return None

    def _release(self, t):
        with self.lock:
            self.active.discard(t["console"])

    def _worker(self):
        while True:
            job = self._claim()
            if not job:
                time.sleep(0.35)
                continue
            try:
                self._run(job)
            except Exception as e:
                # The repr is genuinely the useful thing here, so it goes to pms.log; the queue row
                # gets words. Showing a Python exception to someone installing a game tells them
                # nothing they can act on and looks like the app broke rather than the transfer.
                print("[queue] worker crashed on %r: %r" % (job.get("name"), e))
                self._set(job, state="error", detail=repr(e)[:300],
                          msg="This one stopped unexpectedly — press retry to try it again")
            finally:
                self._release(job)

    def _progress(self, t):
        if t.get("engine") == "pms":
            # Our engine reports its own byte progress — the console is doing the pulling,
            # so the companion's local transfer counter has nothing to say here.
            b = self.fleet.bridge(t.get("console"))
            j = b.engine_job() if b else None
            if not j:
                return None
            # Only trust the engine while it is reporting OUR job. It keeps the last job's
            # result until a new one starts, and reading that as ours marked downloads
            # "Ready to play" seconds after queueing them.
            mine = t.get("engine_job_id")
            if mine is not None and j.get("job_id") != mine:
                return {"max": 0, "total": 0, "state": "starting"}
            return {"max": j.get("done") or 0, "total": j.get("total") or 0,
                    "state": j.get("state"), "msg": j.get("msg")}
        key = t.get("key")
        if t.get("local_progress") and key is not None:
            # Our own console's bucket first; the shared aggregate only as a fallback for a job
            # whose console has no resolvable address (and for single-console setups before the
            # first byte, where the per-IP bucket does not exist yet).
            b = self.fleet.bridge(t.get("console"))
            if b is not None and b.ip:
                mine = self.transfers.get("%s|%s" % (key, b.ip))
                if mine is not None:
                    return mine
            return self.transfers.get(key)
        if t.get("progress_url"):
            return http_get_json(t["progress_url"])
        return None

    def _run(self, t):
        # Cancel is a flag, and until now nothing looked at it until well after the handoff. A job
        # cancelled while it sat in the queue was still claimed, still preflighted and still POSTed
        # to the install host - the console really installed it - and the row then said "Canceled".
        # One check, before any lane can do anything.
        if t.get("cancel"):
            self._set(t, state="canceled", pct=0, msg="Canceled before it started")
            return
        if t.get("lane") == "mount":
            return self._run_mount(t)
        if t.get("lane") == "console-local":
            return self._run_console_local(t)
        if t.get("lane") == "pc-copy":
            return self._run_pc_copy(t)
        if t.get("lane") == "peer-mount":
            return self._run_peer_mount(t)
        if t.get("lane") == "peer-delegate":
            return self._run_peer_delegate(t)
        bridge = self.fleet.bridge(t["console"])
        if bridge is None:
            self._set(t, state="error", msg="No console configured")
            return
        # Our engine lives in OUR on-console ELF, so "is the console reachable" is the only
        # question that matters. We deliberately do NOT probe :12800 or try to revive a
        # third-party daemon here — doing that is what silently launched Elf Arsenal (and
        # garlic / nanodns / ftpsrv / dpiv2 with it) the moment an install was queued.
        # ONE LANE. Our engine needs exactly one thing to be true - PKG MUTANT SHOP answering on
        # the console - and there is no daemon to probe, revive, reconfigure or heal. Everything
        # that used to happen here existed to keep somebody else's payload alive.
        online = bridge.engine_available()
        key, total = t.get("key"), t.get("total") or 0

        if online and t.get("url"):
            # [B7] integrity: verify the local PKG is structurally complete before the handoff. Default
            # "warn" — surfaces the verdict but NEVER blocks the proven path; set integrity.on_local_corrupt
            # ="block" to enforce (blocks only a *confident* truncation / bad-magic / missing-content-id).
            integ = self.cfg.get("integrity", {})
            lib = getattr(self, "library", None)
            local = lib.file_registry.get(key) if (lib and key) else None
            if integ.get("enabled", True) and integ.get("verify_before_install", True) \
                    and local and local.lower().endswith(".pkg") and os.path.isfile(local):
                self._set(t, state="verifying", msg="Verifying integrity…")
                verdict = pkg_meta.pkg_completeness(local)
                t["integrity"] = verdict
                if verdict.get("confident") and not verdict.get("complete"):
                    if integ.get("on_local_corrupt", "warn") == "block":
                        self._set(t, state="error", fail_reason="local_corrupt",
                                  msg="Integrity: %s — fix the file, then retry" % verdict.get("reason"))
                        return
                    self._set(t, msg="⚠ integrity: %s (installing anyway)" % verdict.get("reason"))
            # [B9] SELF-HEALING HANDOFF. The DPI daemon wedges after a heavy install and never
            # recovers on its own, which is what used to strand every following install. So before
            # handing off we check the install lane itself (~15ms when healthy) and, if it is
            # wedged, reload the daemon automatically — kill the process that owns the port, then
            # relaunch the payload — with no user interaction. A healthy console is never touched.
            # NOTHING TO BRING UP. The three blocks that stood here - a proactive daemon reload,
            # a "stop the install host crashing its own job queue" preflight, and a wedge-heal -
            # all existed to nurse a third-party payload through an install. Our installer is
            # spawned per install and has already exited by the time anyone could reload it.

            if t.get("engine") == "pms":
                # The engine handles one package at a time; wait rather than get refused.
                for _ in range(600):
                    j = bridge.engine_job() or {}
                    if j.get("state") not in ("downloading", "installing"):
                        break
                    self._set(t, state="queued", msg="Waiting for the install engine")
                    time.sleep(2)
            self._set(t, state="submitting", msg="Handing off to %s" % bridge.name)
            if t.get("local_progress") and key is not None:
                # Reset OUR console's bucket only. Resetting the shared one is what made a second
                # console's hand-off zero the first console's in-flight counter.
                _b = self.fleet.bridge(t.get("console"))
                _bk = ("%s|%s" % (key, _b.ip)) if (_b is not None and _b.ip) else key
                self.transfers[_bk] = {"max": 0, "total": total or 1}
            # Remember whether the console already had this title. A registered title makes the
            # installer skip the download entirely and keep the old files, so afterwards its mere
            # presence in app.db must not be read as "this install worked".
            try:
                t["_pre_registered"] = bool(t.get("title_id")) and \
                    t.get("title_id") in (bridge.installed_titles(force=True) or [])
            except Exception:
                t["_pre_registered"] = False
            # An ADD-ON cannot be proven by app.pkg: the base game's app.pkg is already on disk and
            # is always far larger than the add-on's package, so a size check passes instantly and
            # means nothing. What proves it is a NEW bgft row of its own. Snapshot the newest row
            # before we hand over, so an old row from the base install cannot be mistaken for ours.
            # Distinguish "this title has no bgft row" from "bgft.db could not be read". Both
            # were None, and an unreadable snapshot turns the add-on freshness test (row != before)
            # into a test that any pre-existing completed row passes instantly. Retry first - the
            # usual cause is a momentary FTP hiccup - and record whether we ever got an answer.
            t["_bgft_before"], t["_bgft_before_ok"] = None, False
            if t.get("title_id"):
                for _try in range(3):
                    try:
                        t["_bgft_before"] = bridge.install_job_row(t.get("title_id"))
                        t["_bgft_before_ok"] = True
                        break
                    except Exception:
                        time.sleep(1.0)

                # IS THE CONSOLE ALREADY BUSY WITH THIS TITLE? This is the question the duplicate
                # guard should always have asked. Handing a package over while its own BGFT job is
                # still running is what produced several rows for one content id, all stuck at zero
                # bytes - and it is the shape of the submission that preceded the 2026-08-25 crash.
                #
                # Reading it here costs nothing: the row above was fetched anyway. Statuses:
                # 1000 parked/queued, 1009 downloading or installing; 1036/1026 are finished and
                # 1021/1022 failed or aborted, none of which is a reason to refuse.
                _row = t["_bgft_before"]
                if _row and not t.get("force") and int(_row[0] or 0) in (1000, 1009):
                    _pct = (100.0 * (_row[1] or 0) / _row[2]) if (_row[2] or 0) else 0.0
                    if bgft_row_is_dead(_row):
                        # QUEUED AND NEVER STARTED - see bgft_row_is_dead(). Not something to wait
                        # for, and the old wording said the opposite in every clause.
                        _msg = ("The PS5 has a stalled download for this game that never started, "
                                "and it is blocking new ones. Cancel it on the PS5 (Downloads, "
                                "select it, Cancel), then install this again.")
                        print("[install] %s has a DEAD bgft row (status %s, 0 bytes) - not a live job"
                              % (t.get("title_id"), _row[0]))
                        self._set(t, state="error", pct=0, fail_reason="console_stalled_download",
                                  msg=_msg)
                        return
                    _msg = ("The console is already installing this - %.0f%% done. It will finish "
                            "on its own; there is nothing to send again." % _pct)
                    print("[install] %s already has a live bgft job (status %s, %.0f%%) - holding"
                          % (t.get("title_id"), _row[0], _pct))
                    self._set(t, state="held", pct=int(_pct), fail_reason="console_busy", msg=_msg)
                    return
                if not t["_bgft_before_ok"]:
                    print("[install] could not read bgft.db before handoff - this install will "
                          "not be auto-confirmed from a bgft row")
            else:
                t["_bgft_before_ok"] = True       # nothing to compare against, by design
            # Rebuild our own LAN URL now, not when the job was queued. A job that waited in
            # the queue overnight was still holding the address this PC had at the time.
            if t.get("relan") and t.get("key"):
                fresh = "http://%s:%d/library/%s" % (companion_ip_for(bridge.ip),
                                                     self.cfg["companion"]["port"], t["key"])
                if fresh != t.get("url"):
                    print("[install] our address changed since this was queued: %s -> %s"
                          % (t.get("url"), fresh))
                    t["url"] = fresh
            t["_submitted_at"] = time.time()
            ok, res = bridge.install(t["url"], t.get("name") or "", force=bool(t.get("force")))
            if ok and isinstance(res, dict) and res.get("job_id") is not None:
                t["engine_job_id"] = res.get("job_id")
            if not ok:
                # Report the DPI host's *actual* failure mode instead of guessing. `res` carries either a
                # transport error (unreachable / timed out) or the daemon's own rejection payload. A bare
                # ping only proves the port is bound — a wedged daemon still accepts TCP but never answers,
                # so we look at *how* install() failed, not just whether the port is open.
                detail = ""
                if isinstance(res, dict):
                    detail = str(res.get("error") or res.get("res") or res.get("hint") or res).strip()
                elif res:
                    detail = str(res).strip()
                low = detail.lower()
                kindf = res.get("kind") if isinstance(res, dict) else None

                # THE CONSOLE REFUSED BECAUSE IT IS BUSY. It is reachable and healthy; something
                # it accepted earlier is still in flight. Hold this job and let the queue try again
                # later - never reload the ELF, because reloading resets the very latch that is
                # protecting the console (that is literally what happened on 2026-08-25).
                if isinstance(res, dict) and res.get("busy"):
                    self._set(t, state="held", pct=0, fail_reason="console_busy", msg=detail)
                    return

                # NO VERDICT. The installer was started and never reported back, so we do not know
                # whether the console is mid-install. Submitting again is how one package becomes
                # three BGFT jobs for one content id, all stuck at zero bytes, blocking each other.
                if isinstance(res, dict) and res.get("no_verdict"):
                    self._set(t, state="held", pct=0, fail_reason="dpi_no_verdict", detail=detail,
                              msg=detail)
                    self._pause_pending(t.get("console"), detail)
                    return

                # No host answered. Nothing was sent anywhere, so this is not a wedge and there is
                # nothing to reload — hold the queue and say what to do. Previously UNKNOWN fell
                # through to Arsenal's protocol and produced a connection reset, which the
                # substring classifier below then read as "wedged" and re-POSTed.
                if kindf == "host_unknown":
                    self._set(t, state="held", pct=0, fail_reason="dpi_host_unknown", msg="⏸ " + detail)
                    self._pause_pending(t.get("console"), detail)
                    return

                # etaHEN VALIDATES BEFORE QUEUEING, and says so: queued=False means the console
                # created no bgft job at all. That is the one case where a clean re-POST is provably
                # safe — the duplicate-job hazard behind I4 only exists for a daemon that queues
                # first and rejects afterwards. One retry, then report the console's real error.
                if isinstance(res, dict) and res.get("queued") is False and not t.get("_requeued"):
                    t["_requeued"] = True
                    print("[install] host rejected before queueing (%s) — retrying once" % detail)
                    self._set(t, state="submitting", pct=0,
                              msg="%s refused it (%s) — trying once more" % (bridge.name, detail[:60]))
                    time.sleep(2)
                    return self._run(t)

                timed_out = any(s in low for s in ("timed out", "timeout", "aborted", "reset", "10053",
                                                   "10054", "winerror", "unreachable", "no response", "refused"))
                seg = os.path.basename(t.get("url", ""))
                risky = any(ch in seg for ch in ("%20", "%5B", "%5D", "%28", "%29", " ", "[", "]"))
                # "Is it there" means one thing now: is OUR shop answering on the console.
                if not bridge.engine_available():
                    print("[install] fail (down) name=%r res=%r" % (t.get("name"), res))
                    self._set(t, state="error", fail_reason="dpi_down", detail=detail or None,
                              msg="The console stopped answering. Load PKG MUTANT SHOP on the PS5 "
                                  "from Payload Manager, then press Start queue.")
                    return
                # The console is there and said no. `detail` already carries its own sentence
                # (the on-console lane decodes the SCE code into words before it ever leaves the
                # PS5), so pass it through rather than paraphrasing it into something vaguer.
                if detail:
                    print("[install] fail (refused) name=%r res=%r" % (t.get("name"), res))
                    self._set(t, state="error", fail_reason="pkg_rejected", detail=detail,
                              msg=detail if detail[:1].isupper() else
                                  ("The console refused this package — %s" % detail))
                    return
                if timed_out:
                    # The port accepts TCP (even answers HEAD) but the install route HANGS: the daemon's
                    # install machinery is wedged — typical after a heavy install on 12.70, and it does NOT
                    # recover on its own. Reload it automatically and re-submit this same task once; only if
                    # that fails do we hold the queue and ask for a manual reload.
                    # NOTHING TO RELOAD. This block used to kill a wedged daemon and relaunch its
                    # payload, because a third-party install host on 12.70 stops accepting work
                    # after a heavy install and never recovers. Our installer is a fresh process
                    # per install that has already exited, so "wedged" is not a state it can be in
                    # - and the reload it used to perform is exactly what kept resurrecting etaHEN.
                    hold = ("\u23f8 The console took this package but never finished the hand-off. "
                            "Check the PS5, then press Start queue to try it again.")
                    print("[install] no verdict — pausing queue. name=%r" % t.get("name"))
                    self._set(t, state="held", msg=hold, pct=0, fail_reason="dpi_wedged")
                    self._pause_pending(t.get("console"), hold)
                    return
                # A 4xx right after a previous install is the daemon wedging, NOT a bad package:
                # its install machinery stops accepting work and answers 400 to everything. Reload
                # it and re-drive this task once before believing the rejection. (Retrying the POST
                # alone does not help — the daemon needs a restart.)
                # A 400 used to mean "the daemon has wedged, restart it and try again". With our
                # own engine a refusal is just a refusal - there is no machinery to unstick.
                msg = "The console refused this package"
                if detail:
                    msg += " — %s" % detail
                if risky:
                    msg += ". It may be the file name — try one with no spaces or [ ] brackets."
                print("[install] fail (rejected) name=%r res=%r" % (t.get("name"), res))
                self._set(t, state="error", msg=msg, detail=detail or None, fail_reason="pkg_rejected")
                return
            # our engine always reports real byte progress from the console
            measurable = (t.get("engine") == "pms") or bool(t.get("local_progress")) or bool(t.get("progress_url"))
            if measurable:
                self._set(t, state="transferring", msg="Connecting to %s" % bridge.name)
                stalled = 0
                last_mx = -1        # most bytes we have ever seen land, to detect a real stall
                while True:
                    if t["cancel"]:
                        self._set(t, state="canceled", msg="Canceled")
                        return
                    pr = self._progress(t) or {}
                    # Our engine reports its own failures — surface them straight away instead
                    # of sitting through the generic 90s "console isn't pulling" timeout.
                    if pr.get("state") == "error":
                        self._set(t, state="error", fail_reason="engine_error",
                                  msg=(pr.get("msg") or "The install engine stopped without "
                                                        "saying why — try this one again"))
                        return
                    mx, tot = pr.get("max", 0), (pr.get("total") or total or 0)
                    pct = min(90, int(mx * 90 / tot)) if tot else 0
                    self._set(t, pct=pct, msg="Downloading %d%%" % pct)
                    if tot and mx >= tot:
                        break
                    # Stall = no NEW bytes, not "still at 0%". Keying this off pct==0 meant that
                    # once a single percent landed the counter reset forever: a transfer that died
                    # at 3% held this worker — and, because installs are serialised per console,
                    # that console's entire queue — open indefinitely with no way out but a restart.
                    if mx > last_mx:
                        last_mx = mx
                        stalled = 0
                    else:
                        stalled += 1
                    if stalled > 180:      # ~90s with no new bytes at ANY point in the transfer
                        if last_mx > 0:
                            self._set(t, state="error", pct=pct, fail_reason="transfer_stalled",
                                      msg="Transfer stopped at %d%% and nothing more arrived for 90s. "
                                          "The console stopped pulling — start it again from the queue."
                                          % pct)
                        else:
                            self._set(t, state="submitted", pct=0,
                                      msg="Console isn't pulling yet — is the PKG MUTANT SHOP ELF loaded on the PS5?")
                        return
                    time.sleep(0.5)
                # POST-DOWNLOAD: follow the on-console install to playable.
                # bgft.db gives live install %, app.db (installed_titles) is the definitive
                # ready signal. If every byte lands but the title never registers, the console
                # rejected the package (bad/incompatible dump) — report that, don't hang.
                tid = t.get("title_id")
                self._set(t, state="promoting", pct=90, msg="Installing on %s" % bridge.name)
                deadline = time.time() + 600           # 10-minute hard cap
                bytes_done_at = None
                last_row, last_row_at = None, None      # [11] evidence of progress, not a stopwatch
                cur, installed = 90, False
                # Was it ALREADY registered before we started? If so, "it is in app.db" proves
                # nothing — that is exactly the state where the console refuses to re-pull the data
                # and leaves the old, broken files in place. We then demand proof on disk instead.
                was_registered = bool(t.get("_pre_registered"))
                pkg_bytes = t.get("total") or 0
                is_addon = str(t.get("kind") or "").lower() in ADDON_KINDS
                before = t.get("_bgft_before")
                while time.time() < deadline:
                    if t["cancel"]:
                        self._set(t, state="canceled", msg="Canceled")
                        return
                    if is_addon:
                        # An update/DLC adds to a title that is ALREADY registered and already has a
                        # full-size app.pkg, so neither app.db nor app.pkg can say anything about it.
                        # Its own completed bgft row is the proof: a row that is not the one we saw
                        # before handing over, fully transferred, with a terminal status.
                        row = bridge.install_job_row(tid) if tid else None
                        if row and row != before and row[2] and row[1] >= row[2] \
                                and row[0] in BGFT_DONE:
                            installed = True
                            break
                    elif tid and tid in (bridge.installed_titles(force=True) or []):
                        # app.db presence is NOT proof, for a new title either. A metadata-only
                        # registration (sceAppInstUtilAppInstallPkg) puts the title in app.db with
                        # no game data behind it — the tile appears, "Ready to play" is shown, and
                        # launching it crashes the console. The only honest proof is the game's own
                        # app.pkg on disk, at /user/app/<TID>/ or /mnt/ext1/user/app/<TID>/.
                        got = bridge.installed_app_pkg(tid)
                        if got and (not pkg_bytes or got >= int(pkg_bytes * 0.98)):
                            # For a title that was ALREADY registered with a full-size app.pkg
                            # before we started, this test proves nothing — the files it is looking
                            # at can be the previous install's. Demand one piece of fresh evidence:
                            # a bgft row that is not the one we snapshotted at hand-off. A genuinely
                            # new title needs no such proof, because app.pkg appearing at full size
                            # where there was nothing IS the new evidence.
                            if was_registered:
                                row = bridge.install_job_row(tid)
                                if not (row and row != before and row[0] in BGFT_DONE
                                        and row[2] and row[1] >= row[2]):
                                    time.sleep(3)
                                    continue            # keep waiting for OUR install to land
                            installed = True
                            break
                    prog = bridge.install_progress(tid) if tid else None
                    if prog and prog[1]:
                        cur = max(cur, 90 + int(9 * min(1.0, prog[0] / prog[1])))
                        if prog[0] >= prog[1] and bytes_done_at is None:
                            bytes_done_at = time.time()      # all bytes in; registration should follow
                    else:
                        cur = min(99, cur + 1)
                    # A CLOCK IS NOT A VERDICT. This used to fail the job after four minutes with
                    # "Console rejected this package … (bad dump)" — an accusation about the file,
                    # produced by a stopwatch. A large PS5 title can spend well over four minutes
                    # copying and decrypting after the last byte lands, and a brief FTP hiccup makes
                    # installed_titles() return None (read as "not there yet"), so a perfectly good
                    # install was called a bad dump and the user was told to re-dump the game.
                    #
                    # Wait on EVIDENCE instead: keep going while the console's own bgft row is still
                    # in a non-terminal state, and only give up when it reaches a terminal status
                    # that is not success, or when the row stops changing for a long time. Then say
                    # what is actually known — "the console has not finished" — not what is guessed.
                    if bytes_done_at:
                        row_now = None
                        try:
                            row_now = bridge.install_job_row(tid) if tid else None
                        except Exception:
                            row_now = None
                        if row_now and row_now != last_row:
                            last_row, last_row_at = row_now, time.time()   # still moving
                        if row_now and row_now[0] in BGFT_FAILED:
                            self._set(t, state="error", pct=99, fail_reason="bgft_failed",
                                      msg="The console's installer rejected this package "
                                          "(bgft status %s). Nothing here was changed — the file "
                                          "may be an incomplete dump." % row_now[0])
                            return
                        stalled = time.time() - (last_row_at or bytes_done_at)
                        if stalled > 1800:
                            self._set(t, state="error", pct=99, fail_reason="install_stalled",
                                      msg="Every byte reached %s, but its installer has not "
                                          "recorded progress for %d minutes. Check the PS5's "
                                          "Downloads notification — nothing here was changed."
                                          % (bridge.name, int(stalled // 60)))
                            return
                    self._set(t, pct=cur, msg="Installing %d%%" % cur)
                    time.sleep(3)
                # There is no ladder to police any more. This block read the third-party host's
                # own last-install record to find out which rung it had used, because its lower rung
                # (sceAppInstUtilAppInstallPkg) registers metadata with no data behind it and still
                # answers ok:true - a tile on the dashboard that crashes when launched. Our engine
                # makes one call, InstallByPackage, and never falls back; the proof it worked is the
                # bgft row and the files, both checked below.
                if installed:
                    # Nothing to mark dirty and nothing to clear. A third-party host had to be
                    # tracked across installs because it wedged after heavy ones; the spawned
                    # installer exits when it is done and leaves nothing behind.
                    # Last check before calling it done: if the game's own data is visibly short,
                    # say so rather than putting a tile on the dashboard that crashes when launched.
                    # `None` means we simply cannot see it — never fail on that, only on real proof.
                    # Size proof only means something for a base install — for an add-on this reads
                    # the PARENT's app.pkg, which is unrelated to the add-on's size.
                    got = bridge.installed_app_pkg(tid) if (tid and not is_addon) else None
                    if got is not None and pkg_bytes and got < int(pkg_bytes * 0.90):
                        self._set(t, state="error", pct=99, fail_reason="short_install",
                                  msg="Only %s of %s reached the console — delete the title there "
                                      "(Options → Delete) and install it again."
                                      % (human_size(got), human_size(pkg_bytes)))
                        return
                    self._set(t, state="playable", pct=100,
                              msg="Update installed" if is_addon else "Ready to play")
                    self._finished_installing(t)
                    if not is_addon:
                        remember_installed(tid)
                elif is_addon:
                    # Never tell someone to delete the GAME because its update did not take.
                    self._set(t, state="error", pct=99, fail_reason="addon_not_confirmed",
                              msg="The PS5 did not record this %s — the base game was left alone. "
                                  "Reload the install engine (⟳ in the queue) and try it again."
                                  % (t.get("kind") or "add-on"))
                elif was_registered:
                    # The documented failure: the title was already registered, so the console kept
                    # the old files and never re-pulled. Saying "Ready to play" here is what put a
                    # broken game on the dashboard, and launching it took the console down.
                    self._set(t, state="error", pct=99, fail_reason="stale_install",
                              msg="Already installed on the PS5, so it was not replaced — the old "
                                  "copy is still there and may be broken. Delete it on the console "
                                  "(Options → Delete), then install again.")
                else:
                    self._set(t, state="error", pct=99, msg="Install did not confirm on the console")
            else:
                self._set(t, state="submitted", pct=0, simulated=True,
                          msg="Sent to %s (progress not measurable for this source)" % bridge.name)
        else:
            # The console is not reachable, so NOTHING was installed. This used to animate progress
            # to 100%, report "Installed (simulated)" and then call remember_installed() — which
            # writes the title into installed.json permanently, so the library went on showing a
            # game as installed that had never left the PC. A demo animation is not worth a lie
            # about the state of someone's console.
            # HOLD, do not error - and take the rest of this console's queue with it.
            # Ten games queued and the PS5 reboots after the third: each remaining job costs about
            # two seconds to discover the console is gone, so the whole queue used to burn down to
            # `error` in under half a minute and every one of them needed re-queueing by hand. The
            # wedge path has held-and-paused since B8; being switched off is no different, and it
            # is far more likely.
            msg = ("\u23f8 %s is not reachable — nothing was installed. Turn the console on, load "
                   "PKG MUTANT SHOP, then press \u25b6 Start queue." % bridge.name)
            self._set(t, state="held", pct=0, fail_reason="console_offline", msg=msg)
            self._pause_pending(t.get("console"), msg)

    def _run_pc_copy(self, t):
        """Copy a game from another PC into this one's library folder.

        Downloads to a .part file and renames only when the whole thing is here, so an
        interrupted copy can never look like a finished game. Resumes with a Range request
        when a partial is already present."""
        url = t.get("url")
        dest_dir = t.get("dest")
        name = t.get("file") or (t.get("name") or "game") + ".pkg"
        if not url or not dest_dir:
            self._set(t, state="error", msg="There is no file to copy — rescan the library")
            return
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            self._set(t, state="error", detail="%s: %s" % (dest_dir, e),
                      msg="Could not create the destination folder — check the drive is "
                          "connected and not read-only")
            return
        final = os.path.join(dest_dir, name)
        part = final + ".part"
        if os.path.exists(final):
            self._set(t, state="playable", pct=100, msg="Already in this library")
            return

        have = os.path.getsize(part) if os.path.exists(part) else 0
        total = t.get("total") or 0
        self._set(t, state="transferring", pct=0, msg="Copying from %s…" % (t.get("from_pc") or "the other PC"))
        try:
            req = urllib.request.Request(url)
            if have:
                req.add_header("Range", "bytes=%d-" % have)
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    have = 0                      # server ignored the range: start over
                clen = int(r.headers.get("Content-Length") or 0)
                if not total:
                    total = have + clen
                mode = "ab" if have else "wb"
                last = 0
                with open(part, mode) as f:
                    while True:
                        if t["cancel"]:
                            self._set(t, state="canceled", msg="Canceled — partial file kept for resume")
                            return
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        if total and have - last > (4 << 20):
                            last = have
                            self._set(t, pct=min(99, int(have * 100 / total)),
                                      msg="Copying %s / %s" % (human_size(have), human_size(total)))
        except Exception as e:
            self._set(t, state="error", detail=str(e)[:300],
                      msg="The copy stopped — %s" % str(e)[:90])
            return

        if total and os.path.getsize(part) != total:
            self._set(t, state="error",
                      msg="Only %s of %s copied — kept so it can resume, press retry"
                          % (human_size(os.path.getsize(part)), human_size(total)))
            return
        try:
            os.replace(part, final)
        except OSError as e:
            self._set(t, state="error", detail=str(e)[:300],
                      msg="The copy finished but could not be renamed — something else may "
                          "have the file open")
            return
        self._set(t, state="playable", pct=100, msg="Copied into this library")
        try:
            self.library.scan()                   # it is a real local game now
        except Exception:
            pass

    def _run_console_local(self, t):
        """CONSOLE-LOCAL lane: the package is already on the PS5 (USB stick, external drive).
        Nothing is transferred from here - the console serves the file to its own install
        daemon, which is the same pipeline a downloaded game goes through. We start it and
        follow the console's status until it finishes."""
        bridge = self.fleet.bridge(t["console"])
        if bridge is None:
            self._set(t, state="error", msg="No console configured")
            return
        path = t.get("local_path")
        if not path:
            self._set(t, state="error", msg="This title has no package file to install")
            return
        # Snapshot the console BEFORE the handoff, for the same reason the download lane does:
        # a title that is already registered, or an old completed bgft row from a previous install,
        # must not be mistaken afterwards for proof that THIS package landed.
        tid = t.get("title_id")
        try:
            t["_pre_registered"] = bool(tid) and tid in (bridge.installed_titles(force=True) or [])
        except Exception:
            t["_pre_registered"] = False
        try:
            t["_bgft_before"] = bridge.install_job_row(tid) if tid else None
        except Exception:
            t["_bgft_before"] = None
        self._set(t, state="submitting", pct=0, msg="Handing the package to the console…")
        try:
            res = bridge.install_local_on_console(path, t.get("name") or "") or {}
        except Exception as e:
            self._set(t, state="error", detail=str(e)[:300],
                      msg="PKG MUTANT SHOP on the PS5 did not answer — load it again from "
                          "Payload Manager")
            return
        if not res.get("ok"):
            self._set(t, state="error",
                      msg=res.get("error") or "The console refused the package")
            return
        # The console installs on its own thread; poll it rather than holding this worker
        # on a socket for the whole install.
        self._set(t, state="promoting", pct=5, msg="Installing on the console…")
        deadline = time.time() + 3600
        while time.time() < deadline:
            if t["cancel"]:
                self._set(t, state="canceled", msg="Canceled (the console may still finish)")
                return
            time.sleep(4)
            try:
                st = bridge._shop("/api/install/status", timeout=15) or {}
            except Exception:
                continue
            if st.get("done"):
                if not st.get("ok"):
                    self._set(t, state="error",
                              msg=st.get("message") or "The install did not finish — press "
                                                       "retry to hand it over again")
                    return
                if not st.get("accepted_only"):
                    # The console did the install in-process and has really finished.
                    self._set(t, state="playable", pct=100, msg="Installed from the drive")
                    self._finished_installing(t)
                    return
                # etaHEN only ACCEPTED it. "SUCCESS: Task started" arrives in about a second and
                # the install then runs for minutes and can still fail. Declaring it playable here
                # is the same lie the download lane stopped telling in 3.18.2, so wait for the
                # console own proof instead.
                self._confirm_console_local(t, bridge)
                return
            if st.get("active") and t["pct"] < 95:
                self._set(t, pct=min(95, t["pct"] + 2), msg=st.get("message") or "Installing…")
        self._set(t, state="error",
                  msg="The console never reported this one finished. It may still be installing "
                      "— check the PS5 before trying again")

    def _confirm_console_local(self, t, bridge):
        """Wait for real proof that a package etaHEN merely ACCEPTED has actually installed.

        Same evidence the download lane uses, because it is the only evidence that does not lie:
          * add-on / already-registered title -> a bgft row that is NOT the one we snapshotted,
            fully transferred, with a terminal status (BGFT_DONE);
          * a brand-new title -> the game own app.pkg present on disk at full size.
        A timeout is reported as a timeout, never as success and never as a bad package.
        """
        tid = t.get("title_id")
        before = t.get("_bgft_before")
        was_registered = bool(t.get("_pre_registered"))
        kind = (t.get("kind") or "base").lower()
        is_addon = kind in ("update", "patch", "dlc", "backport") or was_registered
        pkg_bytes = int(t.get("total") or 0)
        self._set(t, state="promoting", pct=90,
                  msg="Accepted by the console — waiting for it to finish installing…")
        deadline = time.time() + 3600
        if not tid:
            # Without a title id there is nothing to look for. Say so rather than inventing a
            # verdict: the package was handed over and the console is dealing with it.
            self._set(t, state="submitted", pct=0,
                      msg="Handed to the console — no title id, so this app cannot confirm the "
                          "finish. Check the PS5 home screen.")
            return
        while time.time() < deadline:
            if t["cancel"]:
                self._set(t, state="canceled",
                          msg="Canceled — the console may still finish what it accepted")
                return
            time.sleep(5)
            row = None
            try:
                row = bridge.install_job_row(tid)
            except Exception:
                pass
            fresh_done = bool(row and row != before and row[2] and row[1] >= row[2]
                              and row[0] in BGFT_DONE)
            if fresh_done:
                self._set(t, state="playable", pct=100, msg="Installed from the drive")
                self._finished_installing(t)
                return
            if not is_addon:
                try:
                    got = bridge.installed_app_pkg(tid)
                except Exception:
                    got = None
                if got and (not pkg_bytes or got >= int(pkg_bytes * 0.98)):
                    self._set(t, state="playable", pct=100, msg="Installed from the drive")
                    self._finished_installing(t)
                    return
            if row and row[2]:
                self._set(t, pct=min(99, 90 + int(9 * min(1.0, row[1] / float(row[2])))),
                          msg="Installing on the console…")
        self._set(t, state="error", pct=99, fail_reason="local_not_confirmed",
                  msg="The console accepted this package but never recorded it as installed. "
                      "Check the PS5 notifications — nothing here was changed.")

    def _run_peer_delegate(self, t):
        """The PC that owns the backup is sending it to the console. Mirror its job here.

        The transfer happens machine-to-machine without passing through this one, so there is nothing
        to do but reflect it — the user asked for the game on the PS5 and should watch it go there,
        not have to open the app on the other PC to find out how it is doing."""
        owner = t.get("owner_url")
        who = t.get("from_pc") or "the other PC"
        if not owner:
            self._set(t, state="error",
                      msg="This game is no longer where the library said it was — rescan")
            return

        # Start it on the other machine NOW. This happens when THIS task runs, so pressing Start
        # here starts everything — the other PC is never left holding a job waiting to be released,
        # and it never matters which device the button was pressed on.
        oid = t.get("owner_job")
        if not oid:
            self._set(t, state="submitting", pct=0, msg="Asking %s to send it…" % who)
            payload = {"install_key": t.get("owner_key"), "drive": t.get("drive") or "ext1",
                       "title_id": t.get("title_id"), "name": t.get("name"),
                       "mode": "now"}          # always immediate: our queue already decided the wait
            try:
                rq = urllib.request.Request(
                    owner + "/api/install", method="POST",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(rq, timeout=25) as rr:
                    ans = json.loads(rr.read().decode("utf-8", "replace"))
            except Exception as e:
                ans = {"ok": False, "error": str(e)}
            oid = (ans.get("ids") or [None])[0] if isinstance(ans, dict) else None
            if not (isinstance(ans, dict) and ans.get("ok") and oid):
                # It could not take it (asleep, busy, older build). Do the whole thing ourselves
                # rather than stopping: pull the file here, then send it on. Slower, still automatic.
                print("[install] %s could not take the mount (%s) - doing it here instead"
                      % (who, (ans or {}).get("error")))
                self._set(t, msg="%s did not answer — copying it here first" % who)
                t["lane"] = "peer-mount"
                t["url"] = (t.get("owner_url") or "") + "/library/" + str(t.get("owner_key") or "")
                t["file"] = unquote(str(t.get("owner_key") or "")) or (t.get("name") or "game")
                plat = "PS5" if str(t.get("title_id") or "").upper().startswith("PPSA") else "PS4"
                root = self.cfg.get("library", {}).get("root") or LIBRARY_ROOT
                t["dest"] = os.path.join(root, plat if plat in LIBRARY_LAYOUT else "")
                t["mount_dest"] = mount_dest_for_drive(self.cfg, t.get("drive"))
                return self._run_peer_mount(t)
            t["owner_job"] = oid
        self._set(t, state="transferring", pct=0, msg="%s is sending it to the console…" % who)
        misses = 0
        while True:
            if t["cancel"]:
                try:
                    urllib.request.urlopen(owner + "/api/queue/" + quote(str(oid)) + "/cancel",
                                           data=b"{}", timeout=8).read()
                except Exception:
                    pass
                self._set(t, state="canceled", msg="Canceled on %s" % who)
                return
            doc = http_get_json(owner + "/api/queue", timeout=8)
            row = None
            for x in ((doc or {}).get("tasks") or []):
                if str(x.get("id")) == str(oid):
                    row = x
                    break
            if row is None:
                misses += 1
                if misses > 20:          # cleared from its queue, or the PC went away
                    self._set(t, state="error", msg="%s stopped reporting this job" % who)
                    return
                time.sleep(3)
                continue
            misses = 0
            st, pct = row.get("state"), int(row.get("pct") or 0)
            msg = row.get("msg") or ""
            if st in ("playable", "error", "canceled"):
                self._set(t, state=st, pct=100 if st == "playable" else pct,
                          msg=("%s — sent from %s" % (msg, who)) if msg else st)
                if st == "playable":
                    self._finished_installing(t)
                    remember_installed(t.get("title_id"))
                return
            # It was submitted with mode "now", so it is never waiting to be released. If it is
            # briefly "queued" that is only its worker picking it up, or it was held by something
            # else — release it rather than reporting a dead end the user has to go and fix.
            if st == "held":
                try:
                    urllib.request.urlopen(owner + "/api/queue/start", data=b"{}", timeout=8).read()
                except Exception:
                    pass
            self._set(t, state="promoting" if st == "promoting" else "transferring",
                      pct=pct,
                      msg=("Starting on %s…" % who) if st in ("held", "queued")
                          else "%s (on %s)" % (msg or "Sending…", who))
            time.sleep(2)

    def _run_peer_mount(self, t):
        """A backup that lives on ANOTHER PC, going to the console's ShadowMount folder.

        Two steps that already work, run in order and nothing else: pull the container into this
        library exactly as a PC-to-PC copy does, then send it on exactly as a local backup does.
        Neither of those two methods is touched — a failure in either reports itself and stops here.
        """
        self._run_pc_copy(t)                       # step 1: it is a local file afterwards
        if t.get("cancel") or t.get("state") != "playable":
            return                                 # the copy already said what went wrong
        final = os.path.join(t.get("dest") or "", t.get("file") or "")
        if not os.path.isfile(final):
            self._set(t, state="error", detail=final,
                      msg="The copy finished but the file is not where it should be — rescan "
                          "the library")
            return
        t["local_path"] = final
        t["total"] = os.path.getsize(final)
        t["lane"] = "mount"                        # step 2 behaves as any local backup does
        t["dest"] = t.get("mount_dest") or mount_dest_for_drive(self.cfg, t.get("drive"))
        self._set(t, state="transferring", pct=0,
                  msg="Copied from %s — sending to the console…" % (t.get("from_pc") or "the other PC"))
        self._run_mount(t)

    def _run_mount(self, t):
        """MOUNT lane: get the backup into the chosen drive's ShadowMount folder; it mounts itself.

        A PS5 backup is never installed - there is no PKG and no BGFT. ShadowMount watches
        /mnt/<drive>/homebrew and mounts what turns up, so the whole lane is: put the container in
        the right folder under its real name, then wait for the console to say it mounted.

        Two transports, same ending. The console PULLS it with our own downloader whenever it can
        (see fetch_to) - that is one hop even when the file lives on another PC. The PC pushes it
        only for a folder-shaped game or a console running an ELF older than /api/engine/fetch.
        """
        bridge = self.fleet.bridge(t["console"])
        if bridge is None:
            self._set(t, state="error", msg="No console configured")
            return
        local = t.get("local_path")
        dest = t.get("dest") or mount_dest_for_drive(self.cfg, t.get("drive"))
        # A PULL needs no local file - the console fetches it from whichever PC serves it, which may
        # not be this one. Only the push fallback needs something on this disk.
        have_local = bool(local and os.path.exists(local))
        if not have_local and not t.get("url"):
            self._set(t, state="error",
                      msg="The file for this backup is missing — rescan the library")
            return
        is_dir = have_local and os.path.isdir(local)   # a game stored as a FOLDER is equally valid
        total = t.get("total") or ((0 if is_dir else os.path.getsize(local)) if have_local else 0)
        name = (os.path.basename(local.rstrip("\\/")) if have_local
                else (t.get("remote_name") or "backup.ffpfsc"))
        # Check the console can actually REGISTER a new title before spending an hour sending one.
        # Delivering the container was never the hard part; registering it is, and when ShadowMount
        # is on the wrong register route the game mounts and then fails — after the whole transfer.
        # See Ps5Bridge.register_path() for the full story. Fixing the config is free; it only
        # matters for titles the console does not already know, so a restart is skipped when the
        # title is already registered or a game is running.
        if t.get("lane") == "mount":
            try:
                res = bridge.ensure_register_path()
                if res == "fixed":
                    self._set(t, msg="Fixing ShadowMount's register path…")
                    r = bridge.restart_shadowmount()
                    print("[mount] register path fixed on %s; ShadowMount %s" % (bridge.name, r))
                    if r == "busy":
                        self._set(t, msg="Register path fixed — takes effect once a game is not running")
            except Exception as e:
                print("[mount] register-path preflight skipped: %r" % e)
        # sendpkg → install via AppInstallPkg, which needs a clean path (no spaces/brackets): use <titleid>.pkg
        upname = ((t.get("title_id") or "game") + ".pkg") if t.get("lane") == "sendpkg" else name
        try:
            # Through OUR on-console file API, with the third-party FTP only as fs_*'s fallback.
            # Until 3.24.4 this lane dialled `ftp_port` straight from config, so on a console
            # running etaHEN (FTP on 1337, 2121 closed) it could not connect at all - and answered
            # that by animating a fake transfer to 100%.
            if not bridge.fs_mkdir(dest):
                raise IOError("could not create %s on the console" % dest)
        except Exception as e:
            # NOTHING was transferred, so the backup is not on the console. This used to answer an
            # unreachable FTP by animating the bar to 100% and reporting "Deployed to ShadowMount
            # (simulated)" in state `playable` - the exact lie the install lane had already removed
            # (see _run's console_offline branch): a finished-looking green job for a game that
            # never left the PC. Say what actually happened instead.
            self._set(t, state="error", pct=0, fail_reason="console_unreachable",
                      msg="Could not reach %s to prepare %s — nothing was sent. Load "
                          "PKG MUTANT SHOP on the PS5, then press ▶ Start queue again."
                          % (bridge.name, dest))
            print("[mount] console unreachable (%s): %r" % (bridge.ip, e))
            return
        # ---------------------------------------------------------------------------------
        # PREFERRED: THE CONSOLE PULLS IT, using our own downloader.
        #
        # Only a single-file container can be pulled - a game stored as a folder is a tree and has
        # to be walked, which is what the push below does. And we need a URL the console can reach:
        # `url` is already set when the backup lives on a peer, otherwise we serve it ourselves.
        # ---------------------------------------------------------------------------------
        pull_url = t.get("url") or ""
        if not pull_url and t.get("key"):
            pull_url = "http://%s:%d/library/%s" % (companion_ip_for(bridge.ip),
                                                    self.cfg["companion"]["port"], t["key"])
        if not is_dir and pull_url:
            okf, info = bridge.fetch_to(pull_url, dest, upname, size=total)
            if okf:
                self._set(t, state="transferring", pct=0,
                          msg="%s is downloading it to %s…" % (bridge.name, dest))
                stalled, last_done = 0, -1
                while True:
                    if t["cancel"]:
                        self._set(t, state="canceled", msg="Canceled")
                        return
                    j = bridge.engine_job() or {}
                    # Pin the job id. dl_job_t carries one precisely so a caller "can never mistake
                    # the PREVIOUS job's done for its own" - and this loop starts by polling, so
                    # without it a stale "done" from the last install reads as instant success.
                    if info.get("job_id") is not None and j.get("job_id") not in (None, info["job_id"]):
                        self._set(t, state="error", pct=0, fail_reason="transfer_stalled",
                                  msg="Another download started on the console and replaced this "
                                      "one. Try it again on its own.")
                        return
                    st = str(j.get("state") or "")
                    done, tot = int(j.get("done") or 0), int(j.get("total") or total or 0)
                    if st in ("error", "failed"):
                        self._set(t, state="error", pct=0, fail_reason="transfer_stalled",
                                  msg=j.get("msg") or "The console could not download the backup")
                        return
                    if st in ("done", "installed"):
                        break
                    pct = min(99, int(done * 99 / tot)) if tot else 0
                    self._set(t, pct=pct,
                              msg="Downloading to the console %d%% (%s)" % (pct, human_size(done)))
                    # A pull that stops moving is the console's problem, not ours - say so rather
                    # than sitting on a frozen bar. 5 minutes of no new bytes is generous for LAN.
                    stalled = 0 if done != last_done else stalled + 1
                    last_done = done
                    if stalled > 150:
                        self._set(t, state="error", pct=pct, fail_reason="transfer_stalled",
                                  msg="The console stopped downloading at %d%%. Check the drive is "
                                      "connected and this PC is still sharing the file." % pct)
                        return
                    time.sleep(2)
                self._set(t, pct=99, msg="Downloaded — waiting for the backup service")
                return self._mount_confirm(t, bridge, dest)
            if not info.get("unsupported"):
                # A real refusal - a missing drive, a full one, or a download already running.
                # Falling back to another transport would only hide it.
                self._set(t, state="error", pct=0, fail_reason="console_unreachable",
                          msg=info.get("error") or "The console would not take this backup")
                return
            # This console predates /api/engine/fetch. If the file lives on another PC, that PC can
            # still push it the old way; if it is here, we push it ourselves below.
            if not have_local and t.get("owner_url") and t.get("owner_key"):
                print("[mount] this console has no /api/engine/fetch - delegating to %s"
                      % (t.get("from_pc") or "the other PC"))
                t["lane"] = "peer-delegate"
                return self._run_peer_delegate(t)
            if not have_local:
                self._set(t, state="error", pct=0, fail_reason="console_unreachable",
                          msg="This console is running an older PKG MUTANT SHOP that cannot "
                              "download backups itself. Load the current one from Payload Manager.")
                return
            print("[mount] this console has no /api/engine/fetch - falling back to pushing")

        if not have_local:
            self._set(t, state="error", pct=0, fail_reason="console_unreachable",
                      msg="This backup is on another PC and the console could not fetch it.")
            return
        self._set(t, state="transferring", msg="Uploading backup → %s…" % dest)
        try:
            sent = [0]

            def cb(_chunk_len, _running):
                # Raising here is how cancel unwinds a transfer that is already in flight: the
                # exception propagates out of the request, through fs_write, to the handler below.
                if t["cancel"]:
                    raise _Cancelled()
                sent[0] = _running
                pct = min(99, int(sent[0] * 100 / total)) if total else 0
                self._set(t, pct=pct, msg="Uploading %d%%" % pct)

            def push(local_path, remote_path):
                size = os.path.getsize(local_path)
                with open(local_path, "rb") as f:
                    ok = bridge.fs_write(remote_path, _ProgressReader(f, cb),
                                         timeout=3600, size=size)
                if not ok:
                    raise IOError("the console rejected %s" % remote_path)

            if os.path.isdir(local):
                # A game stored as a FOLDER: same destination, same progress, but the tree has to be
                # recreated file by file. Kept as its own branch so the single-file push below — the
                # path every container has always taken — is not altered.
                base = os.path.basename(local.rstrip("\\/"))
                for dp, _dn, fns in os.walk(local):
                    rel = os.path.relpath(dp, local).replace("\\", "/")
                    remote = "%s/%s" % (dest, base) if rel in (".", "") else "%s/%s/%s" % (dest, base, rel)
                    bridge.fs_mkdir(remote)
                    for fn2 in fns:
                        if t["cancel"]:
                            raise _Cancelled()
                        push(os.path.join(dp, fn2), "%s/%s" % (remote, fn2))
            else:
                # Both transports write <name>.part and rename only once every byte is across, so
                # an interrupted transfer can never leave a partial container in ShadowMount's scan
                # folder looking like a real game.
                push(local, "%s/%s" % (dest, upname))
                if total and sent[0] < total:
                    raise IOError("sent %s of %s" % (human_size(sent[0]), human_size(total)))
        except _Cancelled:
            self._set(t, state="canceled", msg="Canceled")
            return
        except Exception as e:
            # fs_write owns its own transport and closes it; there is no socket to leak here.
            self._set(t, state="error", detail=str(e)[:300],
                      msg="The upload stopped — %s" % str(e)[:90])
            return
        return self._mount_confirm(t, bridge, dest)

    def _mount_confirm(self, t, bridge, dest):
        """The container is in ShadowMount's folder. Wait for the console to actually mount it.

        Shared by both transports - the console-pull and the PC-push end in exactly the same place,
        and this used to be inline in the push path only.

        Delivering the file is NOT the same as the game working: ShadowMount still has to mount the
        image AND register the title, and the register step can fail on its own (its
        "AppInstallTitleDir bridge unavailable", which needs a console restart to clear). Reporting
        "Deployed" regardless is what put games on the dashboard that crash when launched.
        """
        self._set(t, state="promoting", pct=99, msg="Mounting on %s" % bridge.name)
        tid = t.get("title_id")
        if tid:
            ok = False
            deadline = time.time() + 180
            while time.time() < deadline:
                if t["cancel"]:
                    self._set(t, state="canceled", msg="Canceled")
                    return
                if bridge.title_is_mounted(tid):
                    ok = True
                    break
                time.sleep(5)
            if not ok:
                self._set(t, state="error", pct=99, fail_reason="mount_not_registered",
                          msg="The backup is on %s, but the PS5 did not finish mounting it. "
                              "ShadowMount can mount the image and still fail to register the "
                              "title — restart the console, then it will pick it up from the same "
                              "file." % dest)
                return
        self._set(t, state="playable", pct=100, msg="Mounted — ready to play")
        self._finished_installing(t)
        remember_installed(t.get("title_id"))

    def _confirm(self, bridge, title_id, seconds):
        if not title_id:
            return False
        deadline = time.time() + seconds
        while time.time() < deadline:
            ids = bridge.installed_titles(force=True)   # fresh read so a just-installed PS4/PS5 title confirms fast
            if ids and title_id in ids:
                return True
            time.sleep(6)
        return False


# installed.json and hashes.json are read-modify-write from several threads at once: the queue
# marks a title installed while the library scan is hashing and the UI is reading. Without a lock,
# two workers finishing together each read the same list and the second write loses the first
# title - the badge reverts and the game looks uninstalled. The re-read happens INSIDE the lock so
# the window between read and write is closed, not merely narrowed.
_state_lock = threading.RLock()


def remember_installed(title_id):
    if not title_id:
        return
    with _state_lock:
        try:
            data = {"installed": []}
            if os.path.exists(INSTALLED_PATH):
                # Never assume the shape, and never let an unreadable file swallow THIS write:
                # a truncated or hand-edited file used to raise inside the outer try, so the title
                # being recorded was simply lost. Start fresh instead and keep going.
                try:
                    with open(INSTALLED_PATH) as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict) and isinstance(loaded.get("installed"), list):
                        data = loaded
                    elif isinstance(loaded, list):
                        data = {"installed": loaded}
                    else:
                        print("[installed] %s had an unexpected shape - starting a fresh list"
                              % INSTALLED_PATH)
                except Exception as le:
                    print("[installed] %s is unreadable (%s) - starting a fresh list"
                          % (INSTALLED_PATH, le))
            if title_id not in data["installed"]:
                data["installed"].append(title_id)
            _atomic_write_json(INSTALLED_PATH, data)
        except Exception as e:
            print("[installed] %s" % e)


def local_installed():
    try:
        with _state_lock, open(INSTALLED_PATH) as f:
            loaded = json.load(f)
        if isinstance(loaded, list):
            return loaded
        return loaded.get("installed", []) if isinstance(loaded, dict) else []
    except Exception:
        return []


def prune_local_installed(console_ids):
    """Self-heal: drop remembered installs the console no longer has (kills stale 'ghost' badges like a
    game that was queued once but isn't really on the PS5). Only call when the console list is real."""
    keep = sorted(set(local_installed()) & set(console_ids))
    _atomic_write_json(INSTALLED_PATH, {"installed": keep})
    return keep


# --------------------------------------------------------------------------- #
# HTTP handler                                                                  #
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "PkgMutantShop/" + VERSION

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, obj, code=200):
        # _install() recurses into itself once per part of a multi-part release, and every one of
        # those calls used to reach here and write a complete HTTP response into the SAME socket.
        # A 3-part release wrote four responses to one request; the client reads the first and the
        # rest are garbage on the wire. Muting lets the recursion collect its results and lets the
        # outermost call emit exactly one response.
        if getattr(self, "_json_muted", False):
            return (obj, code)
        payload = json.dumps(obj).encode("utf-8")
        # /api/library is ~200 KB and it crosses the real LAN: the PS5 loads the page shell from
        # the console over loopback, but every /api/* call goes to a PC companion. Measured
        # 199,546 -> 19,990 bytes, a 10x reduction, for the payload fetched on boot and on every
        # refresh. Only when the client asks, and only when it is worth the CPU; images are
        # already compressed and are never routed through here.
        enc = None
        if len(payload) > 4096 and "gzip" in (self.headers.get("Accept-Encoding") or "").lower():
            try:
                payload, enc = gzip.compress(payload, 6), "gzip"
            except Exception:
                enc = None                      # never fail a response over compression
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if enc:
            self.send_header("Content-Encoding", enc)
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def _plain(self, text, code=200):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        """Some install clients HEAD the PKG (for size / Accept-Ranges) before GETting it."""
        srv = self.server
        path = urlparse(self.path).path
        if path.startswith("/library/"):
            raw = unquote(path[len("/library/"):]).split("?")[0]
            rk = registry_key(srv.library.file_registry, raw)
            if not rk or not os.path.isfile(srv.library.file_registry[rk]):
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(os.path.getsize(srv.library.file_registry[rk])))
            self._cors()
            self.end_headers()
            return
        if path.startswith("/icon/"):
            full = os.path.join(ICON_DIR, os.path.basename(unquote(path[len("/icon/"):]).split("?")[0]))
            ok = os.path.isfile(full)
            self.send_response(200 if ok else 404)
            if ok:
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(os.path.getsize(full)))
            self._cors()
            self.end_headers()
            return
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        path, srv = u.path, self.server
        q = parse_qs(u.query)
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/library/"):
            return self._serve_library(unquote(path[len("/library/"):]))
        if path.startswith("/thumb/"):
            return self._serve_thumb(unquote(path[len("/thumb/"):]))
        if path.startswith("/icon/"):
            return self._serve_icon(unquote(path[len("/icon/"):]))
        if path.startswith("/api/served/"):
            raw = unquote(path[len("/api/served/"):])
            return self._json(srv.transfers.get(raw) or srv.transfers.get(quote(raw)) or {"max": 0, "total": 0})
        if path == "/api/health":
            cons = srv.fleet.consoles
            b = srv.fleet.bridge(cons[0]["id"]) if cons else None
            # PROBE MEMO. This endpoint is polled every 6 s by the console's UI, again by this
            # PC's own UI if it is open, and again by peers - and each call opened fresh TCP
            # connections to the console for ping() and ftp_ok(). On the console those connections
            # land on a single-threaded accept loop that is also serving the page the user is
            # scrolling, so the cost is paid in frames.
            #
            # Memoised HERE rather than inside ping()/ftp_ok(): those two are also called by the
            # install preflight, where a stale answer could matter. Health is a status readout
            # polled on a 6 s cycle; 5 s of staleness is invisible in it and cannot reach the
            # install lane.
            # ONE cheap question before any of the expensive ones. With the console switched off
            # every probe below pays its full timeout, and this endpoint is polled every 6s - the
            # app sat unresponsive for 10s at a time precisely when the user opened it to find out
            # why the console was not there. A closed TCP port settles it in milliseconds.
            reachable = b.up() if b else False
            _pm = getattr(srv, "_health_probe", None)
            if not reachable:
                dpi_reachable, ftp_on = False, False
            elif _pm and (time.monotonic() - _pm[0]) < 5.0:
                dpi_reachable, ftp_on = _pm[1], _pm[2]
            else:
                dpi_reachable = False        # nothing of ours binds :12800 - kept False for old UIs
                ftp_on = b.ftp_ok() if b else False
                srv._health_probe = (time.monotonic(), dpi_reachable, ftp_on)
            # WHAT "READY TO INSTALL" ACTUALLY MEANS NOW.
            # It used to mean "something is listening on :12800" - a third-party daemon we no
            # longer call. Our engine is spawned per install by Payload Manager and needs nothing
            # listening beforehand, so with etaHEN removed the app reported "not connected" while
            # installs worked perfectly. Ask OUR on-console server instead.
            # There is one engine. This used to read dpi.mode to decide whether to even ask the
            # console whether an install could start - and that key no longer exists, so leaving
            # the test in would have reported "third-party" and never probed at all.
            engine_ready = False
            if b is not None and reachable:
                # Cached: /api/health is polled continuously by the UI and this is a round trip to
                # the console. Measured 3.3s per call before caching, which the user feels as a
                # sluggish app. 5s is far shorter than the time it takes anyone to act on it.
                _em = getattr(srv, "_engine_probe", None)
                if _em and (time.monotonic() - _em[0]) < 5.0:
                    engine_ready = _em[1]
                else:
                    try:
                        _h = b._shop("/api/health", timeout=4) or {}
                        engine_ready = bool(_h.get("engine_ready", _h.get("on_console")))
                    except Exception:
                        engine_ready = False
                    srv._engine_probe = (time.monotonic(), engine_ready)
            _b = _first_bridge(srv)
            # Only ask what is running when the console is actually there. This lookup costs two
            # 6-second timeouts against a dead address, which turned every health check into a
            # ~14s stall whenever the PS5 was off or resting — the app looked hung when in fact
            # the console simply was not home.
            _run = ((_b.running_title() if (engine_ready or ftp_on) else {}) if _b else {}) or {}
            # Cached helper state, so adding these two keys costs health nothing - but only ask at
            # all when the console is actually there. helper_status() probes ShadowMount and FTP,
            # and against a switched-off console each of those waits out its own timeout.
            try:
                _helpers = (b.helper_status() if (b and reachable) else {}) or {}
            except Exception:
                _helpers = {}
            return self._json({"ok": True, "version": VERSION,
                               "running_title": _run.get("titleId") or "",
                               "running_name": _run.get("titleName") or "",
                               "ps5_ip": cons[0]["ip"] if cons else srv.cfg.get("ps5_ip"),
                               # Reachability now means OUR lane is usable. dpi_* is unchanged
                               # underneath for anything still reading it.
                               "ps5_online": engine_ready,
                               "engine": "pms-spawn",
                               "engine_ready": engine_ready,
                               "ftp_online": ftp_on,
                               "ftp_port": (getattr(b, "_ftp_port", None) if b else None)
                                           or (b.c.get("ftp_port") if b else None) or 2121,
                               "connected": (engine_ready or ftp_on),
                               "lan_ip": lan_ip(),
                               # The settings panel reads BOTH of these off health every few
                               # seconds. Neither was ever sent, so the poll kept overwriting the
                               # correct values that openSettings() had just fetched: the backups
                               # row flipped to a red "not running", and the library row fell back
                               # to a hardcoded path regardless of what was configured.
                               # None = unknown (ShadowMount is loopback-only, so the PC cannot
                               # see it). The UI must not paint unknown as "not running".
                               "shadowmount": _helpers.get("shadowmount"),
                               "shadowmount_port": int(_helpers.get("shadowmount_port") or 10101),
                               "library_paths": srv.cfg.get("library", {}).get("local_paths", []),
                               "companion_port": srv.cfg["companion"]["port"], "consoles": len(cons),
                               "library_gen": srv.library.gen, "last_scan_ms": srv.library.last_scan_ms,
                               # Surfaced so the UI can warn instead of silently running on
                               # defaults while refusing to persist anything the user changes.
                               "config_unreadable": _CONFIG_UNREADABLE or "",
                               "content_ack": bool(srv.cfg.get("content_ownership_ack"))})
        if path == "/api/engine/log":
            # Proxied, not re-implemented: the console writes this file with O_SYNC precisely so it
            # survives a crash, and it is the only record of an install that happened with this PC
            # switched off. Returned as JSON because everything else this API serves is JSON; the
            # console's own route stays plain text for anyone reading it with curl.
            b = _first_bridge(srv)
            if b is None:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            try:
                raw = b._shop_text("/api/engine/log", timeout=10)
                return self._json({"ok": True, "log": raw, "bytes": len(raw)})
            except Exception as e:
                return self._json({"ok": False,
                                   "error": "PKG MUTANT SHOP on the PS5 did not answer (%s)"
                                            % str(e)[:80]}, 502)
        if path == "/api/engine/state":
            # WHY THIS EXISTS: the settings panel used to describe the install engine by reading
            # dpi_port out of /api/health and printing "ip:12800" next to an LED that meant "is a
            # third-party daemon listening". With our own engine that row was wrong in both halves
            # - the port belongs to software we removed, and the LED was off while installs worked.
            b = _first_bridge(srv)
            mode = (srv.cfg.get("dpi", {}) or {}).get("mode") or "v2"
            ours = mode in ("pms", "spawn")
            pl_port = int((srv.cfg.get("dpi", {}) or {}).get("pldmgr_port", 8084))
            shop_port = int(srv.cfg.get("console", {}).get("shop_port", 8710))
            out = {
                "ok": True, "mode": mode, "ours": ours,
                "name": ("PKG MUTANT SHOP engine" if ours else "third-party install host"),
                "how": ("A fresh installer is started for every install and exits when the console "
                        "has accepted the package. Nothing stays running, so nothing can wedge."
                        if mode == "spawn" else
                        "The console downloads the package itself and registers it."
                        if mode == "pms" else
                        "Installs are handed to our own engine on the console."),
                "shop_port": shop_port, "pldmgr_port": pl_port,
                "shop_ok": False, "shop_version": "", "ready": False,
                "busy": False, "busy_for": 0, "log_bytes": 0,
                "console_ip": (b.ip if b else srv.cfg.get("ps5_ip", "")),
            }
            if b is not None:
                try:
                    h = b._shop("/api/health", timeout=4) or {}
                    out["shop_ok"] = bool(h.get("ok"))
                    out["shop_version"] = str(h.get("version") or "")
                    # engine_ready on the console means "Payload Manager is answering", which is
                    # the one thing our lane actually needs before an install can start.
                    out["ready"] = bool(h.get("engine_ready", h.get("on_console")))
                    out["shadowmount"] = bool(h.get("shadowmount"))
                    out["ftp_port"] = int(h.get("ftp_port") or 0)
                except Exception as e:
                    out["error"] = "PKG MUTANT SHOP on the PS5 did not answer (%s)" % str(e)[:80]
                if ours and out["shop_ok"]:
                    st = b.spawn_status() or {}
                    out["busy"] = bool(st.get("busy"))
                    out["busy_for"] = int(st.get("busy_for") or 0)
                    out["log_bytes"] = int(st.get("log_bytes") or 0)
            # What the panel should SAY, decided once here rather than in three places in the UI.
            if not b:
                out["state"] = "no-console"
                out["detail"] = "No PS5 is set up yet - add its address above."
            elif not out["shop_ok"]:
                out["state"] = "shop-down"
                out["detail"] = ("PKG MUTANT SHOP is not answering on the PS5. Load it from "
                                 "Payload Manager, then reopen this panel.")
            elif not out["ready"]:
                out["state"] = "no-pldmgr"
                out["detail"] = ("Payload Manager is not answering on :%d, and our engine needs it "
                                 "to start each install. Reload it on the PS5." % pl_port)
            elif out["busy"]:
                out["state"] = "busy"
                out["detail"] = "An install is being handed to the console right now."
            else:
                out["state"] = "ready"
                out["detail"] = "Ready - installs can start straight away."
            return self._json(out)
        if path == "/api/move/status":
            # Progress lives on the console (it is doing the copying), so this is a straight
            # forward. "not active, not done" is the honest answer when there is no console.
            b = _first_bridge(srv)
            if b is None:
                return self._json({"active": False, "done": False})
            try:
                return self._json(b._shop("/api/move/status", timeout=10) or
                                  {"active": False, "done": False})
            except Exception:
                return self._json({"active": False, "done": False})
        if path == "/api/devices":                                     # [B6]
            # The PS5 half comes FROM THE PS5. PS5_DEVICES is a hardcoded list of eleven ids with no
            # free space and no way to know which are plugged in, so the move picker used to offer
            # USB1-USB7 on a console that has none of them, each showing an undefined size. The
            # console answers this with real statvfs figures and a `detected` flag; the static list
            # is now only the fallback for when there is no console to ask.
            ps5 = PS5_DEVICES
            b = _first_bridge(srv)
            if b is not None:
                try:
                    d = b._shop("/api/devices", timeout=8) or {}
                    if isinstance(d.get("ps5"), list) and d["ps5"]:
                        ps5 = d["ps5"]
                except Exception:
                    pass                                    # console asleep - fall back to the list
            return self._json({"pc": enumerate_pc_drives(), "ps5": ps5,
                               "library_paths": srv.cfg.get("library", {}).get("local_paths", [])})
        if path.startswith("/api/verify/"):                            # [B7] structural completeness check
            raw = unquote(path[len("/api/verify/"):])
            rk = registry_key(srv.library.file_registry, raw)
            if not rk:
                return self._json({"error": "unknown key"}, 404)
            return self._json(dict(pkg_meta.pkg_completeness(srv.library.file_registry[rk]), key=rk))
        if path == "/api/ps5-log":
            with PS5_LOG_LOCK:
                return self._json({"log": list(PS5_LOG)})
        if path == "/api/config":
            return self._json({k: v for k, v in srv.cfg.items() if not k.startswith("_")})
        if path == "/api/consoles":
            return self._json({"consoles": srv.fleet.status()})
        if path == "/api/library":
            return self._json(build_federated_library(srv))
        if path == "/api/federation":                                  # [B9] advertise our library to peers
            return self._json(federation_self(srv))
        if path == "/api/network":
            # Everything on the network that is part of this setup: this PC, the peers we can
            # see, and the console. This is what the UI shows and what a phone connects to.
            reg = getattr(srv, "peers", None)
            if reg is not None:
                reg.scan_async()
            me = device_identity(srv.cfg)
            b = _first_bridge(srv)
            return self._json({
                "this_pc": {"id": me.get("id"), "name": me.get("name"),
                            "lan_ip": lan_ip(), "port": srv.cfg["companion"]["port"],
                            "url": "http://%s:%d" % (lan_ip(), srv.cfg["companion"]["port"]),
                            "os": platform.system(), "version": VERSION,
                            "counts": _library_counts(srv), "local": True},
                "peers": [{"id": p.get("id"), "name": p.get("name"), "lan_ip": p.get("lan_ip"),
                           "url": p.get("url"), "os": p.get("os"), "version": p.get("version"),
                           "counts": p.get("counts"), "count": p.get("count", 0),
                           "online": bool(p.get("online")), "last_seen": p.get("last_seen")}
                          for p in (reg.known() if reg is not None else [])],
                "console": {"ip": b.ip, "name": b.name, "online": b.pldmgr.alive()} if b else None,
                "scanning": bool(reg.scanning) if reg is not None else False})
        if path == "/api/network/scan":
            reg = getattr(srv, "peers", None)
            if reg is None:
                return self._json({"ok": False, "error": "peer registry unavailable"}, 500)
            # scan() spawns 254 threads and blocks this request until every one finishes, so a
            # press of "Scan network" held the whole single-request path open for the full sweep.
            # scan_async() is the same sweep with a 60 s minimum interval (already used by the two
            # other callers). Kick it off, then give it a SHORT bounded grace period so the button
            # still returns something useful on a cold first press instead of an empty list -
            # rather than either blocking for the full sweep or lying about finding nothing.
            before = len(reg.known())
            reg.scan_async()
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline and len(reg.known()) == before:
                time.sleep(0.1)
            found = reg.known()
            return self._json({"ok": True, "found": len(found),
                               "peers": [{"id": p.get("id"), "name": p.get("name"),
                                          "url": p.get("url"), "counts": p.get("counts")}
                                         for p in found]})
        if path == "/api/federation/peers":
            return self._json({"peers": getattr(srv, "_fed_peers", []),
                               "enabled": bool(srv.cfg.get("federation", {}).get("enabled"))})
        if path == "/api/storage":
            b = srv.fleet.bridge(srv.fleet.ids()[0]) if srv.fleet.consoles else None
            devs = None
            if b is not None:
                try:
                    devs = (b._shop("/api/devices", timeout=8) or {}).get("ps5")
                except Exception:
                    devs = None                     # console asleep
            # THE PROBE ABOVE IS THE LIVENESS TEST, not the app cache. It is a real round-trip
            # behind the cached TCP gate, so it is free when the console is off; and a console that
            # is up always answers it with all eleven destinations. devs is None therefore means
            # exactly "the PS5 did not answer", and its drives drop off the bar until it does.
            return self._json(build_storage(srv, b.console_apps() if b else None, devs,
                                            console_online=devs is not None))
        if path == "/api/console/apps":
            b = srv.fleet.bridge(srv.fleet.ids()[0]) if srv.fleet.consoles else None
            apps = b.console_apps() if b else None
            return self._json({"reachable": apps is not None, "apps": apps or []})
        if path == "/api/discover":
            return self._json({"found": discover_ps5(srv.cfg)})
        if path == "/api/installed":
            cid = (q.get("console") or [None])[0]
            bridge = srv.fleet.bridge(cid) if cid else (srv.fleet.consoles and srv.fleet.bridge(srv.fleet.ids()[0]))
            console = bridge.installed_titles() if bridge else None
            if console is not None:                       # console is authoritative: trust it
                prune_local_installed(console)            # self-heal: drop stale 'ghost' remembered installs
                merged = sorted(console)
            else:                                         # offline: best-effort from remembered installs
                merged = sorted(local_installed())
            return self._json({"installed": merged, "source": "console" if console is not None else "local",
                               "console_reachable": console is not None})
        if path == "/api/sources":
            ranked = srv.engine.rank()
            doc = {"local_paths": srv.cfg["library"]["local_paths"],
                   "lan_url": "http://%s:%d/library/" % (lan_ip(), srv.cfg["companion"]["port"]),
                   "sources_file": srv.cfg["library"].get("sources_file"),
                   "sources": ranked, "best": (srv.engine.best() or {}).get("name"),
                   "helpers": (_first_bridge(srv).helper_status() if _first_bridge(srv) else {})}
            return self._json(_with_peer_sources(srv, doc))
        if path.startswith("/api/hash/"):
            raw = unquote(path[len("/api/hash/"):])
            rk = registry_key(srv.library.file_registry, raw)
            if not rk:
                return self._json({"error": "unknown key"}, 404)
            p = srv.library.file_registry[rk]
            try:
                return self._json({"key": rk, "sha256": sha256_of(p, srv.hashes), "size": os.path.getsize(p)})
            except OSError as e:
                return self._json({"error": str(e)}, 500)
        if path.startswith("/api/manifest/"):
            return self._manifest(unquote(path[len("/api/manifest/"):]))
        if path == "/api/cheats/paths":
            b = _first_bridge(srv)
            if not b:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            info = b.cheat_paths() or {}
            # How to actually get files onto the console. FTP is the fallback now, not the
            # route - so name our own file API first and only mention FTP when one is running.
            port = int(info.get("shop_port") or b.cfg.get("console", {}).get("shop_port", 8710))
            info["upload"] = "http://%s:%d/api/fs/write?path=<destination>" % (b.ip, port)
            live_ftp = b._ftp_port if b.ftp_ok(timeout=1.0) else None
            info["ftp"] = ("ftp://%s:%d" % (b.ip, live_ftp)) if live_ftp else ""
            info["ftp_port"] = live_ftp or 0
            return self._json(info)
        if path == "/api/cheats/rescan":
            b = _first_bridge(srv)
            if not b:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            return self._json(b.cheat_rescan() or {"ok": False})
        if path == "/api/rest/prepare":
            # A plain GET, so a hidden <img src> on a hostile page could stop every homebrew
            # payload on the console. The Origin/Referer guard is what makes that impossible; an
            # <img> from a public page carries that page's Referer.
            if not self._origin_ok():
                return self._json({"ok": False, "error": "forbidden_origin",
                                   "message": "Cross-site request refused."}, 403)
            # Stopping the console's payloads is most useful from HERE — you are about to walk
            # away and rest the console, so driving it from the PC or a phone beats having to
            # open the app on the PS5 first. Relay it; the console does the actual work.
            b = _first_bridge(srv)
            if not b:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            q = urlparse(self.path).query
            # Establish FIRST that the console is actually there. The call below quits the app on
            # the console as its last step, so a dropped connection is the normal ending — but only
            # if it was reachable to begin with. Without this check an offline PS5 would report
            # "stopped, safe to rest", which is the one wrong answer this button must never give.
            try:
                b._shop("/api/health", timeout=6)
            except Exception as e:
                return self._json({"ok": False,
                                   "error": "the PS5 app is not reachable (%s)" % e}, 502)
            try:
                return self._json(b._shop("/api/rest/prepare" + ("?" + q if q else ""), timeout=45))
            except Exception as e:
                return self._json({"ok": True, "self_quit": True, "detached": True,
                                   "note": "the console shut the app down while answering (%s)" % e})
        if path == "/api/payloads/autostart":
            # Helper auto-start is a console setting, so this is only ever a relay. Without it the
            # button 404s here and looks broken, even though it works on the PS5 itself — and the
            # whole point of the toggle is being able to flip it from a PC or a phone.
            b = _first_bridge(srv)
            if not b:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            # Pass the whole query through rather than one named parameter: the console owns this
            # setting and now takes `delay` as well as `on`, and a relay that only knew about `on`
            # would silently drop it.
            q = urlparse(self.path).query
            try:
                # Tiny call. A console that cannot answer in 5s is not there, and waiting longer
                # only leaves the button visible-but-doomed for that much more of the panel's life.
                return self._json(b._shop("/api/payloads/autostart" + ("?" + q if q else ""),
                                          timeout=5))
            except Exception as e:
                return self._json({"ok": False, "error": "console did not answer: %s" % e}, 502)
        if path.startswith("/api/mods/"):
            tid = unquote(path[len("/api/mods/"):]).strip("/")
            b = _first_bridge(srv)
            if not b:
                return self._json({"ok": False, "error": "no console configured"}, 400)

            # What version is actually installed? Look this up FIRST so our engine can pick the
            # exactly-matching cheat file instead of settling for whatever version it finds.
            installed_ver = ""
            for a in (b.console_apps() or []):
                if a.get("title_id") == tid:
                    installed_ver = a.get("app_ver") or ""
                    break

            # OUR engine answers first, for ANY title. When the game is running we also get its
            # pid/base, so mods report live on/off state and can be toggled; when it is not we
            # still list them (read-only) instead of pretending the title has no cheats.
            # CheatRunner is a last resort, never a requirement.
            mine = b.mutant_running(installed_ver)
            live = bool(mine and mine.get("title_id") == tid and mine.get("cheat_file"))
            src = mine if live else (b.mutant_find(tid, installed_ver) or {})
            if src.get("cheat_file"):
                pid = mine.get("pid") if live else None
                base = mine.get("base") if live else None
                doc = b.mutant_mods(src["cheat_file"], pid, base) or {}
                if doc.get("ok"):
                    fv = doc.get("version") or cheat_file_version(src["cheat_file"])
                    # An UNKNOWN installed version is not the same as a mismatch. PS5 titles
                    # often expose no APP_VER, and treating that as incompatible hid every
                    # cheat for them. Only flag a mismatch when we can actually prove one.
                    compatible = (not installed_ver) or bool(src.get("exact")) or (fv == installed_ver)
                    reason = src.get("match", "")
                    if not installed_ver and reason == "other version":
                        reason = "installed version unknown"
                    return self._json({
                        "ok": True, "reachable": True, "engine": "mutant", "title_id": tid,
                        "installed_version": installed_ver, "file_version": fv,
                        "file": src["cheat_file"].rsplit("/", 1)[-1],
                        "path": src["cheat_file"],
                        "format": doc.get("format") or _cheat_format(src["cheat_file"]),
                        "compatible": compatible, "reason": reason,
                        "running": live, "pid": pid, "base": base,
                        "mods": [{"index": m.get("index"), "name": m.get("name"),
                                  "entries": m.get("entries"),
                                  "state": m.get("state") or "unknown",
                                  "on": bool(m.get("on")),
                                  "conflict": (m.get("state") == "partial"),
                                  # Toggling writes into the game's memory, so it needs the game
                                  # running. Listing does not.
                                  "can_toggle": live, "conflicts_with": []}
                                 for m in (doc.get("mods") or [])],
                        # Game patches live in their own XML library, so they are fetched
                        # separately — this was a hardcoded [] and kept 376 files invisible.
                        "patches": b.mutant_patches(tid, installed_ver), "candidates": [],
                        "versions": doc.get("versions") or []})
            cr = b.cheats
            if not cr.alive():
                # Our own engine already had its turn above, so reaching here means we simply
                # have no cheat file for this title -- not that anything is offline.
                # A title with no CHEATS can still have game PATCHES (Dark Souls Remastered is
                # exactly that), so ask for those before declaring there is nothing here.
                pat = b.mutant_patches(tid, installed_ver)
                return self._json({"ok": bool(pat), "reachable": True, "engine": "mutant",
                                   "error": "" if pat else "no_local_cheat_found",
                                   "title_id": tid, "installed_version": installed_ver,
                                   "mods": [], "patches": pat, "candidates": []})
            found = cr.find(tid)
            cands = []
            for c in (found.get("candidates") or []):
                fv = cheat_file_version(c.get("filename", ""))
                cands.append(dict(c, file_version=fv,
                                  compatible=(not installed_ver or not fv or fv == installed_ver)))
            best = found.get("path") or ""
            best_ver = cheat_file_version(found.get("filename", ""))
            doc = cr.doc(tid) if found.get("ok") else {}
            st = cr.state(tid) if found.get("ok") else {}
            # CheatRunner reports each mod as state/label ("on" | "off" | "conflict") plus
            # can-toggle flags and a conflict list. Reading .on/.enabled (which do not exist)
            # made every mod look disabled even after it had been turned on.
            by_index = {}
            for row in (st.get("mods") or []):
                if isinstance(row, dict) and row.get("index") is not None:
                    by_index[int(row["index"])] = row
            return self._json({"ok": bool(found.get("ok")), "reachable": True, "title_id": tid,
                               "installed_version": installed_ver,
                               "file": found.get("filename", ""), "path": best,
                               "file_version": best_ver, "format": found.get("format", ""),
                               "compatible": (not installed_ver or not best_ver or best_ver == installed_ver),
                               "reason": found.get("selectedReason", ""),
                               "candidates": cands,
                               "mods": [_merge_mod(i, m, by_index.get(i, {}))
                                        for i, m in enumerate(doc.get("mods") or [])],
                               "patches": doc.get("patches") or [],
                               "error": found.get("error", "")})
        if path == "/api/cheats/library":            # what we ship vs what the console has
            bridge = _first_bridge(srv)
            if bridge is None:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            return self._json(bridge.cheat_library_status())
        # ^ MUST stay above the prefix route below. /api/cheats/<title-id> is a startswith match,
        # so it swallowed /api/cheats/library and answered it as a title whose id is the literal
        # string "library" - {"title_id":"library","cheats":[],"patches":[]}. The settings panel
        # reads this to show what the cheat library holds, so it always showed nothing.
        # Same trap the /api/cred vs /api/credscan pair hit before.
        if path.startswith("/api/cheats/"):
            tid = unquote(path[len("/api/cheats/"):])
            b = srv.fleet.bridge(srv.fleet.ids()[0]) if srv.fleet.consoles else None
            res = b.console_cheats(tid) if b else None
            return self._json({"title_id": tid, "reachable": res is not None,
                               "cheats": (res or {}).get("cheats", []), "patches": (res or {}).get("patches", [])})
        if path == "/api/queue":
            return self._json({"tasks": srv.queue.snapshot()})
        if path == "/api/dpi/reload":                # the dock's manual reload button
            return self._dpi_reload()
        # [B9] GET twins of the DPI controls. The on-console build is GET-only, so serving both verbs
        # here lets one UI call work whether the page came from the PC or from the PS5 itself.
        return self._static(path.lstrip("/"))

    # ---------------------------------------------------------------- request origin guard
    # WHY THIS EXISTS. Both servers answer with Access-Control-Allow-Origin: * and neither has any
    # authentication - deliberately, because everything on a jailbroken console is open and
    # Payload Manager on :8084 will load an arbitrary kernel-privileged ELF for anyone who asks.
    # Hardening against someone already ON the LAN is therefore mostly theatre.
    #
    # What is NOT theatre is a page on the public internet. Any site the user visits, in any
    # browser on this network, can fire a cross-origin request at http://<this-pc>:8710/... and
    # our handlers would run it. That needs no LAN access and no knowledge of the user - just a
    # visit to a hostile page while the companion is running.
    #
    # The discriminator that works here is NOT "same origin" - the console serves the UI from
    # 10.0.0.99 and legitimately calls this PC cross-origin, which is the whole architecture. It
    # is whether the caller's origin is on the LAN AT ALL. A hostile public site has a DNS
    # hostname; every legitimate caller here is a private-range IP literal or loopback.
    #
    # Absent Origin AND Referer is allowed on purpose: that is curl, our own tooling, and
    # server-to-server calls, none of which a remote page can forge.
    _PRIVATE_HOST = re.compile(
        r"^(?:localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|\[::1\]|::1)$")

    def _origin_ok(self):
        for hdr in ("Origin", "Referer"):
            v = (self.headers.get(hdr) or "").strip()
            if not v or v == "null":
                continue
            try:
                host = urlparse(v).hostname or ""
            except ValueError:
                return False
            if not self._PRIVATE_HOST.match(host):
                return False        # a public site is talking to us - refuse
            return True             # first present header decides
        return True                 # no Origin and no Referer: not a browser page

    def do_POST(self):
        path, srv = urlparse(self.path).path, self.server
        if not self._origin_ok():
            return self._json({"ok": False, "error": "forbidden_origin",
                               "message": "Cross-site request refused."}, 403)
        body = self._body()

        # ---- mods / cheats / patches — OUR Mutant engine first, CheatRunner only as a fallback ----
        mm = re.match(r"^/api/mods/([^/]+)/(select|toggle|apply|disable-all)$", path)
        if mm:
            tid, act = unquote(mm.group(1)), mm.group(2)
            b = _first_bridge(srv)
            if not b:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            body = body or {}
            force = bool(body.get("force"))

            # This block used to open with `if not cr.alive(): return cheatrunner_offline`.
            # That gate ran BEFORE anything else, so every toggle from the PC answered
            # "CheatRunner is not running on the PS5 (:9999)" — on a console whose own ELF
            # implements the entire engine and had already listed the mods on the GET above.
            # The mods panel could read state but never change it. Our engine goes first now,
            # exactly like the GET path, and CheatRunner stays what the comment always claimed
            # it was: a last resort, never a requirement.
            mine = b.mods_action(tid, act, body)
            if not isinstance(mine, dict):
                mine = None
            if mine is not None:
                # Our engine answered — including its own honest refusals ("game_not_running",
                # or a refusal to write because the game's code does not match the cheat file).
                # Only an outright "I have no cheat file for this title" is worth asking
                # CheatRunner about, and only if it happens to be running.
                if not (mine.get("error") == "no_local_cheat_found" and b.cheats.alive()):
                    return self._json(mine)

            cr = b.cheats
            if not cr.alive():
                return self._json({"ok": False, "error": "engine_unreachable",
                                   "message": "The PS5 app is not answering, so mods cannot be "
                                              "changed. Load PKG-MUTANT-SHOP.elf on the console "
                                              "and try again."}, 503)
            if act == "select":
                # Refuse a version-mismatched file unless explicitly forced: cheats built for
                # another build are the usual cause of in-game crashes.
                fv = cheat_file_version(body.get("file") or body.get("path") or "")
                iv = ""
                for a in (b.console_apps() or []):
                    if a.get("title_id") == tid:
                        iv = a.get("app_ver") or ""
                        break
                if fv and iv and fv != iv and not force:
                    return self._json({"ok": False, "error": "version_mismatch",
                                       "file_version": fv, "installed_version": iv,
                                       "message": "This cheat file is for v%s but v%s is installed. "
                                                  "Loading it anyway can crash the game." % (fv, iv)}, 409)
                r = cr.select(tid, body.get("path") or "", force=force)
            elif act == "toggle":
                mine = b.mutant_running()
                if mine and mine.get("title_id") == tid and mine.get("cheat_file"):
                    # our engine: writes the bytes itself and toasts on the TV
                    r = b.mutant_apply(mine["cheat_file"], int(body.get("index", 0)),
                                       bool(body.get("on")), mine["pid"],
                                       int(str(mine["base"]), 16), body.get("name") or "",
                                       force=force)
                    return self._json(r if isinstance(r, dict) else {"ok": False})
                r = cr.toggle(tid, int(body.get("index", 0)), bool(body.get("on")))
                if isinstance(r, dict) and r.get("ok"):
                    b.notify("%s: %s" % ("ON" if body.get("on") else "OFF",
                                         (body.get("name") or "Mod")[:56]))
            elif act == "apply":
                r = cr.apply_patch(tid, index=body.get("index"), entry_id=body.get("entry_id"),
                                   force=force)
                if isinstance(r, dict) and r.get("ok"):
                    b.notify("Patch applied: %s" % (body.get("name") or tid)[:56])
            else:
                r = cr.disable_all(tid)
                if isinstance(r, dict) and r.get("ok"):
                    b.notify("All mods disabled")
            return self._json(r if isinstance(r, dict) else {"ok": False})
        if path == "/api/config":
            srv.cfg = coerce_config(deep_merge(srv.cfg, body))
            save_config(srv.cfg)
            srv.library.cfg = srv.cfg
            srv.fleet.cfg = srv.cfg
            # deep_merge returns a NEW dict, so anything still holding the old one keeps reading
            # pre-save values. The queue was the one that got missed - it bound self.cfg when it
            # was constructed, so integrity and parallelism settings only took effect on restart.
            srv.queue.cfg = srv.cfg
            srv.fleet.reload()
            srv.engine.set_sources(assemble_sources(srv.cfg))
            return self._json({"ok": True})
        if path == "/api/rescan":
            srv.library.scan()
            return self._json({"ok": True, "count": len(srv.library.games), "empty": srv.library.is_empty,
                               "gen": srv.library.gen, "last_scan_ms": srv.library.last_scan_ms})
        if path == "/api/library/prepare":                              # [B6] create Games/PS4|PS5 tree
            root = (body.get("root") or "").strip()
            if not root:
                paths = srv.cfg.get("library", {}).get("local_paths", [])
                root = paths[0] if paths else ""
            if not root:
                return self._json({"ok": False, "error": "no library root — set library.local_paths or pass root"}, 400)
            res = ensure_library_tree(root)
            if res.get("ok"):
                srv.library.scan()
            return self._json(res)
        if path == "/api/open-folder":
            opened = []
            for p in srv.cfg["library"].get("local_paths", []):
                if not os.path.isdir(p):
                    continue
                try:
                    if sys.platform == "win32":
                        os.startfile(p)                       # opens Explorer on the companion PC
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", p])
                    else:
                        subprocess.Popen(["xdg-open", p])
                    opened.append(p)
                except Exception as e:
                    print("[open-folder] %s" % e)
            return self._json({"ok": True, "opened": opened})
        if path == "/api/notify":
            b = _first_bridge(srv)
            return self._json({"ok": bool(b and b.notify(body.get("text", "")))})
        if path == "/api/move":
            # A console-owned operation: it copies the container between the console's OWN drives,
            # verifies, then removes the original. The companion only forwards - it has no business
            # knowing console paths, and the console can finish the job with this PC switched off.
            b = _first_bridge(srv)
            if b is None:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            try:
                return self._json(b._shop("/api/move", timeout=30, data={
                    "path": body.get("path") or "",
                    "drive": body.get("drive") or "",
                    "name": body.get("name") or "",
                }) or {})
            except ShopHTTPError as e:
                return self._json({"ok": False, "error": e.sentence("The console refused the move")}, 502)
            except Exception:
                return self._json({"ok": False,
                                   "error": "PKG MUTANT SHOP on the PS5 did not answer - load it "
                                            "again from Payload Manager"}, 502)
        if path == "/api/game/delete":
            # Remove a PS5 backup container from THE CONSOLE. Proxied, never reimplemented: the
            # console owns the scan that knows where the container is, and keeping the lookup there
            # means this PC never sends a path - so there is nothing here that could be pointed at
            # the local library by accident.
            b = _first_bridge(srv)
            if b is None:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            tid = (body.get("title_id") or "").strip()
            if not tid:
                return self._json({"ok": False, "error": "which game? no title id was given"}, 400)
            try:
                r = b._shop("/api/game/delete-backup?tid=%s" % quote(tid, safe=""), timeout=60) or {}
            except ShopHTTPError as e:
                return self._json({"ok": False, "error": e.sentence("The console refused")}, 502)
            except Exception as e:
                return self._json({"ok": False,
                                   "error": "PKG MUTANT SHOP on the PS5 did not answer - load it "
                                            "again from Payload Manager",
                                   "detail": repr(e)[:200]}, 502)
            if r.get("deleted"):
                # The library's record of it is now wrong in two places; make both re-read.
                try:
                    b.installed_titles(force=True)
                except Exception:
                    pass
                srv.library.scan()
            return self._json(r)
        if path == "/api/open-ps5":
            b = _first_bridge(srv)
            if b is None:
                return self._json({"ok": False,
                                   "error": "no console configured"}, 400)
            # Default to OUR address, not the console's loopback: opening 127.0.0.1 on the console
            # gives the on-console UI, which cannot install anything that lives on this PC.
            url = body.get("url") or ("http://%s:%d/" % (lan_ip(), srv.cfg["companion"]["port"]))
            return self._json(b.open_shop(url))
        if path == "/api/transfer":
            # Pull a game from another PC into this one's library folder.
            key = body.get("install_key")
            if not key:
                return self._json({"ok": False, "error": "need install_key"}, 400)
            # ...unless a different PC was chosen as the destination. The copy has to be run BY the
            # machine that is going to hold the file, so hand the same request to it. Without this
            # the only possible destination was whichever PC you happened to have the app open on.
            dest_pc = body.get("to") or body.get("dest_pc")
            if dest_pc:
                me = device_identity(srv.cfg)
                mine = {str(me.get("id")), str(me.get("name")), lan_ip(), "local", "this"}
                if str(dest_pc) not in mine:
                    reg = getattr(srv, "peers", None)
                    target = None
                    for p in (reg.known() if reg is not None else []):
                        if str(dest_pc) in (str(p.get("id")), str(p.get("name")), str(p.get("lan_ip"))):
                            target = p
                            break
                    if not target or not target.get("url"):
                        return self._json({"ok": False, "error": "unknown destination PC %r" % dest_pc}, 404)
                    fwd = dict(body)
                    fwd.pop("to", None)
                    fwd.pop("dest_pc", None)
                    try:
                        rq = urllib.request.Request(
                            target["url"].rstrip("/") + "/api/transfer", method="POST",
                            data=json.dumps(fwd).encode("utf-8"),
                            headers={"Content-Type": "application/json"})
                        with urllib.request.urlopen(rq, timeout=25) as rr:
                            ans = json.loads(rr.read().decode("utf-8", "replace"))
                        ans["on_pc"] = target.get("name")
                        return self._json(ans)
                    except Exception as e:
                        return self._json({"ok": False,
                                           "error": "%s did not take the copy: %s"
                                                    % (target.get("name") or dest_pc, e)}, 502)
            item = src_game = None
            for cand in build_federated_library(srv).get("games", []):
                for it in (cand.get("base") or []) + (cand.get("updates") or []) + (cand.get("dlc") or []):
                    if it.get("install_key") == key and it.get("peer_url"):
                        item, src_game = it, cand
                        break
                if item:
                    break
            if not item:
                return self._json({"ok": False, "error": "that game is not on another PC "
                                                        "(or it is already here)"}, 404)
            plat = (src_game.get("platform") or "PS4").upper()
            root = srv.cfg.get("library", {}).get("root") or LIBRARY_ROOT
            dest = os.path.join(root, plat if plat in LIBRARY_LAYOUT else "")
            job = srv.queue.add({"name": src_game.get("name") or item.get("file"),
                                 "title_id": src_game.get("title_id"), "kind": item.get("kind", "base"),
                                 "lane": "pc-copy", "console": (srv.fleet.ids() or ["ps5"])[0],
                                 "url": item["peer_url"], "dest": dest,
                                 "file": item.get("file") or os.path.basename(item["peer_url"]),
                                 "from_pc": src_game.get("source_pc"),
                                 "total": item.get("size") or 0,
                                 "hold": body.get("mode") == "queued"})
            return self._json({"ok": True, "id": job, "lane": "pc-copy",
                               "from": src_game.get("source_pc"), "dest": dest})
        if path == "/api/install":
            return self._install(body)
        if path == "/api/hosts/cleanup":             # kept as a no-op: see the reply
            bridge = _first_bridge(srv)
            if bridge is None:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            # There is nothing to clean up. This used to stop Elf Arsenal and the daemons it
            # spawned so they would stop fighting etaHEN for :12800. This app no longer ships,
            # starts or talks to either, and it binds nothing on that port itself - so if
            # something IS on 12800 it is a payload the user loaded, and stopping it is their
            # decision, not ours.
            return self._json({"ok": True, "stopped": [], "survived": [],
                               "install_host": "our own engine",
                               "message": "Nothing to do - this app does not use a separate "
                                          "install payload any more."})
        if path == "/api/cheats/sync":               # push only what the console is missing
            bridge = _first_bridge(srv)
            if bridge is None:
                return self._json({"ok": False, "error": "no console configured"}, 400)
            return self._json(bridge.sync_cheat_library(
                log=lambda m: print("[cheats] %s" % m),
                budget=float(body.get("budget_seconds") or 0) or None))
        if path == "/api/queue/clear":
            srv.queue.clear_done()
            return self._json({"ok": True})
        if path == "/api/queue/start":                                  # [B8] release held tasks, small-first
            return self._json({"ok": True, "started": srv.queue.start_held()})
        if path == "/api/dpi/reload":                # POST twin of the GET above
            return self._dpi_reload()
        # /api/dpi/status was here. It reported whether a third-party install daemon was clean,
        # wedged or down. Nothing in the UI ever called it and there is no such daemon now.
        m = re.match(r"^/api/queue/([^/]+)/cancel$", path)
        if m:
            return self._json({"ok": srv.queue.cancel(m.group(1))})
        m = re.match(r"^/api/queue/([^/]+)/dismiss$", path)
        if m:
            return self._json({"ok": srv.queue.dismiss(m.group(1))})
        m = re.match(r"^/api/queue/([^/]+)/retry$", path)               # [wedge-retry]
        if m:
            return self._json({"ok": srv.queue.retry(m.group(1))})
        return self._json({"error": "not found"}, 404)

    def _dpi_reload(self):
        """Manual "reload the install engine" — the dock's tag. This route did not exist: the UI
        called it, got 404 "not found", and reported "Reload failed" every single time, while three
        different install-failure messages told the user to press it. It is the same recovery the
        queue runs by itself before a handoff, so it is exposed rather than reimplemented.

        There is nothing to reload: our installer is spawned per install and has already exited by
        the time anyone presses this. What CAN be stale is the hand-over state, so that is what it
        clears - and it says so, rather than reporting a reload that did not happen."""
        srv = self.server
        b = _first_bridge(srv)
        if b is None:
            return self._json({"ok": False, "error": "no console configured"}, 400)

        # This control used to call recover_dpi(), which relaunched host_payload_path() - the
        # etaHEN copy the app shipped - so pressing it resurrected the very daemon the user had
        # removed. There is only one body now and it cannot start anything.
        if True:
            st = b.spawn_status() or {}
            b.spawn_cleanup()
            ready = False
            try:
                ready = bool((b._shop("/api/health", timeout=5) or {}).get("engine_ready"))
            except Exception:
                ready = False
            return self._json({
                "ok": ready, "host": "pms-spawn", "reloaded": False, "cleanup": "spawn-state",
                "detail": ("Our engine is started fresh for every install, so there is nothing to "
                           "reload. Cleared the leftover install state" +
                           (" (one was still marked in flight)" if st.get("busy") else "") +
                           (" - ready for the next install." if ready else
                            " - but Payload Manager is not answering on :8084, so the next install "
                            "cannot start. Reload it on the PS5."))})


    def _manifest(self, title_id):
        srv = self.server
        g = srv.library.find_game(title_id)
        if not g:
            return self._json({"error": "unknown title"}, 404)
        files = []
        for kind in ("base", "updates", "dlc"):
            for it in g.get(kind if kind != "updates" else "updates", []):
                p = srv.library.file_registry.get(it.get("install_key"))
                files.append({"file": it["file"], "kind": it["kind"], "size": it["size"],
                              "version": it.get("version"), "install_key": it.get("install_key"),
                              "sha256": cached_sha(p, srv.hashes) if p else None})
        return self._json({"title_id": title_id, "name": g["name"], "platform": g["platform"],
                           "region": g["region"], "files": files})

    def _space_refusal(self, body, targets):
        """A sentence ONLY when the package cannot fit on any connected drive - otherwise None.

        WHERE A PKG LANDS IS THE CONSOLE'S DECISION. Sony's installer takes it from Installation
        Location (Settings > Storage), which the user sets by hand and which no API reports. An
        earlier version of this tried to infer it from where the console last installed something;
        that inference goes stale the instant the setting is changed, and it refused a perfectly
        good install seconds after the user had switched to Extended Storage on purpose.

        So this does not try to know the destination. It answers only the question that needs no
        inference - "is there anywhere on this console it could possibly go?" - and stays silent for
        everything else. A preflight that guesses is worse than none: it blocks work that would
        have succeeded."""
        need = int(body.get("size") or 0)
        if need <= 0:
            return None
        for cid in targets:
            b = self.server.fleet.bridge(cid)
            if not b:
                continue
            try:
                devs = (b._shop("/api/devices", timeout=8) or {}).get("ps5") or []
            except Exception:
                return None                          # cannot ask -> do not stand in the way
            live = [x for x in devs if x.get("detected") and x.get("free")]
            if not live:
                return None                          # nothing to compare against -> say nothing
            # A little room to work in: the installer writes alongside what it is unpacking.
            roomiest = max(live, key=lambda x: int(x.get("free") or 0))
            if need + (1 << 30) <= int(roomiest["free"]):
                continue                             # it fits somewhere - the console picks where
            return ("%s needs %s and no drive on the PS5 has that much free. The most room is %s "
                    "on %s - free some space, or send it to a drive with room."
                    % (body.get("name") or "This game", human_size(need),
                       human_size(roomiest.get("free")), roomiest.get("label") or roomiest.get("id")))
        return None

    def _install(self, body):
        srv = self.server
        url, key = body.get("url"), body.get("install_key")
        target = body.get("console", "")
        all_ids = srv.fleet.ids()
        targets = all_ids if target == "all" else [target] if target in all_ids else (all_ids[:1] or ["ps5-0"])

        # (Legacy "Send to PS5" FTP + on-console engine install removed — every game now installs
        #  through the single install-host path below: serve over LAN -> hand off the URL.)

        # A patch/DLC/backport can only be applied on top of an installed base game — the console
        # rejects it otherwise. Enforce it here too, not just in the UI, so the rule holds for any
        # caller and the user gets a reason instead of a failed job in the queue.
        kind = (body.get("kind") or "").lower()
        tid = body.get("title_id")

        # An add-on has to go where its base game already lives — installing a patch to a
        # different drive than the game is a guaranteed failure, and the user should not have to
        # remember which drive they picked. Override whatever the drive selector says.
        # Do not reinstall something that is already there. For a base game this is not merely
        # wasteful: a registered title makes the console skip the re-pull entirely and keep the old
        # files, which is the stale_install trap. Pass force:true to override deliberately.
        # WILL IT EVEN FIT? Checked before anything is queued, because the console's own answer
        # for "no" arrives as a bare SCE code after the user has waited - and for the case that
        # actually happens, an 85 GB package onto a drive with 20 GB free, it arrives with no bgft
        # row and a message that blames the file. force:true is still honoured; this only refuses
        # what cannot work.
        # NOT FOR ADD-ONS. A patch or DLC is small and the console has already committed the space
        # for the game it belongs to, so the flat headroom below can refuse one that would have
        # installed - and there is no force button for no_space in the UI, so that is a dead end.
        if not body.get("force") and kind not in ("update", "patch", "dlc", "backport"):
            try:
                refusal = self._space_refusal(body, targets)
            except Exception:
                refusal = None
            if refusal:
                return self._json({"ok": False, "error": "no_space", "message": refusal}, 409)

        if tid and not body.get("force"):
            skip_reasons = []
            for cid in targets:
                b = srv.fleet.bridge(cid)
                if not b:
                    continue
                why = b.already_installed(kind or "base", tid,
                                          version=body.get("version") or "",
                                          content_id=body.get("content_id") or "",
                                          pkg_bytes=body.get("size") or 0)
                if why:
                    skip_reasons.append(why)
            if skip_reasons and len(skip_reasons) == len([c for c in targets if srv.fleet.bridge(c)]):
                return self._json({"ok": True, "skipped": True, "ids": [],
                                   "reason": "already_installed", "message": skip_reasons[0]})

        if kind in ("update", "patch", "dlc", "backport") and tid:
            for cid in targets:
                b = srv.fleet.bridge(cid)
                if not b:
                    continue
                try:
                    for a in (b.console_apps() or []):
                        if a.get("title_id") == tid and a.get("installed_drive"):
                            body["drive"] = drive_id_for_label(a.get("installed_drive"))
                            break
                except Exception:
                    pass
                if body.get("drive"):
                    break
        if kind in ("update", "patch", "dlc", "backport") and tid:
            installed = set()
            for cid in targets:
                b = srv.fleet.bridge(cid)
                if b:
                    try:
                        installed |= set(b.installed_titles() or [])
                    except Exception:
                        pass
            installed |= set(local_installed() or [])
            if tid not in installed:
                return self._json({"ok": False, "error": "base_not_installed", "title_id": tid,
                                   "message": "Install the base game first — a %s can only be applied "
                                              "on top of the installed game." % kind}, 409)

        # --- MOUNT lane: a ShadowMount backup (.ffpfsc etc.) -> FTP to the scan folder, not DPI ---
        # A registered path that is a DIRECTORY is a game stored unpacked — it mounts exactly like a
        # container, so it belongs in this lane too rather than falling through to the PKG installer.
        if key and key in srv.library.file_registry and (
                srv.library.file_registry[key].lower().endswith(MOUNT_EXTS)
                or os.path.isdir(srv.library.file_registry[key])):
            path = srv.library.file_registry[key]
            dest = mount_dest_for_drive(srv.cfg, body.get("drive") or body.get("storage"))
            jobs = [srv.queue.add({"name": body.get("name", os.path.basename(path)),
                                   "title_id": body.get("title_id"), "kind": "backup", "lane": "mount",
                                   "console": cid, "drive": (body.get("drive") or "ext1"),
                                   "local_path": path, "dest": dest,
                                   "hold": body.get("mode") == "queued",   # + Queue must not auto-start
                                   "total": srv.library.file_sizes.get(key, 0)}) for cid in targets]
            return self._json({"ok": True, "ids": jobs, "lane": "mount", "dest": dest, "consoles": targets})

        # --- CONSOLE-LOCAL lane: the package is already ON the PS5 (USB stick, external
        #     drive, internal storage). There is nothing to serve or transfer, so hand the
        #     path to the console app, which installs it with the same call it uses when the
        #     PC is switched off. Without this the key was simply not in the PC file registry
        #     and the request came back as "unknown install_key".
        if key and str(key).startswith("local:"):
            # The package is already ON the console, so nothing is transferred - but it still
            # goes through the QUEUE like every other install, so "+ Queue" holds it instead of
            # starting it immediately and the job is visible with the rest.
            local_path = str(key)[len("local:"):]
            if not (srv.fleet.bridge(targets[0]) if targets else _first_bridge(srv)):
                return self._json({"ok": False, "error": "no console configured"}, 400)
            jobs = [srv.queue.add({"name": body.get("name") or os.path.basename(local_path),
                                   "title_id": body.get("title_id"),
                                   "kind": body.get("kind") or "base", "lane": "console-local",
                                   "console": cid, "drive": body.get("drive") or "",
                                   "local_path": local_path,
                                   "hold": body.get("mode") == "queued",
                                   "total": body.get("size") or 0}) for cid in targets]
            return self._json({"ok": True, "ids": jobs, "lane": "console-local",
                               "dest": "console", "consoles": targets,
                               "queued": body.get("mode") == "queued"})

        # --- MULTI-PART: one release split across several packages ---
        # Installing only part 1 leaves the title broken, so every piece is queued, in order,
        # and the queue installs them one at a time per console. Works the same whichever
        # lane they end up in, because expansion happens before the lane is chosen.
        if key and body.get("_part_of") is None:
            parts_item = None
            for cand in srv.library.games:
                for it in (cand.get("base") or []) + (cand.get("updates") or []) + (cand.get("dlc") or []):
                    if it.get("install_key") == key and (it.get("parts") or []):
                        parts_item = it
                        break
                if parts_item:
                    break
            if parts_item and len(parts_item["parts"]) > 1:
                if parts_item.get("parts_gap"):
                    return self._json({"ok": False, "error": "missing_part",
                                       "message": "This release is split into parts and one is "
                                                  "missing, so installing it would leave the game "
                                                  "broken. Add every part and try again."}, 409)
                ids, failed, skipped = [], None, 0
                self._json_muted = True                 # collect, do not write - see _json()
                try:
                    for p in parts_item["parts"]:
                        sub = dict(body)
                        sub["install_key"] = p["install_key"]
                        sub["_part_of"] = key
                        sub["name"] = "%s (part %d/%d)" % (
                            body.get("name") or parts_item.get("name") or "Game",
                            p["part"], len(parts_item["parts"]))
                        # Only the FIRST part honours "install now"; the rest queue behind it so a
                        # console never has two parts of the same game in flight at once.
                        if ids:
                            sub["mode"] = "queued"
                        res = self._install(sub)
                        payload, code = res if isinstance(res, tuple) else (res, 200)
                        # Honour what each part actually said. This was thrown away: a part that
                        # was refused, or already installed, was counted as queued anyway.
                        if isinstance(payload, dict) and payload.get("skipped"):
                            skipped += 1
                        elif isinstance(payload, dict) and payload.get("ok"):
                            ids.extend(payload.get("ids") or [])
                        elif failed is None:
                            failed = (payload, code, p.get("part"))
                finally:
                    self._json_muted = False
                if failed is not None:
                    payload, code, partno = failed
                    msg = (payload or {}).get("message") or (payload or {}).get("error") or "refused"
                    return self._json({"ok": False, "multi_part": True,
                                       "error": (payload or {}).get("error") or "part_failed",
                                       "part": partno, "ids": ids,
                                       "message": "Part %s could not be queued (%s). The parts that "
                                                  "were queued are in the queue; a release is only "
                                                  "playable with all of them."
                                                  % (partno, msg)}, code if code >= 400 else 409)
                total_parts = len(parts_item["parts"])
                if skipped == total_parts:
                    return self._json({"ok": True, "skipped": True, "multi_part": True, "ids": [],
                                       "reason": "already_installed",
                                       "message": "Every part of this release is already installed."})
                return self._json({"ok": True, "multi_part": True, "ids": ids,
                                   "parts": total_parts, "skipped_parts": skipped,
                                   "message": "Queued %d part%s — they install one after the other."
                                              % (len(ids), "" if len(ids) == 1 else "s")})

        total, source, src = 0, "external", {"name": "external"}
        # --- PEER lane: the game lives on ANOTHER PC on this network ---
        # The console fetches it straight from that PC, so nothing is proxied through here and
        # the transfer does not depend on this machine staying awake. build_federated_library()
        # put the peer's own LAN url on the item; without this branch the key was simply not in
        # our file registry and the install came back as "unknown install_key".
        if not url and key and key not in srv.library.file_registry:
            g = None
            peer_base = ""
            peer_item = None
            for cand in build_federated_library(srv).get("games", []):
                for it in (cand.get("base") or []) + (cand.get("updates") or []) + (cand.get("dlc") or []):
                    if it.get("install_key") == key and it.get("peer_url"):
                        g, url, source = cand, it["peer_url"], (cand.get("source_pc") or "peer")
                        total = it.get("size") or 0
                        peer_base = it["peer_url"]
                        peer_item = it
                        break
                if url:
                    break
            # A backup does not go through the PKG installer — it has to reach the console's
            # ShadowMount folder. Locally that already works; from another PC the file simply was
            # not here yet, which is the only piece that was missing. Pull it into this library,
            # then hand it to the same mount lane a local backup uses. Both halves are the existing,
            # proven ones — this only runs them in order.
            # A backup has to reach the console's ShadowMount folder, and the PC that HOLDS it can
            # send it there itself — one transfer instead of two. Pulling it here first and pushing
            # it on afterwards moves a 100GB container across the network twice and, worse, looks
            # like the app is copying between PCs when the user asked for it on the PS5.
            # Ask the owner to run its own (already working) mount lane, and mirror its progress.
            if url and peer_item is not None and is_backup_item(peer_item, g) and peer_base:
                # Queue it HERE and nowhere else. The other PC is only asked to move the file, and
                # only at the moment this task actually runs — see _run_peer_delegate. Contacting it
                # at queue time and passing this queue's mode along was wrong: a game added with
                # "+ Queue" sat held on the other machine, so pressing Start here did nothing and the
                # app told you to go and start it over there. Which device you press the button on is
                # not something you should ever have to think about.
                # Queued as a MOUNT task carrying BOTH routes. _run_mount asks the console to
                # fetch `url` itself - one hop, straight from the PC that holds it, with nothing of
                # ours running there - and falls back to delegating through owner_url/owner_key if
                # this console's ELF is too old to have /api/engine/fetch. The decision is made when
                # the task RUNS, not now, because which console it lands on is not known here and a
                # queued job may not run for hours.
                _drive = body.get("drive") or body.get("storage") or "ext1"
                jobs = [srv.queue.add({
                    "name": body.get("name") or g.get("name") or peer_item.get("file"),
                    "title_id": g.get("title_id"), "kind": "backup", "lane": "mount",
                    "console": cid, "drive": _drive,
                    "dest": mount_dest_for_drive(srv.cfg, _drive),
                    "url": peer_base,                       # the console pulls from here
                    "remote_name": os.path.basename(unquote(peer_item.get("install_key") or "")),
                    "from_pc": g.get("source_pc"),
                    "owner_url": peer_base.split("/library/")[0],
                    "owner_key": peer_item.get("install_key"),
                    "hold": body.get("mode") == "queued",   # this queue's hold, honoured locally
                    "total": total}) for cid in targets]
                return self._json({"ok": True, "ids": jobs, "lane": "mount",
                                   "via": g.get("source_pc"), "pull": True,
                                   "drive": _drive})
            if url and peer_item is not None and is_backup_item(peer_item, g):
                plat = (g.get("platform") or "PS4").upper()
                root = srv.cfg.get("library", {}).get("root") or LIBRARY_ROOT
                dest_dir = os.path.join(root, plat if plat in LIBRARY_LAYOUT else "")
                drive = body.get("drive") or body.get("storage") or "ext1"
                jobs = [srv.queue.add({
                    "name": body.get("name") or g.get("name") or peer_item.get("file"),
                    "title_id": g.get("title_id"), "kind": "backup", "lane": "peer-mount",
                    "console": cid, "drive": drive,
                    "dest": dest_dir, "url": url,
                    "file": peer_item.get("file") or os.path.basename(url),
                    "from_pc": g.get("source_pc"),
                    "mount_dest": mount_dest_for_drive(srv.cfg, drive),
                    "hold": body.get("mode") == "queued",
                    "total": total}) for cid in targets]
                return self._json({"ok": True, "ids": jobs, "lane": "peer-mount",
                                   "from": g.get("source_pc"), "drive": drive,
                                   "dest": mount_dest_for_drive(srv.cfg, drive)})
            if url:
                # base_url lets served_url() derive the peer's /api/served/<key>, which is how
                # this lane gets real progress instead of sitting at 0%.
                root = peer_base.split("/library/")[0] if "/library/" in peer_base else ""
                src = {"name": source, "peer": True, "kind": "companion",
                       "base_url": (root + "/library") if root else ""}

        # --- INSTALL lane: PKG via DPI v2 ---
        if not url and key:
            if key not in srv.library.file_registry:
                return self._json({"error": "unknown install_key"}, 400)
            total = srv.library.file_sizes.get(key, 0)
            filename = os.path.basename(srv.library.file_registry[key])
            _tb = srv.fleet.bridge(targets[0]) if targets else None      # [B11] PC IP on the console's subnet
            lan_url = "http://%s:%d/library/%s" % (companion_ip_for(_tb.ip if _tb else None),
                                                   srv.cfg["companion"]["port"], key)
            resolved, src_name, _rank = srv.engine.resolve(filename)   # best reachable mirror, LAN fallback
            url, source = (resolved or lan_url), (src_name or "companion-lan")
            src = next((s for s in srv.engine.sources if s.get("name") == source), {"name": "companion-lan"})
        if not url:
            return self._json({"error": "need url or install_key"}, 400)
        local_progress = source == "companion-lan"     # local byte-count vs remote /api/served poll
        progress_url = None
        if not local_progress and key and (source_engine.is_companion(src) or src.get("peer")):
            progress_url = source_engine.served_url(src, key)
        measurable = local_progress or bool(progress_url)
        hold = body.get("mode") == "queued"                             # [B8] add-to-queue vs install-now
        jobs = [srv.queue.add({"name": body.get("name", "PKG"), "title_id": body.get("title_id"),
                               "kind": body.get("kind", "base"), "lane": "install", "hold": hold,
                               "drive": body.get("drive", "internal"), "url": url, "console": cid, "source": source,
                               "key": key if measurable else None, "total": total if measurable else 0,
                               # Remember that this URL points at US, so it can be rebuilt at
                               # submit time. Held overnight, the PC can move (DHCP, or Windows
                               # switching Ethernet to Wi-Fi) and every queued job would then hand
                               # etaHEN a URL on an address that no longer exists.
                               # Carried so the duplicate-submission guard can be overridden
                               # deliberately. `force` already bypasses the already-installed check
                               # above; it has to mean the same thing all the way down.
                               "force": bool(body.get("force")),
                               "relan": bool(local_progress and key),
                               "local_progress": local_progress, "progress_url": progress_url}) for cid in targets]
        return self._json({"ok": True, "ids": jobs, "url": url, "source": source, "consoles": targets})

    def _static(self, rel):
        rel = rel.split("?")[0]
        safe = os.path.normpath(rel).replace("\\", "/").lstrip("/")
        if ".." in safe.split("/"):
            return self._plain("bad path", 400)
        # A ".." check alone is NOT enough on Windows: os.path.join(WEB_DIR, "C:/Windows/win.ini")
        # throws WEB_DIR away entirely, because the drive letter makes the second argument absolute.
        # This handler is the catch-all for every unmatched GET, and the companion listens on
        # 0.0.0.0, so that turned "GET /C:/Windows/win.ini" into an arbitrary file read for anything
        # on the LAN (verified: HTTP 200 with the file body). Resolve the real path and require it
        # to be inside WEB_DIR - that covers drive letters, UNC paths and symlinks in one check.
        full = os.path.realpath(os.path.join(WEB_DIR, safe))
        try:
            if os.path.commonpath([full, os.path.realpath(WEB_DIR)]) != os.path.realpath(WEB_DIR):
                return self._plain("bad path", 400)
        except ValueError:                      # different drive entirely - never ours
            return self._plain("bad path", 400)
        if not os.path.isfile(full):
            return self._plain("web/index.html missing" if rel in ("", "index.html") else "not found",
                               500 if rel in ("", "index.html") else 404)
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return self._plain("read error", 500)
        self.send_response(200)
        self.send_header("Content-Type", guess_type(full))
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    THUMB_PX = 320          # ~1.5x the largest card so it stays crisp on a 4K TV

    def _thumb_for(self, tid):
        """Path to a card-sized cover for `tid`, generating it once. "" if we have no source.

        Never raises and never blocks the caller twice: on any failure it returns "" and the caller
        falls back to the full-size icon, so a missing codec or an odd PNG degrades to today's
        behaviour rather than a broken card.
        """
        if not tid or not re.match(r"^[A-Za-z0-9_-]{1,32}$", tid):
            return ""
        try:
            os.makedirs(THUMB_DIR, exist_ok=True)
        except OSError:
            return ""
        out = os.path.join(THUMB_DIR, tid + ".webp")
        srcp = os.path.join(ICON_DIR, tid + ".png")
        if os.path.isfile(out):
            # regenerate only if the source is newer than the thumb
            try:
                if not os.path.isfile(srcp) or os.path.getmtime(out) >= os.path.getmtime(srcp):
                    return out
            except OSError:
                return out
        if not os.path.isfile(srcp):
            b = _first_bridge(self.server)          # pull it off the console if we have never seen it
            if b:
                try:
                    b.console_icon(tid)
                except Exception:
                    pass
        if not os.path.isfile(srcp):
            return ""
        try:
            from PIL import Image
            im = Image.open(srcp)
            im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.thumbnail((self.THUMB_PX, self.THUMB_PX), Image.LANCZOS)
            tmp = out + ".part"                     # never let a reader see a half-written file
            im.save(tmp, "WEBP", quality=82, method=4)
            os.replace(tmp, out)
            return out
        except Exception as e:
            print("[thumb] %s: %r" % (tid, e))
            return ""

    def _art_response(self, path, data, ctype):
        """Serve cover art with a validator so a reload costs ~150 bytes instead of the file.

        Art for a title id never changes, so it is `immutable`; the ETag is still derived from
        size+mtime so a regenerated thumbnail is picked up immediately. Without this every icon
        was re-fetched in full once a day - up to 541,883 bytes each.
        """
        try:
            st = os.stat(path)
            etag = '"%x-%x"' % (st.st_size, int(st.st_mtime))
        except OSError:
            etag = ""
        if etag and (self.headers.get("If-None-Match") or "").strip() == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=604800, immutable")
            self._cors()
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=604800, immutable")
        if etag:
            self.send_header("ETag", etag)
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _serve_thumb(self, name):
        base = os.path.basename(name.split("?")[0])
        tid = re.sub(r"\.(webp|png|jpg)$", "", base, flags=re.I)
        path = self._thumb_for(tid)
        if not path:
            return self._serve_icon(tid + ".png")   # graceful: full size beats no cover
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self._plain("no thumb", 404)
        return self._art_response(path, data, "image/webp")

    def _serve_icon(self, name):
        base = os.path.basename(name.split("?")[0])
        full = os.path.join(ICON_DIR, base)
        if not os.path.isfile(full):
            # lazy-fetch the real icon from the console (base is "<title_id>.png")
            tid = base[:-4] if base.lower().endswith(".png") else base
            b = _first_bridge(self.server)
            if b:
                b.console_icon(tid)
        if not os.path.isfile(full):
            return self._plain("no icon", 404)
        with open(full, "rb") as f:
            data = f.read()
        return self._art_response(full, data, "image/png")

    def _serve_library(self, key):
        srv = self.server
        raw = key.split("?")[0]
        rk = registry_key(srv.library.file_registry, raw)
        if not rk or not os.path.isfile(srv.library.file_registry[rk]):
            return self._plain("unknown library file", 404)
        path = srv.library.file_registry[rk]
        size = os.path.getsize(path)
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                g1, g2 = m.group(1), m.group(2)
                if g1 == "" and g2:                 # suffix range: last N bytes
                    start, end = max(0, size - int(g2)), size - 1
                else:
                    if g1:
                        start = int(g1)
                    if g2:
                        end = min(int(g2), size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.end_headers()
                    return
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % os.path.basename(path))
        self.end_headers()
        # Two buckets: one per requesting console (what our own jobs read), and the shared
        # aggregate (what a peer companion polls, since it only knows the key).
        peer_ip = ""
        try:
            peer_ip = self.client_address[0]
        except Exception:
            peer_ip = ""
        agg = srv.transfers.setdefault(rk, {"max": 0, "total": size})
        agg["total"] = size
        tr = srv.transfers.setdefault("%s|%s" % (rk, peer_ip), {"max": 0, "total": size}) \
            if peer_ip else agg
        tr["total"] = size
        chunk = max(65536, int(srv.cfg.get("performance", {}).get("serve_chunk_bytes", 1048576)))   # [B4] tunable serve buffer
        with open(path, "rb") as f:
            f.seek(start)
            remaining, pos = length, start
            while remaining > 0:
                buf = f.read(min(chunk, remaining))
                if not buf:
                    break
                try:
                    self.wfile.write(buf)
                except (BrokenPipeError, ConnectionResetError):
                    return
                pos += len(buf)
                remaining -= len(buf)
                if pos > agg["max"]:
                    agg["max"] = pos          # aggregate, for peers polling by key alone
                if pos > tr["max"]:
                    tr["max"] = pos


def guess_type(path):
    p = path.lower()
    return ("text/html; charset=utf-8" if p.endswith(".html") else
            "application/javascript" if p.endswith(".js") else
            "text/css" if p.endswith(".css") else
            "application/json" if p.endswith(".json") else
            "image/png" if p.endswith(".png") else
            "image/svg+xml" if p.endswith(".svg") else "application/octet-stream")


# --------------------------------------------------------------------------- #
# main                                                                          #
# --------------------------------------------------------------------------- #
# ---- PS5 -> PC log channel -------------------------------------------------
# On-console payloads (e.g. the tile installer) open a TCP connection to this
# listener and send one diagnostic line at a time, so we can debug them from the
# PC without watching the TV. Read them via  GET /api/ps5-log.
PS5_LOG = []
PS5_LOG_LOCK = threading.Lock()
PS5_LOG_MAX = 1000


def ps5_log_add(line, src=""):
    ts = time.strftime("%H:%M:%S")
    with PS5_LOG_LOCK:
        PS5_LOG.append({"t": ts, "src": src, "line": line})
        if len(PS5_LOG) > PS5_LOG_MAX:
            del PS5_LOG[:len(PS5_LOG) - PS5_LOG_MAX]
    print("[PS5 %s] %s" % (ts, line))


def _ps5_log_conn(conn, addr):
    ip = addr[0] if addr else "?"
    buf = b""
    conn.settimeout(15)
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("utf-8", "replace").strip()
                if s:
                    ps5_log_add(s, src=ip)
        if buf.strip():
            ps5_log_add(buf.decode("utf-8", "replace").strip(), src=ip)
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def start_ps5_log_listener(host="0.0.0.0", port=9097):
    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((host, port))
        except OSError as e:
            print("[PS5-log] cannot bind :%d (%s) - is it already running?" % (port, e))
            return
        srv.listen(16)
        print("[PS5-log] listening on :%d (on-console payloads report here)" % port)
        while True:
            try:
                conn, addr = srv.accept()
            except OSError:
                break
            threading.Thread(target=_ps5_log_conn, args=(conn, addr), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()


def start_pc_register_thread(cfg, fleet):
    """Push our LAN address to the on-console shop server so the PS5 auto-finds the PC (2-way connect).
    Fire-and-forget; harmless when the on-console server isn't running."""
    port = cfg["companion"]["port"]
    oc_port = cfg.get("console", {}).get("onconsole_port", 8710)

    def loop():
        while True:
            for c in list(fleet.consoles):
                # Per console, not once for all of them. On a dual-homed PC (Ethernet on the
                # console's subnet, Wi-Fi holding the default route) lan_ip() returns the Wi-Fi
                # address, so the console was told to reach us on an address it may have no route
                # to - while the install URL, built with companion_ip_for(), correctly used the
                # Ethernet one. The console's own UI then pointed at the wrong PC.
                ip = companion_ip_for(c.get("ip"))
                try:
                    urllib.request.urlopen(
                        # State our version too: the console ranks companions by it, and until
                        # now it could only learn a version from a federation document read during
                        # a library merge - which a warm peer cache skips entirely.
                        "http://%s:%d/api/register-pc?ip=%s&port=%d&ver=%s"
                        % (c["ip"], oc_port, ip, port, VERSION),
                        timeout=2).read()
                except Exception:
                    pass
            time.sleep(8)

    threading.Thread(target=loop, daemon=True).start()


def run_tray(port):
    """Hidden background app: show a system-tray icon (right-click → Quit). Returns False if pystray
    isn't available so the caller just blocks instead."""
    try:
        import pystray
        from PIL import Image
        import webbrowser
    except Exception:
        return False
    try:
        img = Image.open(os.path.join(WEB_DIR, "assets", "icon.png"))
    except Exception:
        from PIL import Image as _I
        img = _I.new("RGBA", (64, 64), (18, 18, 18, 255))

    def do_open(icon, item):
        webbrowser.open("http://localhost:%d/" % port)

    def do_quit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Open PKG MUTANT SHOP", do_open, default=True),
        pystray.MenuItem("Quit", do_quit),
    )
    pystray.Icon("pkgmutantshop", img, "PKG MUTANT SHOP · By XavyProd", menu).run()
    return True


def sweep_temp_dbs(max_age_sec=3600):
    """Delete leftover pms_* temp DB pulls (bgft/app.db copies) + orphaned atomic-write temps older than
    max_age from the temp dir + companion dir. [B4]"""
    now = time.time()
    for d in (tempfile.gettempdir(), HERE):
        try:
            for fn in os.listdir(d):
                if not (fn.startswith("pms_") or fn.startswith(".pms_tmp_")):
                    continue
                p = os.path.join(d, fn)
                try:
                    if now - os.path.getmtime(p) > max_age_sec:
                        os.remove(p)
                except OSError:
                    pass
        except OSError:
            pass


def library_signature(cfg):
    """Cheap fingerprint of the library folders: sorted (path, size, mtime) for every .pkg/.ffpfsc. Changes
    only when a file is added / removed / modified — so we auto-rescan only when something really changed."""
    sig = []
    for root in cfg.get("library", {}).get("local_paths", []):
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                low = fn.lower()
                if not (low.endswith(".pkg") or low.endswith(MOUNT_EXTS)):
                    continue
                ap = os.path.join(dirpath, fn)
                try:
                    st = os.stat(ap)
                    sig.append((ap, st.st_size, int(st.st_mtime)))
                except OSError:
                    pass
    return tuple(sorted(sig))


def start_library_watch(httpd):
    """Debounced auto-rescan: poll the library signature; when it changes, wait for it to settle (so we
    never parse a half-copied PKG) then rescan. Gated by maintenance.auto_rescan (default on). [B4]"""
    # Always start the thread; the flag is read INSIDE the loop instead. It used to be read
    # here, once, so switching the setting ON did nothing until the next restart, and switching it
    # OFF did nothing at all - the loop it was meant to stop had already captured the old value.
    m = httpd.cfg.get("maintenance", {})
    interval = max(3, int(m.get("rescan_interval_sec", 10)))
    settle = max(0, int(m.get("rescan_settle_sec", 3)))

    def loop():
        last = library_signature(httpd.cfg)
        while True:
            time.sleep(interval)
            # Re-read every tick, from the LIVE config, so the setting takes effect when it is
            # changed rather than when the app is next started.
            if not httpd.cfg.get("maintenance", {}).get("auto_rescan", True):
                continue
            try:
                sig = library_signature(httpd.cfg)
                if sig == last:
                    continue
                if settle:
                    time.sleep(settle)
                    if library_signature(httpd.cfg) != sig:
                        last = sig          # still changing (big copy in progress) — recheck next tick
                        continue
                httpd.library.scan()        # atomic swap inside; safe for concurrent /library/
                last = sig
                print("[watch] library changed -> rescanned (gen=%d)" % httpd.library.gen)
            except Exception as e:
                print("[watch] %s" % e)

    threading.Thread(target=loop, daemon=True).start()


def start_cheat_sync_thread(httpd):
    """Self-repair the console's cheat library in the background.

    The library ships inside the exe, so the console should always have it — but a fresh console,
    a wiped /data, or an interrupted first sync leaves it short. This notices and fills the gap with
    no user action. It is incremental (only missing files) so the normal case costs one directory
    listing and sends nothing, and it is time-budgeted so it can never monopolise the FTP link that
    installs depend on: it stops after the budget and finishes on a later pass.
    """
    def loop():
        time.sleep(25)                       # let the library scan and the first UI poll settle
        while True:
            try:
                bridge = _first_bridge(httpd)
                if bridge is not None and bridge.engine_available():
                    st = bridge.cheat_library_status()
                    missing = st.get("missing_total")
                    if missing:
                        print("[cheats] console is missing %d file(s) — syncing" % missing)
                        r = bridge.sync_cheat_library(log=lambda m: print("[cheats] %s" % m),
                                                      budget=90.0)
                        print("[cheats] sent=%s failed=%s partial=%s in %ss"
                              % (r.get("sent"), r.get("failed"), r.get("partial"), r.get("seconds")))
            except Exception as e:
                print("[cheats] sync skipped: %r" % e)
            time.sleep(900)                  # re-check every 15 min; a healthy console is a no-op
    threading.Thread(target=loop, daemon=True).start()


# start_dpi_probe_thread() lived here. It polled every console's :12800 every 20 seconds to keep
# a "live / wedged / down" cache warm for the UI. There is no daemon on 12800 any more, so the
# thread had nothing to measure - and it was the last caller of dpi_host_kind() that no config
# setting could switch off, which made it the one thing keeping the whole third-party probe alive.


def _already_running(port):
    """Is another PKG MUTANT SHOP already serving on this port? Returns its version, or None.

    Asked BEFORE binding. Two copies on one port is not hypothetical - the loser of the bind race
    keeps every background thread running (library rescan, console probes, installed.json writes),
    which is how one process silently overwrites the other's record of what is installed.
    """
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=2) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        return str(j.get("version") or "?") if j.get("ok") else None
    except Exception:
        return None


def main():
    _log_open()                       # before anything else: the boot lines are worth keeping
    os.makedirs(ICON_DIR, exist_ok=True)
    cfg = _adopt_library_root(load_config())
    library = Library(cfg)
    library.scan()
    fleet = Fleet(cfg)
    # If the saved PS5 isn't reachable, auto-find it on the LAN so the app just works with no config.
    try:
        b0 = fleet.bridge(fleet.ids()[0]) if fleet.consoles else None
        if b0 is None or not b0.engine_available():
            found = discover_ps5(cfg)
            if found:
                newip = found[0]["ip"]
                confirmed = bool(found[0].get("confirmed"))
                cfg["ps5_ip"] = newip
                if cfg.get("consoles"):
                    for c in cfg["consoles"]:
                        c["ip"] = newip
                else:
                    cfg["consoles"] = [{"id": "ps5", "name": "PS5", "ip": newip,
                                                            "ftp_port": cfg["ftp"].get("port", 2121)}]
                fleet = Fleet(cfg)
                # Only a host that CONFIRMED it is a console gets written to disk. A guess is good
                # enough to try for this run, but must not outlive it - that is how the console
                # address silently became a peer PC's and stayed there.
                if confirmed:
                    try:
                        save_config(cfg)
                    except Exception:
                        pass
                    print(" Auto-found PS5 at %s" % newip)
                else:
                    print(" Trying %s as the PS5 for this run (it did not confirm it is a console, "
                          "so this is not being saved)" % newip)
    except Exception as e:
        print(" PS5 auto-find skipped: %s" % e)
    transfers = {}
    queue = Queue(fleet, transfers, cfg)
    queue.library = library          # [B7] so the worker can verify the local PKG before handoff
    library.queue = queue            # and back, so renaming never touches a file a live job holds
    engine = source_engine.SourceEngine(assemble_sources(cfg))

    # SINGLE INSTANCE. Ask before binding: on Windows a second bind can succeed while the first
    # process keeps running every background thread it owns, and both then write installed.json.
    _other = _already_running(cfg["companion"]["port"])
    if _other:
        print("[boot] PKG MUTANT SHOP v%s is ALREADY running on port %d - not starting a second "
              "copy. Close the other one first, or change companion.port."
              % (_other, cfg["companion"]["port"]))
        try:
            # imported locally: the module-level imports here are deliberately minimal, and the
            # other caller in this file does the same.
            import webbrowser as _wb
            _wb.open("http://127.0.0.1:%d/" % cfg["companion"]["port"])
        except Exception:
            pass
        return 0

    httpd = ThreadingHTTPServer((cfg["companion"]["host"], cfg["companion"]["port"]), Handler)
    httpd.cfg, httpd.library, httpd.fleet, httpd.queue = cfg, library, fleet, queue
    httpd.transfers, httpd.engine, httpd.hashes = transfers, engine, load_hashes()
    httpd.peers = PeerRegistry(cfg)

    sweep_temp_dbs(cfg.get("maintenance", {}).get("temp_sweep_age_sec", 3600))   # [B4] clean leftover temps
    start_library_watch(httpd)      # [B4] auto-rescan on library changes (debounced)
    start_cheat_sync_thread(httpd)  # ships the cheat library to a console that is missing it

    start_ps5_log_listener(port=cfg.get("console", {}).get("log_port", 9097))
    start_pc_register_thread(cfg, fleet)   # announce our address to the on-console shop server
    # Find and keep the other PCs on our own: first sweep in the background (startup must not wait
    # on a /24 scan), then keep them fresh. Without this, PC-to-PC only worked while the console was
    # up to introduce them — which also made moving a game between PCs depend on the PS5 being on,
    # and it should not.
    httpd.peers.keepalive(httpd)

    ip, port = lan_ip(), cfg["companion"]["port"]
    print("=" * 64)
    print(" PKG MUTANT SHOP  companion  v%s" % VERSION)
    _dev = device_identity(cfg)
    print(" UI:        http://localhost:%d   (LAN http://%s:%d)" % (port, ip, port))
    print(" Phone/tablet: open  http://%s:%d  on any device on this network" % (ip, port))
    print(" This PC:   %s   id=%s" % (_dev.get("name"), _dev.get("id")))
    print(" Consoles:  %s" % (", ".join("%s@%s" % (c["name"], c["ip"]) for c in fleet.consoles) or "(none set)"))
    print(" Parallel:  %d worker(s), per-console-parallel=%s" %
          (queue.max_parallel, queue.allow_ppc))
    print(" Library:   %d title(s)%s" % (len(library.games), " [empty — set library.local_paths]" if library.is_empty else ""))
    print("=" * 64)
    # Pop the shop UI in the default browser so it's obvious the app started.
    try:
        import webbrowser
        threading.Thread(target=lambda: (time.sleep(1.2),
                         webbrowser.open("http://localhost:%d/" % port)), daemon=True).start()
    except Exception:
        pass
    # Serve in the background; a system-tray icon fronts it (hidden app, no console window).
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if not run_tray(port):
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nbye")


if __name__ == "__main__":
    main()
