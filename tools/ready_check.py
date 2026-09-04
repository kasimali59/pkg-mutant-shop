"""One command that answers "is everything actually working?" - PC first, then the console.

Written after a session where the same facts were re-established five times by hand. Everything it
checks was a real bug at some point; each check names the thing it is guarding against.

    python tools/ready_check.py                 # wait for the console, then check everything
    python tools/ready_check.py --offline       # only what can be checked with no PS5
    python tools/ready_check.py --wait 600      # how long to wait for the console (default 180s)
    python tools/ready_check.py --no-route      # skip the transfer test (it moves a real backup)

It never installs a PKG and never deletes one of your games. The one thing it writes to the console
is a small file it removes again.
"""
import argparse
import ftplib
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
G, Y, R, B, X = "\033[92m", "\033[93m", "\033[91m", "\033[94m", "\033[0m"

FAILED, CHECKS, NOTES = [], [0], []


def rec(group, label, ok, detail=""):
    CHECKS[0] += 1
    if not ok:
        FAILED.append("%s: %s  %s" % (group, label, detail))
    print("  [%s%s%s] %-52s %s" % (G if ok else R, "PASS" if ok else "FAIL", X, label, detail))
    return ok


def head(t):
    print("\n%s== %s ==%s" % (B, t, X))


def get(url, timeout=20, body=None):
    """Returns (ok, parsed_or_text, err). A 4xx still carries the server's JSON - read it, the way
    the browser does. urlopen raises on non-2xx; fetch() in the UI does not."""
    try:
        if body is None:
            r = urllib.request.urlopen(url, timeout=timeout)
        else:
            r = urllib.request.urlopen(urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}), timeout=timeout)
        raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            return False, None, "HTTP %s" % e.code
        try:
            return True, json.loads(raw), "HTTP %s" % e.code
        except ValueError:
            return False, raw, "HTTP %s" % e.code
    except Exception as e:
        return False, None, e.__class__.__name__
    try:
        return True, json.loads(raw), ""
    except ValueError:
        return True, raw, ""


