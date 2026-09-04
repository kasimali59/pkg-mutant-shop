#!/usr/bin/env python3
"""
PKG MUTANT SHOP - deploy tool
=============================
Push things to the jailbroken PS5. Stdlib only. Reads defaults from config.json.

  python deploy.py check
      Test the ELF-loader port and FTP login on the PS5.

  python deploy.py payload <file.elf> [--ip IP] [--port 9021]
      Send an .elf payload to the PS5 payload/ELF-loader port (like a netcat sender).
      Use this to inject websrv-ps5.elf, shadowmountplus.elf, etc.

  python deploy.py app [--ip IP] [--ftp-port 2121] [--dest /data/homebrew/PKG_MUTANT_SHOP]
                       [--companion http://PC-IP:8710]
      Bundle the homebrew app (icon + manifest + web UI + assets), inject config.js pointing at your
      companion, and FTP-upload it to the PS5 homebrew folder. Launch it from the homebrew launcher
      (Elf Arsenal / etaHEN / websrv). See BUILD-PS5-APP.md.
"""
import argparse
import ftplib
import os
import shutil
import socket
import sys
import tempfile
import urllib.parse
import urllib.request

from server import load_config, lan_ip

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "web")
PS5APP = os.path.join(ROOT, "ps5-app")

G, Y, R, X = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def tcp_ok(ip, port, timeout=2.0):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
def send_payload(path, ip, port):
    if not os.path.isfile(path):
        print(R + "no such file: %s" % path + X)
        return False
    data = open(path, "rb").read()
    print("Sending %s (%d bytes) -> %s:%d" % (os.path.basename(path), len(data), ip, port))
    try:
        with socket.create_connection((ip, port), timeout=8) as s:
            s.sendall(data)
        print(G + "sent." + X + " (watch the PS5 - the payload runs on receipt)")
        return True
    except OSError as e:
        print(R + "send failed: %s" % e + X)
        return False


# --------------------------------------------------------------------------- #
# Which FTP the console is running decides the port: Elf Arsenal's ftpsrv served 2121, etaHEN's own
# FTP serves 1337, and Arsenal is no longer bundled. `deploy.py app` is how the on-console UI is
# updated, so dialling only the configured port meant a console on etaHEN could not be deployed to
# at all - it just said "upload error" and the PS5 kept serving the old page.
FTP_FALLBACKS = (2121, 1337)


def ftp_ports(configured):
    seen, order = set(), []
    for p in [configured] + list(FTP_FALLBACKS):
        if p and p not in seen:
            seen.add(p)
            order.append(p)
    return order


# ---------------------------------------------------------------------------------------------
# Prefer OUR OWN on-console file API. deploy.py used to be pure FTP, and FTP on a PS5 belongs to
# whichever third-party payload is running - so the moment etaHEN was removed, our own deploy tool
# stopped working. That is a funny way to be independent. The shop ELF serves /api/fs/write on
# :8710 and does not care whether any FTP server exists.
# ---------------------------------------------------------------------------------------------
def shop_write(ip, remote, local, port=8710, timeout=1800):
    """Push one file through the on-console shop. Returns True on success, False to fall back."""
    try:
        size = os.path.getsize(local)
        url = "http://%s:%d/api/fs/write?path=%s" % (ip, port, urllib.parse.quote(remote, safe=""))
        with open(local, "rb") as fh:
            req = urllib.request.Request(
                url, data=fh, method="POST",
                headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return b'"ok":true' in r.read()
    except Exception:
        return False


def shop_alive(ip, port=8710, timeout=4):
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (ip, port), timeout=timeout) as r:
            return b'"ok":true' in r.read()
    except Exception:
        return False


def ftp_login(ip, port):
    last = None
    for p in ftp_ports(port):
        ftp = ftplib.FTP()
        try:
            ftp.connect(ip, p, timeout=8)
            ftp.login()
            if p != port:
                print("  (FTP answered on :%d, not the configured :%d)" % (p, port))
            return ftp
        except Exception as e:
            last = e
            try:
                ftp.close()
            except Exception:
                pass
    raise last if last else OSError("no FTP port reachable on %s" % ip)


def ftp_mkd(ftp, path):
    try:
        ftp.mkd(path)
    except ftplib.error_perm:
        pass  # already exists


def ftp_put_tree(ftp, localdir, remotedir):
    ftp_mkd(ftp, remotedir)
    for root, _dirs, files in os.walk(localdir):
        rel = os.path.relpath(root, localdir).replace("\\", "/")
        rdir = remotedir if rel == "." else remotedir + "/" + rel
        if rel != ".":
            ftp_mkd(ftp, rdir)
        for fn in files:
            local = os.path.join(root, fn)
            with open(local, "rb") as f:
                ftp.storbinary("STOR " + rdir + "/" + fn, f)
            print("  put %s" % (rdir + "/" + fn))


