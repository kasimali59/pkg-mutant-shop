# PKG MUTANT SHOP — the PS4 dashboard app

This builds **`IV0000-PKGM00001_00-PKGMUTANTSHOP001.pkg`**: a real, fake-signed PS4 application
package that puts the shop on the console's home screen.

Before this existed, a PS4 had nothing to press. The shop only existed while a payload happened to
be injected, and the jailbreak's binary loader does not survive rest mode — so after one suspend
there was no app on the console at all, whatever had been "installed".

```bash
bash ps4-app/tile-pkg/build-wsl.sh     # WSL; fetches its toolchain on first run
```

---

## Status — read this before believing anything below

| | |
| --- | --- |
| the package builds | **yes**, verified — `pkg_validate` reports 28 checks `[OK]`, 0 errors, 0 warnings |
| the package is on the console | **yes**, verified — `/data/pkg/PKG-MUTANT-SHOP.pkg`, SHA-256 matched after upload |
| the package has been **installed** | **no** — `/user/app` holds three games and no `PKGM00001` |
| the icon has been seen on the home screen | **no** |
| the eboot has been run | **no** |

Everything about the *file* is measured. Everything about its *behaviour on a PS4* is reasoned from
the toolchain's own samples and from what this project measured on the payload side. Nobody has
pressed this icon, because it has never been installed. Do not read "built and validated" as
"working" — that substitution is the same shape as the mistake that cost this project a console.

**What is actually missing is one install**, and it needs either the payload running (which needs a
payload loader) or thirty seconds at the television. See *Installing it the first time*.

## What it is

| | |
| --- | --- |
| title id | `PKGM00001` — the same identity the PS5 tile uses. One app, whichever console it is on, and it cannot collide with a game (those are `CUSA` / `NPXS`). |
| content id | `IV0000-PKGM00001_00-PKGMUTANTSHOP001` (`IV0000` is the homebrew publisher prefix) |
| category | `gd`, content type `0x1A`, flags `0x0A000000` — **byte-for-byte the shape of a retail base game** |
| contents | `eboot.bin`, `sce_sys/param.sfo`, `sce_sys/icon0.png`, and the shop's payload |

That last row is the point: it is an **ordinary PS4 package**. `companion/pkg_meta.py` reads it
exactly as it reads a retail title, our own integrity check calls it complete, and it installs down
the same lane every other game does. Nothing special-cases it.

## What pressing the icon does

1. Is the shop already answering on `127.0.0.1:8710`? → open it.
2. If not, hand the payload the package carries to a payload loader — GoldHEN's BinLoader on
   `:9090` (an HTTP POST), then `elfldr` on `:9021` (the bare ELF) if that one is not there.
3. Wait up to thirty seconds for the shop, then open the console's own browser at it.
4. If none of that worked, say so on screen, naming the piece that is missing.

**It never opens a connection it does not then fill with the payload.** An empty connection to an
ELF loader can stop it listening: that is how this console lost its loader twice while this was
being built, from nothing more than a port scan. That rule is also why there is no "is a loader
there?" probe — the connect *is* the probe, and the payload always follows it.

### Two things that made it look dead, fixed at 3.62.1

**The notification's icon field must stay zero.** `useIconImageUri = 1` selects the form that draws
an icon beside the text, and on this console family that form returns success and renders
**nothing** — the PS5 side has been caught by it twice. `server_ps4.c` sends the plain form because
that is the one measured working on this console, and the tile now sends the identical struct. An
app whose every message is invisible is an app that looks broken whatever it actually did.

**The browser needs the user service first.** The reference program for this on a PS4 — the browser
sample in the ps4-payload-dev SDK — calls `sceUserServiceInitialize` and only launches the browser
if it succeeded. The payload inherits a process that has already done it; a sandboxed application
has not. The tile initialises it, launches, and hands it back.

### Why it loads a payload instead of *being* the server

An application is suspended the moment the browser comes to the foreground, and a suspended process
stops answering its socket. A shop served by this app would therefore die at the exact moment the
page tried to load it. The payload lives in a long-running system process instead, which is why it
survives the browser, the app closing, and everything else.

**This app is the button. The payload is the shop.**

Nothing here is privileged: loopback sockets, its own `/app0`, and three public system-service
calls. An application runs sandboxed, and a tile that needed more than a sandbox allows is a tile
that does not work.

### The one thing this shape cannot do

If **no payload loader is listening**, pressing the icon cannot start the shop — it can only say so.
That is not a defect in the package, it is the shape of the platform: an unprivileged application
cannot inject code into another process, and the shop has to live in another process to survive the
browser coming forward. A console in that state was observed while this was written: GoldHEN's FTP
answering on `2121` while `9090` and `3232` were both refusing, with `[BinLoader] Enabled = 1` in
its own config. Re-running the jailbreak with the payload loader enabled is the remedy, and the
app now says exactly that instead of "run the jailbreak again".

## Why the PC installs it, and not the ELF

On the PS5 the ELF carries its tile and installs it on boot. The PS4 cannot work that way, and the
reason is worth stating so nobody "fixes" it later:

> **The package carries the payload**, so that pressing the icon can start the shop with every PC
> switched off. A payload that also carried the package would therefore contain a copy of itself —
> and each rebuild would embed the last one, growing without limit.