def port_open(ip, port, t=2.0):
    s = socket.socket()
    s.settimeout(t)
    try:
        return s.connect_ex((ip, port)) == 0
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------
# OFFLINE - the things that do not need a PS5
# ------------------------------------------------------------------------------------------------
def offline():
    head("the build itself")
    ver = {}
    src = io.open(os.path.join(ROOT, "companion", "server.py"), encoding="utf-8").read()
    m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
    ver["companion"] = m.group(1) if m else "?"
    c = io.open(os.path.join(ROOT, "ps5-app", "onconsole", "server.c"), encoding="utf-8").read()
    m = re.search(r'#define SHOP_VERSION "([^"]+)"', c)
    ver["elf"] = m.group(1) if m else "?"
    h = io.open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    m = re.search(r'var APP_VERSION="([^"]+)"', h)
    ver["ui"] = m.group(1) if m else "?"
    # The two artifacts shipping different numbers is how you lose an afternoon to "which build is
    # actually running". stamp_version.py stamps all three from companion/server.py.
    # "?" means the regex found nothing. Three unknowns are all equal to each other, so without
    # this the check goes green precisely when it has learned nothing.
    rec("build", "all three carry the same version",
        len(set(ver.values())) == 1 and "?" not in ver.values(),
        "companion=%(companion)s elf=%(elf)s ui=%(ui)s" % ver)

    # The ELF embeds the UI. A UI fix that is not re-bundled is invisible on the console.
    bundle = io.open(os.path.join(ROOT, "ps5-app", "onconsole", "web_bundle.h"),
                     encoding="utf-8", errors="replace").read()
    m = re.search(r'\{"index\.html", \w+, (\d+)\}', bundle)
    disk = os.path.getsize(os.path.join(ROOT, "web", "index.html"))
    rec("build", "the embedded web bundle matches web/index.html",
        bool(m) and int(m.group(1)) == disk,
        "bundle=%s disk=%d" % (m.group(1) if m else "?", disk))

    for tool, args in (("check_web.py", []), ("message_report.py", ["--check"])):
        p = subprocess.run([sys.executable, os.path.join(HERE, tool)] + args,
                           capture_output=True, text=True)
        rec("build", tool, p.returncode == 0, (p.stdout or p.stderr).strip().splitlines()[-1][:70]
            if (p.stdout or p.stderr).strip() else "")

    head("the UI's own logic, run against the shipped file")
    # These functions decide whether a game can be sent to a drive of your choosing. Gating them on
    # `lane` was what silently removed the destination picker: no live title carries lane "mount".
    for fn in ("isBackupTitle", "backupDriveOf", "ps5Destinations", "driveLabelOf"):
        rec("ui", "%s() is present" % fn, ("\nfunction %s(" % fn) in h)
    # `lane` is not a reliable backup test - no live title carries "mount" - so isBackupTitle() is
    # the ONLY place allowed to read it. Comments are stripped first, otherwise the note explaining
    # this very rule counts as a violation of it.
    code = re.sub(r"/\*.*?\*/", "", h, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    m = re.search(r"\nfunction isBackupTitle\(", code)
    inside = ""
    if m:
        i = code.index("{", m.end())
        depth, j = 0, i
        while j < len(code):
            if code[j] == "{":
                depth += 1
            elif code[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inside = code[i:j + 1]
    total = len(re.findall(r'lane==="mount"', code))
    allowed = len(re.findall(r'lane==="mount"', inside))
    rec("ui", "only isBackupTitle() reads lane===\"mount\"",
        total > 0 and total == allowed,
        "%d in code, %d of them inside isBackupTitle" % (total, allowed))
    # Settings is ONE PAGE of cards now. The tab machinery is gone, and it must stay gone: the
    # answer to "why will this not install" used to be behind whichever tab you were not on.
    # Column flow, not a grid: a grid stretched every row to its tallest card and left a band of
    # dead space before the next row. break-inside is what stops a column splitting a card.
    rec("ui", "settings cards flow in columns",
        ".setgrid{column-count:3" in h and "break-inside:avoid" in h)
    rec("ui", "settings has all eight cards", h.count('<section class="scard') == 8,
        "%d found" % h.count('<section class="scard'))
    rec("ui", "the settings tab machinery is gone",
        "showSetTab" not in h and "settab" not in h and "setpane" not in h)
    # applyI18n() assigns textContent, so a title carrying data-i18n directly would wipe its icon.
    # The key has to sit on an inner span - and every card must still carry one, or the seven
    # translations stop reaching the headings.
    rec("ui", "every card title is translatable", h.count('</svg></span><span data-i18n="sec_') == 8,
        "%d of 8" % h.count('</svg></span><span data-i18n="sec_'))
    # The five saved settings must remain real inputs/selects with these exact ids: openSettings()
    # dereferences setIp and setPort UNGUARDED, so losing one stops the panel opening at all.
    for _id in ("setIp", "setPort", "setParallel", "setVerify", "setAutoScan"):
        rec("ui", "saved control %s survives" % _id, ('id="%s"' % _id) in h)
    rec("ui", "no title= tooltips left (the PS5 browser never shows them)",
        'title="' not in h, "%d found" % h.count('title="'))


# ------------------------------------------------------------------------------------------------
# ONLINE
# ------------------------------------------------------------------------------------------------
def js_predicate_check(pc_lib, con_lib, devices):
    """Run the REAL functions out of index.html in node, against the REAL library."""
    h = io.open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()

    def grab(name):
        m = re.search(r"\nfunction %s\(" % re.escape(name), h)
        if not m:
            return ""
        i = h.index("{", m.end())
        depth, j = 0, i
        while j < len(h):
            if h[j] == "{":
                depth += 1
            elif h[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        return h[m.start() + 1:j + 1]

    def grab_var(name):
        m = re.search(r"\nvar %s=" % re.escape(name), h)
        return h[m.start() + 1:h.index(";", m.end()) + 1] if m else ""

    js = "\n".join([
        grab_var("MOUNT_FMTS"), grab_var("PS5_DESTS"),
        grab("isBackupTitle"), grab("backupDriveOf"), grab("ps5Destinations"),
        "var state={devices:%s};" % json.dumps({"ps5": devices}),
        "var PC=%s, CON=%s;" % (json.dumps(pc_lib), json.dumps(con_lib)),
        """
var out={};
function scan(g){var b=0,miss=0,wrong=0;
  g.forEach(function(x){
    var is=isBackupTitle(x);
    if(is) b++;
    if(x.backup_path && !is) miss++;
    if(is && !x.backup_path && x.source!=="backup" && x.lane!=="mount" &&
       !(x.format&&MOUNT_FMTS[String(x.format).toLowerCase()]) &&
       !(x.base&&x.base[0]&&x.base[0].kind==="backup")) wrong++;
  });
  return {n:g.length,b:b,miss:miss,wrong:wrong};}
out.pc=scan(PC.games||[]); out.con=scan(CON.games||[]);
var all=ps5Destinations();
out.dest={known:all.length, seen:all.filter(function(d){return d.detected!==false;}).length,
          list:all.filter(function(d){return d.detected!==false;}).map(function(d){return d.id;})};
console.log(JSON.stringify(out));
""",
    ])
    f = os.path.join(ROOT, ".ready_check.js")
    io.open(f, "w", encoding="utf-8").write(js)
    try:
        p = subprocess.run(["node", f], capture_output=True, text=True, timeout=60)
        return json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else None
    except Exception:
        return None
    finally:
        try:
            os.remove(f)
        except OSError:
            pass


def online(a):
    con = "http://%s:%d" % (a.ip, a.shop_port)
    pc = a.companion

    head("reachability")
    rec("net", "the PC companion answers", port_open("127.0.0.1", 8710), pc)
    ok, ph, _ = get(pc + "/api/health", 15)
    rec("net", "companion health", ok and isinstance(ph, dict) and ph.get("ok"),
        "v%s" % (ph or {}).get("version"))
    rec("net", "the console shop answers", port_open(a.ip, a.shop_port), con)
    ok, ch, _ = get(con + "/api/health", 15)
    rec("net", "console health", ok and isinstance(ch, dict) and ch.get("ok"),
        "v%s built %s" % ((ch or {}).get("version"), (ch or {}).get("built")))
    # None == None is true, so with neither side answering this passed while proving nothing.
    rec("net", "both sides are the same version",
        bool((ph or {}).get("version")) and (ph or {}).get("version") == (ch or {}).get("version"),
        "%s vs %s" % ((ph or {}).get("version"), (ch or {}).get("version")))
    # Third-party install hosts must be absent - the whole point of our own engine.
    rec("net", "no third-party install host is listening",
        not any(port_open(a.ip, p, 1.5) for p in (12800, 9090, 9081, 1337, 9040)),
        "12800/9090/9081/1337/9040 closed")
    for k in ("dpi_online", "dpi_reachable", "dpi_state", "dpi_port", "capabilities"):
        pass
    dead = [k for k in (ch or {}) if k.startswith("dpi")] + \
           [k for k in (ch or {}) if k == "capabilities"]
    rec("net", "health carries no purged etaHEN/DPI fields", not dead, ", ".join(dead) or "clean")

    head("the library, and what it says about each game")
    ok, pl, _ = get(pc + "/api/library", 60)
    ok2, cl, _ = get(con + "/api/library", 60)
    rec("lib", "both servers return a library",
        ok and ok2 and isinstance(pl, dict) and isinstance(cl, dict),
        "PC=%d CON=%d titles" % (len((pl or {}).get("games", [])), len((cl or {}).get("games", []))))
    backs = [g for g in (pl or {}).get("games", []) if g.get("backup_path")]
    rec("lib", "backups carry movable + local_path (else Move is hidden)",
        bool(backs) and all(g.get("movable") and g.get("local_path") for g in backs),
        "%d of %d" % (sum(1 for g in backs if g.get("movable") and g.get("local_path")), len(backs)))

    ok, dv, _ = get(con + "/api/devices", 20)
    devices = (dv or {}).get("ps5", [])
    res = js_predicate_check(pl or {}, cl or {}, devices)
    if res is None:
        rec("lib", "UI predicate replayed over the live library", False, "node did not run")
    else:
        rec("lib", "every backup is recognised as one (PC)",
            res["pc"]["miss"] == 0, "%d backups, %d missed" % (res["pc"]["b"], res["pc"]["miss"]))
        rec("lib", "every backup is recognised as one (console)",
            res["con"]["miss"] == 0, "%d backups, %d missed" % (res["con"]["b"], res["con"]["miss"]))
        rec("lib", "no PS4 package is mistaken for a backup",
            res["pc"]["wrong"] == 0 and res["con"]["wrong"] == 0)
        rec("lib", "the drive picker offers only connected storage",
            0 < res["dest"]["seen"] < res["dest"]["known"],
            "%d of %d offered: %s" % (res["dest"]["seen"], res["dest"]["known"],
                                      ", ".join(res["dest"]["list"])))

    head("controls that must work from BOTH servers")
    for name, base in (("console", con), ("PC", pc)):
        ok, r, _ = get(base + "/api/game/delete", 30, {"title_id": "CUSA00099"})
        rec("both", "%s: delete answers for an unknown title" % name,
            ok and isinstance(r, dict) and r.get("already_gone") is True)
        ok, r, _ = get(base + "/api/game/delete", 30, {})
        rec("both", "%s: delete refuses with a reason when no id is sent" % name,
            ok and isinstance(r, dict) and r.get("ok") is False and "title id" in str(r.get("error", "")))
        ok, r, _ = get(base + "/api/move/status", 20)
        rec("both", "%s: move status is served" % name,
            ok and isinstance(r, dict) and "active" in r)
    ok, r, _ = get(con + "/api/queue/console-local/dismiss", 20, {})
    rec("both", "console: the queue X is implemented",
        ok and isinstance(r, dict) and r.get("ok") is True)
    ok, r, _ = get(con + "/api/queue/console-local/retry", 20, {})
    rec("both", "console: the queue Retry is implemented",
        ok and isinstance(r, dict) and "not supported on console" not in json.dumps(r))

    head("the delete boundary (a drive root is not a watch folder)")
    # SHADOWMOUNT MOUNTS WHATEVER APPEARS IN A WATCH FOLDER, THE INSTANT IT APPEARS - and this probe
    # writes into one. A 4 KB file of zeroes named like a compressed PFS container is precisely the
    # half-written container that has crashed this console before. The test is the only proof that
    # delete really removes a file from the PS5, so it stays; it is no longer something that just
    # happens to you. Two guards now: it must be asked for by name, and it is written as ".part"
    # first so ShadowMount ignores it until the moment it is complete.
    if not a.test_delete:
        rec("delete", "delete round trip (skipped - pass --test-delete)", True,
            "the probe writes a container ShadowMount would try to mount")
        return _after_delete(a, con, pc, ph)
    probe = "/mnt/ext1/homebrew/[PS5] CUSA00007 - READY CHECK.ffpfsc"
    try:
        f = ftplib.FTP()
        f.connect(a.ip, a.ftp_port, 20)
        f.login("anonymous", "")
        # .part first, rename after: ShadowMount skips partials, so it never sees an incomplete file.
        f.storbinary("STOR " + probe + ".part", io.BytesIO(b"\0" * 4096))
        f.rename(probe + ".part", probe)
        f.quit()
        ok, r, _ = get(pc + "/api/game/delete", 60, {"title_id": "CUSA00007"})
        rec("delete", "the PC proxy really deletes on the console",
            ok and isinstance(r, dict) and r.get("deleted") is True and r.get("bytes") == 4096,
            (r or {}).get("path", "")[:52])
        f = ftplib.FTP()
        f.connect(a.ip, a.ftp_port, 20)
        f.login("anonymous", "")
        gone = True
        try:
            f.voidcmd("TYPE I")
            f.size(probe)
            gone = False
            f.delete(probe)
        except Exception:
            pass
        f.quit()
        rec("delete", "the file is really gone from the drive", gone)
    except Exception as e:
        rec("delete", "delete round trip", False, repr(e)[:60])

    return _after_delete(a, con, pc, ph)


def _after_delete(a, con, pc, ph):
    """Everything that follows the delete test. Split out so the skip path above can jump here
    without duplicating it."""
    head("the install lane")
    ok, ss, _ = get(con + "/api/engine/spawn-status", 15)
    rec("install", "no install is stuck in flight",
        ok and isinstance(ss, dict) and not ss.get("busy"),
        "busy_for=%ss" % (ss or {}).get("busy_for"))
    ok, fl, _ = get(con + "/api/fs/list?path=" + urllib.parse.quote("/data/pkg-mutant-shop", safe=""), 20)
    names = [e.get("name") for e in (fl or {}).get("entries", [])]
    # The auto-clean removes both handover files after every install. Either one still sitting
    # here means the last install did not finish cleanly.
    rec("install", "the handover files are cleaned up",
        "installer-req.txt" not in names and "installer-res.json" not in names,
        ", ".join(n for n in names if n.startswith("installer")) or "clean")
    ok, fl, _ = get(con + "/api/fs/list?path=" + urllib.parse.quote("/data/pkg-mutant-shop/payloads", safe=""), 20)
    inst = next((e for e in (fl or {}).get("entries", []) if e.get("name") == "pms-installer.elf"), None)
    rec("install", "our installer is on the console", bool(inst and inst.get("size")),
        "%s bytes" % (inst or {}).get("size"))

    if not a.no_route:
        head("does a chosen drive really receive the file?")
        # The fetch lane, which is what puts a PS5 backup on the drive you picked. Uses a small
        # PKG as the payload and a throwaway name, so no game is touched.
        ok, lib, _ = get(pc + "/api/library", 60)
        key = None
        for g in (lib or {}).get("games", []):
            b = (g.get("base") or [None])[0] or {}
            if b.get("install_key", "").lower().endswith(".pkg") and (b.get("size") or 0) < 300e6:
                key = b["install_key"]
                break
        if not key:
            rec("route", "a small package to test with", False, "none found in the library")
        else:
            lan = (ph or {}).get("lan_ip") or "127.0.0.1"
            src = "http://%s:8710/library/%s" % (lan, key)
            dest = "/mnt/%s/homebrew" % a.route_drive if a.route_drive != "internal" else "/data/homebrew"
            q = ("/api/engine/fetch?url=" + urllib.parse.quote(src, safe="") +
                 "&dest=" + urllib.parse.quote(dest, safe="") +
                 "&name=" + urllib.parse.quote("ZZ-ready-check.bin", safe=""))
            ok, r, _ = get(con + q, 120)
            started = ok and isinstance(r, dict) and r.get("ok")
            rec("route", "the console accepted a transfer to %s" % a.route_drive, started,
                (r or {}).get("path", "")[:52])
            if started:
                landed = False
                for _ in range(40):
                    time.sleep(3)
                    ok, j, _ = get(con + "/api/engine/job", 15)
                    if ok and isinstance(j, dict) and j.get("state") == "done":
                        landed = True
                        break
                    if ok and isinstance(j, dict) and j.get("error"):
                        break
                rec("route", "it finished", landed)
                # OUR OWN file API, not FTP. ftpsrv is a separate payload the user may simply not
                # have loaded - it was not, on this run - and then a passing transfer was reported
                # as a failure and its 227 MB probe was left behind because the cleanup needed FTP
                # too. The shop is by definition running, or none of this test could have got here.
                probe = dest + "/ZZ-ready-check.bin"
                ok, st, _ = get(con + "/api/fs/stat?path=" + urllib.parse.quote(probe, safe=""), 30)
                sz = (st or {}).get("size") if isinstance(st, dict) else None
                rec("route", "the file is on %s, not somewhere else" % a.route_drive,
                    bool(sz), "%s bytes in %s" % (sz, dest))
                get(con + "/api/fs/delete?path=" + urllib.parse.quote(probe, safe=""), 30)
                ok2, gone, _ = get(con + "/api/fs/stat?path=" + urllib.parse.quote(probe, safe=""), 20)
                rec("route", "the probe cleaned itself up",
                    not (isinstance(gone, dict) and gone.get("ok")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=None, help="PS5 address (default: from config.json)")
    ap.add_argument("--shop-port", type=int, default=8710)
    ap.add_argument("--ftp-port", type=int, default=2121)
    ap.add_argument("--companion", default="http://127.0.0.1:8710")
    ap.add_argument("--wait", type=int, default=180, help="seconds to wait for the console")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--no-route", action="store_true", help="skip the transfer test")
    ap.add_argument("--route-drive", default="usb0")
    ap.add_argument("--test-delete", action="store_true",
                    help="write a probe container into ShadowMount's scan folder to test the "
                         "delete lane. OFF by default: ShadowMount will try to mount whatever "
                         "appears there.")
    a = ap.parse_args()

    if not a.ip:
        try:
            cfg = json.load(io.open(os.path.join(ROOT, "companion", "config.json"), encoding="utf-8"))
            a.ip = cfg.get("ps5_ip", "10.0.0.99")
        except Exception:
            a.ip = "10.0.0.99"

    print("%sPKG MUTANT SHOP - ready check%s   console %s   companion %s" % (B, X, a.ip, a.companion))
    offline()

    if a.offline:
        pass
    else:
        if not port_open(a.ip, a.shop_port, 2.0):
            print("\n%swaiting up to %ds for the shop on %s:%d ...%s"
                  % (Y, a.wait, a.ip, a.shop_port, X))
            t0 = time.time()
            while time.time() - t0 < a.wait and not port_open(a.ip, a.shop_port, 2.0):
                time.sleep(5)
        if port_open(a.ip, a.shop_port, 2.0):
            online(a)
        else:
            print("\n%sThe console never answered. Load PKG-MUTANT-SHOP.elf from Payload Manager,"
                  "\nthen run this again. Everything above is the PC-side result.%s" % (Y, X))

    print("\n%s== result ==%s" % (B, X))
    if FAILED:
        print("  %d checks, %s%d failed%s" % (CHECKS[0], R, len(FAILED), X))
        for f in FAILED:
            print("   %s-%s %s" % (R, X, f))
    else:
        print("  %d checks, %sall passed%s" % (CHECKS[0], G, X))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
