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
2. If not, hand the payload the package carries to the jailbreak's binary loader on `:9090`.
3. Wait for the shop, then open the console's own browser at it.
4. If none of that worked, say so on screen — with what to do about it.

**It never opens a connection it does not then fill with the payload.** An empty connection to an
ELF loader can stop it listening: that is how this console lost its loader twice while this was
being built, from nothing more than a port scan.

### Why it loads a payload instead of *being* the server

An application is suspended the moment the browser comes to the foreground, and a suspended process
stops answering its socket. A shop served by this app would therefore die at the exact moment the
page tried to load it. The payload lives in a long-running system process instead, which is why it
survives the browser, the app closing, and everything else.

**This app is the button. The payload is the shop.**

Nothing here is privileged: loopback sockets, its own `/app0`, and two public system-service calls.
An application runs sandboxed, and a tile that needed more than a sandbox allows is a tile that does
not work.

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

## If it goes wrong

`sceAppInstUtilAppUnInstall("PKGM00001")` removes it — the canonical signature, from the toolchain's
own `AppInstUtil.h`. It can also be deleted from the PS4's home screen like any other application.

The failure that cost a console on the PS5 side was a **registration with no data behind it**: a
title written into the database by hand, which the system then tried to launch as a game. This is
the opposite — a complete package handed to the console's own installer, which is the only thing
that writes those records correctly. That is why the package route was taken and the shortcut was
not.
