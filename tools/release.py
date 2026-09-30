# -*- coding: utf-8 -*-
"""Cut a GitHub release: check everything first, then tag, then upload.

    python tools/release.py 3.86.0            # check, tag, push, publish
    python tools/release.py 3.86.0 --dry-run  # every check, uploads nothing, tags nothing
    python tools/release.py 3.86.0 --draft    # published but not visible yet

WHY THIS IS A TOOL AND NOT A CHECKLIST. A release is the one thing in this repo that other people's
consoles act on: the app reads the release feed and offers the assets by name, so a release with the
wrong version in its binaries, a stale ELF, or a renamed asset is not a cosmetic mistake - it breaks
the update lane on every installed copy, and it breaks it silently.

So this refuses rather than guesses, and each refusal is a thing that has actually gone wrong
somewhere in this repo:

  * THE VERSION MUST ALREADY BE IN THE SOURCE, in all four places. It is not written by this tool.
    A release whose tag says 3.86.0 while the artifacts report 3.85.0 is worse than no release,
    because the app compares the tag against the version INSIDE the file it holds - so the update
    would be offered for ever and taking it would change nothing.
  * THE ARTIFACTS MUST BE NEWER THAN THEIR SOURCES. Building only ps5-app/onconsole/build-wsl.sh and
    forgetting ps4-app/build-all-wsl.sh is a mistake already made here: the home-screen icon carries
    its own copy of the payload, so the icon stayed a version behind while everything reported fine.
  * THE ASSET NAMES ARE A CONTRACT. companion/payloads.py picks a console's file out of the release
    by name - anything containing "ps4" is the PS4's, the neutral name is the PS5's. They are
    uploaded under their build names, never renamed.
  * EVERY GATE RUNS. tools/ready_check.py is the same thing CI would be.

See RELEASING.md for what is worth publishing at all (not every version is).
"""
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# WHERE THE VERSION IS WRITTEN, AND HOW IT READS IN EACH FILE. Four files, one number; the app's own
# update check reads the fourth one back OUT of the built binaries (payloads.OURS_VER_RE), which is
# why web/index.html is in this list and not just the servers.
VERSION_SITES = [
    ("companion/server.py",            r'^VERSION\s*=\s*"([0-9][0-9.]*)"'),
    ("ps5-app/onconsole/server.c",     r'^#define\s+SHOP_VERSION\s+"([0-9][0-9.]*)"'),
    ("ps4-app/onconsole/server_ps4.c", r'^#define\s+SHOP_VERSION\s+"([0-9][0-9.]*)"'),
    ("web/index.html",                 r'var APP_VERSION="([0-9][0-9.]*)"'),
]

# THE THREE ARTIFACTS, and the newest source each one must be younger than. "we only have 3 real
# artifacts" - the owner, about a fourth file that was not one.
ARTIFACTS = [
    ("companion/dist/PKG-MUTANT-SHOP.exe",      ["companion", "web", "assets"]),
    ("ps5-app/onconsole/PKG-MUTANT-SHOP.elf",   ["ps5-app/onconsole", "web"]),
    ("ps4-app/onconsole/PKG-MUTANT-SHOP-PS4.elf", ["ps4-app/onconsole", "web"]),
]

# Directories whose contents are build OUTPUT, so their timestamps say nothing about whether an
# artifact is stale. Without this every artifact looks older than the tree that contains it.
IGNORE_DIRS = {".git", "__pycache__", "build", "dist", "release", "node_modules", ".cache"}
IGNORE_EXT = {".elf", ".exe", ".pkg", ".h", ".pyc", ".bak", ".part", ".prev"}
# RUNTIME STATE IS NOT SOURCE. The running app rewrites companion/config.json (the saved console
# addresses, the device id) and installed.json whenever anything changes, so with these counted a
# freshly built exe is "older than its sources" within seconds of being started - a staleness check
# that fires while you are looking at the thing it says is stale is a check that gets switched off.
# They are not in the exe, so they cannot make it stale.
IGNORE_FILES = {"config.json", "installed.json", "pms.log", "sources.json", "known-versions.json"}