So the companion ships the package, registers it in its file registry (which means the proven
`/library/` route serves it, byte ranges and all — the console's installer requires them), and
installs it the first time it sees a PS4 without it. `POST /api/ps4/tile` forces it; Settings shows
its state and offers a button. The payload answers `/api/tile/status` so the console can report on
itself, proved by `app.pkg` on disk rather than an `app.db` row.

With no PC at all, the package can be installed from the console's own package installer — put it
somewhere the installer scans and install it once, and the icon is there for good.

## Installing it the first time

There is a chicken-and-egg here and it is worth naming rather than hiding: **the shop installs the
icon, and the icon starts the shop.** Somebody has to break the loop once. After that first install
the icon is on the home screen permanently — it survives rest mode, reboots and losing the
jailbreak, exactly like a game — and the shop keeps itself up to date from then on.

**Route A — the console's own installer. No PC, no payload loader, one time.**

The package is already on the console at **`/data/pkg/PKG-MUTANT-SHOP.pkg`** (uploaded over
GoldHEN's FTP and verified by SHA-256 after the transfer, not just by size).

On the television: **Settings → Debug Settings → Package Installer**, set the source to the internal
drive if it is pointed at USB, pick **PKG-MUTANT-SHOP.pkg**, install. GoldHEN's Debug Settings menu
is already enabled on this console (`Enabled = 2` in `/data/GoldHEN/config.ini`).

The exact wording of that menu moves between GoldHEN point releases, so read the screen rather than
this paragraph. If the installer only offers USB, copy the same file to the root of a FAT32 or
exFAT stick and install it from there — it is the same package either way.

**Route B — the shop installs its own icon.** If the payload is running (port `8710` answering),
the companion does it with one button: **Settings → PS4 home-screen app → Install**, or
`POST /api/ps4/tile`. This is the route that needs no television at all, and it is what will happen
automatically on any PS4 the app meets that does not have the icon yet.

**Proving it worked.** Not by an `app.db` row — this project has been burned by that twice. The
test is the file:

```
/user/app/PKGM00001/app.pkg      exists, and is NOT zero bytes
/user/appmeta/PKGM00001/         exists
```

`appmeta` alone is artwork and survives a reset, so it is not proof of anything on its own.

### Why there is no remote install button for the very first time

This was looked for rather than assumed. GoldHEN's FTP server was asked what it can do
(`FEAT`, `HELP`, `SITE HELP`) and its whole vocabulary is file transfer plus `SITE CHMOD`,
`SITE UMASK`, `MTRW` and a disabled `DECRYPT`. There is no install, run or execute command. The two
honest ways to get a package registered are the console's own installer and our own BGFT lane, and
the BGFT lane lives inside the payload. Anything else means writing the system's own records by
hand, which is the exact failure that cost a console here before.

## The toolchain

Everything comes from the [OpenOrbis PS4 Toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain)
v0.5.4, which `build-wsl.sh` fetches on first run:

* `clang` (FreeBSD 12 x86-64 target) → an ELF
* `create-fself --paid 0x3800000000000011` → `eboot.bin`, a signed fself
* `PkgTool.Core sfo_new` / `sfo_setentry` → `param.sfo`
* `create-gp4` → the project file
* `PkgTool.Core pkg_build` → the package

The build also unpacks **libssl 1.1 beside the toolchain**: PkgTool's bundled .NET runtime needs it,
this machine has libssl 3, and there is no root here. Extracting the `.deb` locally and pointing
`LD_LIBRARY_PATH` at it keeps the whole build in one script and touches nothing outside that folder.

The `Makefile` is adapted from the toolchain's own `hello_world` sample rather than invented, so the
steps are the ones its authors document.

### Checked, not assumed

* the built `.oelf` is `SCE Executable (ASLR) - 0xFE10` with a FreeBSD ABI — what a PS4 eboot is
* extracting the finished package shows the payload inside at its exact byte count
* `pkg_meta.parse_pkg` reads back the title, title id, content id, category and version
* `pkg_meta.pkg_completeness` calls it complete
* the toolchain's own `hello_world` was built and packaged first, so the chain was proven on
  something known-good before ours was trusted

## If it goes wrong — the way out

**Try these in order. The first one is almost certainly enough.**

1. **Delete it from the home screen.** Highlight the icon → Options → Delete. This is the normal
   path and it is available: every package our lane has installed on this console carries
   `canRemove = 1` in `app.db`, so the menu item is not greyed out.
2. **Over FTP**, if the icon is there but will not delete: remove `/user/app/PKGM00001` and
   `/user/appmeta/PKGM00001`. This leaves a row behind in `app.db`; on this console a row with no
   data behind it is already demonstrably tolerated — `CUSA00001` "THE PLAYROOM" has sat like that
   without consequence — but it is still untidy, not a fix.
3. **`sceAppInstUtilAppUnInstall("PKGM00001")`** — the canonical signature, from the toolchain's own
   `AppInstUtil.h`. **`dlsym` it before calling it.** On this firmware `libSceBgft`'s older names
   are absent and only the `ServiceInt` family resolves, so nothing here is safe to call on the
   strength of a header. Guessing a system call's shape is what crashed a console on the PS5 side.

**Do not use Rebuild Database.** On a jailbroken PS4 it runs outside HEN and drops every fake-package
entry from the home screen — it would take Riptide GP2, Metal Slug XX and Call of Duty with it.

The failure that cost a console on the PS5 side was a **registration with no data behind it**: a
title written into the database by hand, which the system then tried to launch as a game. This is
the opposite — a complete package handed to the console's own installer, which is the only thing
that writes those records correctly. That is why the package route was taken and the shortcut was
not.

The failure that cost a console on the PS5 side was a **registration with no data behind it**: a
title written into the database by hand, which the system then tried to launch as a game. This is
the opposite — a complete package handed to the console's own installer, which is the only thing
that writes those records correctly. That is why the package route was taken and the shortcut was
not.
