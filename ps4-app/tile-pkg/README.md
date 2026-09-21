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
| the package builds | **yes**, verified — `pkg_validate`: 28 checks `[OK]`, 0 errors, 0 warnings |
| the ELF carries it | **yes**, verified — found in the ELF, byte-identical, checked by a build gate |
| the ELF installs it on boot | **yes**, verified on 13.52 — from its own embedded copy, over the console's loopback |
| the package has been **installed** | **yes** — `/user/app/PKGM00001/app.pkg`, 6,619,136 bytes, with `app.pbm` / `app.json` / `app.xml` beside it |
| the ELF **updates** it | **yes**, verified — `01.00` → `01.01` → `01.02` → `01.03` on the hardware |
| the installed bytes are ours | **yes**, verified — read back over FTP and hashed: `01.03` = `5968c773…fb`, identical to the build |
| the icon is on the home screen | **yes** — listed by the console as *PKG MUTANT SHOP* |
| **the eboot has been run** | **no** — and it cannot be. The console fails to mount `/app0` before any process exists: `PrepareProcessLaunchPkg() ret = 80990019` |
| is that our package's fault | **no.** A fake-signed *retail* game on the same console fails at the identical line with the identical code |

Everything about the *file*, the *install* and the *update* is measured on a real PS4 at 13.52.
**The app still cannot be launched from the home screen, and that is now understood well enough to
say it is not this package's doing.**

Captured on the console — `research/klog-ce32930-7-app0-mount-2026-09-21.txt`:

```
[SceLncService] launchApp(PKGM00001)  category={gd}  appType={SCE_LNC_APP_TYPE_BIG_APP}  appVer={01.03}
[SceLncService] PrepareProcessLaunchDir()
[PS]Error: process_starter\process_mount.cpp at 3577
[PS]Error: process_starter\process_mount.cpp at 4442
PrepareProcessLaunchPkg() ret = 80990019
[SceLncService] lnc_mount_root.cpp(425)  mountApp0Dir:      LNC_ISOK::0x80990019
[SceLncService] lnc_application.cpp(321) initializeApp0Dir: LNC_ISOK::0x80990019
[SceLncService] lnc_manager.cpp(439)     launchApp:         LNC_ISOK::0x80990019
```

`CE-32930-7` is `0x80990019` out of `PrepareProcessLaunchPkg`, surfacing as a failure to mount
`/app0`. **No process is ever created** — the eboot is never reached, so nothing in `pms/` can be
the cause and nothing in `pms/` can fix it.

**The control test was run, and it is the whole answer.** Riptide GP2 (`CUSA02365`) — a fake-signed
*retail* game, nothing to do with this build — was pressed on the same console minutes later and
failed at the **identical line with the identical code**. On top of that, every flag field in our
PKG header matches the installed retail-derived packages read off that same console:

| field | ours | CUSA02365 / CUSA11740 |
| --- | --- | --- |
| `pkg_type` @0x04 | 1 | 1 |
| `drm_type` @0x70 | 15 | 15 |
| `content_type` @0x74 | 26 | 26 |
| `content_flags` @0x78 | `0x0A000000` | `0x0A000000` |
| `pfs_flags` @0x408 | `0x80000000000003CC` | `0x80000000000003CC` |
| `ekc_version` @0x9C | 1 | 1 |

Only sizes and entry counts differ, as they must for a smaller application.

**So: this console cannot launch any fake-signed package from the dashboard.** Mounting an installed
package as `/app0` happens in `SceShellCore`, not in our code and not in the package — which puts it
in the jailbreak layer, on a firmware (13.52) newer than the GoldHEN build being used with it. That
last step is the reasonable reading of the evidence rather than a measured fact, and it is labelled
as such deliberately: two earlier explanations for this error were confidently wrong.

Do not read "installed and verified" as "launchable". The build, the install and the self-update are
proven. The launch is blocked outside this repository.

## What it is

| | |
| --- | --- |
| title id | `PKGM00001` — the same identity the PS5 tile uses. One app, whichever console it is on, and it cannot collide with a game (those are `CUSA` / `NPXS`). |
| content id | `IV0000-PKGM00001_00-PKGMUTANTSHOP001` (`IV0000` is the homebrew publisher prefix) |
| category | `gd`, content type `0x1A`, flags `0x0A000000` — **byte-for-byte the shape of a retail base game** |
| contents | `eboot.bin`, `sce_sys/param.sfo`, `sce_sys/icon0.png` — **and nothing else** |

That last row is the point: it is an **ordinary PS4 package**. `companion/pkg_meta.py` reads it
exactly as it reads a retail title, our own integrity check calls it complete, and it installs down
the same lane every other game does. Nothing special-cases it.

## What pressing the icon does

1. Is the shop answering on `127.0.0.1:8710`? → open the console's browser at it.
2. If not, say so on screen: the button cannot conjure the server.

That is the whole program, and it is the same contract the PS5 tile has always had — the tile opens
the shop, the ELF *is* the shop.

## Why it carries nothing, and why the ELF carries it

The ELF installs this package, the way the PS5 ELF installs its tile. So this package must not
contain the ELF:

> The ELF would contain a package containing the ELF, and every rebuild would embed the previous
> one, growing without limit.

An earlier version did ship the payload inside, so that pressing the icon could start the shop by
handing the payload to a loader. It worked on paper and it is exactly the cycle above, so it is
gone. **One direction only: package first, then ELF.** Both build scripts say so, and
`ps4-app/onconsole/build-wsl.sh` refuses to build without the package.

### The version check

`ps4-app/onconsole/server_ps4.c` carries `PS4_TILE_VER` and, once its socket is listening, compares
it with the installed app's `APP_VER` from the console's own `tbl_appinfo`:

| console has | what happens |
| --- | --- |
| this version or newer, with its `app.pkg` on disk | nothing at all — not even a write |
| an older version | installed over it, which is how the PS4 updates a title |
| nothing | installed |

"Installed" is `app.pkg` **with bytes**, never an `app.db` row on its own — the rule this project
learned on the PS5, where trusting the row produced 53 phantom installs. Versions compare
numerically, so `1.00` and `01.00` are the same number and a leading zero cannot look older.

The staged copy goes to `/data/pkg-mutant-shop/pms-tile.pkg` — **our own folder**. Nothing this app
does touches the jailbreak's files or folders; the jailbreak's only job is to start the ELF.

### Nothing privileged

A loopback connect, its own `/app0`, two public system-service calls. An application runs sandboxed
— it cannot read `/data` and it cannot inject code into another process — so a button that needed
either would be a button that does not work.

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