# THE NOTES ARE UTF-8 AND A WINDOWS CONSOLE IS NOT. `--notes-only` died on the arrow in "3.85.0 ->
# 3.86.0" because cp1252 cannot encode it - a release tool that crashes while printing the release
# notes is a release tool nobody runs. The file it writes for `gh` was always UTF-8; this is only
# about what reaches the terminal.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def say(msg):
    print(msg)


def die(msg):
    print("\n  REFUSED: %s" % msg)
    sys.exit(1)


def run(args, **kw):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, **kw)


def sha256(path):
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_version(path):
    """The version a built artifact reports, or "" if it does not report one.

    TWO ARTIFACTS ARE NOT THE SAME KIND OF FILE. Both ELFs embed web/index.html with `.incbin`, so
    the line is sitting in the binary as plain bytes and a search finds it - which is exactly how
    companion/payloads.py reads the version off a downloaded asset. The EXE is a PyInstaller
    archive and its copy of the page is COMPRESSED, so the same search finds nothing and this
    refused to release a perfectly good build. Its bundled files have to be extracted, the way
    tools/check_stale_exe.py already does.
    """
    blob = io.open(path, "rb").read(64 << 20)
    m = re.search(b'var APP_VERSION="([0-9][0-9.]*)"', blob)
    if m:
        return m.group(1).decode("ascii", "replace")
    if not path.lower().endswith(".exe"):
        return ""
    try:
        from PyInstaller.archive.readers import CArchiveReader
        arch = CArchiveReader(path)
    except Exception:
        # No PyInstaller here to read it with. Say so rather than passing an unchecked artifact.
        die("cannot read %s as a PyInstaller archive - install PyInstaller, or the version inside "
            "the exe goes unchecked" % os.path.relpath(path, ROOT))
    for cand in ("web/index.html", os.path.join("web", "index.html"), "web\index.html"):
        if cand in set(arch.toc):
            got = arch.extract(cand)
            page = got[1] if isinstance(got, tuple) else got
            m = re.search(b'var APP_VERSION="([0-9][0-9.]*)"', page)
            return m.group(1).decode("ascii", "replace") if m else ""
    return ""


def newest_source(rels):
    """Newest mtime under these directories, ignoring build output.

    Only SOURCE counts. Including the generated headers (web_bundle.h, cheat_bundle.h) or the
    artifacts themselves would make every artifact permanently "stale", which is the failure mode
    that makes a staleness check get switched off.
    """
    newest, where = 0.0, ""
    for rel in rels:
        base = os.path.join(ROOT, rel.replace("/", os.sep))
        if os.path.isfile(base):
            t = os.path.getmtime(base)
            if t > newest:
                newest, where = t, rel
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in IGNORE_DIRS]
            for f in fn:
                if os.path.splitext(f)[1].lower() in IGNORE_EXT or f in IGNORE_FILES:
                    continue
                p = os.path.join(dp, f)
                try:
                    t = os.path.getmtime(p)
                except OSError:
                    continue
                if t > newest:
                    newest, where = t, os.path.relpath(p, ROOT).replace("\\", "/")
    return newest, where


def check_version(want):
    say("  version is %s everywhere" % want)
    for rel, rx in VERSION_SITES:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        try:
            s = io.open(p, encoding="utf-8", errors="replace").read()
        except Exception as e:
            die("cannot read %s (%s)" % (rel, e))
        m = re.search(rx, s, re.M)
        if not m:
            die("no version line in %s - the pattern in VERSION_SITES no longer matches" % rel)
        if m.group(1) != want:
            die("%s says %s, not %s. Set the version in the source first; this tool does not "
                "write it, because a tag that disagrees with the binaries breaks the update lane."
                % (rel, m.group(1), want))
        say("     %-40s %s" % (rel, m.group(1)))


