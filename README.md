# PKG MUTANT SHOP

A homebrew **package manager + store front-end** for jailbroken PlayStation 5 (FW 12.70, Y2JB/P2JB).
Browse a library, see a game's updates / DLC / cheats, pick a drive, and install directly — driven from
the console itself or from a PC. Built to **sit on top of the tools you already run** — a DPI v2 host
(Elf Arsenal / ps5-dpi-v2 / etaHEN), ShadowMountPlus, kstuff-lite — rather than reinvent them.
Current for **Y2JB on FW 12.70**; see [TOOLCHAIN.md](TOOLCHAIN.md).

> Colors: black · light matte yellow · gray. One HTML app for every device (PC browser **and** PS5 WebKit).

---

## What this is (and isn't)

**Is:** a source-agnostic installer/manager. You configure your own content sources — a folder on your PC
served over HTTP, an FTP/NFS share, a USB drive, or your own self-hosted repo `sources.json`. The app
groups everything by game (base / update / DLC), shows install state, and hands install jobs to the
console.

**Isn't:** a scraper for any piracy site, and it ships **no bundled catalog of copyrighted games**. You
point it at content you have the right to install. This keeps the tool clean, legal to develop, and
resilient (no dependency on some site's HTML staying the same). There's a one-time content-ownership
acknowledgement in Settings.

---

## Architecture

```
                        ┌──────────────────────────────────────────────┐
                        │  web/index.html  (self-contained UI)          │
                        │  • runs in PC browser AND PS5 WebKit          │
                        │  • black / matte-yellow / gray theme          │
                        │  • demo mode when offline                     │
                        └───────────────┬──────────────────────────────┘
                                        │  HTTP /api/*
                        ┌───────────────▼──────────────────────────────┐
                        │  companion/server.py  (Python, stdlib only)   │
                        │  • serves the UI                              │
                        │  • indexes YOUR library (local / sources.json)│
                        │  • tracks the install queue                   │
                        │  • bridges to the console services  ──────┐   │
                        └───────────────────────────────────────────┼───┘
                                                                    │
   on the PS5 (already running, part of your JB setup):            │
                        ┌───────────────────────────────────────────▼───┐
                        │  etaHEN DPI v2   POST :12800/api/install       │  ← install PKGs (AppInstUtil)
                        │  ShadowMountPlus :9021 + /data/shadowmount     │  ← mount PS5/PS4 backups
                        │  kstuff-lite                                   │  ← runtime patches
                        └───────────────────────────────────────────────┘
```

**Why a companion instead of pure browser→console?** The PS5's WebKit is sandboxed and can't reliably do
cross-origin POSTs, FTP, or read your PC's library folder. The companion is a thin, zero-dependency
broker. When the UI runs *on the console* against local services, the same companion role is filled by
etaHEN's own HTTP services — the front-end code is identical either way.

---

## How installing actually works (the real pipeline)

The PS5 does **not** use the PS4's BGFT (`sceBgft*` returns `SCE_BGFT_ERROR_NOT_SUPPORTED 0x80990006`).
It uses **`libSceAppInstUtil.sprx`**:

- `sceAppInstUtilInstallByPackage(MetaInfo, PkgInfo, PlayGoInfo)` — async install; the URI can be a
  **local path** (`/data/x.pkg`) **or an HTTP URL** (`http://host/x.pkg`). That HTTP-URL support is what
  makes "download + install on-console, no USB" possible natively.
- `sceAppInstUtilGetInstallStatus` — status (`transferring → promoting → playable / error`) + downloaded
  / total bytes + percent.

A **DPI v2 host** (on FW 12.70 that's **Elf Arsenal** or **`ps5-dpi-v2`**; etaHEN on older FW) wraps this as an
HTTP service on port **12800** — same API either way (see [TOOLCHAIN.md](TOOLCHAIN.md)):

```bash
curl -X POST http://<ps5-ip>:12800/api/install \
     -H 'Content-Type: application/json' \
     -d '{"url":"http://<your-pc-ip>:8710/library/Game.pkg"}'
# -> {"res":"0"}
```

PKG MUTANT SHOP's companion calls exactly this. (The matching **status/progress** endpoint isn't publicly
documented yet — we capture it from the console's own DPI WebUI tonight and wire live progress. Until
then the queue shows submitted/working/done state.)

---

## Run it (on the laptop, right now)

**One click:** double-click **`start.cmd`** (Windows) or run **`./start.sh`** (Linux/macOS/home-box). It
creates `config.json` from the template, opens the UI, and starts serving.

**Or manually:**
```bash
cd pkg-mutant-shop/companion
cp config.example.json config.json      # then edit ps5_ip + library.local_paths (+ consoles[])
python server.py                        # -> open http://localhost:8710
```
Preview with zero setup: just open `web/index.html` in a browser (demo mode).

Python 3.8+ (stdlib only — no `pip install`).

### What it does now (v0.5)
- Real `param.sfo` metadata + real `icon0.png` box art · install-state detection (FTP + app DB)
- **Intelligent multi-source selection** — auto-picks the fastest reachable mirror, fails over
- **Multi-console fleet** — target one PS5 or "All consoles"; parallel queue
- **Real byte-accurate progress** (local + remote-companion) · **SHA-256 integrity** · per-title manifests
- **Native PS5 app** with your icon (homebrew launcher, no browser) — deploy over FTP; ELF payload injector

### Deploy to the PS5 (native app, not the browser)
```bash
python companion/deploy.py check                                  # loader + FTP reachable?
python companion/deploy.py payload path\to\websrv-ps5.elf          # inject the runtime (or use etaHEN)
python companion/deploy.py app --companion http://YOUR-PC-IP:8710  # FTP the app (your icon) to /data/homebrew
```
Then launch **PKG MUTANT SHOP** from etaHEN/websrv's homebrew launcher. See [BUILD-PS5-APP.md](BUILD-PS5-APP.md).

### Move it to any PC
`bootstrap.cmd` auto-installs the only dependency (Python) and starts it — no DLLs/drivers (stdlib-only).
`build_exe.cmd` makes a single-file `.exe` that needs nothing on the target PC.

**Going further:** [ARCHITECTURE.md](ARCHITECTURE.md) · [SETUP-REMOTE.md](SETUP-REMOTE.md)
(from-anywhere via Tailscale) · [BUILD-PS5-APP.md](BUILD-PS5-APP.md) · [CHANGELOG.md](CHANGELOG.md) · [ROADMAP.md](ROADMAP.md).

## Run it (against the PS5, tonight)

1. On the PS5 (FW 12.70): run Y2JB → P2JB → autoloader, then your **DPI v2 host** (Elf Arsenal / `ps5-dpi-v2`) on port **12800**,
   confirm ShadowMountPlus + kstuff-lite are loaded (your usual boot).
2. On the PC: set `ps5_ip` in `config.json`, point `library.local_paths` at your PKG folder, run
   `python server.py`.
3. In the app: pick a title → **Install** → watch the queue. First real target: install one PS4 FPKG
   end-to-end, then capture the progress endpoint.

See **ROADMAP.md** for exactly what's verified vs. pending, and **CHANGELOG.md** for version history.
