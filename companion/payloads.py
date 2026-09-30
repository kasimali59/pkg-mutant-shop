# -*- coding: utf-8 -*-
"""Payloads & Homebrews: the catalogue, the two send lanes, and state nobody has to take on trust.

WHAT THIS IS. The owner keeps a folder of third-party payload ELFs and homebrew packages. This
module turns that folder into something the panel can show and press, on either console, and it is
deliberately small: every hard part already exists somewhere in this app and is called, not
reimplemented.

  * the catalogue is built at BUILD time by tools/gen_payload_catalog.py into
    web/assets/payloads-catalog.json, which both ELFs embed and the exe carries, so the panel works
    on a console with every PC switched off;
  * a payload reaches a PS5 through Payload Manager, exactly the way our own installer does, and a
    PS4 through GoldHEN's loader, exactly the way our own shop does;
  * a homebrew package is installed by THE SAME install engine the games use - it is handed to the
    queue as an ordinary install against a key this module registers for serving, which is how the
    PS4's home-screen package has always been shipped (PS4_TILE_KEY);
  * "is it running" is a port that answered, never an assumption, and a payload with nothing
    listening says so rather than showing a light nobody can back.

WHAT THIS MODULE MUST NEVER DO, each learned the hard way somewhere else in this repo:

  * never add the source folder to library.local_paths. Library.scan() registers every .pkg it
    finds as a game and normalise_pkg_names() RENAMES files on disk whose stem has characters
    outside [A-Za-z0-9._-] - it would rewrite the owner's
    "PS4-Xplorer 2.0 (LAPY20009) - 2.08.pkg" and put five homebrews on their game shelf;
  * never TCP-probe a PS4's :9090. Opening GoldHEN's payload port and closing it again stops it
    listening. The POST of an ELF is the probe;
  * never resolve a PS5 payload load by bare basename. Payload Manager resolves /loadpayload by
    BASENAME against its own registered directory, so a file uploaded anywhere else can silently
    run an older copy. Everything here writes to /data/pldmgr/payloads/<stem>/<file>, which is the
    convention companion/deploy.py already uses for our own ELF;
  * never start a jailbreak-layer payload as a side effect. payload_bundle.h states the rule in its
    own words and this module enforces it with `layer == "jailbreak"` needing an explicit confirm.
"""
import hashlib
import io
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Where the console keeps what we give it. Both consoles already own SHOP_DATA_DIR and both already
# write into it at boot, so this is a subfolder of a folder that exists, not a new place on disk.
CONSOLE_PAYLOAD_DIR = "/data/pkg-mutant-shop/payloads"
CONSOLE_HOMEBREW_DIR = "/data/pkg-mutant-shop/homebrews"
# Payload Manager's OWN directory. /loadpayload resolves by basename against this, so a payload has
# to be here to be the one that actually runs. See companion/deploy.py's header.
PLDMGR_DIR = "/data/pldmgr/payloads"

CATALOG_NAME = "payloads-catalog.json"
DEFAULT_SRC = os.path.join("C:" + os.sep, "Mutant Payloads & HomeBrews")

# The key prefix under which a homebrew is registered for serving. It is NOT a library title: the
# registry entry is added after `games` is assembled, the way PS4_TILE_KEY is, so nothing about it
# can reach the owner's game shelf.
SERVE_PREFIX = "PMS-HOMEBREW/"

_cat_lock = threading.Lock()
_cat = {"path": None, "mtime": 0.0, "data": None}

_rel_lock = threading.Lock()
_rel_cache = {}                    # repo -> {"at": ts, "tag": str, "assets": [...], "error": str}
RELEASE_TTL_S = 6 * 3600           # a release check is not urgent; six hours is plenty


# --------------------------------------------------------------------------- #
# the catalogue                                                                #
# --------------------------------------------------------------------------- #
def catalog_path(web_dir):
    return os.path.join(web_dir, "assets", CATALOG_NAME)


def catalog(web_dir):
    """The shipped catalogue, re-read only when the file changes.

    Returns {} rather than raising when it is missing: a build without one must show an empty panel
    and keep the rest of the app working, not fail a request the header polls.
    """
    p = catalog_path(web_dir)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    with _cat_lock:
        if _cat["path"] == p and _cat["mtime"] == mt and _cat["data"] is not None:
            return _cat["data"]
    try:
        with io.open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    with _cat_lock:
        _cat.update({"path": p, "mtime": mt, "data": data})
    return data


def source_root(cfg):
    """Where the owner's folder is. Configurable, because a second PC will not have the same drive."""
    try:
        v = ((cfg or {}).get("payloads") or {}).get("root")
    except Exception:
        v = None
    return os.path.normpath(v) if v else DEFAULT_SRC


def bundled_ours(item):
    """Our own artifact as the running exe carries it, or None.

    THE EXE SHIPS THE PS4 PAYLOAD. server.py has bundled `ps4-elf/PKG-MUTANT-SHOP-PS4.elf` since
    long before this panel existed, for the reason written there: it is the difference between
    "switch the PS4 on" and "switch the PS4 on, then go and find the icon". So a second PC with no
    source folder was reporting "not on this PC" about a file it was carrying inside itself - the
    owner saw exactly that on Casita. The PS5's 34 MB ELF is deliberately NOT bundled, so this
    answers None for it and the peer lane takes over.

    Read from the frozen bundle only. From source the ordinary folder lookup already finds the repo
    copy, and pointing at ../ps5-app/onconsole here would make a dev checkout claim to hold a build
    it may not have made yet.
    """
    if not getattr(sys, "frozen", False):
        return None
    if (item or {}).get("file") != "PKG-MUTANT-SHOP-PS4.elf":
        return None
    p = os.path.join(getattr(sys, "_MEIPASS", ""), "ps4-elf", "PKG-MUTANT-SHOP-PS4.elf")
    return p if p and os.path.exists(p) else None


def local_path(cfg, item):
    """Absolute path of an item's bytes on THIS PC, or None when the folder is not here.

    None is an ordinary answer, not an error: the exe runs on machines that never had the source
    folder, and the panel says "not on this PC" instead of pretending the file is reachable.
    """
    rel = (item or {}).get("path")
    if not rel:
        return None
    p = os.path.join(source_root(cfg), rel.replace("/", os.sep))
    if os.path.exists(p):
        return p
    return bundled_ours(item) if (item or {}).get("ours") else None


def serve_key(item):
    """The /library/<key> key a homebrew is served under. Stable, and never a real title's key."""
    return SERVE_PREFIX + (item.get("path") or item.get("id") or "").replace("\\", "/")