def build_bundle(companion_url):
    tmp = tempfile.mkdtemp(prefix="pms_app_")
    for item in os.listdir(PS5APP):
        s, d = os.path.join(PS5APP, item), os.path.join(tmp, item)
        shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy2(s, d)
    shutil.copy2(os.path.join(WEB, "index.html"), os.path.join(tmp, "index.html"))
    assets = os.path.join(WEB, "assets")
    if os.path.isdir(assets):
        dst = os.path.join(tmp, "assets")
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(assets, dst)
    with open(os.path.join(tmp, "config.js"), "w", encoding="utf-8") as f:
        f.write('window.PMS_API = "%s";\n' % companion_url)
    return tmp


def deploy_app(ip, ftp_port, dest, companion_url, web_root="/data/pkg-mutant-shop/web"):
    print("Bundling app (companion = %s)…" % companion_url)
    bundle = build_bundle(companion_url)
    n = sum(len(fs) for _r, _d, fs in os.walk(bundle))

    # OUR OWN file API first. It needs no FTP server of any kind, which is the whole point: the
    # shop ELF is what we ship, so deploying to it should not depend on somebody else's payload
    # still running.
    if shop_alive(ip):
        print("  %d files -> %s:8710 %s  (our own file API, no FTP needed)" % (n, ip, dest))
        ok = True
        for root in ([dest] + ([web_root] if web_root and web_root.rstrip("/") != dest.rstrip("/") else [])):
            for dp, _dn, fns in os.walk(bundle):
                rel = os.path.relpath(dp, bundle).replace("\\", "/")
                remote = root if rel in (".", "") else "%s/%s" % (root, rel)
                for f in fns:
                    if not shop_write(ip, "%s/%s" % (remote, f), os.path.join(dp, f)):
                        print(R + "  failed: %s/%s" % (remote, f) + X)
                        ok = False
        shutil.rmtree(bundle, ignore_errors=True)
        if ok:
            print(G + "app deployed." + X + " Launch 'PKG MUTANT SHOP' from the homebrew launcher.")
        return ok

    print("  %d files -> ftp %s:%d %s  (shop not reachable, using FTP)" % (n, ip, ftp_port, dest))
    try:
        ftp = ftp_login(ip, ftp_port)
    except OSError as e:
        print(R + "FTP connect/login failed: %s" % e + X)
        print(Y + "Neither the PKG MUTANT SHOP ELF (:8710) nor an FTP server is answering. Load the "
              "shop from Payload Manager - then no FTP is needed at all." + X)
        shutil.rmtree(bundle, ignore_errors=True)
        return False
    try:
        ftp_put_tree(ftp, bundle, dest)
        # DEPLOY-GAP FIX: the on-console server.c serves the UI from WEB_ROOT (/data/pkg-mutant-shop/web),
        # which is NOT the homebrew app folder — so without this mirror, UI changes (version/i18n/devices)
        # never reach the PS5. Push the same bundle there too.
        if web_root and web_root.rstrip("/") != dest.rstrip("/"):
            print("  mirroring UI -> %s (path the on-console app actually serves)" % web_root)
            ftp_put_tree(ftp, bundle, web_root)
        ftp.quit()
        print(G + "app deployed." + X + " Launch 'PKG MUTANT SHOP' from the homebrew launcher.")
        return True
    except (ftplib.error_perm, OSError) as e:
        print(R + "upload error: %s" % e + X)
        return False
    finally:
        shutil.rmtree(bundle, ignore_errors=True)


# --------------------------------------------------------------------------- #
def main():
    cfg = load_config()
    ip_default = (cfg.get("consoles") or [{}])[0].get("ip") if cfg.get("consoles") else cfg.get("ps5_ip")
    ftp_default = cfg.get("ftp", {}).get("port", 2121)
    comp_default = "http://%s:%d" % (lan_ip(), cfg["companion"]["port"])

    ap = argparse.ArgumentParser(description="PKG MUTANT SHOP deploy tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--ip", default=ip_default)
    c.add_argument("--elf-port", type=int, default=9021)
    c.add_argument("--ftp-port", type=int, default=ftp_default)

    p = sub.add_parser("payload")
    p.add_argument("file")
    p.add_argument("--ip", default=ip_default)
    p.add_argument("--port", type=int, default=9021)

    a = sub.add_parser("app")
    a.add_argument("--ip", default=ip_default)
    a.add_argument("--ftp-port", type=int, default=ftp_default)
    a.add_argument("--dest", default="/data/homebrew/PKG_MUTANT_SHOP")
    a.add_argument("--companion", default=comp_default)

    args = ap.parse_args()
    if not args.__dict__.get("ip"):
        print(R + "No PS5 IP. Set ps5_ip/consoles in config.json or pass --ip." + X)
        sys.exit(2)

    if args.cmd == "check":
        print("PS5 %s:" % args.ip)
        print(("  ELF loader :%d  " % args.elf_port) + (G + "reachable" + X if tcp_ok(args.ip, args.elf_port) else R + "no route" + X))
        try:
            ftp_login(args.ip, args.ftp_port).quit()
            print("  FTP :%d  %sreachable%s" % (args.ftp_port, G, X))
        except OSError:
            print("  FTP :%d  %sno route%s" % (args.ftp_port, R, X))
    elif args.cmd == "payload":
        send_payload(args.file, args.ip, args.port)
    elif args.cmd == "app":
        deploy_app(args.ip, args.ftp_port, args.dest, args.companion)


if __name__ == "__main__":
    main()