def check_artifacts(want):
    say("  the three artifacts are built, current, and report %s" % want)
    out = []
    for rel, srcs in ARTIFACTS:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(p):
            die("%s is not built" % rel)
        # THE VERSION THE ARTIFACT ITSELF REPORTS, read the same way the app reads it off a
        # downloaded asset. If this disagrees, the build is older than the source it was built from
        # and every other check here would still have passed.
        got = artifact_version(p)
        if not got:
            die("%s does not report a version - it is not a build of this app, or the web bundle "
                "did not go in" % rel)
        if got != want:
            die("%s was built at %s, not %s - rebuild it" % (rel, got, want))
        src_t, src_f = newest_source(srcs)
        art_t = os.path.getmtime(p)
        if art_t < src_t:
            die("%s is older than %s - rebuild it%s"
                % (rel, src_f,
                   " (remember ps4-app/build-all-wsl.sh, not just the payload)"
                   if "PS4" in rel else ""))
        out.append((rel, p, os.path.getsize(p), sha256(p)))
        say("     %-44s %8.1f MB  %s" % (os.path.basename(rel), os.path.getsize(p) / 1048576.0, got))
    return out


def drop_release(tag):
    """Delete an existing release and tag so this version can be cut again.

    WHY THIS EXISTS RATHER THAN "just re-upload the assets". A release's assets and its tag have to
    be the same build: the tag says which source produced them, and that is the only thing anybody
    can check a download against. When the artifacts are rebuilt AFTER tagging - which happened the
    first time this tool was used, because the payload catalogue records a version that changes when
    the artifacts do - replacing the assets alone leaves a release whose files were never built from
    the commit it points at. Re-cutting is honest; clobbering is not.

    This is for a release nobody has taken yet. Once one is out, the next version is the answer.
    """
    say("  removing the existing %s so it can be cut again" % tag)
    r = run(["gh", "release", "delete", tag, "--yes", "--cleanup-tag"])
    if r.returncode != 0 and "release not found" not in (r.stderr or "").lower():
        say("     gh: %s" % (r.stderr.strip() or r.stdout.strip()))
    run(["git", "tag", "-d", tag])
    run(["git", "push", "origin", ":refs/tags/" + tag])
    if run(["git", "tag", "-l", tag]).stdout.strip():
        die("%s is still there after trying to remove it" % tag)


def check_tree(tag, dry):
    st = run(["git", "status", "--porcelain"])
    if st.returncode != 0:
        die("this is not a git checkout")
    if st.stdout.strip() and not dry:
        die("the working tree has uncommitted changes:\n%s"
            % "\n".join("       " + l for l in st.stdout.strip().splitlines()[:12]))
    ex = run(["git", "tag", "-l", tag])
    if ex.stdout.strip():
        die("%s already exists as a tag" % tag)
    say("  working tree is clean and %s is free" % tag)