def items_for(web_dir, platform=None, kind=None):
    out = []
    for it in (catalog(web_dir).get("items") or []):
        if platform and str(it.get("platform", "")).upper() != str(platform).upper():
            continue
        if kind and it.get("kind") != kind:
            continue
        out.append(it)
    return out


# --------------------------------------------------------------------------- #
# state: what is observably true right now                                      #
# --------------------------------------------------------------------------- #

def proc_stem(name):
    """The comparable stem of a payload filename.

    Payload Manager reports the name the payload was BUILT as, which is not always the name the
    file has on disk: measured on this console, "ftpsrv-ps5.elf" runs as "ftpsrv.elf" and
    "pldmgr_v0.5.2.elf" as "pldmgr.elf". Comparing raw filenames therefore reports two live
    payloads as stopped. Strip the extension, the platform suffix and a version suffix, and the two
    names meet in the middle.
    """
    n = (name or "").strip().lower()
    if n.endswith(".elf"):
        n = n[:-4]
    n = re.sub(r"[-_](ps4|ps5)$", "", n)
    n = re.sub(r"[-_]v?\d+(\.\d+)*[a-z0-9]*$", "", n)
    return n

def port_open(ip, port, timeout=0.9):
    if not ip or not port:
        return False
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((ip, int(port)))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def live_ports(ip, ports, timeout=0.9):
    """Probe several ports at once and return the set that answered.

    Threads because the panel asks about up to eight payloads and a console's accept loop should be
    touched briefly, once, rather than eight times in series while somebody watches a spinner.

    :9090 IS NOT PROBEABLE and is never in `ports` - see the module header.
    """
    ports = sorted({int(p) for p in ports if p})
    if not ip or not ports:
        return set()
    out, lock = set(), threading.Lock()

    def one(p):
        if port_open(ip, p, timeout):
            with lock:
                out.add(p)

    ts = [threading.Thread(target=one, args=(p,), daemon=True) for p in ports]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout + 0.4)
    return out


# --------------------------------------------------------------------------- #
# sending a payload                                                             #
# --------------------------------------------------------------------------- #
def ps5_payload_dest(item):
    """Where a payload has to live for Payload Manager to actually run THAT copy."""
    fname = item.get("file") or os.path.basename(item.get("path") or "")
    stem = re.sub(r"\.elf$", "", fname, flags=re.I) or "payload"
    return "%s/%s/%s" % (PLDMGR_DIR, stem, fname)


class bridge_ip_shim(object):
    """console_load() wants something with an .ip; send_ps4 is handed the address itself."""
    def __init__(self, ip):
        self.ip = ip


