# PKG MUTANT SHOP

A homebrew **package manager + store front-end** for a jailbroken PlayStation 5 (FW 12.70, Y2JB).
Browse a library, see a game's updates / DLC / cheats / patches, and install directly - from the
console itself, from a PC, or from a phone. Version **3.60.0**. Licence: **GPL-3.0** (`LICENSE`,
`THIRD-PARTY-NOTICES.md`).

**Start here: [SETUP.md](SETUP.md)** - the one current runbook (PC side, console side, how an
install flows, how to update, triage). Then `MUTANT PKG ENGINE.md` section 0 for the install engine.

> Colors: black · light matte yellow · gray. One HTML page for every device (PS5 WebKit, PC browser,
> phone). Fifteen languages.

---

## What this is (and isn't)

**Is:** a source-agnostic installer/manager. You configure your own content sources - a folder on
your PC served over HTTP, another PC running the same app, or your own self-hosted repo
`sources.json`. The app groups everything by game (base / update / DLC), shows install state, and
hands install jobs to the console.

**Isn't:** a scraper for any piracy site, and it ships **no bundled catalog of copyrighted games**.
You point it at content you have the right to install. This keeps the tool clean, legal to develop,
and resilient (no dependency on some site's HTML staying the same).

**What it does ship besides its own code:** a cheat and patch library (json / shn / mc4 cheats and
patch XML, copied from the GPL-3.0 HEN-Cheats-Collection, credits inside each file) and
ShadowMountPlus (GPL-3.0), both embedded in the console ELF. Origins and licences are listed in `THIRD-PARTY-NOTICES.md`.

---

## Architecture

```
   any browser (PC · phone · the PS5's own browser via the Media tile)
                     │  http://<pc>:8710  or  http://<ps5>:8710
      ┌──────────────┴──────────────────────────────────────────────────┐
      │  web/index.html  (ONE self-contained ES5 page, embedded in BOTH   │
      │  artifacts). Served by the console, it re-points its API at the   │
      │  newest PC companion that announced itself.                       │
      └──────────────┬───────────────────────────────┬──────────────────┘
                     │ /api/*                         │ /api/*
   ┌─────────────────▼───────────────┐   ┌───────────▼─────────────────────────────────┐
   │ PKG-MUTANT-SHOP.exe  (PC)       │   │ PKG-MUTANT-SHOP.elf  (PS5, :8710)            │
   │ companion/server.py             │   │ ps5-app/onconsole/server.c                   │
   │ • scans C:\Mutant Games\PS4|PS5 │   │ • serves the UI from /data/pkg-mutant-shop   │
   │ • serves packages on :8710      │◄──┤ • install engine: writes the request, asks   │
   │   (/library/<key>, HTTP Range)  │   │   Payload Manager (:8084) to SPAWN            │
   │ • install queue, one per console│   │   pms-installer.elf, which calls             │
   │ • confirms via bgft.db 1036/1026│   │   sceAppInstUtilInstallByPackage and exits   │
   │ • finds other PCs (federation)  │   │ • cheat/patch engine on the running game     │
   │ • announces itself every 8 s    │   │ • dashboard tile (pms-tile.pkg), backups     │
   └─────────────────────────────────┘   │ • carries + starts ShadowMountPlus (:10101)  │
                                         └──────────────────────────────────────────────┘
                                                 requires: Payload Manager on :8084
                                                 (the jailbreak's own loader) - nothing else
```

The console downloads each package **itself**, from the PC, through Sony's own installer (BGFT).
The PC serves bytes and keeps the queue; it never pushes a game over FTP. No third-party install
daemon exists in this design: nothing on 12800, no DPI host, no etaHEN, no Elf Arsenal.

---

## How installing actually works

1. Install is pressed (any device). The PC's queue claims the job - one running install per console.
2. The PC asks the console: `GET http://<ps5>:8710/api/engine/install-spawn?uri=http://<pc>:8710/library/<key>&name=...`
3. The ELF writes `/data/pkg-mutant-shop/installer-req.txt`, writes its embedded
   `pms-installer.elf` into Payload Manager's directory and asks Payload Manager to spawn it.
4. `pms-installer.elf` - a **fresh process**, not injected code - calls
   `sceAppInstUtilInstallByPackage(uri)`, writes its verdict to `installer-res.json`, and exits.
   (From an injected payload the same call answers `0x80B2116F`; that is the whole reason for the
   separate process.)
5. The system installer (BGFT) downloads the package from the PC with HTTP Range requests and
   installs it; the PS5 shows its own toasts. The PC's byte counter on `/library/<key>` is the
   progress bar.
6. Done means **`bgft.db` status 1036** (base game) or **1026** (update/DLC) plus a full-size
   `app.pkg` under `/user/app/<TID>/` (or `/mnt/ext1/user/app/<TID>/`). A row in `app.db` alone is
   never treated as proof.

Where a PKG lands is the console's own *Installation Location* setting; the app does not guess it.
PS5 **backups** (`.ffpfsc` etc.) take a different lane: the console copies the file to the drive you
pick (`/mnt/<drive>/homebrew`, staged as `.part`) and ShadowMountPlus mounts it.

---

## Run it

**PC:** run `PKG-MUTANT-SHOP.exe`. It creates `C:\Mutant Games\PS4` and `\PS5`, scans them, finds the
PS5 on the LAN, opens `http://localhost:8710` and sits in the tray. `config.json` is written beside
the exe. Drop `.pkg` files into the folders.

**Console:** with Payload Manager running, load `PKG-MUTANT-SHOP.elf` from it
(`/data/pldmgr/payloads/PKG-MUTANT-SHOP/PKG-MUTANT-SHOP.elf`). The ELF writes the UI, starts
ShadowMountPlus if it is not up, puts the **PKG MUTANT SHOP** tile under Media if it is missing,
and toasts that it is ready. Nothing else to install.

**From source:** `python companion/server.py` (Python 3.8+, stdlib; Pillow optional, for card
thumbnails and the tray icon) or `start.cmd`, which creates `companion/config.json` from the
template. `bootstrap.cmd` installs Python first if it is missing. `build_exe.cmd` builds the exe
through `companion/PKG-MUTANT-SHOP.spec` (all gates fatal).

Details, ports, config keys, a second PC: [SETUP.md](SETUP.md).

---

## Deploy a new build to the PS5

```
python companion/deploy.py elf            # FTP the ELF over Payload Manager's registered copy
                                          # (.part -> rename), /api/quit, wait for :8710 to close,
                                          # /loadpayload, confirm /api/health version + built
python companion/deploy.py check          # shop :8710, Payload Manager :8084, FTP 2121/1337
```

Payload Manager loads **by basename from its own directory**, so the registered file is the one that
has to change - see SETUP.md section 5. Build the ELF in WSL with `bash ps5-app/onconsole/build-wsl.sh`.

---

## What it does (3.60.0)

- Library from `param.sfo` metadata and real `icon0.png` art; base / update / DLC grouped by title id;
  install state read from the console (`bgft.db`, `app.pkg` presence) - never from `app.db` alone
- Installs through the console's own installer, spawned per install; per-console serialization;
  integrity check before hand-off; out-of-space refusal only when a package fits on no drive
- PS5 backups to any drive; ShadowMountPlus carried and started by the ELF; move / delete backups
- Cheats and patches applied to the running game by the ELF's own engine (expect-gated, revertable)
- Dashboard tile (`pms-tile.pkg`, embedded) that opens the shop in the PS5 browser
- Multi-PC: companions find each other on the LAN; the console installs from whichever PC has the game
- Fifteen languages, RTL for Arabic; one page for TV, PC and phone; controller-navigable

`CHANGELOG.md` has every version. `ARCHITECTURE.md`, `ROADMAP.md`, `ROADMAP-OVERHAUL.md`,
`TOOLCHAIN.md`, `BUILD-PS5-APP.md` and `SETUP-REMOTE.md` are older design and status notes and each
carries a banner saying what in it is no longer true.