def changelog_section(want):
    """This version's own section of CHANGELOG.md, as the release notes.

    The notes are not written twice. If the changelog does not have a section for this version then
    the release has no notes, and that is a reason to stop: a release nobody can read is a payload
    people are asked to trust on the strength of a version number.
    """
    p = os.path.join(ROOT, "CHANGELOG.md")
    s = io.open(p, encoding="utf-8", errors="replace").read().splitlines()
    start = None
    for i, line in enumerate(s):
        if re.match(r"^#{1,3}\s", line) and want in line:
            start = i
            break
    if start is None:
        die("CHANGELOG.md has no section for %s. Write it before releasing." % want)
    level = len(re.match(r"^(#+)", s[start]).group(1))
    body = []
    for line in s[start + 1:]:
        m = re.match(r"^(#+)\s", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    text = "\n".join(body).strip()
    if not text:
        die("CHANGELOG.md's section for %s is empty." % want)
    return text


def gates():
    say("  every gate (tools/ready_check.py)")
    r = run([sys.executable, os.path.join(HERE, "ready_check.py")])
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    for l in tail:
        say("     %s" % l)
    if r.returncode != 0:
        die("ready_check failed - fix it, do not release around it")


def write_release_dir(want, arts, commit):
    d = os.path.join(ROOT, "release")
    if not os.path.isdir(d):
        os.makedirs(d)
    man = {"version": want, "commit": commit,
           "assets": [{"name": os.path.basename(rel), "size": sz, "sha256": h}
                      for rel, _p, sz, h in arts]}
    mp = os.path.join(d, "manifest.json")
    io.open(mp, "w", encoding="utf-8", newline="\n").write(
        json.dumps(man, indent=2, sort_keys=True) + "\n")
    sp = os.path.join(d, "SHA256SUMS")
    io.open(sp, "w", encoding="utf-8", newline="\n").write(
        "".join("%s  %s\n" % (h, os.path.basename(rel)) for rel, _p, _sz, h in arts))
    say("  wrote release/manifest.json and release/SHA256SUMS")
    return mp, sp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--dry-run", action="store_true",
                    help="every check, then print what would be uploaded. Touches nothing.")
    ap.add_argument("--draft", action="store_true", help="create the release as a draft")
    ap.add_argument("--replace", action="store_true",
                    help="delete an existing release and tag of this version first, then cut it "
                         "again. For a release nobody has taken yet - see drop_release().")
    ap.add_argument("--notes-only", action="store_true",
                    help="print the release notes this version would use, and stop")
    a = ap.parse_args()

    want = a.version.lstrip("v")
    if not re.match(r"^\d+\.\d+\.\d+$", want):
        die("%r is not a version like 3.86.0" % a.version)
    tag = "v" + want

    if a.notes_only:
        print(changelog_section(want))
        return 0

    say("\nRELEASE %s%s\n" % (tag, "   (dry run - nothing will be changed)" if a.dry_run else ""))
    if a.replace and not a.dry_run:
        drop_release(tag)
    check_tree(tag, a.dry_run)
    check_version(want)
    arts = check_artifacts(want)
    gates()
    notes = changelog_section(want)
    say("  release notes: %d lines from CHANGELOG.md" % len(notes.splitlines()))
    commit = run(["git", "rev-parse", "HEAD"]).stdout.strip()[:12]
    mp, sp = write_release_dir(want, arts, commit)

    uploads = [p for _rel, p, _sz, _h in arts] + [mp, sp]
    if a.dry_run:
        say("\n  would tag %s at %s and upload:" % (tag, commit))
        for u in uploads:
            say("     %s" % os.path.relpath(u, ROOT).replace("\\", "/"))
        say("\n  nothing was changed.\n")
        return 0

    say("\n  tagging %s" % tag)
    r = run(["git", "tag", "-a", tag, "-m", "PKG MUTANT SHOP " + want])
    if r.returncode != 0:
        die("git tag failed: %s" % (r.stderr.strip() or r.stdout.strip()))
    r = run(["git", "push", "origin", tag])
    if r.returncode != 0:
        run(["git", "tag", "-d", tag])
        die("could not push the tag (it has been removed locally): %s" % r.stderr.strip())

    nf = os.path.join(ROOT, "release", "NOTES.md")
    io.open(nf, "w", encoding="utf-8", newline="\n").write(notes + "\n")
    cmd = ["gh", "release", "create", tag, "--title", "PKG MUTANT SHOP " + want,
           "--notes-file", nf]
    if a.draft:
        cmd.append("--draft")
    cmd.extend(uploads)
    say("  creating the GitHub release and uploading %d files" % len(uploads))
    r = run(cmd)
    if r.returncode != 0:
        die("gh release create failed (the tag is pushed; delete it if you are starting over):\n%s"
            % (r.stderr.strip() or r.stdout.strip()))
    say("\n  %s\n" % (r.stdout.strip() or "released"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