def console_payload_sizes(ip, timeout=6.0):
    """{stem: size} for the payloads a console holds in PB_DIR, or None if it could not say.

    PB_DIR is the folder /api/payloads/load runs from, so this is the only set of sizes worth
    comparing against before deciding a send can be skipped. By stem, because the console carries
    each payload under the stable catalogue id while this PC has whatever the owner's file is
    called.
    """
    if not ip:
        return None
    try:
        with urllib.request.urlopen("http://%s:8710/api/payloads" % ip, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    have = ((j.get("state") or {}).get("have"))
    if have is None:
        return None
    out = {}
    for e in have:
        try:
            out[proc_stem(e.get("n"))] = int(e.get("s") or 0)
        except Exception:
            continue
    return out


def console_copy_is_current(bridge, cfg, item):
    """Does the copy the console would RUN match the one on this PC?

    AFTER AN UPDATE IT DOES NOT, AND THAT IS THE WHOLE POINT. Both ELFs write the payloads they
    carry to PB_DIR at boot, so "start the console's own copy" is normally free and correct - but
    the moment the update button replaces a file here, the console is still holding the previous
    build, and starting it would silently run the old version while the panel showed the new number.

    IT HAS TO BE THE COPY THAT WOULD ACTUALLY RUN. Comparing against the file we last uploaded to
    Payload Manager's folder answers a different question: /api/payloads/load resolves by stem
    against PB_DIR, so PB_DIR is what decides. Getting that wrong meant the first press sent the new
    bytes and the second happily started the old ones.

    Unknown - the console could not be asked, or this PC has no copy to compare - counts as current:
    refusing to start something on a doubt is worse than starting the copy that is there.
    """
    src = local_path(cfg, item)
    if not src:
        return True
    try:
        want = os.path.getsize(src)
    except OSError:
        return True
    st = console_state(getattr(bridge, "ip", ""))
    sizes = (st or {}).get("have")
    if sizes is None:
        return True
    got = sizes.get(proc_stem(os.path.basename(src)))
    if got is None:
        return False          # the console does not carry it at all - send it
    return got == want


def console_load(bridge, item, timeout=60):
    """Ask the console to start a payload out of the copy ITS OWN ELF carries.

    Both ELFs write the bundled payloads to /data/pkg-mutant-shop/payloads at boot, so on any
    console running 3.84.0 or newer this is both faster than uploading two megabytes again and the
    only thing that works from a PC that has never had the owner's folder. Returns True only when
    the console says it started it; anything else falls back to the upload path, so an older payload
    on the console behaves exactly as it did before this existed.
    """
    fname = item.get("file") or ""
    ip = getattr(bridge, "ip", "")
    if not (ip and fname):
        return False
    try:
        with urllib.request.urlopen(
                "http://%s:8710/api/payloads/load?name=%s"
                % (ip, urllib.parse.quote(fname)), timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        return bool(j.get("ok"))
    except Exception:
        return False


def send_ps5(bridge, cfg, item, log=None):
    """Upload a payload to the PS5 and ask Payload Manager to run it.

    Returns (ok, sentence). The sentence is what the owner reads, so it says what happened rather
    than what was attempted.
    """
    say = log or (lambda m: None)
    src = local_path(cfg, item)
    if not src:
        # NOT ON THIS PC IS NOT THE END OF IT. The console carries this payload too, so ask it to
        # start its own copy rather than refusing - which is what makes the panel work from a PC
        # that has never had the owner's folder.
        if console_load(bridge, item):
            return True, "Started %s from the console's own copy." % (item.get("title") or item.get("id"))
        return False, "That file is not on this PC, and the console does not carry it either."
    dest = ps5_payload_dest(item)
    size = os.path.getsize(src)
    sent = False
    if console_file_size(bridge, dest) == size:
        say("[payloads] %s is already on the console - not sending it again" % dest)
    else:
        sent = True
        try:
            with io.open(src, "rb") as f:
                ok = bridge.fs_write(dest, f, size=size, timeout=600)
        except Exception as e:
            return False, "The console did not take the file (%s)." % e.__class__.__name__
        if not ok:
            return False, "The console did not take the file."
        say("[payloads] uploaded %s (%d bytes)" % (dest, size))
        # ...AND INTO THE CONSOLE'S OWN FOLDER, under the name it carries this payload as. PB_DIR is
        # what /api/payloads/load runs from and what console_copy_is_current() compares against, so
        # without this the console keeps answering with the build its ELF shipped and every press
        # would send the same bytes again for ever. It is overwritten from the ELF at the next boot,
        # which is correct: the embedded copy is only refreshed by a rebuild, and
        # sync_payload_bins --check is what makes sure that build happens.
        mirror = "%s/%s" % (CONSOLE_PAYLOAD_DIR, proc_stem(os.path.basename(src)) + ".elf")
        try:
            with io.open(src, "rb") as f:
                if bridge.fs_write(mirror, f, size=size, timeout=600):
                    say("[payloads] refreshed the console's own copy at %s" % mirror)
        except Exception:
            pass          # best effort: the load below uses the path we already wrote

    # A PROPERTY, NOT A METHOD. Ps5Bridge.pldmgr is @property and calling it raised
    # "'PayloadManager' object is not callable" AFTER the upload had already succeeded - the file
    # landed and the press still reported failure. getattr keeps working if it ever becomes a
    # method again.
    pm = bridge.pldmgr
    if callable(pm):
        pm = pm()
    if not pm:
        return False, "Payload Manager is not answering, so nothing can be started."
    if not pm.load(dest):
        return False, "Payload Manager did not take it."
    # SAY WHICH OF THE TWO THINGS HAPPENED. "Sent" when nothing was sent is a small lie that makes
    # the owner think a copy went over the network every time they press Run.
    title = item.get("title") or item.get("id")
    return True, (("Sent %s to the PS5." % title) if sent
                  else ("Started %s on the PS5 - it was already there." % title))


def send_ps4(ip, cfg, item, log=None, timeout=120):
    """Hand a payload to GoldHEN's loader on a PS4.

    THE POST IS THE PROBE. There is no connect-first check anywhere in this function, deliberately:
    opening :9090 and closing it again stops the loader listening, which is why the rest of this
    app refuses to touch that port any other way.
    """
    say = log or (lambda m: None)
    src = local_path(cfg, item)
    if not src:
        if console_load(bridge_ip_shim(ip), item):
            return True, "Started %s from the console's own copy." % (item.get("title") or item.get("id"))
        return False, "That file is not on this PC, and the console does not carry it either."
    try:
        body = io.open(src, "rb").read()
    except Exception:
        return False, "That file could not be read."
    req = urllib.request.Request("http://%s:9090/" % ip, data=body,
                                 headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
    except Exception as e:
        return False, "The payload loader did not take it (%s)." % e.__class__.__name__
    say("[payloads] posted %s to %s:9090" % (item.get("file"), ip))
    return True, "Sent %s to the PS4." % (item.get("title") or item.get("id"))


# --------------------------------------------------------------------------- #
# seeding a homebrew onto the console, so no PC is needed afterwards            #
# --------------------------------------------------------------------------- #
def console_homebrew_path(item):
    fname = item.get("file") or os.path.basename(item.get("path") or "")
    return "%s/%s" % (CONSOLE_HOMEBREW_DIR, fname)


def seed_homebrew(bridge, cfg, item, log=None, progress=None):
    """Copy a homebrew package onto the console's own disk, once.

    This is what makes requirement 7 true in the only way the arithmetic allows: the packages are
    264 MB and cannot ride inside an ELF that is loaded into RAM, but they can sit in the console's
    own data folder for ever. Once seeded, the console installs them with every PC switched off,
    because pkgfile_path_allowed() already accepts /data/...*.pkg and serves it to the installer
    over loopback.
    """
    say = log or (lambda m: None)
    src = local_path(cfg, item)
    if not src:
        return False, "That file is not on this PC."
    dest = console_homebrew_path(item)
    size = os.path.getsize(src)
    if console_file_size(bridge, dest) == size:
        return True, "Already on the console."
    try:
        with io.open(src, "rb") as f:
            ok = bridge.fs_write(dest, f, size=size, timeout=3600)
    except Exception as e:
        return False, "The copy did not finish (%s)." % e.__class__.__name__
    if not ok:
        return False, "The copy did not finish."
    say("[payloads] seeded %s (%d bytes)" % (dest, size))
    return True, "Copied to the console."


# --------------------------------------------------------------------------- #
# upstream releases                                                             #
# --------------------------------------------------------------------------- #
def gh_token(cfg):
    """A GitHub token, if the owner has given us one. "" is the normal answer.

    IT IS ONLY EVER NEEDED FOR A REPOSITORY THAT IS NOT PUBLIC, which right now is ours: the app's
    own releases are private while they are being prepared, and api.github.com answers 404 to an
    unauthenticated request for a private repository - indistinguishable from "this project has no
    releases". Every third-party upstream in curated.json is public and needs none of this.

    Read from config (`updates.github_token`) or the environment, never written anywhere, never
    logged, and never sent to any host but api.github.com / objects.githubusercontent.com.
    """
    t = ""
    try:
        t = str(((cfg or {}).get("updates") or {}).get("github_token") or "").strip()
    except Exception:
        t = ""
    return t or str(os.environ.get("PMS_GITHUB_TOKEN") or "").strip()


def github_latest(repo, timeout=8.0, force=False, token=""):
    """The newest release of a GitHub project, cached.

    THIS IS THE ONLY THING IN THIS APP THAT TALKS TO THE INTERNET. Everything else speaks to the
    LAN. So it is written to fail quietly and completely: no retry, one short timeout, and every
    failure returns a dict with "error" set rather than raising into a request the panel polls.
    An owner who blocks Sony does not necessarily allow GitHub, and a firewall that drops this must
    cost one greyed line in a panel, never a broken app.
    """
    repo = (repo or "").strip().strip("/")
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
        return {"error": "no upstream"}
    now = time.time()
    # THE CACHE KEY CARRIES WHETHER WE WERE AUTHENTICATED. Without that, one unauthenticated 404 on
    # our own private repo would be served back for five minutes to a caller that now HAS a token.
    ckey = repo + ("|auth" if token else "")
    with _rel_lock:
        c = _rel_cache.get(ckey)
        if c and not force and (now - c.get("at", 0)) < RELEASE_TTL_S:
            return c
    out = {"at": now, "repo": repo}
    hdrs = {"Accept": "application/vnd.github+json",
            # GitHub refuses a request with no User-Agent.
            "User-Agent": "PKG-MUTANT-SHOP"}
    if token:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/releases/latest" % repo, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        out["tag"] = str(j.get("tag_name") or j.get("name") or "").strip()
        out["url"] = str(j.get("html_url") or "")
        out["published"] = str(j.get("published_at") or "")[:10]
        # `api_url` IS NOT THE SAME LINK AND IS NEEDED FOR A PRIVATE RELEASE. browser_download_url
        # is only fetchable without credentials on a public repo; an asset of a private release is
        # fetched from the API url with the token and Accept: application/octet-stream. Both are
        # recorded so download_asset can choose without asking GitHub twice.
        out["assets"] = [{"name": a.get("name"), "size": a.get("size"),
                          "url": a.get("browser_download_url"), "api_url": a.get("url")}
                         for a in (j.get("assets") or []) if a.get("name")]
    except urllib.error.HTTPError as e:
        # THE REASON MATTERS, AND "HTTPError" IS NOT ONE. Unauthenticated api.github.com allows 60
        # requests an hour from one address, and a panel that checks ten projects burns that in six
        # presses - so a rate limit is the ordinary failure here, not an exotic one, and it must not
        # read the same as "this project has no releases". 404 is the other common answer and means
        # the repo or its releases are not there, which is a wrong entry in curated.json, not a
        # network problem.
        if e.code == 403 or e.code == 429:
            out["error"] = "rate-limited"
            out["retry_after"] = str(e.headers.get("x-ratelimit-reset") or "")
        elif e.code == 404:
            # 404 MEANS TWO DIFFERENT THINGS AND ONLY A TOKEN TELLS THEM APART. Authenticated, it
            # is definitive: the repo is there and has published nothing. Unauthenticated against a
            # PRIVATE repo - which ours is while it is being prepared - GitHub returns the same 404
            # it returns for a repo that does not exist, deliberately, so that a private name
            # cannot be probed. Reporting "no releases" in that case would be a claim we cannot
            # back, and it would go on being reported after the first release was published.
            out["error"] = "no releases" if token else "not visible"
        else:
            out["error"] = "HTTP %d" % e.code
    except Exception as e:
        out["error"] = e.__class__.__name__
    with _rel_lock:
        # A FAILURE IS CACHED TOO, BRIEFLY. Without this, a panel that polls while GitHub is
        # refusing would keep asking and keep being refused for the rest of the hour.
        if out.get("error"):
            out["at"] = now - RELEASE_TTL_S + 300      # try again in five minutes, not six hours
        _rel_cache[ckey] = out
    return out


def version_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", str(s or ""))[:4])


def newer_than(tag, have):
    """Is `tag` a higher version than `have`? Unknown answers False, never True.

    A release check that guesses "newer" pushes somebody to replace a working payload for nothing.
    """
    a, b = version_tuple(tag), version_tuple(have)
    if not a or not b:
        return False
    return a > b


# --------------------------------------------------------------------------- #
# the folder scan - shared with tools/gen_payload_catalog.py                    #
# --------------------------------------------------------------------------- #
# OUR OWN ARTIFACTS, BY NAME. The owner keeps a copy of the shop in that folder, which is useful to
# them and must never become an .incbin: an artifact carrying a copy of itself is the recursion
# ps4-app/build-all-wsl.sh exists to prevent. Catalogued and marked, never embedded.
OURS = ("PKG-MUTANT-SHOP.elf", "PKG-MUTANT-SHOP-PS4.elf")
# OUR OWN VERSION, READ THE ONE WAY THAT WORKS FOR OUR OWN ARTIFACT.
#
# identify_elf() cannot be used on these and it is worth writing down why, because the reason is
# the whole shape of this app: the PS5 ELF EMBEDS ftpsrv, nanodns, kstuff, OnionHEN, Payload
# Manager and the WebKit autoloader, so every one of those projects' markers is inside it. The
# generic identifier requires exactly one marker hit and would therefore find six - and answer
# "unknown", correctly, for ever. That is also why `ours` items skipped identification entirely
# and consequently showed no version at all, which the owner noticed.
#
# So it is read by the one string only our own build writes: the web bundle's APP_VERSION. That
# line is generated from the single source of the version at build time, appears in both ELFs, and
# the other five-digit-looking numbers in the binary (a 3.84.0 quoted inside a code comment) do not
# match it. Nothing is guessed here: either that exact line is present or the version is unknown.
OURS_VER_RE = b'var APP_VERSION="([0-9][0-9.]*)"'


def ours_version(path):
    """The version of one of OUR OWN ELFs, out of the binary. "" when the line is not there."""
    try:
        with io.open(path, "rb") as f:
            blob = f.read()
    except Exception:
        return ""
    m = re.search(OURS_VER_RE, blob)
    try:
        return m.group(1).decode("ascii", "replace") if m else ""
    except Exception:
        return ""
PLATFORMS = ("PS4", "PS5")

def slug(s):
    s = re.sub(r"[^A-Za-z0-9]+", "-", (s or "").strip().lower())
    return re.sub(r"-+", "-", s).strip("-")


def sha256(path):
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version_from_name(name):
    """The version a filename admits to, or "".

    Only Payload Manager writes a version into its own binary; everything else here carries it in
    the filename if at all. Reading it from the name and SAYING that is where it came from is
    honest; inventing one is not.
    """
    m = re.search(r"[_\- ]v?(\d+\.\d+(?:\.\d+)?(?:[a-z]+\d*)?)(?=[_\-. ]|$)", name, re.I)
    return m.group(1) if m else ""


def param_json_in(path, limit=2 << 20):
    """A PS5 param.json embedded near the front of a file, or None.

    InternetBrowser-PS5M.pkg is not a package this app's parser reads - it begins \\x7fFIH and holds
    a real package further in - but it carries a plain param.json in its header region, and that is
    where its real name and content id come from. Reading it is how the tile shows "Internet
    Browser" instead of a filename.
    """
    try:
        with io.open(path, "rb") as f:
            head = f.read(limit)
    except Exception:
        return None
    i = head.find(b'"contentId"')
    if i < 0:
        return None
    start = head.rfind(b"{", 0, i)
    while start >= 0:
        depth, j, ins, esc = 0, start, False, False
        while j < len(head):
            c = head[j:j + 1]
            if ins:
                if esc:
                    esc = False
                elif c == b"\\":
                    esc = True
                elif c == b'"':
                    ins = False
            elif c == b'"':
                ins = True
            elif c == b"{":
                depth += 1
            elif c == b"}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(head[start:j + 1].decode("utf-8", "replace"))
                    except Exception:
                        obj = None
                    # IT PARSED IS NOT THE SAME AS IT IS THE RIGHT ONE. The nearest "{" before the
                    # key is usually a SIBLING that closed before it - here it is "ageLevel": {...},
                    # which is perfectly good JSON - and returning that silently loses the title and
                    # the content id. Keep walking outwards until the object actually holds the key
                    # we came looking for.
                    if isinstance(obj, dict) and "contentId" in obj:
                        return obj
                    break
            j += 1
        start = head.rfind(b"{", 0, start)
    return None


def identify_elf(path, curated_payloads):
    """Which project this ELF is, read out of the FILE rather than its name.

    THE OWNER RENAMES THINGS, AND SAID SO. A payload called anything at all is still ftpsrv if the
    ftpsrv banner is inside it, and it keeps its port, its upstream and its warnings. Matching on
    the filename would lose every one of those the moment somebody tidied a folder.

    Returns (id or None, version or "", where the version came from). A marker that matches more
    than one curated project identifies nothing - that is a marker that needs to be better, and
    saying "unknown" is the honest answer until it is.
    """
    try:
        with io.open(path, "rb") as f:
            blob = f.read()
    except Exception:
        return None, "", ""
    hits = []
    for pid, c in (curated_payloads or {}).items():
        mark = c.get("marker")
        if mark and mark.encode("utf-8", "replace") in blob:
            hits.append(pid)
    if len(hits) != 1:
        return None, "", ""
    pid = hits[0]
    rx = (curated_payloads.get(pid) or {}).get("ver_re")
    if rx:
        m = re.search(rx.encode("utf-8", "replace"), blob)
        if m:
            try:
                return pid, m.group(1).decode("ascii", "replace"), "file"
            except Exception:
                pass
    return pid, "", ""


def read_pkg(path):
    """Title/id/version out of a package, using the app's OWN parser.

    The library and this panel must agree about what a package is, so this calls the same
    companion/pkg_meta.py the library calls rather than a second implementation that could drift.
    """
    try:
        import pkg_meta
    except Exception:
        return None
    try:
        return pkg_meta.parse_pkg(path)
    except Exception:
        return None


def folder_app(d):
    """A PS5 app folder (eboot.bin + sce_sys/param.json), or None.

    This is the RetroArch shape. It is not a package and must never be sent down the package lane;
    it goes where a game backup goes.
    """
    pj = os.path.join(d, "sce_sys", "param.json")
    if not (os.path.isfile(pj) and os.path.isfile(os.path.join(d, "eboot.bin"))):
        return None
    try:
        with io.open(pj, encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception:
        return None


_dirstat_memo = {}


def dir_stats(d):
    """Files and bytes under a folder-shaped app, remembered between scans.

    RetroArch alone is 3,000 files. Walking it on every live rescan - which now happens while the
    panel is open - would make the panel the most expensive thing in the app. The folder's own mtime
    changes when anything is added or removed at its top level, and for a shipped app that is the
    only thing that ever changes, so it is a sound key. A cold cache costs exactly one walk.
    """
    try:
        key = (d, os.path.getmtime(d))
    except OSError:
        return 0, 0
    hit = _dirstat_memo.get(key)
    if hit:
        return hit
    n = 0
    total = 0
    for dp, _dn, fn in os.walk(d):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
                n += 1
            except OSError:
                pass
    if len(_dirstat_memo) > 64:
        _dirstat_memo.clear()
    _dirstat_memo[key] = (n, total)
    return n, total


def scan(src, curated=None):
    items = []
    for kind, top in (("payload", "Payloads"), ("homebrew", "Homebrews")):
        for plat in PLATFORMS:
            base = os.path.join(src, top, plat)
            if not os.path.isdir(base):
                continue
            for group in sorted(os.listdir(base)):
                gdir = os.path.join(base, group)
                if not os.path.isdir(gdir):
                    continue
                items.extend(scan_group(kind, plat, group, gdir, src, curated))
    items.sort(key=lambda it: (it["kind"], it["platform"], it["id"]))
    return items


def scan_group(kind, plat, group, gdir, src, curated=None):
    out = []
    gslug = slug(group)
    for name in sorted(os.listdir(gdir)):
        p = os.path.join(gdir, name)

        if os.path.isdir(p):
            pj = folder_app(p)
            if pj and kind == "homebrew":
                nfiles, total = dir_stats(p)
                tid = (pj.get("titleId") or name or "").strip()
                loc = (pj.get("localizedParameters") or {})
                title = ((loc.get(loc.get("defaultLanguage") or "en-US") or {}).get("titleName")
                         or group)
                out.append({
                    "id": tid or gslug, "kind": kind, "platform": plat, "group": group,
                    "shape": "folder", "path": os.path.relpath(p, src).replace("\\", "/"),
                    "title": title, "title_id": tid,
                    "content_id": pj.get("contentId") or "",
                    "version": pj.get("masterVersion") or pj.get("contentVersion") or "",
                    "files": nfiles, "size": total,
                })
            continue

        low = name.lower()
        if kind == "payload" and low.endswith(".elf"):
            # CONTENT FIRST, FOLDER SECOND. The marker inside the binary decides what this is; the
            # folder name is only the fallback for something we have never seen before.
            det, dver, dfrom = (None, "", "")
            if name in OURS:
                # Identified by being one of our own filenames - see OURS_VER_RE above for why the
                # content identifier cannot be asked about a binary that carries six other projects.
                dver = ours_version(p)
                if dver:
                    dfrom = "file"
            else:
                det, dver, dfrom = identify_elf(p, (curated or {}).get("payloads"))
            ver = dver or version_from_name(name)
            out.append({
                "id": det or gslug, "kind": kind, "platform": plat, "group": group,
                "shape": "elf", "path": os.path.relpath(p, src).replace("\\", "/"),
                "file": name, "title": group, "version": ver,
                "version_from": (dfrom or ("name" if ver else "")),
                "identified_by": "content" if det else "folder",
                "size": os.path.getsize(p), "sha256": sha256(p),
                "ours": name in OURS,
            })
        elif kind == "homebrew" and low.endswith(".pkg"):
            m = read_pkg(p) or {}
            pj = None if m else param_json_in(p)
            tid = (m.get("title_id") or "").strip()
            cid = (m.get("content_id") or "").strip()
            title = (m.get("title") or "").strip()
            unreadable = not m
            if pj:
                cid = cid or (pj.get("contentId") or "")
                loc = (pj.get("localizedParameters") or {})
                title = title or ((loc.get(loc.get("defaultLanguage") or "en-US") or {})
                                  .get("titleName") or "")
            if not tid and cid:
                mm = re.match(r"[A-Z0-9]{6}-([A-Z0-9]{9})_", cid)
                if mm:
                    tid = mm.group(1)
            out.append({
                "id": tid or gslug, "kind": kind, "platform": plat, "group": group,
                "shape": "pkg", "path": os.path.relpath(p, src).replace("\\", "/"),
                "file": name, "title": title or group, "title_id": tid, "content_id": cid,
                "version": (m.get("app_ver") or m.get("version") or version_from_name(name) or ""),
                "category": m.get("category") or "",
                "size": os.path.getsize(p), "sha256": sha256(p),
                # NOT A FAILURE, A FACT. The owner asked for this one to be listed and for the
                # console to decide, so it is carried with the flag set rather than dropped.
                "unreadable": unreadable,
            })
    return out


def apply_curated(items, curated):
    for it in items:
        table = curated.get("payloads" if it["kind"] == "payload" else "homebrews", {})
        c = table.get(it.get("title_id") or "") or table.get(it["id"]) or table.get(
            slug(it.get("group", ""))) or {}
        # `marker` travels with the item because the update lane checks it INSIDE a download
        # before letting it replace a working payload.
        for k in ("title", "blurb", "port", "autostart", "layer", "repo", "asset", "ours",
                  "marker", "probe"):
            if k in c and c[k] is not None and (k != "title" or c[k]):
                it[k] = c[k]
        it.setdefault("port", 0)
        it.setdefault("autostart", False)
        it.setdefault("repo", None)
        it["curated"] = bool(c)
    return items


def known_versions(root=None):
    """Versions this app has PROVEN, keyed by the file's own sha256.

    Some payloads carry no version anywhere - not in the binary, not in the filename - so the only
    honest answer would be a blank. But a file that is byte-for-byte the size of an asset in an
    upstream release IS that release, and the running app makes that comparison every time it
    checks for updates. What it learns is written to assets/payloads/known-versions.json and
    stamped in here, so the answer ships inside both ELFs and is there on a console with no PC and
    no internet. Keyed by sha256, so a different build of the same project never inherits it.
    """
    here = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(here, "assets", "payloads", "known-versions.json")
    try:
        with io.open(p, encoding="utf-8") as f:
            return (json.load(f).get("versions") or {})
    except Exception:
        return {}


def build(src, curated, known=None):
    """The catalogue for a folder. `curated` is the hand-written table, passed in by the caller.

    It is passed in rather than read here because the two callers get it from different places: the
    build tool reads assets/payloads/curated.json, and the running app reads the copy the generator
    embeds in the catalogue itself - so a live rescan needs no file the exe does not carry.
    """
    if not os.path.isdir(src):
        raise SystemExit("source folder not found: %s" % src)
    items = apply_curated(scan(src, curated), curated)
    kv = known if known is not None else known_versions()
    for it in items:
        if it.get("version") or it.get("kind") != "payload":
            continue
        hit = kv.get(it.get("sha256") or "")
        if hit and hit.get("version"):
            it["version"] = hit["version"]
            it["version_from"] = "release"
    tot = {}
    for it in items:
        key = "%s_%s" % (it["kind"], it["platform"].lower())
        tot[key] = tot.get(key, 0) + 1
    return {
        # NO TIMESTAMP. A generated file that changes every run cannot be compared by --check, and
        # this project already has a gate that exists because a stale blob shipped unnoticed.
        "schema": 1,
        # THE CURATED TABLE TRAVELS WITH THE CATALOGUE. A live rescan - after the update button
        # replaces a payload, or the owner drops a file in while the panel is open - needs the
        # markers, ports and upstreams, and this way it needs no file the exe does not already ship.
        "curated": curated,
        "source_name": os.path.basename(src.rstrip("\\/")),
        "counts": tot,
        "items": items,
    }


# --------------------------------------------------------------------------- #
# the live view                                                                 #
# --------------------------------------------------------------------------- #
_live_lock = threading.Lock()
_live = {"sig": None, "cat": None}


def folder_sig(root):
    """A cheap fingerprint of the source folder: what is there, how big, how recently touched.

    DELIBERATELY SHALLOW. Payloads are walked fully - eleven files - but a homebrew is identified by
    its own entry rather than by everything inside it, because one of them is a 3,000-file app
    folder and this runs while somebody watches a panel. Adding, removing, renaming or replacing
    anything the catalogue can see changes this string; editing a texture three levels inside
    RetroArch does not, which is the right trade.
    """
    parts = []
    for top in ("Payloads", "Homebrews"):
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        for plat in PLATFORMS:
            pdir = os.path.join(base, plat)
            if not os.path.isdir(pdir):
                continue
            try:
                groups = sorted(os.listdir(pdir))
            except OSError:
                continue
            for g in groups:
                gdir = os.path.join(pdir, g)
                try:
                    names = sorted(os.listdir(gdir))
                except OSError:
                    continue
                for n in names:
                    p = os.path.join(gdir, n)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    parts.append("%s/%s/%s|%d|%d" % (plat, g, n, st.st_size, int(st.st_mtime)))
    return hashlib.sha256("\n".join(parts).encode("utf-8", "replace")).hexdigest()[:16]


def live_catalog(cfg, web_dir):
    """The catalogue as the folder is RIGHT NOW, or the shipped one when the folder is not here.

    WHY THIS EXISTS. The catalogue is generated at build time so a console with no PC still has one.
    But the panel also has to notice a payload the update button just replaced, and a file the owner
    dropped in while looking at it - neither of which a baked file can show. So the PC rescans when
    the folder's fingerprint changes and serves that; a PC without the folder, and both consoles,
    keep using the shipped copy.

    The curated table comes from the shipped catalogue itself, so this needs no file the exe does
    not already carry.
    """
    baked = catalog(web_dir)
    root = source_root(cfg)
    if not os.path.isdir(root):
        return baked, ""
    try:
        sig = folder_sig(root)
    except Exception:
        return baked, ""
    with _live_lock:
        if _live["sig"] == sig and _live["cat"] is not None:
            return _live["cat"], sig
    try:
        cat = build(root, baked.get("curated") or {})
    except Exception:
        return baked, sig
    with _live_lock:
        _live["sig"], _live["cat"] = sig, cat
    return cat, sig


# --------------------------------------------------------------------------- #
# updating a payload from its upstream release                                  #
# --------------------------------------------------------------------------- #
def pick_asset(rel, item):
    """The one file in a release that would replace THIS item, or None.

    A release usually carries several things - the WebKit autoloader ships an installer ELF, a
    Python host and a Windows exe - and only one of them is the payload we hold. The curated
    `asset` substring narrows it, and the extension decides the rest: a payload is an .elf and a
    homebrew is a .pkg, so a release of host tools is correctly "no update for this".
    """
    want_ext = ".pkg" if item.get("kind") == "homebrew" else ".elf"
    needle = str(item.get("asset") or "").lower()
    plat = str(item.get("platform") or "").lower()          # "ps4" / "ps5"
    other = "ps4" if plat == "ps5" else "ps5"
    cands = []
    for a in (rel.get("assets") or []):
        name = str(a.get("name") or "")
        low = name.lower()
        if not low.endswith(want_ext):
            continue
        if needle and needle not in low:
            continue
        cands.append((low, a))
    if not cands:
        return None
    # THE PLATFORM IS PART OF THE MATCH, and leaving it out was a real bug: ftpsrv publishes
    # ftpsrv-ps4.elf and ftpsrv-ps5.elf in one release, and picking "the shortest name that ends
    # .elf" offered the PS4 build as the PS5's update. An asset naming the OTHER console is never
    # a candidate; one naming this console wins; anything neutral is the fallback.
    named = [a for low, a in cands if plat and plat in low]
    if named:
        return min(named, key=lambda a: len(str(a.get("name") or "")))
    neutral = [a for low, a in cands if not (other and other in low)]
    if not neutral:
        return None
    return min(neutral, key=lambda a: len(str(a.get("name") or "")))


def download_asset(cfg, item, asset, log=None, timeout=180):  # noqa: C901
    """Fetch a release asset and put it where the old file was.

    Returns (ok, sentence, new_version).

    THREE THINGS THIS REFUSES TO DO, each of which would be worse than not updating:

      * overwrite before the download is complete - it writes <name>.part and only then moves it
        into place, so a dropped connection leaves the working payload untouched;
      * accept a file that is not the thing it replaces - the marker that identifies the project is
        checked INSIDE the downloaded bytes before anything is moved. A release whose asset names
        drifted, or a redirect to something else entirely, is refused rather than installed;
      * leave two copies behind. The new file usually has a new name (…_v0.5.1.elf beside
        …_v0.5.0.elf) and both would then be catalogued as the same project, so the old one is
        removed once the new one is in place - and kept as .bak until that moment.
    """
    say = log or (lambda m: None)
    src = local_path(cfg, item)
    if not src:
        return False, "That file is not on this PC, so there is nothing to replace.", ""
    # A PRIVATE RELEASE IS NOT FETCHED FROM THE BROWSER LINK. With a token we ask the API for the
    # asset itself, which is the only route that works while our own repository is private; without
    # one the public browser link is right and nothing changes for the third-party upstreams.
    tok = gh_token(cfg)
    url = (asset.get("api_url") if (tok and asset.get("api_url")) else asset.get("url"))
    if not url:
        return False, "That release has no download for this file.", ""
    folder = os.path.dirname(src)
    newname = os.path.basename(str(asset.get("name") or "")) or os.path.basename(src)
    dest = os.path.join(folder, newname)
    part = dest + ".part"
    try:
        dh = {"User-Agent": "PKG-MUTANT-SHOP"}
        if tok and url == asset.get("api_url"):
            dh["Authorization"] = "Bearer " + tok
            dh["Accept"] = "application/octet-stream"
        req = urllib.request.Request(url, headers=dh)
        with urllib.request.urlopen(req, timeout=timeout) as r, io.open(part, "wb") as f:
            got = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
    except Exception as e:
        try:
            os.remove(part)
        except OSError:
            pass
        return False, "The download did not finish (%s)." % e.__class__.__name__, ""

    want = int(asset.get("size") or 0)
    if want and got != want:
        try:
            os.remove(part)
        except OSError:
            pass
        return False, "The download was %d bytes and should be %d." % (got, want), ""

    # IS IT STILL THE SAME PROJECT? The marker is what identifies this payload no matter what the
    # file is called, so it is also the right thing to check before letting a download replace it.
    mark = (item.get("marker") or "")
    if mark:
        try:
            with io.open(part, "rb") as f:
                blob = f.read()
        except Exception:
            blob = b""
        if mark.encode("utf-8", "replace") not in blob:
            try:
                os.remove(part)
            except OSError:
                pass
            return False, "That download is not %s, so it was not used." % (
                item.get("title") or item.get("id")), ""

    bak = src + ".bak"
    try:
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(src, bak)          # the old one survives until the new one is in place
        os.replace(part, dest)
        os.remove(bak)
    except Exception as e:
        # put it back exactly as it was
        try:
            if not os.path.exists(src) and os.path.exists(bak):
                os.replace(bak, src)
        except OSError:
            pass
        try:
            os.remove(part)
        except OSError:
            pass
        return False, "Could not replace the old file (%s)." % e.__class__.__name__, ""

    say("[payloads] updated %s -> %s (%d bytes)" % (os.path.basename(src), newname, got))
    # THE VERSION WE JUST INSTALLED, out of the file where the name does not carry it. Our own
    # releases publish PKG-MUTANT-SHOP.elf with no version in the filename - deliberately, because
    # the name is a contract the update lane matches on - so version_from_name() answered "" and the
    # panel reported an update with no version at all. Our artifacts say it inside themselves.
    ver = version_from_name(newname)
    if not ver and (item or {}).get("ours"):
        ver = ours_version(dest)
    return True, "Updated to %s." % newname, ver


_kv_lock = threading.Lock()


def remember_version(item, tag, repo, asset_name, root=None):
    """Write a proven version into assets/payloads/known-versions.json.

    Best effort and never fatal: this runs inside a request the panel polls, on a PC that may have
    the repository checked out read-only, and a version we could not write down is a cosmetic loss
    - the app simply proves it again next time. Writes through a .part and renames, because a
    half-written JSON here would make the next BUILD blind rather than just this run.
    """
    sha = (item or {}).get("sha256")
    if not (sha and tag):
        return False
    here = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(here, "assets", "payloads", "known-versions.json")
    with _kv_lock:
        try:
            with io.open(p, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            doc = {"versions": {}}
        vers = doc.setdefault("versions", {})
        if vers.get(sha, {}).get("version") == tag:
            return True                         # already known - do not rewrite the file
        vers[sha] = {"version": tag, "from": repo, "asset": asset_name}
        try:
            tmp = p + ".part"
            with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
            os.replace(tmp, p)
            return True
        except Exception:
            try:
                os.remove(p + ".part")
            except OSError:
                pass
            return False


def console_state(ip, timeout=6.0):
    """Everything a console knows about payloads and homebrews, in ONE request.

    console_live() and console_payload_sizes() each asked the same route for a different field,
    which on a PS5 - one accept loop - is two round trips for one answer. This is that answer:

        live  {stem}          what is running, by comparable stem
        have  {stem: size}    the payloads it holds in PB_DIR, i.e. what it could start by itself
        kept  {size: path}    every homebrew package it can reach, on /data or a USB stick

    None when the console did not answer. "It told me nothing is running" and "it did not tell me"
    are different, and only the first should grey a tile.
    """
    if not ip:
        return None
    try:
        with urllib.request.urlopen("http://%s:8710/api/payloads" % ip, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    st = j.get("state")
    if not isinstance(st, dict):
        return None
    out = {"live": None, "have": None, "kept": {}}
    if st.get("live") is not None:
        out["live"] = {proc_stem(n) for n in st["live"] if n}
    if st.get("have") is not None:
        out["have"] = {}
        for e in st["have"]:
            try:
                out["have"][proc_stem(e.get("n"))] = int(e.get("s") or 0)
            except Exception:
                continue
    for e in (st.get("kept") or []):
        try:
            if e.get("s"):
                out["kept"][int(e["s"])] = e.get("p") or e.get("n")
        except Exception:
            continue
    return out


def console_live(ip, timeout=6.0):
    """The payloads a console says are running, as comparable stems, or None if it could not say.

    THE CONSOLE IS THE ONLY ONE THAT CAN SEE SOME OF THEM. A PC can probe a TCP port from outside,
    but nanodns listens on UDP and answers no query sent to it from the LAN even while running -
    measured on both consoles - so the only observable fact is that its port is taken, and only
    something running ON the console can try to take it. The PS5 additionally has Payload Manager's
    process list, which catches kstuff and ShadowMountPlus. Asking the console gets all of that for
    one request; probing from here gets none of it.

    None, not an empty set, when the console did not answer: "it told me nothing is running" and
    "it did not tell me" are different, and the second must not turn every tile grey.
    """
    if not ip:
        return None
    try:
        with urllib.request.urlopen("http://%s:8710/api/payloads" % ip, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    live = ((j.get("state") or {}).get("live"))
    if live is None:
        return None
    return {proc_stem(n) for n in live if n}


def console_file_size(bridge, path, timeout=15):
    """Size of one file on a console, or None when it is not there / could not be asked.

    There is no fs_stat on the bridge - only fs_list - so this asks for the parent directory and
    picks the entry out. That matters more than it sounds: seed_homebrew() guarded on a
    `hasattr(bridge, "fs_stat")` that was never true, so it re-copied a 60 MB package every single
    time instead of noticing the console already had it.
    """
    if not path or bridge is None:
        return None
    parent, _, name = path.rpartition("/")
    if not (parent and name):
        return None
    try:
        rows = bridge.fs_list(parent, timeout=timeout)
    except Exception:
        return None
    if rows is None:
        return None
    for e in rows:
        try:
            if not e.get("dir") and str(e.get("name") or "") == name:
                return int(e.get("size") or 0)
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- #
# the fleet: this PC, the console, and any other companion on the network       #
# --------------------------------------------------------------------------- #
def fleet_summary(cfg, web_dir):
    """What THIS companion can hand over, compact enough to advertise to peers.

    A second PC running this exe has no copy of the owner's folder, so on its own every tile reads
    "not on this PC" and nothing can be pressed. But the bytes exist - on a console, or on the
    machine that does have the folder - and the app is meant to behave as one thing however many
    devices are looking at it. This is the half of that a peer can see.
    """
    cat, _sig = live_catalog(cfg, web_dir)
    out = []
    for it in (cat.get("items") or []):
        # OUR OWN ARTIFACTS ARE ADVERTISED NOW. They were skipped here on the reasoning that every
        # machine builds its own, but that is not the situation the owner is in: the PS5's ELF is
        # 34 MB and deliberately not bundled in the exe, so a second PC has no copy at all and the
        # tile read "not on this PC" for the one payload the fleet most obviously has - while the PC
        # that built it sat on the same LAN. peer_with() matches on size, so a peer can only stand
        # in for a byte-identical build, and ask_peer() has the peer run its OWN deploy lane
        # (version-expect, .prev backup, pldmgr staging), not a raw copy of bytes we shipped it.
        if not local_path(cfg, it):
            continue
        out.append({"id": it.get("id"), "platform": it.get("platform"),
                    "kind": it.get("kind"), "size": it.get("size", 0),
                    "sha256": it.get("sha256", ""), "version": it.get("version", "")})
    return out


def peer_with(peers, item):
    """The first peer advertising this exact item, or None.

    Matched on id + platform + size: the id says which project, and the size says it is the same
    build. A peer holding an older release of the same payload is not a substitute for this one.
    """
    want_id = (item or {}).get("id")
    want_plat = str((item or {}).get("platform") or "").upper()
    want_size = int((item or {}).get("size") or 0)
    # SIZE IS NOT PART OF THE MATCH FOR OUR OWN ARTIFACT, and the difference is the whole question
    # being asked. For a third-party payload it is "is this the same build the tile is describing?",
    # and a peer holding a different ftpsrv is not a substitute - it would quietly downgrade or
    # upgrade something the owner chose. For OUR app the question is "can anyone here give me the
    # shop at all?", and any build of it is an answer: a PC with no folder has no copy to be
    # inconsistent with, and the peer runs its own deploy lane and sends whatever it actually has.
    #
    # Measured, which is why this is not a hypothetical: a second PC's baked catalogue recorded our
    # PS5 ELF at 34,139,632 bytes (the 3.83.3 copy that was in the folder when its exe was built),
    # the PC beside it was advertising the 45,350,920-byte 3.86.0 build, and the sizes disagreeing
    # made the tile read "nobody has this" about a file on the same LAN.
    ours = bool((item or {}).get("ours"))
    for p in (peers or []):
        if not p.get("online"):
            continue
        for e in (p.get("payloads") or []):
            if (e.get("id") == want_id
                    and str(e.get("platform") or "").upper() == want_plat
                    and (ours or not want_size or int(e.get("size") or 0) == want_size)):
                return p
    return None


def ask_peer(peer, action, body, timeout=900):
    """Have another companion carry out an action this one cannot.

    IT IS THE SAME REQUEST, SENT ONE HOP FURTHER. The peer has the file and the same LAN access to
    the same consoles, so the honest way for a PC without the folder to start a payload is to ask a
    PC that has it - not to invent a way of moving bytes it does not hold. The reply is passed back
    untouched, so the owner reads the peer's own words.
    """
    url = str(peer.get("url") or "").rstrip("/")
    if not url:
        return None
    try:
        data = json.dumps(dict(body or {}, via_peer=1)).encode("utf-8")
        req = urllib.request.Request(url + "/api/payloads/" + action, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
