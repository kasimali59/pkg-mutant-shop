# Changelog — PKG MUTANT SHOP

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning is [SemVer](https://semver.org/).

Legend: `[VERIFIED]` = tested/confirmed · `[WIRED]` = implemented against a known spec, pending on-device test · `[STUB]` = placeholder with a clear integration point.

---

---

## [3.62.0] - 2026-09-21 - "The PS4 joins the fleet" `[VERIFIED]`

### What this release is

PKG MUTANT SHOP now runs on a **PS4 on firmware 13.52**, alongside the PS5, from the same app. One
library, one page, one companion, one queue — a second console in the picker. Everything the PS5 side
does is untouched: `ps5-app/onconsole/server.c` has exactly one changed token in this release, the
version stamp, and the install lane was not modified at all.

Everything below was measured on the hardware (PS4 at 13.52, GoldHEN 2.4b18 as the jailbreak layer).
Nothing was carried over from the PS5 side on the assumption that it would hold.

### One ELF for both consoles is not possible, and that was tested first

The PS5 ELF was posted to the PS4's payload loader before a line of new code was written. It was
accepted — HTTP 200, *"payload launched successfully", "format: ELF"* — and **nothing ran**: port 8710
never opened and klog stayed silent. Different SDK, different ABI, different system libraries.

So there are two payloads and one of everything else. `PKG-MUTANT-SHOP.elf` is the PS5's;
`PKG-MUTANT-SHOP-PS4.elf` is the PS4's; both embed the same `web/` directory, both answer the same
API, and both are held to the same build gates. From the user's side there is one app.

### New — the PS4 payload (`ps4-app/onconsole/server_ps4.c`)

* Serves the shared UI and the JSON API on **:8710**, one thread per connection.
* Reads the console's installed games from the PS4's own `app.db` — a **different schema** from the
  PS5's: per-user `tbl_appbrowse_<userid>` plus a key/value `tbl_appinfo` whose `APP_VER` is the
  installed version. Category `gd` is a real game; `gdi` system stubs are skipped.
* Installs packages from the PC over **BGFT**, with the console's own progress and notifications.
* Installs packages **already on the console** — `/mnt/usb0…7` are scanned and listed in
  `/api/library` with `source: "usbN"`, so a stick is installable with every PC switched off.
* `/api/open` launches the console's own browser at the shop, so one tap puts it on the television.
* Notifications, storage, drives, the file API (`/api/fs/*`), icons and the install log all work the
  same way they do on the PS5, so the companion needed no new transport.
* Cheats, mods and patches answer *"not available on the PS4 yet"* — see the limits section.

### New — the companion speaks to two consoles

* Settings has a **PS4 address** box beside the PS5 one, in all 15 languages. Filling it in adds the
  console; clearing it removes it. `reconcile_consoles()` folds the two boxes into the fleet list in
  one place, so nothing else has to know how they map.
* A console's platform is **learned from the console**, never guessed: the PS4 payload reports
  `platform: "ps4"` in `/api/health` and the PS5 payload has never had that field, so a console that
  names no platform is a PS5. A platform saved in `config.json` wins, so a switched-off console is
  still handled correctly.
* The library now merges **every** console's installed games, not just the first. With two consoles
  configured, the second one's games used to be invisible — shown as not installed, and offered for
  installation again. Each game carries `installed_on` with the consoles that have it; the existing
  single-value fields keep describing the first console that does, so a one-console machine behaves
  exactly as before.
* The install hand-off carries the package's **content id, real size and type**, which the PS4
  requires and the PS5 ignores.
* A **PS5 package is refused for a PS4** with a sentence, rather than letting "All consoles" collect
  one console-side refusal per game. The reverse stays allowed: a PS5 runs PS4 games.

### Fixed — auto-discovery corrupted a two-console config

`main()`'s auto-find exists so a fresh install locates the console with nothing filled in, and it
worked by writing one address. With two consoles configured, that write stamped the discovered
address onto **every** entry — so finding the PS4 rewrote the PS5's address to the PS4's, in
`config.json`, permanently. Auto-find now runs only when there is at most one console; anything the
user has deliberately set up is left alone. It also records the platform it found, and only writes
`ps5_ip` for a console that actually said it was a PS5.

### Fixed — `Fleet.bridge()` answered with the wrong console

An unknown console id fell back to "the first console there is". Harmless with one console; with two
it would quietly hand a PS4's install to the PS5. The fallback now applies only when there is exactly
one console, and an unknown id becomes a refusal the caller can report.

### Four PS4-side bugs found by running it

* **BGFT task table filled up and every install was refused.** `0x80990086` on register, with the
  directories of dead tasks still under `/user/bgft/task`. A task only disappears when something
  unregisters it, so a shop that does not clean up after itself stops being able to install anything
  — including packages it had installed an hour earlier. A finished job now releases its own task,
  and a sweep recovers tasks stranded by a payload reload or a crash. A task counts as ours only when
  its record carries a plain-http URL on the `/library/` route; a Store task is https on a Sony host.
  Both halves must match, which is what kept the seven tasks this console already had — the user's
  own Store and firmware downloads, one of them plain http — untouched.
* **A finished job read as still running.** The job state was only refreshed by `/api/engine/job`, so
  a finished or failed install still looked busy to the very next request and the next install was
  refused. Both the busy check and the cleanup route refresh first now.
* **A finished job was rewritten back to "downloading".** Once its task is handed back, BGFT answers
  every progress call with an error — which read as "no error, no bytes yet" and overwrote a job that
  had already failed. The verdict is reached once and then left alone.
* **Raw SCE codes reached the screen.** The house style has always been that a hex code belongs in
  the install log, not on a television, and `tools/message_report.py --check` enforces it — but the
  report never read the PS4 payload, so the rule was not being applied to it. The report now covers
  both payloads (445 messages checked) and the PS4's sentences were rewritten to obey it.

### Known limit — a title the Store has an update for will not install

Measured twice on this console, and worth stating plainly because it is the one thing that does not
work. Before downloading anything, BGFT asks PlayStation Network whether the title has a newer
version. When it does, BGFT builds a **two-part** task — the package we offered *plus* the update —
and then cannot fetch the second part, because the first did not come from the Store.

> METAL SLUG XX (no Store update): `status=0` → installed in 15 seconds, 532,217,856 of 532,217,856.
> Castle Crashers Remastered (Store has 01.04): `status=1` → the task asked for 238,419,968 against a
> 227,540,992-byte package, the extra 10,878,976 being the update, and ended `0x80990004` with
> nothing written.

No task option changes it: `FORCE_UPDATE`, `INTERNAL`, `REMOTE`, `INVISIBLE` and `entitlementType`
0–3 were each tried and all failed identically. Stopping the console from reaching PlayStation
Network is what clears it.

So the shop **says so, and only when it can prove it**: the job reports the merge attempt only if
BGFT asked for more bytes than the package contains and nothing landed on disk. Anything else gets
the plain sentence. The cause is never guessed.

### Known limit — no cheats or mods on the PS4 yet

GoldHEN does not provide the `kexec` syscall the SDK's C runtime expects, so a payload on this
console has **no kernel read/write**. The cheat engine finds a game's memory by walking its CR3 page
tables, which needs exactly that. `/api/cheat*`, `/api/mods*` and `/api/patch*` answer with one
sentence saying so rather than failing in a way that looks like a bug.

### Known limit — no dashboard tile on the PS4 yet

The PS5 ships one: a fake-signed PS5 package embedded in the ELF and installed on boot. A PS4 tile is
a different container and a different problem — a real application with a signed `eboot.bin`, needing
an fpkg build tool and an fself signer, neither of which is in this repo. The PS5 experience is that
a wrong app registration leaves a tile that crashes the console hard enough to need the jailbreak
re-run, so it is not worth guessing at. `/api/open` covers the need in the meantime: one tap in the
app opens the shop on the console's own screen.

### Getting the payload to run at all

The [ps4-payload-dev SDK](https://github.com/ps4-payload-dev/sdk) builds the payload, and its C
runtime had to be patched or `main()` never ran — `payload_init()` calls `__kernel_init()` →
`kexec(...)`, which GoldHEN does not provide, and treated the failure as fatal. Three edits in
`sdk-goldhen.patch.py` make the kernel steps advisory; the build script applies them automatically
and upstream is left unmodified. Symbol resolution stays fatal.

The last of the three was the least obvious: `__rtld_init` restored the process jail
*unconditionally*, failed, and returned −1 **after** every library had already loaded fine.

Two more things worth writing down:

* **Never `_exit()`.** GoldHEN injects the payload into a shared host process (`ScePartyDaemon`), so
  `_exit()` tears that process down — including the copy of the payload just loaded to replace this
  one. `/api/quit` closes the listening socket and returns instead.
* **`RTLD_DEFAULT` is not enough.** Every BGFT symbol came back NULL from the default scope and
  resolved fine from the handle of the library we opened. The first build of this file refused every
  install with "BGFT is incomplete on this firmware", which was our own lookup being wrong.

### Tooling

* `ps4-app/onconsole/build-wsl.sh` — fetches and patches the SDK the first time, then runs **the same
  gates the PS5 build runs** (`check_web.py`, `i18n_report.py --check`, `message_report.py --check`),
  because both payloads embed the same UI and say the same sentences.
* `tools/ps4_sync_sqmini.py --check` — a build gate that keeps the PS4's copy of the PS5 payload's
  SQLite reader honest, rather than letting two copies drift.
* `tools/stamp_version.py` now stamps the PS4 payload too. Left out, it drifted immediately: it was
  written at 3.62.0 while everything else said 3.61.0, which is the exact failure that file exists to
  prevent.
* `tools/message_report.py` now reads both payloads.
* `ps4-app/onconsole/README.md` — what was measured on the console, why each decision was made, and
  the SCE codes behind each one.

### Verified on hardware

* The payload runs as root, serves the 762 KB shared UI on :8710, and hot-reloads over a running copy.
* `app.db` read with real names and `APP_VER` versions; `/api/installed` lists only titles with their
  own `app.pkg` on disk.
* **Riptide GP2** (107,806,720 bytes) and **METAL SLUG XX** (532,217,856 bytes) installed end to end
  from this PC's library — exact byte sizes on disk, `app.pbm`/`app.json`/`app.xml` present,
  registered in `app.db`, `sceAppInstaller::AppInstallApp` → `0x00000000`.
* The task sweep released exactly the three tasks the shop had stranded and left the console's own
  nine alone.
* A failed install reports as failed, is cleared by the next `spawn-cleanup`, and the install after it
  starts normally.

---

## [3.61.0] - 2026-09-04 - "The audit pass: 262 findings, and the sync that never converged" `[VERIFIED]`

### Where this release came from

A read-only audit of 3.60.0, end to end: the companion, the console payload, the page, the tools,
the build and every document. Fifteen readers, one subsystem and one lens each; then an
adversarial verifier per finding told to assume it was wrong and re-read the cited lines. 339 raw
findings became **262 confirmed, 12 refuted, 65 left unverified** when a usage limit cut the
verification short. The report is the audit dossier; this entry is what was done about it.

Then a fix pass on four file lanes (companion, console, page, tools+docs), each change reviewed
adversarially against a git baseline and re-checked by hand. **This release also starts the
repository's history**: commit `4fe2409` is byte-identical 3.60.0, so every line below is a
`git diff` away. A folder copy of 3.60.0 and its shipped exe/elf sits in `backups/` beside the repo.

Nothing here changes what the app does. The install lane came through the audit clean and was
not touched where it matters - see "What was deliberately left alone" at the end.

### The cheat library re-sent 2,092 files every fifteen minutes

Every `[cheats] console is missing 2092 file(s)` line in pms.log since 25 August was the same
arithmetic. The console's directory listing was capped at **96,000 bytes** - about 1,265 entries -
and the three big cheat folders are larger than that:

| folder | on the PC | console reported | re-sent |
|---|---|---|---|
| json | 1994 | 1265 | 729 |
| mc4 | 2142 | 1262 | 880 |
| shn | 1763 | 1280 | 483 |

729 + 880 + 483 = **2092**. The four small folders (376, 369, 369, 9) reported correctly. The
console said so honestly - `"truncated":true` on every capped page - and the PC never read the
flag, so it computed "missing" from a partial listing, re-sent files that were already there, and
did it again fifteen minutes later. About 30 MB and 45-90 s of console I/O per pass, on the
single-threaded accept loop the UI and installs share.

Both halves are fixed. The console lists into a 1 MiB heap buffer that doubles to 8 MiB before it
will admit truncation. The PC honours `truncated`: a folder it cannot list in full falls back to
the FTP listing when there is one, and otherwise is reported as unknown and **nothing is sent to
it**. The 15-minute thread and `POST /api/cheats/sync` are mutually exclusive now.

The library itself lost its dead weight. `xml/`, `xml_orbis/` and `xml_prospero/` were 747 files
the console writes to disk on every boot and never reads (368 of 369 in each are byte-identical to
`patches/`; the three that were not are copied into `patches/` by the packer). The 706
`<name>.mc4.xml` twins in `mc4/` are unreadable by the engine and produced phantom versions like
`01.00.mc4` in the version list. The pack went from **7,022 files / 32.2 MB to 5,569 / 27.2 MB**,
and the companion syncs the same four folders the console reads.

### Four protective holds that raised instead of holding

`Queue._pause_pending` was decorated `@staticmethod` and declared `self`. Its four callers - no
verdict from the installer, an unknown host, a wedged hand-off, and the console being switched
off with jobs queued - each passed two arguments to a function that wanted three. Every one of
those safety paths raised `TypeError`, the worker's catch-all painted the job red with "This one
stopped unexpectedly", and the rest of the queue marched into the same failure. The behaviour
described in 3.17-3.29 had never once run. The decorator is gone; the four holds now hold, and
Start queue releases them.

### The install lane, tightened without being changed

- **A stopwatch no longer overrides the evidence.** The promoting loop's 600 s cap sat below the
  1,800 s "row has not moved" rule, so that rule was dead code and a large title still copying
  after ten minutes ended as `stale_install` with advice to delete it. The cap is 2,100 s; when it
  is reached with no evidence either way the job ends `install_unconfirmed` and says the console
  may still be installing. The acceptance conditions are byte-for-byte what they were.
- The loop's heavy reads (a full app.db pull plus four folder scans, ~40 requests per 3 s tick)
  run every 15 s; the bgft row stays on the 3 s cadence.
- `install_spawn` pops its 120 s debounce entry on every early return, so the retry-once and a
  Start pressed on a held job are no longer refused by our own debounce with the real cause lost.
- Cancel is read between phases, not only at the top of `_run`; a cancel during the mount pull
  lane asks the console to stop the download.
- **The verdict now carries a token.** The shop writes a per-request token as line 3 of
  `installer-req.txt`; `pms-installer.elf` echoes it into `installer-res.json`, and a verdict
  without this request's token is ignored, so a late result from an installer that was still
  pre-allocating when the lane gave up can no longer be read as the next job's. The installer
  consumes the request file the moment it has read it, so a copy launched by hand from Payload
  Manager installs nothing. Both ends keep every existing field and the file names.
- The spawn latch's check-then-set is one step under a mutex; `pm_get` counts only an HTTP 2xx (or
  the loader's `OK`) as a spawn and waits 10 s, not 30, on the accept thread.
- Every hand-off and every verdict is one dated line in pms.log now, with the task id.

### Console safety

- **The move lane wrote into a watch folder under its final name.** `move_thread` copied straight
  to `<drive>/homebrew/<name>`, which is the partial-mount trap recorded in 3.24 on a different
  lane. It copies to `.part`, keeps the size comparison, and renames. `/api/move` accepts only a
  backup under the homebrew roots and checks the destination drive is really mounted (the same
  `st_dev` test `/api/devices` uses) before copying.
- Rest mode refuses - unless forced - while a spawn hand-off is live, a move is running, or an
  upload is in flight, not only during a download. The PC relay stops answering "safe to rest" on
  a timeout: only a connection the console closed after acknowledging counts, and it refuses while
  our own queue has a running task.
- `restart_shadowmount()` asked `running_title()` for `title_id`; that function returns `titleId`.
  The busy guard could never fire, so ShadowMount could be reloaded under a game running off a
  backup. One key name.
- Delete-backup uses `lstat` and never follows a symlink; a container that is still mounted is
  reported as staying playable until the console restarts, which is what happens.
- Cheat engine: the expect-gate ran only when the on and off byte strings were the same length -
  549 real entries were written blind. It now reads `max(on, off)` bytes and gates every non-forced
  write. The patch undo file was truncated on every Apply; it is created once (per installed
  version) and appended, so a second Apply cannot destroy the originals. A partly applied patch says
  so. `hex2bytes` refuses an odd digit count instead of dropping a nibble. Cheat lines up to 64 KiB
  and entries up to 4,096 bytes parse instead of vanishing; a mod with dropped entries is refused
  rather than reported ON.

### The console API

- **Uploads no longer freeze the console.** `/api/fs/write` runs on its own thread with the socket,
  exactly like `/pkgfile/`, with a 120 s receive timeout instead of the accept loop's 8 s - so a
  brief PC stall no longer aborts a 90 GB push, and the UI keeps answering during it. A body with
  no Content-Length is refused instead of becoming an empty file.
- Any page in any browser on the LAN could fire a state-changing GET with no Origin and no
  Referer. Both servers now also refuse `Sec-Fetch-Mode: no-cors|navigate` and image/script/frame
  destinations on state-changing routes only; absent headers stay allowed, and the app's own
  `fetch()` calls are untouched. `host_is_private` consumes the whole token, so
  `10.0.0.1.attacker.example` is a name, not a private address.
- `/api/fs/delete` and `/api/fs/mkdir` are bounded to the roots the file API already trusts;
  `/api/engine/lprobe` and `/api/engine/install-url` sit behind the diagnostics flag. The notify
  icon probe no longer sleeps on the accept loop. Cheat routes parse a document once per request.
- `appinst_once()` runs under a mutex. `sceAppInstUtilInitialize` is still called exactly once,
  lazily, and `sceAppInstUtilAppInstallPkg` still has exactly its two call sites.

### The companion

- The page is sent gzipped with an ETag and answers 304, and static assets carry a week of cache;
  `index.html` is compressed once per build and served from memory.
- A 300 s idle timeout reaps connections that never send a request line; the socket a package
  streams over is explicitly exempt. Listen backlog 64. HEAD answers what GET would.
- `_origin_ok` gained the same Sec-Fetch rule, applies to the state-changing GET relays too, and
  accepts the page's own hostname - so opening the PC by name no longer gets 403 on every POST.
- `config.example.json` is a template again, not a live config layer; a saved `shadowmount.port`
  of 9021 (elfldr) is read as 10101 in memory. `/api/engine/state` says `spawn`, which is the
  only lane there is.
- `helper_status()` is memoised for 5 s (its comment always said so), `console_usb_packages()`
  for 20 s, missing console icons for 10 minutes; `federation_self()` asks for the LAN address
  once per call, not once per game. A peer that re-identifies drops its old entry.
- `installed.json` is written only when it changes, under the lock. pms.log rotates at write time,
  not only at start. Handler exceptions log one line and answer 500 JSON instead of an empty reply.
- At boot the frozen exe sweeps its own orphaned `_MEI*` extraction folders (170 of them, 1.2 GB,
  were in %TEMP%); only folders carrying our page and older than a day, never the live one.
- A backup file name outside latin-1 no longer aborts `/library/<key>` before the first byte
  (RFC 6266 `filename*`). `/api/hash` refuses files over 8 GiB instead of reading them on the
  request thread.

### The page

- `api()` has a timeout (12 s default, longer where a route is known to be slow) and a non-2xx
  answer reaches the caller as a refusal with the server's words. `refreshHealth` cannot pile up.
- **The console page fails over.** When the PC companion misses three health polls in a row the
  page repoints itself at the console's own API, says so in the pill, and keeps looking.
- Toasts are set as text (never markup), stay 6.5 s for errors, dismiss on click, stack to five.
  Peer names, library paths and format strings are escaped everywhere they reach `innerHTML`.
- Save in Settings and a language change no longer push a second panel entry that froze grid
  virtualisation and swallowed a Back press. A mod tile keeps its name after one toggle. The drive
  picker re-reads free space when its list is stale or was the static fallback. Queue rows say
  "PS5 decides the drive" for a package instead of printing `internal`, and the queue keeps polling
  while the dock is open so installs started from another device appear.
- `Open it on the TV` read `/http://10.0.0.99:8710`: the start-ellipsis on `.svc .v.ell` uses
  `direction:rtl`, which moves a trailing slash to the visual front. The value is written into a
  left-to-right span with an LRM guard. The Installing card explained the engine twice; it now
  keeps the server's sentence.
- The 29 keys used only through `tsub()` had never been translated into the eight newest
  languages, and the gate could not see them. They are translated; the gate counts them.
- Contrast (`--muted2` to 5.0:1), 44 px touch targets under a coarse pointer, reduced motion
  covering every animation, focus moved into a panel on open and back on close, RTL mirroring for
  the search glyph and chevrons, a sticky Settings header. "Running v1.05" became "Installed
  v1.05"; the header pill says "ready to install" instead of engine jargon.

### Tools, build and documents

- `tools/i18n_report.py` counts `tsub()` and ternary keys. `tools/message_report.py` bans the
  words its docstring promised (etaHEN, Elf Arsenal, DPI, :12800) and now sees server-side error
  sentences: 438 messages checked, 0 off style. `tools/check_web.py` has real ES5 rules, not only
  a parse. `tools/stamp_version.py` stamps three targets and refuses if one is missing.
- The exe excludes numpy (Pillow never needed it): **26.8 MB to 15.6 MB**, and 26 MB less unpacked
  per launch. The spec and `build-wsl.sh` run the i18n, message and storage-tile gates as fatal,
  `build-wsl.sh` refuses to link a stale bundle, and the web bundle is an allow-list.
- `tools/ready_check.py` no longer deletes a real-looking title id or writes into a watch folder,
  and refuses to hand the console `127.0.0.1`. `tools/verify_console.py` clears the install latch
  only when it is stale, and stages 5-7 are opt-in. `doctor.py` checks the engine that exists.
- `deploy.py app` is retired (it pushed the whole 92 MB `ps5-app/` tree into ShadowMount's watch
  folder). **`deploy.py elf` is the scripted console update**: FTP probe, `.part` upload to Payload
  Manager's registered path, pre-quit busy checks, quit, reload, and a wait for the new version.
  `register_tile.py --apply` refuses without an explicit flag.
- `SETUP.md` is the one current runbook. README is rewritten to the truth; ARCHITECTURE, TOOLCHAIN,
  ROADMAP, ROADMAP-OVERHAUL, BUILD-PS5-APP and SETUP-REMOTE carry dated superseded banners and
  corrected sentences; the engine manual's sections marked current were re-read against the code.
  `LICENSE` (GPL-3.0) and `THIRD-PARTY-NOTICES.md` exist. `config.example.json` mirrors
  `DEFAULT_CONFIG`.

### Addendum, 2026-09-04 (later the same day) - ShadowMountPlus updated

The ShadowMountPlus build embedded in the console ELF (`payload_bundle.h` ->
`payloads/shadowmountplus.elf`) was replaced with the newly released one: 2,465,896 bytes,
md5 `3aaf3e4f…`, in place of the 24 August build (1,762,880 bytes, md5 `faa6cf21…`). The ELF was
rebuilt at the same version - **3.61.0 stays 3.61.0** - and redeployed; the exe does not carry
ShadowMountPlus and was not touched. Nothing else changed.

Two things worth knowing, read out of `payload_bootstrap()`. The shop writes its embedded copy to
`/data/pkg-mutant-shop/payloads/` and launches it **only when nothing already owns port 10101** -
with ShadowMountPlus up, the whole step is skipped, so after this reload the on-console copy was
still the old 1,762,880-byte file and the running instance is unchanged; the new build lands
there at the next boot or rest-wake where ShadowMountPlus is not yet running. And the launch goes
through Payload Manager, which resolves `/loadpayload` **by basename to its own registered copy**
when it has one (the pldmgr trap): so the copy Payload Manager holds must be updated too - the
file under `ps5_autoloader/` on the Desktop is that one, and the app never reads it.

### What was deliberately left alone

- The install verdict conditions (`_bgft_before_ok`, following a live bgft job from a
  `console_busy` hold, gating the retry-once on SCE codes). Each changes when a job is called done;
  that is an owner decision, not a fix.
- HTTP/1.1 keep-alive on the companion, moving the console's state-changing routes to POST, and
  reflecting Origin instead of `*`: each needs the console page verified on hardware first.
- The ~216 dictionary keys nothing uses (about 110 KB of the page): a cleanup, not a fix.
- Bounding cheat writes to the game's image needs the module map the engine does not read.
- Naming (Add-ons vs DLC, Installed vs MOUNT vs Mounted) and a confirmation before Re-send backup.

### Verified

`py_compile` on every Python file; `check_web` (node-parsed, ES5 rules); i18n 15 languages x 291
live keys; message style 438/0; `test_storage_tiles`; `prospero-clang -Wall -fsyntax-only` on
`server.c` and `installer_probe.c` - the same 7 pre-existing warnings as 3.60.0 and none new
(two dead-declaration warnings went away). Install call-site counts, the `RUNNING` set, and the
`/api/health` key set (plus one additive key, `config_path`) compared against the baseline. The
ELF embeds the rebuilt `pms-installer.elf` (built `-g`) and the 3.61.0 page verbatim; the exe
carries the same page.

---

## [3.60.0] - 2026-09-02 - "Fifteen languages, and the panel says what it is" `[VERIFIED]`

### Fifteen languages, all complete

Eight added - **Chinese, Hindi, Russian, Italian, Korean, Turkish, Polish, Dutch** - chosen by
number of speakers. With English, Spanish, Portuguese, French, German, Japanese and Arabic that is
**15**, and `tools/i18n_report.py --check` reports every one of them at **100%**.

**The wiring more than doubled: 94 keys live, now 213.** 117 edits were located across the game
panel, the mods grid, the cards, the drives row and the queue.

**The coverage tool was measuring the wrong thing and said so confidently.** It compared each
language against every key DEFINED (503) rather than every key USED (213), so eight fully-translated
languages read as "42.3% complete". The dictionary carries keys nothing asks for yet; translating
those is work no one can see. It now measures live coverage, which is the number that means
something.

**The language selector derives from the dictionary.** Adding a language is one step - add its
dictionary - and the selector offers it, because it filters its name list against `I18N`. It showed
7 before these eight landed and 15 after, with no separate list to keep in step.

### The edit reviewer died, so the edits were verified by measurement instead

Two workflow agents failed, one of them the adversarial reviewer meant to check every proposed edit.
Nothing was applied on trust. A validator checked, for all 117: that the `find` text exists in the
real file **verbatim and exactly once**; that every `t("key")` in the replacement exists in the
dictionary; and that nothing introduces `title=` or a ninth `scard`/`sec_` match. It rejected 11 -
eight referencing keys that do not exist and three with stale anchors - and 106 applied with zero
skips. Measuring beat reading.

The eight new locales were validated the same way: every placeholder (`{n}`, `{id}`) preserved
exactly, no brand or acronym translated away, nothing three times longer than its English button.
**Zero problems across 8 languages x 213 keys.** MOUNT survived verbatim everywhere, which three
languages had got wrong in the previous pass.

### The settings header on a phone

Three grid columns - back, a centred `nowrap` title, the controls - do not fit 375px, so the
language selector rode over "SETTINGS". Two rows on a phone: back and title, then the controls at
full width. Measured: title `[128-355]`, selector `[39-257]` on its own row, no overlap.

### The controller legend is gone

It was drawn only under `html.ps5` - but a phone opening the console's own page **receives that
class**, because it comes from the `on_console` server fact rather than a UA sniff. So it was never
console-only, which is why it turned up on an iPhone. Markup and all three rules removed.

### The status pill moved beside the game title

`Installed` / `Update available` / `Not installed` sat among a dozen grey detail pills, where the
one fact you want at a glance looked like all the others. It now sits to the right of the name, and
wraps beneath it rather than squashing when the name is long.

### Size

`index.html` is now 577KB. The console served the previous 506KB in 16-31ms on a home network, so
this remains a non-issue; it will be re-measured after deploy.

---

## [3.59.1] - 2026-09-02 - "Translation corrections" `[VERIFIED]`

A review pass over the six machine-produced locales. Ten corrections, each one a change of MEANING
or of FIT rather than of taste:

- **`gp_badge_mount` in pt, fr and ar** was translated as a past participle - "Montado", "Monte",
  and an Arabic word meaning *installing/fitting*. MOUNT is a CATEGORY badge on a card that is
  explicitly **not** on the console yet, so all three were making the opposite claim. Left as MOUNT.
- **`gp_badge_update` in es** was "Actualizar", the verb. It is a status badge, so it needs the
  noun: "Actualizacion".
- **`ui_install_base` and `gp_btn_install_base_default` in fr** dropped the word "base". The panel
  deliberately distinguishes the base game from an update, and "Installer le jeu" loses it.
- **`ui_filter_addons` and `gp_pill_addons` in fr** were left in English while every other locale
  localised them.
- **Three strings that did not fit their control**: es "Instalar solo la actualizacion" (30 chars
  against 19 on the panel's primary button) and es "Se instala en: el destino por defecto de la
  consola" (50 against 28).

### A verification gap worth recording

`tools/i18n_report.py` checked that every key EXISTS in every language, and reported 100%. It does
not check that a value was actually TRANSLATED, and the review claimed all six locales stopped
partway. Measured against the source: they had not - real coverage is **96-99%** per language
(es 13, pt 19, fr 24, de 23, ja 4, ar 4 strings identical to English out of 614), and of the 69 keys
actually wired to something on screen only **four** matched English, three of them legitimately
("Patches" in pt and de, "Add-ons" in de). So the reviewer overstated the problem and was right
about the specifics - which is why each item above was checked against the source before being
applied rather than taken on trust.

---

## [3.59.0] - 2026-09-02 - "Languages, and a phone that behaves" `[VERIFIED]`

### The panel's Back button also pressed Sleep

Measured, not guessed: with a panel open the panel's Back button occupies `[25,69,102,112]` and the
main page's power button `[61,51,101,91]` - they overlap almost exactly. Hit-testing was already
**correct** (`elementFromPoint` returned `#dback` at both centres and the scrim covered the header),
so this was never a layering fault. It is the touch-screen ghost click: the tap closes the panel and
the click the browser synthesises afterwards lands on whatever is now underneath.

A shield keeps the scrim hit-testable but invisible for 450ms after any panel closes. Verified both
ways: with touch, the point where Back was now resolves to the scrim and dispatching the ghost click
fired the power button **zero** times; without touch the shield never engages, so the PC and the
console are untouched. It is gated on a real `touchstart` event rather than a media query, because
the console reports a coarse pointer in places and must never take that path.

### The queue fell off the screen in portrait

`.dockbody` is centred on the PILL (`left:50%` + `translateX(-50%)`), which is right when the pill is
centred and wrong on a phone where it sits at the left of its own row. Pinned to the viewport
instead, with real safe-area insets. Measured: 10 to 365 of a 375px screen.

### The mobile top bar

239px in five accidental rows, now 193px in four deliberate ones - logo + console status, the queue,
the icon buttons + search, the library summary. Every child gets an explicit `order`, because a
second `max-width:640px` block exists further up the sheet and the result would otherwise be decided
by source position. Measured: zero overflowing elements at 375px.

### Landscape safe areas

The landscape gate had **no** horizontal insets at all, which is why controls sat under the cutout.
Header, sub-bar, main, toasts and the queue all take `env(safe-area-inset-*)` now. The values come
from the device, so a notch, a punch-hole, a curved edge or a plain tablet each get what they need
with no device sniffing anywhere.

### Languages

**494 keys across seven languages, every language at 100%** - `tools/i18n_report.py --check` passes.
`applyI18n` learned `data-i18n-tip` (tooltips) and `data-i18n-aria` (the accessible name of an
icon-only button, which is the only name a screen reader ever gets), so anything added later only
has to carry the attribute. `setLang` now re-runs every path that paints words - grid (FORCED: it
memoises on a signature of the games list, which does not change when only the language does),
queue, drives, source bar, services, and an open game panel rebuilt in place.

**A bug caught before it shipped.** Three elements had been given `data-i18n` whose text JavaScript
rewrites with live values - the connection pill, "Start queue (n)" and "Add-ons (n)". Because
`applyI18n` assigns `textContent`, every language change overwrote the live value with the static
one: the connection pill fell from "PS5 10.0.0.99 - engine not ready" back to "Connecting..." and
stayed there. The rule is that an element JavaScript writes must never carry `data-i18n`; the
translation belongs inside the writer. Verified after the fix: "Iniciar cola (0)", "Complementos (3)"
- translated, and still carrying their counts.

Arabic finally has styling: `applyI18n` had always set `dir="rtl"` and the stylesheet had not one
rule to answer it. Flexbox and text alignment follow `dir` on their own; what does not is anything
positioned with a physical left/right, so exactly those are corrected - the four card corner chips
and the panel's back and close buttons. Mirroring the whole interface is a larger design question
and is deliberately not attempted.

### Honest scope

447 keys are translated and in the file; about 94 are wired to something on screen. The remainder are
strings built inside JavaScript and need `t()` at each call site rather than an HTML attribute. Only
unambiguous matches were wired - 109 candidates with no unique childless element were left alone,
because a wrong `data-i18n` silently replaces real text on every device. The 167 toast strings are
excluded entirely: routing them through `t()` would drop them out of `tools/message_report.py`'s
house-style check, and that gate is not being gutted quietly.

**Size:** `index.html` went from 271KB to 506KB. The companion gzips its responses; the console's C
server does not, so the PS5 receives that raw.

---

## [3.58.0] - 2026-09-02 - "The phone, the queue and the language switch" `[VERIFIED]`

### The phone view did not work, and it was two faults stacked

Measured on the live page at 375x812, not estimated. `body{overflow:hidden}` propagates to the
**viewport**, so the document can never scroll - proven by setting `scrollTop` to 500 and reading
back 0. That leaves `<main>` as the only scroller, and `header` (328px) + `.subbar` (433px) are both
`flex:0 0 auto`, so they took **761px of 812** and left `main` a **51px** content box holding a
7145px grid. On a real iPhone, with Safari's toolbars up, that sliver sits entirely off-screen.
Fixing the viewport unit alone leaves a 52px box; shrinking the chrome alone leaves the bottom under
the toolbar. Both had to go.

| | before | after |
|---|---|---|
| header | 328px | 239px |
| filter bar | 433px | 175px |
| **main content box** | **51px** | **366px** |
| grid | unreachable | 2 columns, scrolls |

The storage block went from a 309px wrapping right-aligned pile to a 56px sideways-scrolling row;
the two `.spacer` divs (about 272px of nothing on a narrow screen) are hidden; the layout is sized
in `svh` so it is never taller than the visible band.

**THE GATES ARE ARITHMETIC ONLY, deliberately not `html:not(.ps5)`.** The console's own page sets
`.ps5` on `<html>` from a **server fact** (`h.on_console`), and a phone browsing
`http://<ps5>:8710` - which is exactly how this app is opened on a phone - receives that class too.
Guarding on it would have switched the entire phone layout off on the one device it exists for.
The two gates are `max-width:640px` and `(min-width:641px) and (max-width:960px) and
(max-height:540px)`; they are disjoint, and neither can match 1920x1080, 3840x2160, 1400x900,
1280x800 or an iPad.

`main` stays the scroller on every device. `virtScroller()` finds it by walking up from `#grid`
testing computed `overflow-y`, and `virtRender()` does `sc.scrollTop - grid.offsetTop` against that
same box - handing the scroll to the document would have silently frozen the virtualized grid.
Only `main`'s height changes.

### The queue could not be opened by tapping it

`#dockhead`'s click handler was a bare `classList.toggle("open")`. `renderQueue()` returns early
while the dock is shut - it will not rebuild rows nobody can see - and `dockShow()` is what adds the
class *and then paints*. On a mouse the `mouseenter` listener had already painted, which hid the
bug completely; on a phone there is no `mouseenter`, so tapping opened an **empty** drawer. The
click path now goes through `dockShow()`.

### The queue bar covered the open game panel

`.dock` was `z-index:60` against `.drawer` 50 **and `.scrim` 40** - so it painted over the artwork
*and stayed live through the scrim*. With `dockShow` bound to `mouseenter` and the console's stick
cursor sweeping continuously, merely crossing the pill popped the 580px queue drawer over the panel.
Now `z-index:30`. Not a guess: the dock was tested at 60, 30, 1 and auto, and its body won the hit
test over a grid card in every case - nothing under it ever needed 60.

### The language switch left text in the old language

Not the `renderGrid` signature memo, which was the obvious suspect and is **not** the cause -
nothing `buildCard()` emits goes through `t()`, so a forced re-render would change no card text.
The real cause was `QLBL_IDLE`: `renderQueue()` cached the dock label's `textContent` on its first
run and never invalidated it, so `setLang()` ran `applyI18n()` to write the translation and then its
own `renderQueue()` call one line later put the boot-language string straight back. That label was
then stuck until the page reloaded - exactly the reported symptom. It now reads the dictionary every
time, and the pill's other states are translated in all seven languages.

### Detail pills

The game panel's top bar now always begins Platform, Region, Title ID; everything after keeps its
previous relative order.

### Honest scope note

Translation coverage is still partial: the file holds roughly 650 user-visible English strings and
only a fraction pass through the dictionary. This release fixes the *mechanism* that made switching
unreliable and translates the queue. Routing the 126 toast strings through `t()` would silently drop
84 of them out of `tools/message_report.py`'s house-style check, so that is a deliberate decision to
take separately rather than a free win.

---

## [3.57.0] - 2026-09-02 - "One fact sheet, two columns, and the whole cover" `[VERIFIED]`

The game panel restructured to the user's sketch. Every action, id and handler is unchanged.

### The details were being said twice

An unlabelled chip row in the header and a labelled "DETAILS" grid below it. Four facts were
duplicated outright (platform, region, size, title id) and two more said the same thing in
different words: for an installed game `whereIs()` and the grid's `locTxt` **both** return
`g.installed_drive`, which is the `INSTALLED TO / LOCATED ON: Internal SSD` pair in the screenshot.

The grid is gone and every fact it carried is a labelled pill in the header, once. Pills size to
their own content — `inline-flex` with no width, so a two-character region gets a two-character
pill — and a value past its cap ellipsizes with the full string in the tooltip, which is exactly
what the grid's `cellTip()` already did for File and Path.

**`.dpill`, not `.chip`.** That class is shared by 19 elements — the settings cheat chips, the
"✓ Installed" badges on the Base/Updates/DLC rows, and `partLabel()`'s inline chip, which sits in a
text line where `max-width` and `text-overflow` do nothing at all. `.chip` is untouched.

Pure CSS, deliberately: `ResizeObserver` and `requestIdleCallback` are **not** in `check_web.py`'s
`KNOWN_GLOBALS`, so the obvious implementation of "panels that resize themselves" aborts both builds.

### ON CONSOLE deleted, without losing anything

Its four facts moved into pills first. `g.backup_path` mattered most: that `📂` line was its **only**
appearance anywhere in the file, and for a ShadowMount backup it is the answer to "where does this
actually live". It is now a Container pill with the full path in its tooltip. `g.console_size` — the
real installed size — now feeds the Size pill.

### The install bar to the left, mods to the right

One token on line 1860. The rescue two lines above stays byte-identical and the bar is **moved as a
node**, never re-authored: `#driveSel`/`#installAll`/`#queueBtn`/`#sendPkg`/`#moreBtn` keep their
parse-time bindings, and `.dcol > .dfoot` is a direct-child rule both columns satisfy.

### The tile IS the switch

A real `<button class="row modtile">`, not a div with `tabindex`. That keeps `disabled` — which is
what enforces all four refusal cases — makes Enter/Space work with no handler of our own, and puts
it in the tab order so the PS5's spatial navigation reaches it. Children are spans; a `<div>` inside
a `<button>` is not valid content.

**The state order is load-bearing.** Both engines set `can_toggle` to the same value as `running`
(`companion/server.py` `"can_toggle": live`), so testing `m.conflict || can_toggle === false` before
`running` — as the first draft did — makes the grey idle state **unreachable** and paints every tile
as a conflict whenever the game is closed. `running` is tested first:

| state | border |
|---|---|
| game not installed or not running | grey |
| running, mod off | red |
| mod on | green |
| conflicts with another mod | amber `#d08a3a` |
| cheat does not match this build | `N/A` |

Conflict is **not** `var(--warn)`: that is byte-identical to `--accent` (`#e6c454`), so conflict and
the focus ring would have been the same colour.

Two columns via a **nested** `.modgrid` wrapper holding only tiles. The compatibility banner, every
`cheatNote()` and "Disable all mods" are `.row` elements too, and gridding the container would have
laid them out as cells beside the tiles. `minmax(168px,1fr)`, never `column-count` — that is recorded
as causing open-time jank on PS5 WebKit.

Patches keep their own Apply button, full width, outside the grid. A patch is written into live game
code and the UI never sends the engine's `unapply`; it must not look like a reversible switch.

### The banner shows the whole cover

`icon0` is a 512×512 **square** and the banner is 1120×160 on the TV, so `object-fit:cover` scales to
the width: the image renders 1120×1120 and a 160px band survives — **14.3%** of the picture, rows
43–57%. That is why a game's logo was never on screen.

Growing the box downward does not fix it, and this was measured rather than assumed: at +96px the
window is still only rows 39–61%, so the logo stays cut. So: two layers. `.artbg` is the old cover
crop, dimmed to .34 and bleeding under the title, doing the one job it was good at — filling the
width with the cover's own colour. `.art` sits on top at its natural square, height-fitted, so
**100%** of the artwork is visible. The panel does not grow by one pixel: `.dbanner` still occupies
exactly 160px and both images are absolutely positioned. Same `src`, so one decode. No blur — it is
the most expensive property on this GPU.

### Verified

Against the patched source on a live server, driving the real `openDrawer()`: no throw; 10 pills;
Details grid gone; ON CONSOLE gone; `.dfoot` in the left column; `#driveSel` populated; two banner
layers; **reopening the panel twice does not throw and `.dfoot` survives** (the regression this
file has shipped before). On Cuphead: one `.modgrid`, 5 tiles, all `<button>`, all `idle` with
"Launch the game first" and all `disabled` — proving the grey state is reachable — grid containing
only tiles with the 3 note rows outside it, and `MODS.rows[i].row === .btn` so `refreshModsQuiet`
patches the right node.

### Known, unchanged from before

`info.running` is read once when the panel opens. Launch a game while it is open and the borders
stay grey until the panel is reopened. A standing poll was designed and rejected: it is not
registered with the page's own arm/disarm, `/api/mods` is not cheap on the console, and
`refreshModsQuiet`'s bare `.catch` would hide a failing chain forever.

---

## [3.56.0] - 2026-08-28 - "A title id is not a name, and the art gets the whole card" `[VERIFIED]`

### Cards that read "CUSA00304"

Reproduced before being touched, against two live machines: Casita showed CUSA00304 / CUSA00579 /
CUSA01412 / CUSA01589 / CUSA07083 / CUSA53974 as the card **name**, while the other PC showed
*Trials Fusion*, *LEGO Batman 3*, *Tony Hawk's Pro Skater 5*, *Dark Souls II*, *Cars 3* and
*LEGO Party* for the same six title ids.

The chain, both halves of which were behaving as written:

`build_library()` appends a console-only entry for a PS4 game that IS installed on the console but
is absent from that PC's folder. The PS5's app.db carries no row for PS4 games on this firmware, so
there is no name to read and it falls back to the title id (`server.py`, `"name": tid`). As a last
resort on a machine that knows nothing else about the game, that is right - an id beats a blank card.

`build_federated_library()` then merges the peers. A peer that *holds the package* advertises the
real name (`pg["name"]` is the game name; the filename lives separately in `items[].file`). When the
title was already present it folded in that peer's base/updates/DLC and even **borrowed its
artwork** - but never touched the name. The card ended up with the peer's package, the peer's cover,
and a title id for a name. `base=1` on the affected entries is the fingerprint of exactly that path.

It also explains why reloading sometimes fixed it: if the console entry had not been built yet on
that pass, the peer path created the game outright and used the peer's name.

Fixed three lines from where the artwork is already borrowed, and in one direction only - a
placeholder is upgraded to a real name, a real name is never overwritten. `is_placeholder_name()`
treats an empty name, a name equal to the title id, and a bare `CUSA/PPSA/PLAS/NPXS/NPUB/NPEB` id as
placeholders; "CUSA Adventures" is not one.

**Verified by reproduction:** a companion configured exactly like Casita - empty library, the other
PC as its peer, the same console - now returns all six real names and **zero** titles displaying an
id, against 6 before.

### The artwork runs the whole card

**No box metric moved.** `.grid` keeps its column widths, `.cover` keeps `padding-top:60%`, the name
row keeps its height. Measured at 375 / 1000 / 1920 / 2400: one distinct card height at every width,
including a card whose name wraps to two lines - so `virtMetrics()` still measures one probe card
and gets the truth.

`object-fit:cover` scales to the larger ratio, so a square 320x320 thumbnail in a 240x144 box was
drawn 240x240 with **40% of the picture cut off** top and bottom - which is what clipped the logos
on Back 4 Blood, Borderlands 4, Crimson Desert and Black Myth. The image is now given the card's
full content height (its own box plus the name row, `--nmh`), so the same source at the same scale
loses only ~17%: **about 83% of a cover survives, against 60%.** It is not upscaled, so not softer.

`.cover` stops clipping and `.card{overflow:hidden}` does it instead, which is what carries the art
into all four rounded corners. The name sits on top through a four-stop gradient - near-solid under
the text, fading to .30 at its top edge so the picture reads through the join rather than meeting a
hard bar (.52 there showed as a visible band across Cuphead and LEGO) - plus a text-shadow so a
title stays legible over pale artwork.

`--nmh` is the name row's real height (2.5em of clamped text + 8px + 9px) and is restated at each of
the three breakpoints where `.nm`'s font-size changes. Measured: the art ends flush with the card's
bottom border at every width. **The chips did not move** - `.cover`'s box is unchanged, so `.tid`
and `.sz` still sit exactly 8px above where the name begins.

---

## [3.55.1] - 2026-08-28 - "Two corrections from the adversarial pass" `[VERIFIED]`

Both found by the review panel on 3.55.0, both verified against the file before being touched.

### Plain `:focus` was sticky on a PC

Click a card, dismiss the drawer with Escape, and that card keeps DOM focus - so its four chips
stayed lit while every other card was blank, which reads as a rendering fault rather than a feature.

The reveal is now `:focus-visible`, which by definition does not match a click-focused
`div[tabindex]` (line 581, `:focus:not(:focus-visible)`, exists to encode exactly that). The reason
3.55.0 used plain `:focus` was the console's stick cursor, which focuses by *clicking* - and that
case is not lost: `.card:hover` covers it, and `.ps5` keeps plain `:focus` as a third path. So the
console now has hover **and** focus-visible **and** focus, and only the PC drops the sticky one.

### The justification comment was false

3.55.0 shipped `.cover .badge.run{opacity:1}` with a comment claiming *".card.running already tints
the border, and the two together are how you find it"*. **`.card.running` (line 167) is dead CSS** -
`buildCard` creates the element as `el("div","card")` and nothing anywhere adds a `running` class;
`isRunning` only chooses the badge. So that border tint has never rendered, and the badge exemption
is not one of two signals but the **only** on-card indication that a game is running - which makes
it more load-bearing than the comment claimed, not less. Comment corrected in place, no CSS change.
The dead rule is left alone: wiring it up would change what a running card looks like, which is a
separate decision.

### A measurement trap worth recording

`getComputedStyle` under Chrome's `--virtual-time-budget` reads a **frozen** transition - the value
never leaves its start point, so an element mid-fade reports `opacity: 0` forever. That made a
working `:focus-visible` reveal look broken in scripted checks while the `.ps5` variant (which has
`transition:none`) reported `1`. Painted screenshots are the honest check for anything transitioned.

---

## [3.55.0] - 2026-08-28 - "The chips step aside" `[VERIFIED]`

The four chips that sit on a card's artwork - platform, status, title id and size - are hidden until
the card is hovered or focused. The artwork gets the whole box back; the facts arrive the moment you
land on one.

### Why this is not just `:hover`

**The PS5 has no single answer, it has two.** The console drives a left-stick cursor (which is why
`.ps5 .card:hover` already existed - *"the cursor sweeps across cards continuously"*) **and** a D-pad
that moves the browser's own spatial navigation onto the `tabindex="0"` cards - the entire X-to-open
path. Both are wired, so neither has to be the one that works.

`:focus`, deliberately **not** `:focus-visible`. The console's cursor mode focuses a card by
*clicking* it, and a click-focused `div[tabindex]` does not match `:focus-visible` - line 581
(`:focus:not(:focus-visible)`) exists to encode exactly that distinction. Keying the reveal on
`:focus-visible` would have worked under the D-pad and failed under the stick.

**Every reveal is its own rule.** One unrecognised selector invalidates an entire comma-separated
list, so pairing a universal selector with a newer one is how you lose both at once. Every selector
shipped here is CSS2/3.

**A phone gets neither** - no hover, and a tap opens the drawer rather than parking focus - so a
coarse pointer that is not the console keeps the chips visible. Being unable to ask for the id and
size at all is worse than a busier card. `html:not(.ps5)` keeps the console out of that branch
whatever pointer type it reports; if the detection ever failed, it fails toward *visible*.

### What was deliberately not done

- **`pointer-events:none`** - the natural-looking way to guarantee the overlay cannot swallow a
  click. It would have silently killed the two tip-carrying badges' tooltips. Not needed: `opacity:0`
  leaves the spans hit-testable and a click on one bubbles to `.card`'s own `onclick`, the same path
  that opened the drawer when they were opaque.
- **`display:none`** - drops the id and size out of the accessibility tree, and zeroes a tooltip
  anchor's rect so the tip parks itself at 0,0.
- **any geometry change** - `virtMetrics()` derives the whole scroll window from ONE probe card's
  measured height. All four chips are `position:absolute` inside a `.cover` whose height is a
  percentage of its own *width*, so opacity cannot move anything.
- **a fade on the console** - these are the same four elements that had `backdrop-filter` stripped
  because a full grid window asked this GPU for ~450 blurs. With a stick sweeping a grid, every card
  crossed would start four more transitions, so `.ps5` gets the reveal instantly instead.

### The one thing that stays up

`.badge.run`. A running game is live state, not a static fact, and there is only ever one of it -
`.card.running` already tints the border and the two together are how you find it in a full grid.

### Verified

Rendered headlessly at 1920x1080 from the **patched file's own stylesheet**, with real library
artwork, in three states - idle, one card focused, and again under `html.ps5`. Idle: all four chips
gone on every card, Running still up. Focused: that card alone shows `PS5 / INSTALLED / PPSA27616 /
1.3 GB`, its neighbours untouched. Card geometry byte-identical between states.


---

## [3.54.0] - 2026-08-28 - "Half-height cards, and drives that follow the console" `[VERIFIED]`

Main page: the storage bar stops describing a console that is switched off, and the game cards lose
just over half their height without losing anything they display.

### Drives for a console that was not there

With the PS5 off, the bar still showed EXT1, Internal SSD, Extended Storage and USB0 — every one of
them reading *"space unknown"*, and the M.2 appearing **twice**. Reproduced exactly against the real
`build_storage()` before being touched: 34 / 51 / 6 / 19 games, the same four tiles as the report.

Two things behind it. `console_apps()` serves its last good list for the **lifetime of the process**
once it has one — past 30s it starts a background refresh and returns the stale list anyway
(`server.py:3603`). That is right for the library, where browsing your games with the console off is
the entire point, and wrong for a storage bar, which is a live readout of what is plugged in. So
`apps is not None` was true forever and the tiles were drawn from a remembered `app.db`, while their
capacity came from `/api/devices`, which correctly fails when the console is asleep.

The empty capacity map also **resurrected the duplicate**. `loc:2` folds into the real extended drive
only `if dst in cap`; with `cap` empty that fold cannot run, so the M.2 came back as EXT1 (its 34
backups, bucketed by container path) *and* as Extended Storage (its 6 packages, bucketed by app.db's
location code). The duplicate was only ever visible offline — which is why it survived the 3.51.0 fix.

Console drives are now gated on `/api/devices` having actually answered. It is a live round-trip
behind the cached TCP probe, so it costs nothing when the console is off, and a console that is up
always answers it with all eleven destinations — `None` means exactly *"did not answer"*. The tiles
come back on their own the moment it does; nothing has to be pressed. PC and peer tiles are untouched.

### Cards at just over half the height

`.grid` is **untouched** — `minmax(172px,1fr)` and every breakpoint override of it — because the
column width was already right on both the PC and the console. Only the height came down, and every
badge, tag, id, size and name still renders exactly as before.

The artwork box was `padding-top:133.33%`, a 3:4 portrait reservation. The source is a **320x320
square** thumbnail (`THUMB_PX`, confirmed live), so that box was already cropping a quarter off each
*side*; `60%` keeps the middle band instead, which is where a game logo sits.

The name row was `min-height:32px` against a two-line clamp. `min-height` is a floor, not a size: two
lines at the TV's 15.5px is 38.75px, so **on the console a card with a wrapping name stood 6.8px
taller than one without** — measured at 1920x1080, seven cards at 372.2px and one at 379.0px. That
matters more than it looks, because `virtMetrics()` measures **one** probe card and computes the
whole scroll window arithmetically from it, so mixed heights make the window drift down a long
library. `height:2.5em` is exactly the two lines it clamps to and follows the font size through every
breakpoint.

Measured, all breakpoints, all cards uniform:

| viewport | card | was | clear artwork between the tag rows |
|---|---|---|---|
| 1920x1080 (console) | 241 x **201.4** | 372.2 / 379.0 | 83.7px |
| 2400 (4K) | 303 x **244.7** | ~458 | ample |
| 598 | 155 x **143.1** | ~290 | 35.7px |
| 378 (phone) | 166 x **147.4** | ~300 | 42.5px |

**Quality goes up, not down.** `object-fit:cover` scales to the larger ratio, so a 300px 4K card used
to ask 400px of height from a 320px thumbnail — upscaling. Measured old/new sampling at 4K:
**1.25 -> 0.94**. Every breakpoint now downsamples, which is the sharp direction.


---

## [3.53.0] - 2026-08-28 - "Three things the 1500px measurement could not see" `[VERIFIED]`

An adversarial review of 3.52.0 found three real defects. All three were confirmed against the file
before being touched, and the third only exists on the device this app is for.

### The About logo was squashed on the console, and only on the console

`.mark{width:44px;height:44px}` lives inside `@media (min-width:1600px)` - the block whose own
comment reads *"PS5 on a 1080p TV"*. **The PS5 renders at 1920px, so the console fires it.** The new
`.scard h3 .mark` rule declared no width or height, so the old one was unopposed: the span became
44x44, `max-width:100%` then capped the image to 44px, and `overflow:hidden` clipped what was left.

`class="mark"` appears **exactly once** in the file - on the About logo added in 3.49.0. The page
header it was originally written for uses `.brandlogo`. So the rule had one consumer left and it was
squashing it. Removed, and the settings rule now states `width:auto;height:auto` so the next one
cannot creep back in.

Measured at 1920x1080: the wordmark renders **63x22**, its natural shape, inside a card ending 16px
to its right. Every earlier measurement was taken at 1500px, which is below the threshold - the bug
was never in view.

### Two buttons renamed themselves after one press

Moving the actions into the card headers meant shortening two labels, and the handlers still restored
the originals: `#testConsole` markup said **Test** and its handler wrote back **Test connection**;
`#cheatRescan` said **Rescan** and wrote back **Rescan cheats**. So each read correctly until first
use and wrongly for the rest of the session - and in Your PS5, the one card with three header
buttons, that is enough to wrap the row permanently.

`#devScan` does this correctly (markup and handler both say "Find PCs"), which is what shows these
two were an oversight rather than a decision. Verified by pressing both: labels unchanged, no header
row wrapped.

### Also

- **The inbox path was rendering proportional.** `mono` and `ell` are not standalone rules in this
  file - they only work through a parent like `.svc .v`. On a plain `.hint` div they did nothing, so
  the one path a user has to type by hand was in the body font. It now sets `var(--mono)` directly,
  and `word-break:break-all` so a long path wraps inside the card instead of straining it.
- **The fleet list is hidden on `!== 1` rather than `> 1`.** With `> 1` a console count of *zero*
  also hid it, taking the "none configured" message with it. It is only redundant when there is
  exactly one console to name.

**48 checks, all passed.**

---

## [3.52.0] - 2026-08-28 - "Settings, second pass: the panel stops scrolling" `[VERIFIED]`

Six changes from a marked-up screenshot, plus one the markup made obvious.

### The dead space between rows is gone, and so is the scrollbar

The cards were a CSS **grid**, and a grid stretches every row to its tallest card - so a short card
left a band of empty space before the next row started. That is the gap the user drew a box around,
twice. The cards flow in **columns** now (`column-count` with `break-inside:avoid`), so each one ends
where it ends and the next starts immediately below it.

Measured: largest gap between stacked cards is **13px** - exactly the intended margin - and the panel
**no longer scrolls at all** at 1500x1000. Reflow verified at 1500 / 1000 / 700 / 430 px: 3 -> 2 -> 1
columns, nothing overflowing at any width.

### A card's actions moved up beside its title

`Your PS5` (Find it / Test / Open on PS5), `Cheats & mods` (Rescan), `Other devices` (Find PCs) and
`Advanced` (Clear state / Install log). Each card's `<h3>` is now wrapped in a `.chead` flex row with
its buttons on the right - **siblings of the heading, not inside it**, so the heading still reads as
a heading.

**Power & helpers was deliberately left out.** Its button stops every homebrew payload on the console
the instant it is pressed, and a header is where you put the things that are safe to hit by accident.

### And the rest

- **About stopped spanning two columns.** At 750px wide its four label/value pairs were so far apart
  the user drew leader lines between them. It is 372px now, the same as every other card.
- **The logo was hanging out of its card**, over the one beside it. Height-capped, allowed to
  shrink, clipped to the box: right edge 1305 inside a card ending at 1321.
- **The address box was full width for fifteen characters.** 190px now.
- **"PS5 - 10.0.0.99" is gone.** The Connection row two lines above it already said that. The fleet
  list stays in the DOM and reappears the moment there is more than one console to tell apart.
- **Inbox is a chip again**, like the four beside it. It had been a printed row because a chip keeps
  its folder in a tooltip and `.tipbox` is `display:none` under `pointer:coarse` - so the inbox path
  is now *also* printed under the chips, where a phone can read it. Verified visible at 430px.

### One the screenshot could not show

Header buttons measure **51x23px**. That is fine for a mouse and fine for a controller - which
focuses rather than aims - but under half of what a finger needs, and this file already sets a 44px
floor for buttons elsewhere under `pointer:coarse`. They get `min-height:38px` and real padding on
touch.

**48 checks, all passed.** 63/63 ids, every handler bound, six LEDs green, no empty rows, eight
translatable titles, and `restOff` confirmed absent from every header.

---

## [3.50.0] - 2026-08-27 - "The cheat folders were unreachable on the console" `[VERIFIED]`

Two defects in yesterday's Settings redesign, both found by an audit of the page I had just shipped.

### The chips hid their paths from the only devices that needed them

The Cheats card exists to tell you where to put files. 3.49.0 turned the five folders into chips and
moved every path into `data-tip` - with a source comment claiming the tooltip was *"still reachable
with a controller"*. That was wrong, and both escape routes were closed:

- `index.html:500` - `@media (pointer:coarse){ .tipbox{display:none!important} }`. Tooltips do not
  exist on touch, which is the phone the Devices card invites you to open.
- The tip driver's other route is `focusin` (`index.html:3680`), and the chips were bare `<span>`
  elements. **A span with no `tabindex` never receives focus**, so that path could never fire.

Split by what you actually do with each path. The **inbox** is the one you type, so it is a printed
row again (`.v.mono.ell`, which truncates from the left - the correct end for a path). The four
format folders are destinations you never type, so they stay chips but gained `tabindex="0"` and a
focus ring, which makes them reachable with a D-pad.

### The wordmark was 81px left of centre

`.sheet h2{flex:1}` centres the title inside its own box, and that box sat between a narrow left
cluster (Back) and a much wider right one (language, save, close). Measured: **81px off**. The header
is a `[1fr auto 1fr]` grid now, which is the only arrangement that holds the centre however wide the
side clusters get. Measured after: **-5px**, and it no longer moves when the save status flashes.

*(An audit lens also claimed the title shifted whenever `saveState` flashed. Measured at 81px both
with and without the longest status string - refuted, and the fix landed anyway on the real defect.)*

**48 checks, all passed.** 63/63 ids, every handler bound, all six LEDs green, live data in every row.

---

## [3.49.0] - 2026-08-27 - "Settings: one page of cards" `[VERIFIED]`

### Eight tabs become one page

The tabs were the reason the page was hard to use: the answer to *"why will this not install"* was
always behind a tab you were not on. Settings is now a single grid of eight cards - **Your PS5,
Installing, Your games, Cheats & mods, Other devices, Power & helpers, About, Advanced** - each with
its own icon and heading, reflowing 3 columns to 2 to 1 as the panel narrows. Verified at 1500 /
1000 / 700 / 430 px: no card overflows and the panel never scrolls sideways.

Every one of the **63 controls kept its id**, and that is not tidiness. Handlers here are bound
**once at parse time**, so a renamed id is a button that silently stops working - and two are worse
than silent: `openSettings()` reads `setIp` and `setPort` **unguarded**, so losing either would throw
before `_showSettings(true)` and the panel would never open at all.

### What the redesign changed beyond the layout

- **A plain connection line.** The card now opens with a lit dot and *"Connected to 10.0.0.99"*.
  There was no such row anywhere - you had to read the engine block and infer it.
- **The cheat folders became chips.** Five rows of long Windows paths told you nothing at a glance.
  The chips say which formats are handled; the folder moves into the tooltip, which the PS5 draws on
  focus as well as hover, so a controller can still reach it.
- **A controller footer** - X Select / O Back / OPTIONS Help - drawn only on the console, keyed off
  the `.ps5` class the app already sets. No JS has to remember to toggle it.
- **The rest-mode explanation was printed twice**, verbatim, ten pixels apart. Once now.
- **`restOff` stays a red-outlined button on its own row.** It stops every homebrew payload on the
  console the moment it is pressed; the mockup's slot for it looked like a toggle, and a destructive
  action must not be able to read as a preference.

### Removed

`setTabs` / `.settab` / `showSetTab()` / `_setTab` - the whole tab mechanism. `engineName`, which had
no JS reference at all. `fixHosts` + `hostsNote` + `cleanupHosts()` - an inline-hidden button whose
only caller was its own handler, calling `/api/dpi/reload`, which the console does not implement.

### A translation bug, found and fixed on the way

`applyI18n()` assigns **textContent**, which has two consequences the redesign had to respect. A
title carrying `data-i18n` directly would have **wiped its own icon**, so the key sits on an inner
span. And `&amp;` in a dictionary value renders as those five literal characters - *"Cheats &amp;
mods"* had been showing exactly that. Four values across the language files are fixed.

Verified across all seven languages: titles translate (`Tu PS5`, `Vos jeux`, `Energía y servicios`),
all eight icons survive the switch, and no raw entities remain.

### Proven, not assumed

63/63 ids present · every handler still bound · all five saved settings round-trip through
`POST /api/config` and back **with the original values restored afterwards** · every status row
populated from live data with all six LEDs green · `consoleNote` appears and the three PC-only
buttons disable under a simulated console · the install log fetches and renders.

`tools/ready_check.py` gained checks for the new shape - card grid, eight cards, no tab machinery,
all eight titles translatable, all five saved controls present - and **stopped depending on FTP** for
its transfer check. `ftpsrv` is a separate payload the user may not have loaded; it was not on this
run, and a passing transfer was reported as a failure while its 227 MB probe was left behind, because
the cleanup needed FTP too. It uses our own file API now, which is by definition running.

**48 checks, all passed.** Nothing outside the Settings panel was touched.

---

## [3.48.0] - 2026-08-26 - "One drive, one tile, four facts" `[VERIFIED]`

### The M.2 appeared twice

Two tiles both read **Extended Storage**: one with 33 backups (1557 GB), one with 6 games (44 GB).
They are the same physical drive. A title with a container is bucketed by where that container
really is (`ext1`); a title without one falls back to app.db's location code, and code `2` *is* the
extended drive. Nothing merged them. The app.db buckets now fold into the real drive they name -
`0` into internal, `2` into whichever extended drive is present.

### 790.9 GB of games on a 673.9 GB drive

Internal claimed more used space than the drive physically has. The figure was the sum of each
title's **app.db size**, and app.db's size is metadata - this project already records it as bogus
and reads real container sizes wherever it can. It overshot by 138 GB and said so on screen.

When the console gives us real `statvfs` numbers, used is now `total - free`: what the filesystem
says is occupied. The per-title sum survives only as the fallback where nothing can be measured.

    before   Internal SSD   790.9 GB used   of 673.9 GB     <- impossible
    after    Internal SSD   652.8 GB used   of 673.9 GB     21.1 GB free   51 games

### Every tile said something different

A console drive showed free space. A PC showed a game count. One PC also showed its IP address. Read
side by side they looked like different kinds of thing. Every tile now carries the same four facts -
**device, what its games occupy, free of total, how many games** - and no addresses: a tile says what
a device holds, not how to reach it. PCs report the capacity of the drive their library actually sits
on, which they always knew and never sent.

### "Casita (this PC)" - on the PS5

`local` was set by whichever **companion answered**, which is the wrong question. On the console the
page is served by the PS5 and answered by a PC, so the console's screen announced a PC in another
room as "this PC". The browser is the only thing that knows where it is being looked at, so it
decides now: on the console, no PC is "this PC". Verified by driving the real page - the marker
appears exactly once on a PC and disappears entirely under a PlayStation user agent.

### The Devices list had the same split personality

Peers got an indicator, a title count and a PS4/PS5 breakdown; **this PC** got a bare line with
none of them. Both are written the same way now - indicator, name, titles, PS4/PS5 split, OS - so
the list reads as one list. The address stays *here*, in Settings, where knowing it is the point.

**Note:** a peer only reports its drive capacity from 3.48.0 onward, so Casita shows *"space
unknown"* until it is updated. Nothing else about it is affected.

---

## [3.47.0] — 2026-08-26 · "Audit pass: the tool that could crash the console, and five things it hid" `[VERIFIED]`

A full six-lens audit of everything this session touched, every load-bearing finding checked a second
time by an agent whose job was to refute it. Ten fixes landed; a dozen further claims were refuted on
re-check and deliberately left alone.

### The worst finding was in our own tooling

`tools/ready_check.py` wrote a 4 KB zero-filled **`.ffpfsc` straight into `/mnt/ext1/homebrew`** —
a ShadowMount **watch folder**. ShadowMount mounts whatever appears there the instant it appears,
and a 4 KB file named like a compressed PFS container is exactly the partial-mount crasher this
project documented long ago. It ran on **every** non-`--offline` invocation, by default, unprompted.

It is now behind `--test-delete`, and when it does run it writes `<name>.ffpfsc.part` and renames
only once the bytes are down — the same `.part` convention every other writer here already uses, for
exactly this reason.

### Two gates in that tool passed when they had found nothing

- *"all three carry the same version"* — if all three regexes missed, every value was `"?"`, and
  three unknowns are equal, so it went **green**.
- *"both sides are the same version"* — with neither server answering, `None == None` was **green**.

A readiness tool that reports success on absence is worse than no tool.

### PS4 games on extended storage were invisible

The detector rewritten in 3.43 scans **one** root, `/user/app`. Every other place in this project
that asks the same question scans four, because a PS4 game installed to extended storage lives under
`/mnt/ext1/user/app`. With Installation Location now set to the M.2, **every game installed from here
on would have read as not-installed** — and `/api/install` refuses an update whose base is missing,
so their patches would have been rejected with `base_not_installed`.

It scans all four now, and reports which drive each game is actually on instead of asserting
Internal SSD for every one.

### A failed read was being written to disk as a fact

In the same scan, *"I could not read this folder"* and *"this folder is empty"* were the same answer.
That is not cosmetic: a title dropped there falls out of `console_apps()` → `installed_titles()` →
`/api/installed` → `prune_local_installed()`, which **rewrites `installed.json` without it**. Nothing
ever puts it back. A failed read now raises, and the existing handler returns the previous cache.

### One unlucky TCP connect could abandon a healthy install

`install_spawn()`'s verdict poll allows three consecutive transport failures before declaring the
console gone — but `up()` caches a negative for 8 seconds and the loop sleeps 2. So a single failed
`connect_ex` was re-served from cache twice and spent all three strikes in about **four seconds**, on
an install running perfectly. The message it produced is the one that invites a mid-install ELF
reload — which is precisely how the duplicate-install crash happened. A strike now requires a forced
fresh probe; the offline-speed caching is otherwise untouched.

### The error table exists in three files and only one was updated

`install_error_text()` lives in `companion/server.py`, `server.c` and `installer_probe.c`, and each
one's own comment says *"change one, change all three"*. 3.44 changed one. So an out-of-space failure
told the user *"the package may be incomplete, so download it again"* — on an 85 GB file, the single
action that cannot help — everywhere except the console's own copy. All three agree again.

### Four UI defects

- **The patch-only branch never repaired the button's class.** `installItem()` writes `"btn done"` on
  success, dropping `primary`; the main branch repairs it, this one did not — so a patch-only title
  opened after any successful install showed a finished-looking green button.
- **The busy label was inverted**: Install said *"Queued…"*, ＋ Queue said *"Adding…"*. And every
  restore wrote the literal `"Install"` back onto whichever button had been borrowed — relabelling
  the ＋ Queue button, and a PS5 backup's *"Mount backup"* footer, on any cancel or refusal. The
  resting label is captured and restored now.
- **`/api/engine/state` does not exist on the console**, which answers unknown `/api/` GETs with a
  bare `{}`. `{}` is truthy and `e.ok` is `undefined`, not `false`, so the guard passed and the
  Settings panel painted *engine: no*, *shop: not answering*, every LED dark — on a console where the
  engine was working. That is the panel someone reads immediately before deciding to reload the ELF
  mid-install.
- **A title can be both a PKG and a backup.** `isBackupTitle()` answers about the *title*, which is
  right for the MOUNT badge and for Move; the install footer needs to know about the *file*, because
  the server routes on the file's extension. New `installsAsBackup()` for the four decision points
  that make a user-facing promise. (Zero titles differ in the current library — this guards the case
  rather than fixing a visible symptom.)

### Also

- A failed `gen_web_bundle.py` only **warned**, and the build then linked the previous
  `web_bundle.h` — shipping a console UI older than `web/index.html` with nothing to say so. It
  aborts now, and `ready_check --offline` asserts `bundle == disk` as the backstop. That assertion
  caught this release's own stale bundle during testing, exactly as intended.
- The space preflight no longer applies to **add-ons**: a patch is small, the console already
  committed the space for its game, and there is no force button for `no_space` in the UI.
- `check_web.py` now rejects **calls to functions defined nowhere** and the two-argument
  `classList.toggle`. Proven by re-injecting both bugs: caught at their exact lines, build stopped.
  This is the gate that would have caught `T()` instead of `t()`.
- `renderQueue()` had a live two-argument `classList.toggle("busy", …)` — pre-existing, and the file's
  own comment 600 lines below warns against it.

### Deliberately not done

`sceAppInstUtilCancelInstall` is not coming back in any form. Calling it with an inferred signature
took the console down, and "a spawned process contains the crash" was wrong reasoning: it acts on
shared ShellCore state. A stalled download is named plainly and the user is pointed at the PS5's own
Downloads list, which clears it safely. Describing a manual step is not a defeat.

---

## [3.45.0] — 2026-08-26 · "Stop guessing where the console installs" `[VERIFIED]`

### The space check blocked a working path — reverted to something that cannot

3.44.0 refused an install when the target drive looked too small, and worked out the target by
looking at where the console **last** put a package. That inference goes stale the moment the setting
changes — which is precisely the user's normal workflow: change **Installation Location** on the PS5
by hand, then install. They had just switched to Extended Storage (380 GB free) and the app still
insisted there was no room, because its guess still said internal.

**Where a PKG lands is the console's decision, and the app has no business predicting it.** The check
no longer tries. It answers only the question that needs no inference — *is there anywhere on this
console it could possibly go?* — and stays silent otherwise:

- fits on **any** connected drive → goes through, the console decides where, as it always did
- fits on **none** of them → refused, naming the largest free space there is
- console will not answer → says nothing at all

A preflight that guesses is worse than no preflight: it blocks work that would have succeeded.

**Verified:** RDR2 (85.3 GB) with internal at 21.2 GB and ext1 at 380.3 GB now queues normally,
where 3.44.0 refused it.

### The Queue button never came back

`installItem()` reports its result by writing onto the button it was handed — `"＋ In queue"`,
`className = "btn done"` — and the drawer's footer buttons are reused by every game. Only the
primary button's *text* was ever reset, so after a single queue press the Queue button read
"＋ In queue", styled as finished, **for every game opened afterwards**: two buttons that both looked
like Install and no way left to queue anything. `installAll` had the matching half — its text was
restored but its `className` was not, so it silently lost `primary` and stayed grey.

Both are now restored to their resting state every time the drawer opens.

### The footer says Install and Add to queue

"Install base" / "Reinstall base" / "Install latest update" are all just **Install** now. Which file
that means is on the panel above and spelled out in the button's tooltip; the button says what
pressing it does. A PS5 backup keeps **Mount backup** / **Re-send backup** — it is mounted, not
installed, and that distinction earns its words.

**Verified in the browser against the shipped exe**: three consecutive drawer opens, each deliberately
dirtied in between the way a real press does, every one coming back to `Install` + `＋ Queue` with
both visible — and a PS5 backup still reading `Re-send backup`.

### Note

`check_web.py` parses the UI script but cannot catch an undefined reference — a first cut of this
change called a translation helper that does not exist (`T()` instead of `t()`), which parsed fine
and threw at runtime on every drawer open. It was caught by driving the real page in a browser.
Syntax passing is not the same as the page working.

---

## [3.44.0] — 2026-08-26 · "Out of room, said plainly" `[VERIFIED]`

### "Some games install, others fail" was the console running out of space

Red Dead Redemption 2 (85.29 GB) failed with `0x80B21104`, and the app said:

> The console's installer refused it — the package may be incomplete, so download it again

The package was not incomplete. `/api/verify` reads *"complete (pfs image fits + tail high-entropy),
confident"*. What was actually true at that moment:

    internal  free  20.1 GB   total 673.9 GB      <- where PKG installs were landing
    ext1      free 380.3 GB
    usb0      free 204.9 GB

and `bgft.db` has **no row at all** for `CUSA03041` — it was refused before the transfer began,
which is what "not enough room to start" looks like. The 17 games in the same queue that worked were
the ones that fit; ~446 GB had just gone onto internal in one sitting (Mafia III 46 GB, Dragon Age
44 GB, Black Ops III 43.6 GB, all `status 1036`). That is exactly *"some work, some don't"* — and it
had nothing to do with which games "always worked before".

**It refuses before starting now, with the numbers:**

> Not enough room on the PS5. Red Dead Redemption 2 needs 79.43 GB and Internal SSD has only
> 18.68 GB free. Extended Storage has 354.22 GB free — change Installation Location on the PS5
> (Settings › Storage) to install there.

The hard part was knowing *which* drive a PKG lands on: Sony's installer takes that from the
console's own Installation Location setting and no API reports it — and guessing a registry key is
a rule this project already has for good reason. It does not need the setting. It reads the
**outcome**: the drive the console last actually put a package on. That is evidence, and it is right
for the only case that matters — the drive that is filling up is the drive in use. If the console
will not say how much room it has, the check says nothing and stays out of the way; `force` still
overrides.

`0x80B21104` is also named now instead of falling into the generic `0x80B2xxxx` bucket, whose
sentence sent the user off to re-download an 85 GB file — the one action guaranteed not to help.

### Queue buttons had to be pressed twice

`renderQueue()` did `body.innerHTML=""` and rebuilt every row from scratch, **every 1.2 seconds**
while anything was installing. A press is not instantaneous — there is a gap between the button
going down and the click being delivered — and any repaint inside that gap replaces the element the
press started on, so the click lands on nothing. With eighteen rows in the dock that is a lot of
chances to miss, and a controller press is slower than a mouse click, so it missed more often.

Rows are now **built once and updated in place**: only the status line, the bar and the retry
button's visibility change, and the button elements survive from one repaint to the next.

**Verified in the browser**: the same button object survives 5 consecutive renders (it did not
before), and a cancel click landed and removed its row *while renders were firing every 120 ms* —
the exact condition that used to swallow presses. It is also far less work for the PS5's browser: an
idle queue now costs nothing per tick instead of a full rebuild.

---

## [3.43.0] — 2026-08-26 · "Artwork is not a game" `[VERIFIED]`

### Fifty-three PS4 games the console did not have were shown as installed

After a **reset database** on the PS5, the console reported 2 PS4 games installed. The app reported
55. Everything downstream of that was wrong, and it was all one bad signal:

    "PS4 games the PS5 app.db drops are still really installed:
     they keep /user/appmeta/<CUSA>/icon0.png"

That was verified in July and was true then. But it is not a test of *installation* — it is a test of
whether the console has ever heard of the title. **Artwork outlives the game.** Measured here:
`/user/appmeta` holds 55 CUSA folders, and ARK (not installed) has an 8.2 MB `icon0.png`
indistinguishable from Riptide's (installed).

The real test is the game's own payload, which a reset *does* remove:

| | folders | with a non-empty `app.pkg` |
|---|---|---|
| `/user/appmeta` | 55 | — (artwork only) |
| `/user/app` | 31 | **2** — `CUSA02365` (107 MB), `CUSA58072` (738 MB) |

Two of thirty-one, matching the console's own count exactly. The other 29 `/user/app` folders are
**empty shells** left by the reset — so the folder is not enough either; the payload is. Detection now
requires `/user/app/<CUSA>/app.pkg` with bytes in it, and returns that size, which is the only real
figure anyone has for these titles (app.db has no row for them).

**One signal, four symptoms** — all fixed by correcting it:

- games shown as installed that were not
- **no size** on those rows, because there was nothing real to measure
- **Install offering the UPDATE instead of the base game** — a title believed installed has only
  updates left to apply (`stateOf()` returns `"upd"`)
- **progress stuck at 0%** — an update whose base is not there cannot make progress

Verified after the fix: 2 CUSA installed, matching the console. ARK, Bloodborne, Cuphead and Tony
Hawk now read *not installed* with real sizes (15.7 / 31.4 / 3.6 / 19.9 GB) and Install picks the
base. A real install of Cuphead ran `6% → 12% → … → 88% → 100%` with live byte counts.

### The storage tiles claimed 3352 GB on a 2 TB drive

    before:  Internal SSD      used 1.3 GB     free -  total -   3 games
             Extended Storage  used 3352 GB    free -  total -  52 games

Three faults in `build_storage()`:

1. **It bucketed by app.db's location code**, which has two values — 0 "Internal" and 2 "Extended" —
   so every external drive collapsed into one tile. This console has two: ext1 holds 33 backups,
   usb0 holds 19. Their sizes were added together. The tile was never a drive; it was
   "everything not internal".
2. **`free` and `total` were always `null`**, so a tile could only show a used figure with nothing to
   judge it against — and nothing to contradict it when it went wrong. The console has had the real
   numbers all along: `/api/devices` runs `statvfs` per drive.
3. **A backup's drive is where its container is**, which app.db does not know. `backup_path` does.

    after:   Internal SSD      used    1.3 GB   free  582.8 GB   total  673.9 GB    3 games
             Extended Storage  used 1557.2 GB   free   71.2 GB   total 2000.4 GB   33 games
             USB0              used 1794.7 GB   free  204.9 GB   total 2000.4 GB   19 games

USB0 now reads 1794.7 used + 204.9 free = 2000 GB, which is a 2 TB drive.

---

## [3.42.0] — 2026-08-26 · "The app stops pretending a PC is your console" `[VERIFIED]`

### It adopted a peer PC as the PS5 — and wrote it to config

Found with the console switched off: `config.json`'s `ps5_ip` changed itself from `10.0.0.99` to
`10.0.0.72` — the address of *Casita*, another PC on the network running PKG MUTANT SHOP — and the
change was **saved to disk**, so it would have outlived the console coming back. The app would have
kept reporting the console offline while quietly talking to a PC.

`discover_ps5()` accepted any host with `8710`, `8084` or `2121` open. Every PC running the companion
has 8710 open and answers the same API, so a peer and a console are indistinguishable at the port
level. `main()` then took `found[0]` — lowest address wins, which is arbitrary — and persisted it.

**An open port proves a host is running something, not what it is.** Discovery now asks the host
itself: our on-console ELF reports `on_console: true` / `server: "on-console"` and the companion does
not. A host that identifies as a PC is never a candidate, whatever ports it has open. If it will not
answer at all, it must have **8084** (Payload Manager) open — a PS5-only fingerprint — otherwise it
is not considered, which stops a slow-to-answer peer from passing as a busy console. A confirmed
console always outranks an unconfirmed one, and **only a confirmed console is written to disk**: a
guess is good enough to try for one run and must not outlive it.

Verified with the console off: config stays `10.0.0.99`, health reports `ps5_ip=10.0.0.99,
connected=false`, and nothing is adopted.

### With the console off, the app was unusable

Every call waited out a full HTTP timeout against a machine that was not there. The UI polls health
every 6 seconds, so the whole app sat grey — exactly when someone would open it to find out why.

| | before | after |
|---|---|---|
| `/api/health` | **10.4 s** | **0.00 s** |
| `/api/library` | 20.0 s | 0.00 s |
| `/api/devices` | 8.0 s | 0.00 s |
| `/api/move/status` | 10.0 s | 0.00 s |

A TCP connect settles "is it there" in milliseconds; an HTTP request waits out its whole timeout.
`Ps5Bridge.up()` probes the shop port with a 0.6 s budget and caches the verdict briefly — 4 s for a
yes, 8 s for a no, so a console that is reloaded reappears almost at once. Every console-bound call
asks first: the proxies (`_shop`), the file API, the health probes, and `helper_status()`.

Deliberately one-way: unknown is treated as "try it", so the gate can only ever skip a call that was
going to fail. No install, delete or transfer behaviour changes.

### `tools/ready_check.py`

One command that answers "is everything actually working?" — `--offline` for what can be checked with
no PS5, otherwise it waits for the console and checks the lot. Every check names the bug it guards
against: versions matching across all three artifacts, the embedded web bundle matching
`web/index.html`, that **only `isBackupTitle()` reads `lane==="mount"`**, both servers serving delete
and move, the queue verbs, the delete boundary, the auto-clean state, and a real transfer to a chosen
drive. It never installs a package and never deletes one of your games.

Written after re-establishing the same facts by hand five times in one session.

### Note on the repo's own config

`companion/config.json` was pointing at `10.0.0.72`. It is a development file — the shipped exe reads
the `config.json` beside it, which had the correct address throughout — but it is what made the
discovery bug visible, and it is now correct.

---

## [3.41.0] — 2026-08-26 · "Pick the drive again — and only the ones that are plugged in" `[VERIFIED]`

### PS5 backups can be sent to any drive again

The destination selector had gone dead for every PS5 game: whatever you chose, it read *"Installs
to: Extended Storage"* and could not be changed.

The gate was one line — `var isMount = g.lane === "mount";` — and `lane` is not a reliable way to
ask whether a title is a backup. A title the console reports as installed is rebuilt with `lane`
hardcoded to `"installed"` (`companion/server.py:257`), which throws away the fact that it is a
ShadowMount backup. **Zero of the 112 titles in the live library carry `lane:"mount"`**, on either
server — so the test was false for all 52 backups, they fell into the *"a reinstall must land where
the game already is"* branch, and the select was disabled.

`isBackupTitle()` replaces it and reads the signals that actually survive — `source:"backup"`,
`backup_path`, a container `format`, or `base[0].kind` — any one of which is enough. Replayed over
the live library from both servers: **52 backups matched, 0 PS4 packages misidentified, 0 missed.**

Nothing about the routing needed fixing. `drive → mount_dest_for_drive() → dest → fetch_to() →
/api/engine/fetch` was intact the whole time; only the control was missing. **Verified on hardware:**
Evergate (1.98 GB) sent to **usb0** — not the ext1 default — landed in `/mnt/usb0/homebrew`, mounted,
playable, 100 s. A second 107 MB fetch to usb0 confirmed the lane independently.

PS4 and PS5 **packages** are unchanged: Sony's installer takes its destination from the console's own
Installation Location setting, so those still read "Installs to: console default".

### Only drives that are actually plugged in

The picker offered eleven destinations — internal, ext0, ext1 and usb0–usb7 — on a console that has
three. The console has always known better: `/api/devices` `stat()`s each path and compares `st_dev`
against `/mnt`'s own, so an empty placeholder folder is never mistaken for a mounted stick, then
requires `statvfs` to return a real capacity. That `detected` flag was simply ignored.

Now: **3 offered, 8 hidden.** Each shows its real free space, a drive too small for the game says
*"not enough room"* rather than failing on the console minutes later, and the default is the drive
the container is really on — read from `backup_path`, not from `installed_drive`, which reports where
app.db has the title registered and disagrees with where the file sits (app.db says 52 titles are on
"Extended Storage"; the containers are 33 on ext1 and 19 on usb0).

If the console cannot be reached, every destination is listed again rather than none, and the tooltip
says why.

### An installed backup could not be moved at all

"Move to another drive" needs `movable` **and** a path. The removable-stick scan sent both; the
app.db row — how *every* already-installed backup is reported — sent `backup_path` and neither of the
others. So the one control that puts a game on a different drive was hidden for exactly the games
that have a container to move, and with no PC on the network an installed backup had no route to
another drive at all: no Install (its `base` is empty on the console) and no Move.

Both servers now send `movable`, `local_path` and `runs_in_place` for a title with a container.
All 52 backups carry them.

This also fixed a message that would have been wrong: the new *"you already have a copy on ext1"*
warning points at "Move to another drive", which was not in the menu. Making the control exist beats
rewording the sentence.

### Settings: one column, and the tab bar crosses the panel

The tabs made two columns redundant, and the layout fought them: `.setbody` was a **grid** and the
tab strip was one of its **grid items**, so the bar could only ever be as wide as the first column
with pane content sitting beside it rather than under it. `display:block` fixes both halves — the
strip becomes full width, the panes stack beneath it. A negative margin cancels the body's own 20 px
padding so the strip's rule runs the whole width of the panel, and the sheet narrows from 1040 px to
880 px, since 1040 was the width of two columns.

### Smaller things

- **Choosing a drive on a console-served page with no PC now says so.** The console's own
  `/api/install` never reads a destination and has no mount lane, so offering the choice there was
  offering one that could not be honoured. It points at "Move to another drive", which *is*
  drive-aware on the console and works with no PC at all.
- One predicate replaced five ad-hoc backup tests scattered through the UI (the badge, the install
  button label, the delete menu item, the delete itself, clean reinstall), which had drifted apart —
  the delete already used a broader test than the picker did, which is why deleting worked while
  choosing a drive did not.
- **The version stamper's warning became a stamp** in 3.40.0; this release is the first where the
  ELF and the companion carried the same number automatically.

---

## [3.40.0] — 2026-08-26 · "Delete works, and the controls only a PC can do say so" `[VERIFIED]`

### Deleting a PS5 game finally removes it

Deleting a backup from the PS5 home screen was never enough: the container stayed in the drive's
homebrew folder, ShadowMount saw it on the next scan, and the title came back. The only real fix was
FTP-ing in by hand. The Actions menu had carried a **Delete from PS5** item for months that answered
*"on-console delete isn't enabled yet"* and stopped there.

It removes the container now, on the console, and **nothing on the PC is touched** — the app sends a
title id, never a path, and the console does the lookup in its own scan. A PS4 package is left alone
on purpose: it removes itself completely from the dashboard, so the app says that instead of offering
a button that would do less.

**Verified on hardware:** a container at a drive root deleted (2 048 B, path echoed back); a nested
folder dump deleted whole (3 levels, 24 576 B of contents), with the unrelated file beside it
untouched and the watch folder's other 34 entries intact.

### Four bugs in that delete, all mine, all found before the user hit them

- **The "refuse while a game is running" guard never fired — in either state.** `running_game()`
  returns `0` on success; written as a bare truth test the `&&` short-circuited exactly when a game
  *was* running. Every other call site in the file gets this right. Pulling a container out from
  under a mounted, running game corrupts a save and hangs the shell.
- **A folder-shaped backup could be found but never deleted.** The removal was `unlink()` with an
  `rmdir()` fallback, and `rmdir` only takes an *empty* directory — so it failed with `ENOTEMPTY` and
  blamed the drive. Folder dumps get a bounded recursive delete now: homebrew roots only, re-checked
  per subdirectory, `lstat` so a symlink is removed rather than followed, depth-limited.
- **The delete was unreachable from the console-served page** — the no-PC case the feature exists
  for. `POST /api/game/delete` was on the companion only, and the console answers unknown `/api/`
  paths with `200 {}`, so the button would have done nothing at all. The console serves it now by
  forwarding to the one implementation rather than growing a second.
- **Drive roots were folded into the homebrew list.** ShadowMount scans a drive's root as well as its
  homebrew folder, so a container left at `/mnt/usb0/` has to be reachable — but a drive root is not
  a folder that exists to hold games. This console's `/mnt/usb0` also holds *PS5 Xplorer*,
  *InternetBrowser*, `shadowmountplus.elf` and Sony's own `PS5` folder. The roots are two tiers now:
  homebrew folders take containers *and* folder dumps; drive roots take containers only, regular
  files only, never a directory whatever is inside it.

### Controls only a PC can carry out no longer pretend otherwise

Rescan, Open folder and Create folders all act on the games folder **on the PC**. With the PS5
serving the page the console answered every one `{"ok":false,"error":"not supported on console"}` and
not one caller looked:

| Control | What it said | What had happened |
|---|---|---|
| Rescan | "Library rescanned — **undefined** titles" | nothing |
| Rescan (toolbar) | "Library: rescanned" | nothing |
| Open folder | a failure, drawn in the **success** colour | nothing |
| Verify | "Inconclusive — **undefined**" | nothing |

They are disabled together now, with a tooltip saying which machine can do it, and they re-enable by
themselves the moment a PC answers — `resolveApi()` already clears the flag, so no reload is needed.
Every one of those handlers reads `ok:false` properly now too, because a control can be enabled and
*still* get an error back.

### Move to another drive was dead on the PC, and offered drives that were not there

`/api/move` and `/api/move/status` existed only on the console, so with the PC serving the page the
move posted to a route the companion had never had. Both are forwarded now.

The picker was worse than dead: `PS5_DEVICES` is a hardcoded list of eleven ids with no free space
and no check that any are plugged in, so it offered **USB1–USB7 on a console that has none of them**,
each showing an undefined size — and the space check `dst.free && dst.free < g.size` passed silently
because `undefined` is falsy, so the move started and failed on the console instead of being refused
here. The companion asks the console now (real `statvfs` figures and a `detected` flag) and falls
back to the static list only when there is no console to ask. Measured: three real drives, not eleven.

### The queue's ✕ and Retry did nothing on the console

The console implemented `/cancel` but not `/dismiss` or `/retry`, and both callers ignore the
error — so the row sat there and the arrow did nothing. That is the *"it doesn't even let me remove
them from the queue"* complaint, reproduced exactly whenever no PC is running. Both verbs land on the
console's single install slot now: dismiss clears a slot that is not running, retry re-runs the
package the slot still remembers — which is the point of retry when there is no PC to re-send from.

### The last of the etaHEN / Elf Arsenal residue

The install lane, client and config went in 3.36–3.38. What survived was the **reporting**:
`/api/health` still announced `dpi_online` / `dpi_reachable` / `dpi_state` / `dpi_port:12800`, and the
console recomputed them **on every poll with a live TCP probe of :12800** — a port nothing of ours has
bound since 3.36.0. Every health request, several times a minute, paid for a connect to a port that
exists only if the user is running someone else's daemon. All four fields are gone, and the probe
with them.

Also gone: the header's *" · connected (DPI off)"* branch; a **Send to PS5** tooltip still selling
*"the Debug Package Installer (no USB, no DPI)"*; `_host_kind` / `_host_kind_at`, cached state for
"which foreign daemon owns :12800", assigned once and never read; and the comment block above
`DEFAULT_CONFIG["dpi"]` still documenting `mode`, `host` and `auto_reload` as though they could be
set, three lines above the note saying they were removed.

### Smaller things

- **Seven tooltips the PS5 browser never showed.** `title=` renders on hover; the console is driven
  with a controller. All seven moved to the project's own `data-tip`, which draws on focus too — the
  settings button had no accessible name at all beyond that invisible `title`.
- **`capabilities.uninstall` was dead** — hardcoded `false` by the companion, never sent by the
  console, so the clean-reinstall branch guarded by it had never run once. Clean reinstall splits by
  what the title *is* now: a backup is deleted and re-sent in one step; a package keeps the dashboard
  instruction, because that genuinely does remove it completely.
- **The version stamper warned instead of stamping.** It printed *"companion is 3.40.0 but the ELF is
  3.39.0"* and carried on — and a warning in the middle of a long build scrolls past. It stamps
  `server.c` now. Today that mismatch meant the only way to tell which build was on the console was
  the compile timestamp in `/api/health`.
- The console's "why is the percentage unknown" comment still blamed etaHEN for doing the fetching.
  The real reason is that our installer runs as a separate spawned process — the separation that
  makes installs work at all — and it reports a verdict rather than progress.

---

## [3.33.0] — 2026-08-25 · "The PS5 lane finally runs on our own engine" `[VERIFIED]`

### The queue trapped two jobs, and it was my guard that did it

The duplicate-submission guard added hours earlier asked **"did we send this URL in the last 30
minutes?"** That is not the dangerous condition. The dangerous condition is *the console is already
fetching or installing this content*, which is what actually preceded the crash.

The difference showed up the moment the user tried anything real: they deleted two games, reinstalled
four minutes later, and both were refused with *"may still be installing it"* — false; the installs
had finished — and told to *use Force*, which the UI has no button for. Two jobs held, unstartable.

It asks the console now. `bgft.db` says whether a title has a live job (status `1000`/`1009`),
`install_job_row()` already reads it, and `Queue._run` already fetches that row one line earlier for
another purpose — so the correct check costs nothing and cannot produce that false positive. What
remains in `install_spawn` is a 120-second debounce that clears on the verdict (~2 s later), so it
only ever catches a genuine double-click.

**Verified:** both games queued and installed with no force — 3.7 s and 7.3 s, `bgft 1036`, five
files each, and the bgft `title` column reading their real names.

### Reinstall is now offered instead of refused

A title that genuinely is installed used to be a dead end: a toast, a greyed button, and no way to
say *"yes, put it back anyway"* without editing config. `force` had always been plumbed from
`installItem()` through `POST /api/install`; nothing in the UI ever passed it.

> **Riptide GP2 is already installed on PS5.**
> Reinstall it anyway? The console overwrites the existing copy. Your saves are not touched.

Confirmed → force → reinstalled in 8.5 s. The server-side message dropped *"delete it there first
if you want to replace it"*, which stopped being true the moment the prompt existed, and now names
the game rather than its title id — a question someone can answer.

### X on a queue row now removes it

The backend cancel worked the whole time; the row simply **stayed**, reading "Canceled". The only
control that removes rows is a small icon in the dock header that nobody finds. New
`POST /api/queue/<id>/dismiss`: a finished row goes immediately, a live one is stopped first and
then goes. `dismiss()` refuses while a task is still running, because a task that vanishes from
under its own worker is how a queue starts reporting states for jobs nobody can see.

---

## The PS5 backup lane, reconnected to our engine

A `.ffpfsc` backup is never installed — ShadowMount mounts it. All we have to do is put the
container in the chosen drive's homebrew folder. That worked, but the **PC pushed** the file over
the file API: built for the Elf Arsenal era, never reconnected to our own downloader.

**`GET /api/engine/fetch?url=&dest=&name=&size=`** — download-only, on the console:

- our own `http_download()` — resumable, Range-aware, the same code that moves PS4 packages;
- writes `<name>.part` and renames only once the last byte lands, because ShadowMount mounts
  whatever appears in that folder and a half-written container mounts as a broken game;
- **preserves the filename exactly.** `/api/engine/install-url` sanitises non-alphanumerics into
  underscores, which would turn `[PS5] PPSA27616 - Bendy and the Ink Machine.ffpfsc` into something
  ShadowMount would not recognise. That is why this is a sibling endpoint and not a flag.

### Delegation was the wrong shape once the console can pull

A backup on another PC used to be handled by asking **that** PC to run its own mount lane and
mirroring its progress — one hop instead of two. Sound reasoning, real cost: the other machine has
to run our code, report progress, and stay up for the whole transfer, and the result is only as good
as whatever build it happens to have.

With the console pulling, the same single hop happens and the other PC only has to **serve the
file** — a static HTTP read it was already doing. Peer backups are queued as `mount` tasks carrying
both routes; delegation survives as the fallback for a console whose ELF predates the endpoint, and
the choice is made when the task *runs*, not when it is queued.

**Verified end to end, both titles previously deleted, pulled from a different PC (Casita):**

| | Bendy and the Ink Machine | Evergate |
|---|---|---|
| size | 1 346 961 408 | 1 983 840 256 |
| pulled in | ~13 s (≈100 MB/s) | ~19 s |
| mounted + confirmed | **31.8 s total** | **50.0 s total** |
| filename on disk | exact, spaces and brackets intact | exact |
| `.part` left behind | none | none |

```
fetch: requested  http://10.0.0.72:8710/library/%5BPS5%5D%20PPSA27616%20-%20Bendy…ffpfsc -> /mnt/ext1/homebrew
fetch: delivered  /mnt/ext1/homebrew/[PS5] PPSA27616 - Bendy and the Ink Machine.ffpfsc
```

Nothing in this lane touches etaHEN, Elf Arsenal, DPI or `:12800`.

### "Internal" is a 31 GB system partition

Read off the console: `internal` resolves to `/data/homebrew`, which held **zero** backups — the
real ones are on `ext1` (32) and `usb0` (18). Internal has **31 GB free of 673 GB**, and PS5 backups
in this library run to 125 GB. Sending one there would fill the system partition, and finding out
after an hour of transfer is not an answer.

`/api/engine/fetch` now checks `statvfs` against the declared size before accepting, with 256 MB of
headroom:

```
{"ok":false,"error":"Not enough room on that drive - BigGame.ffpfsc needs 512000 MB
 and /data/homebrew has 29393 MB free. Pick another drive."}
```

It also write-tests the destination first, so an absent or read-only drive says so in a second.

---

## etaHEN is no longer shipped at all

We stopped *starting* it long ago, but the ELF still embedded 4.7 MB of it and wrote it to
`/data/pkg-mutant-shop/payloads/` **on every boot** — for a daemon we never run, that the user does
not want, and whose presence on disk was the only thing making an accidental launch possible.
Nothing in our lane read it: `pb_etahen` appeared only in the bundle table, and the companion's
`host_payload_path()` resolves through Payload Manager's own directory, in the legacy `v2` mode we
return early from anyway.

Removed from the bundle, and the app now sweeps what it already left:

```
boot: removed etaHEN.elf - no longer shipped
boot: removed PKG-MUTANT-SHOP.elf - no longer shipped
```

**The ELF is 37.3 MB, down from 42.1 MB.** Only `shadowmountplus.elf` and `pms-installer.elf`
remain in our payload folder.

### What `pms-installer.elf` is, since it appears in Payload Manager

It is our install engine — the separate process that exists because
`sceAppInstUtilInstallByPackage` returns `0x80B2116F` from an injected payload and `0` from a
spawned one. It runs for about two seconds per install and exits; Payload Manager **lists** it as an
available payload, which is why it looks active. Confirmed against the live process list: the only
thing of ours running is `payload.elf` (the shop). It sits in pldmgr's directory because
`/loadpayload` resolves by basename against that directory.

---

## [3.32.0] — 2026-08-25 · "The message that crashed the console"

### ✅ THE INDEPENDENCE TEST — PASSED on a console that never loaded etaHEN `[VERIFIED]`

Fresh Y2JB. The user's stack only: Payload Manager, nanoDNS, ftpsrv, kstuff-lite, ShadowMount.
**etaHEN, ps5debug NG and everything else were never started this boot.** Baseline before touching
anything: 12800 / 9090 / 9081 / 1337 / 9040 all closed, 8710 closed.

Deployed over FTP to `.part` + verified byte count + rename, loaded once via Payload Manager, and
**up in 1.6 seconds**:

```
version 3.32.0   built "Aug 25 2026 15:43:24"   engine pms-spawn
engine_ready true   dpi_online false   shadowmount true (10101)   ftp 2121
```

**Test A — console-direct spawn, no display name.** Riptide GP2, not installed, nothing forced:

```
rc 0x00000000   via spawned-process   pid 182   authid 0x4801000000000013
content_id EP0786-CUSA02365_00-RIPTIDEGP2PS4001
```

**Test B — the full PC lane, never verified until now.** Castle Crashers Remastered, not installed,
nothing forced. Queue → console → confirmed in **6.1 seconds**:

```
0.0s queued  1.2s submitting  3.6s transferring 53%  4.9s promoting 90%  6.1s playable
```

**The proof, from the console's own records:**

| | Riptide GP2 `CUSA02365` | Castle Crashers `CUSA14409` |
|---|---|---|
| bgft status | **1036** | **1036** |
| bytes | 107 806 720 / 107 806 720 | 227 540 992 / 227 540 992 |
| bgft `title` column | `PKG MUTANT SHOP` | **`Castle Crashers Remastered`** |
| files at `/mnt/ext1/user/app/<TID>/` | 5 | 5 |
| `/user/appmeta/<TID>` | 9 entries | 8 entries |
| `tbl_contentinfo` | registered, 108 527 616 | registered, 228 261 888 |

`app.db integrity_check = ok`. **All five third-party install ports still closed afterwards.**

Three of this release's fixes are visible working in the console's own log:

```
install: verdict {"ok":true,"rc":"0x00000000",...}          <- Test A
install: previous verdict was never collected - releasing the lane
install: requested (async) uri=...Castle-Crashers...        <- Test B started immediately
install: verdict {"ok":true,"rc":"0x00000000",...}
install: cleaned up - ready for the next one
```

The middle line is the busy-latch auto-release: Test A's verdict was never collected, and the lane
freed itself instead of blocking Test B for the full 600-second expiry. And `title` reading
**`Castle Crashers Remastered`** rather than `PKG MUTANT SHOP` is the name passthrough landing —
before it, every PC-driven install filed itself under the app's own name.

### The crash, closed out

`install.log` survived (append-only, `O_SYNC`) and was recovered over FTP before anything was
loaded. It corroborates the audit independently:

```
1787680030  install: verdict rc=0x00000000      <- first install COMPLETED
1787680368  ==== BOOT: PKG MUTANT SHOP 3.31.0 ==== <- the reload, on bad advice
1787680392  install: requested (async)          <- the duplicate, 24s later
1787680392  install: installer spawned rc=0
            <no verdict — the console died inside InstallByPackage>
```

And `bgft.db` names the victim: `CUSA02365 status 1022, 65536/107806720, title="PKG"` sitting on top
of a completed `1036`. So it was a re-install over a **fully registered** title — reached only
because the test passed `force: true`, overriding the already-installed guard this project had
deliberately built. Both halves are now defended: the guard is honoured, and a repeat of the same
URL inside 30 minutes is refused on the PC side, where a console reload cannot erase it.


A 7-lens audit (81 agents, every non-cosmetic finding attacked by an independent skeptic, 73
findings, 34 survived) went looking for what killed the console. It found the chain by reading
`pms.log`, and the first link was a sentence added earlier the same day.

### What actually happened

```
13:49        install-spawn accepted  ->  rc=0, content_id   (a LIVE BGFT download now exists)
13:50:25     the queue sends the same package
             the console answers  409 Conflict  "an install is already being handed to the console"
             the companion reports:  "PKG MUTANT SHOP on the PS5 did not answer -
                                      load it again from Payload Manager"
~13:51       the ELF is reloaded on that advice
             a fresh process starts with  static volatile int g_spawn_busy = 0
             >>> THE RELOAD DELETED THE REFUSAL <<<
~13:53       the same package is submitted again, on top of the still-running download
             installer spawned, no verdict, console gone - including pldmgr and elfldr,
             which are not ours
```

`_shop()` uses `urlopen`, which **raises `HTTPError` on 409**, and the caller caught it with a bare
`except Exception` that asserted a cause it had never checked. Before that message was "improved" it
printed `repr(e)` — `<HTTPError 409: 'Conflict'>` — and nobody would have reloaded anything.

**An error message that names a cause it has not verified is worse than a raw exception.**

### The four fixes that break that chain

- **`ShopHTTPError`** carries the status and the console's own sentence. A 409 now relays *"An
  install is already running on the console — let it finish"* and sets `do_not_reload: True`. The
  queue holds the job as `console_busy` instead of erroring.
- **A missing verdict is "state unknown", not "nothing was queued."** The 90-second timeout returned
  `queued: False`, which `Queue._run` treats as *provably safe to re-drive* — a gate written for a
  daemon that validates before queueing. Ours does not. It now returns `no_verdict: True` and the
  job is **held**. At 13:54:43, with the console already dead, the old code logged "retrying once".
- **The duplicate guard lives on the PC**, in `Bridge._accepted`, because the console's latch is
  cleared by any reload — which is precisely the failure mode. Overridable with `force`, which is
  now plumbed from `POST /api/install` all the way down.
- **The poll loop tells "slow" from "dead."** `except Exception: continue` treated the expected 404
  ("no verdict yet") and a dead console identically and logged neither, which is why there is a
  **124-second hole in the record with the fatal window inside it**. Three consecutive transport
  failures now stop the loop and say so.

### Ruled out by proof — do not re-investigate

- **The display-name passthrough.** `fixed[1300]` + `nm[300]` → `line[1750]`: worst case 1600 bytes,
  150 of margin, `snprintf` cannot truncate so the `write()` length is always real. Bounded again to
  239 bytes in the installer. The actual payload was `Riptide GP2` — 11 ASCII characters. My prime
  suspect was wrong.
- **The busy-latch auto-release race.** Real bug, but it needs `localinst_thread` running
  concurrently; the fatal attempt was companion-driven only.
- **The double `url_decode`.** `Riptide%20GP2` → `Riptide GP2` → `Riptide GP2`. A no-op here.

### Console hardening, defensive only — the call that installs is untouched

`spawn_installer_write()` streamed 117,952 bytes straight over the file Payload Manager is about to
**execute**, ignoring a failed `open()`, abandoning the file on a short write, with no `fsync`, no
size check, and a `void` return nobody could check. `/data` also holds the logs, the cheat library
and the web bundle, so ENOSPC there is not hypothetical — and a truncated ELF then goes to elfldr.

It now writes to `.part`, verifies the byte count, fsyncs, renames, returns `int`, and **both** lanes
refuse to spawn if it failed. The same 118 KB was also being written a **second time inline**, a
verbatim duplicate — four truncate-and-rewrite cycles of the live payload per request, the last two
immediately before execution. Deleted.

Two failure paths also left `g_spawn_busy` set, locking the queue out for 600 seconds;
`spawn_install_wait` had always cleared it and the async lane never did.

### Every build is now distinguishable

`SHOP_VERSION` is hand-edited, and on 2026-08-25 **two different ELFs both reported `3.31.0`** — so
nothing could say which binary was answering when the console died. `/api/health` now reports
`built` from `__DATE__ " " __TIME__`, which the compiler fills in whether or not anyone remembers to
bump the version.

### The exe had no UI gate at all

`check_web.py` and `stamp_version.py` ran only in the ELF build. Building just the exe could package
a UI whose script does not parse — which blanks the app on every device while the server keeps
answering 200 — or a stale `APP_VERSION`. It stayed safe only because the ELF happened to be built
first every time; ordering is not a safeguard. Both now run inside `PKG-MUTANT-SHOP.spec` with the
UI gate **fatal**, and `build_exe.cmd` builds from the spec instead of its own drifted flags.

### `MUTANT PKG ENGINE.md` was describing a lane we no longer have

It opens with *"If the engine is broken, work from this document"* and then hands every install to
etaHEN on `:12800`. §2.1 stated flatly: **"`:12800` — the install host — is started by us."**
Following it would put back the daemon this project spent two weeks removing.

Rewritten from source: a new **§0** (what the engine is today), §1, §2.1, §2.2, §2.3, §4, §12, §14.2,
plus §13.6 for the `-O2` landmine — and a header saying exactly which sections are current and which
are etaHEN-era history kept for diagnosis. Three of its claims were wrong in ways that mattered:

- §2.2 still called `0x80B2116F` unsolved, "12 hypotheses eliminated".
- §2.3 equated `dpi.mode="pms"` with `AppInstallPkg`, the call that "cost a console, twice".
  Verified from source: `install_full()` calls **only** `g_ai_installbypkg`; there is no
  `AppInstallPkg` fallback for a game anywhere, and `AppInstallPkg` has exactly two call sites —
  `tile_install()` and the diagnostics-gated `/api/engine/install-local`.
- §4 put ShadowMount on 9021 (it is 10101; 9021 is elfldr, always up).

The Settings engine picker is relabelled to match: `pms` is *"Ours - in-process (fails on base
games)"*, not the neutral phrasing it shipped with.

### New: `tools/verify_console.py`

Ten stages in an order that **separates the hypotheses** rather than just confirming things work —
install with no name, then a plain ASCII name, then a non-ASCII name, then the full PC-driven lane —
with a console health probe between every stage, so if it dies the stage number names the culprit.
`/api/open` is deliberately last. Stage 0 asserts 12800/9090/9081/1337/9040 are **closed** (the
independence proof); stage 9 pulls `bgft.db` through our own file API and checks the row says
`PKG MUTANT SHOP`. `--forensics` reads the console's install log and changes nothing.

---

## [3.31.0] — 2026-08-25 · "Every word, and a Settings page that describes the app we actually built"

### `-O2` BREAKS THE INSTALLER. Build it with `-g`. `[VERIFIED — both directions]`

The single most important line in this release. `pms-installer.elf` had always been built by hand;
building it this time with `-O2` instead of `-g` produced a binary that fails **every** install:

```
  -O2  sceAppInstUtilInstallByPackage -> 0x80B2116F, content_id empty
  -g   the identical source, same console, same URI, minutes apart -> 0x00000000, content_id set
```

Same process type (`via: spawned-process`), same `authid` (`0x4801000000000013`), same everything
the last two weeks of work established as load-bearing. Only the optimiser changed. The likely
mechanism is the 9984-byte `PlayGoInfo` the call takes by pointer: only a few of its fields are
written, and at `-O2` the compiler is free to treat the rest of that stack object as dead.

Two things now stop this happening again:

- **`ps5-app/onconsole/build-installer-wsl.sh`** exists, pins `-Wall -g`, and says why in a comment.
- **`build-wsl.sh` calls it first.** `payload_bundle.h` `.incbin`s the installer straight into the
  shop ELF, so building the shop without rebuilding the installer shipped a new shop carrying an
  old installer — silently. That is exactly how it drifted.

### Every companion-driven install said "Your game"

`/api/engine/install-spawn?uri=…&name=…` read `uri` and dropped `name`, so `installer-req.txt` had
no second line and the spawned installer fell back to its placeholder. The blocking lane (USB
installs, started on the console) passed the name through, which is why every test done from the
console itself looked right. Every install started from a PC — that is nearly all of them —
announced itself as *"Your game is installing"*.

### The busy latch could outlive the install it was guarding

Reproduced: an install finished, the installer wrote its verdict and exited, and the latch stayed
set because only `spawn-cleanup` clears it and the caller had gone away. The next install then
waited behind a flag protecting a process that no longer existed — measured at **122 seconds of an
apparently frozen queue row**, followed by *"PKG MUTANT SHOP on the PS5 did not answer"*, which was
untrue: the console had answered every poll.

- **The console releases it itself.** A verdict file on disk is proof the job is over, so
  `install-spawn` and `spawn-status` both treat "has result" as "not busy".
- **The companion waits 30 s, not 120 s**, then clears the latch and carries on, and says so in the
  log rather than inventing a cause.

### The queue reported the wrong reason for every refusal

```python
if not bridge.ping():          # "is a daemon listening on :12800"
    ... "The console stopped answering"
```

`ping()` is permanently false in our own lane — nothing binds 12800 any more — so **every** package
the console refused was reported as the console having vanished, sending the user off to reload a
payload that was fine. It asks `engine_available()` now when our engine is in play, and a refusal
gets a branch of its own that repeats what the console actually said.

---

## Every word the shop says

212 messages, read out of the source by **`tools/message_report.py`**, which also enforces the
house style with `--check`:

> line 1 is the event, naming its subject · line 2 is what it means or what to do · the words
> "PKG MUTANT SHOP" appear only when the shop itself is the subject, because the icon already says
> who is speaking · no raw error codes, JSON or file paths on a television · no bare-verb failures ·
> no reference to software we do not ship

`python tools/message_report.py --check` → **212 checked, 0 off style.**

### One error table, three binaries

`0x80B2116F` used to reach the television as `0x80B2116F`. A number is not an answer.
`install_error_text()` now exists in the shop ELF, the installer ELF and the companion — three
copies because they are three separate binaries, each annotated to say so — and decodes the five
families this project has actually observed on hardware, with fallbacks that keep an unseen code
producing a true, actionable sentence.

### A parsing bug that pasted a JSON document onto the TV

```c
const char *rcp = strstr(buf, "\"rc\":\"");
snprintf(reply, rsz, "the console refused this package (%s)", rcp ? rcp + 6 : "…");
```

`rcp + 6` points at the digits, but there is no terminator there — it is the middle of a JSON
document. So the "reason" shown on the television and in the queue was:

```
the console refused this package (0x80B2116F","init_rc":"0x00000000","via":"spawned-process",
"pid":447,"authid":"0x4801000000000013","content_id":"","uri":"http://10.0.0.76:8710/…)
```

### Duplicates removed

- `notify_branded()` / `notify_branded_f()` were byte-for-byte copies of `notify()` / `notifyf()`.
  Two names for one behaviour is how the cheat toasts drifted into four wordings; they are gone.
- **Four** copies of the cheat-result wording existed. There are two functions now —
  `cheat_result_toast()` and `patch_result_toast()` — and the patch route no longer prints its
  engineer-facing `detail` on a television, nor rounds a partial apply up to "applied".
- The cheat-intake toast was written out three times in three files. `notify_cheats_filed()`.

### `errText()` — one place in the UI

Thirty-odd toasts each did their own `((r&&r.error)||"?")`, so what you saw depended on which
button you pressed: *"Move failed: ?"*, *"Could not toggle: no_local_cheat_found"*, *"Could not
stop them: unknown"*. `errText()` prefers a sentence the server sent, falls back to a known
translation, and — for a code nobody has taught it yet — prettifies the identifier rather than
showing `?`. A new server-side code degrades to readable English on its own.

---

## Settings, rebuilt around the app we actually have

**The row this panel got most wrong** read `Install engine  10.0.0.99:12800` with an LED that meant
"is a third-party daemon listening". With our own engine running perfectly it showed a red light
next to a port nothing binds. It is replaced by what our lane actually needs, each with its own
light: **the shop answering on the console** and **Payload Manager answering on :8084** — plus a
sentence underneath that says what to do when one of them is missing, from the new
`GET /api/engine/state`.

**The port box was `dpi.port_v2`.** Typing in it changed nothing and saving it wrote a dead
setting. It is `console.shop_port` now — the port everything actually uses.

**Four settings existed only in `config.json`.** How many installs run at once, whether to check a
package before handing it over, whether to watch the games folder, and *which engine installs go
through* — all editable by hand next to the exe, which means nobody ever edited them. They are on
the page, and Save writes them. Changing one shows **"Not saved yet"** until you do.

**"Open on PS5" has never worked.** It opened a socket to `console.notify_port` (9099); nothing has
ever listened there. The console has `GET /api/open` now, which resolves
`sceSystemServiceLaunchWebBrowser` by name — **available on 12.70, returns 0, the browser opens** —
and always sends a notification as well, so the control does something visible either way.
`Bridge.notify()` had the same dead 9099 socket and has the same fix.

**The install log was unreachable.** The console has kept `/data/pkg-mutant-shop/install.log` since
3.29.0 and nothing in the app could read it. There is a button, and `apiText()` copes with the
console answering text and the companion answering JSON.

Also: `config_unreadable` is surfaced (the app silently ran on defaults and refused to save);
`paintServices()` replaces two service blocks that had already drifted apart; the PS5 itself joined
the "devices on this network" list; `state.online` means "reachable" instead of "is a daemon on
12800"; and the library path fallback printed **`C:Mutant Games`** — `\M` is not an escape in a
JavaScript string, so the backslash was silently dropped.

---

### Verified on hardware at 3.31.0

- ELF built, uploaded through our own file API (**42 MB in 2.5 s**) and reloaded via Payload
  Manager; console came back on 3.31.0 with `engine_ready: true`
- `GET /api/open` → `launched: true, rc: 0` — the PS5 browser opened
- `GET /api/engine/state`, `/api/engine/log`, `/api/engine/spawn-status` (now with `busy_for`,
  `stale`, `log_bytes`)
- `/api/cheats/library` returns real counts — the route-order collision with
  `path.startswith("/api/cheats/")` is fixed
- `/api/dpi/reload` clears spawn state instead of relaunching a third-party payload
- **a full spawned install: `rc=0x00000000`, `content_id=EP0786-CUSA02365_00-RIPTIDEGP2PS4001`**
- Settings panel checked in a live browser: every control bound, every LED honest with the console
  off, `C:\Mutant Games` correct, the config warning correctly hidden

### Not verified

The companion-driven end-to-end run after the latch and name fixes did not complete: the console
dropped off the network mid-install — **all** ports, including Payload Manager and elfldr, which
are not ours, and it stopped answering ICMP. That is the console powering down or resting, not our
payload. It needs re-running once the console is back.

---

## [3.30.0] — 2026-08-25 · "Messages that were confidently wrong" `[VERIFIED]`

A full inventory of every user-visible string in the app — **603 of them** across the television,
the interface, the button captions and the server's own replies — turned up several that were not
merely awkward but **stating things that were not true**. Reference published at
`research/message-reference.html`.

### "Nothing was written." — while patches were live in the game

`cheat_apply_mod` ends with `return failed ? -(100 + failed) : written;`. So `rc <= -100` means
*some entries were refused* — it does **not** mean nothing landed. A mod whose first two writes
succeeded and whose third was refused returns `-101`, and the toast said:

> *"X not applied — The game code does not match this cheat file. Nothing was written."*

while two patches were live in the running game. That is the worst shape of wrong: it tells someone
their game is untouched when it is half-patched. It now parses `written=` back out of the detail and
splits:

> *"X is only partly ON — 2 patches applied before the rest were refused. This cheat looks built for
> another version of the game. Turn it off to undo them."*

### "Move failed / God of War"

The second line of that toast printed the **game name** where a reason belongs — the shape of an
explanation with none in it. The actual cause ("the destination drive may be full") was written to
`g_move.error` and only ever reached `/api/move/status`. All three move failures now name the game,
give the cause, and say the original was not removed.

### A clean sweep that wasn't

*"All cheats turned off — 3 mods reverted"* counted cheats that were **already off** as reverted,
and computed a `failed` count it then never mentioned. Three undone and two refused still announced
success. Now three distinct outcomes, and failures are spoken.

### Two copies of the same messages, already drifting

The two cheat routes each had their own copy: one said *"The game code"*, the other *"The game's
code"*; one separated with `" - "`, the other with `" · "`. A fourth branch existed on one route and
not the other, so an identical failure was **spoken on one lane and silent on the other**. All of it
now goes through one `cheat_result_toast()`.

Also: `"%s could not be applied
%s"` piped an engineer-facing string (`cannot read <truncated
path>`, `mod 4 not found`) onto the television. The detail still reaches the API response and the
install log — it just no longer goes on screen.

### [audit 29] A twenty-second stopwatch, and a title list mistaken for proof

The in-process engine waited 20 × `sleep(1)` for a title to appear, then declared `JOB_DONE
"Installed via X"` or `JOB_ERROR "never appeared"`. Both halves were wrong: twenty seconds is not
long enough for a large title on a busy console, and **appearing in the title list is not proof the
game is there** — a metadata-only registration does exactly that, and that is the tile that fails
with "Cannot start the game". It now waits on evidence (up to 300 s), and requires the game's own
`app.pkg` on disk before saying installed. A title that registers with no data behind it gets its
own message telling the user *not to launch it*.

### [audit 05] Single-instance guard

Two companions on one port is not hypothetical — the loser of the bind race keeps every background
thread running (library rescan, console probes, `installed.json` writes), which is how one process
overwrites the other's record of what is installed.

The first attempt at this guard **was itself wrong**: it asked `/api/health` with a 2-second timeout,
so it timed out on exactly the slowness that makes the guard matter and would have started a second
copy anyway. A guard that fails open under the conditions it exists for is worse than none. It is a
TCP connect now, with the version lookup as decoration. Verified by launching a second copy: it
refused and logged
`[boot] PKG MUTANT SHOP v3.30.0 is ALREADY running on port 8710 - not starting a second copy.`

### Found by probing our own endpoints

- **The ⟳ reload control was resurrecting etaHEN.** `recover_dpi()` relaunches
  `host_payload_path()` — the etaHEN copy we ship ourselves — so pressing it after removing etaHEN
  silently brought it back. Caught because etaHEN reappeared with a fresh pid minutes after being
  killed, and the only thing in between was that probe. In our own lanes it now clears the install
  state and says there is nothing to reload: **0.05 s instead of 4 s**, and etaHEN stays gone.
- **`/api/health` took 3.3 s** — the most-polled endpoint in the app. The `engine_ready` check added
  in 3.28.1 put a console round trip on every call. Cached for 5 s: **3.3 s → 0.05 s**.
- **`/api/cheats/paths` still advertised `ftp_port: 2121`.** It now names our own
  `/api/fs/write` and only mentions FTP when one is actually running.

---

## [3.29.0] — 2026-08-25 · "The 30-second mystery toast, and every message says something true" `[VERIFIED]`

### The toast that appeared out of nowhere

`"PKG MUTANT SHOP is ready · Mounts on · Installs on · FTP on"` fired roughly **30 seconds** after
the app started — `AUTOSTART_DELAY_DEFAULT` — with no visible cause, long after the version toast
had already said we were up.

It was also, by then, **lying in the most confusing direction possible**. "Installs" meant *is a
third-party daemon listening on `:12800`*, so with etaHEN removed it announced **"Installs off"**
while installs were working perfectly through our own engine, and **"FTP off"** while file access
was fine through ours.

Both halves are fixed:

- It now speaks **only when it actually started something** (`started_any`). If everything was
  already running — the normal case — it says nothing at all, because the startup toast already did.
- When it does speak it reports what it changed, not a port belonging to software we do not use.

### One startup toast, and it tells you what to do

```
  before:  PKG MUTANT SHOP v3.28.2
  after:   PKG MUTANT SHOP v3.29.0 is ready
           Open it from Media, or 10.0.0.99:8710 in any browser
```

It fires the moment the server is listening — which is also the moment installs become possible,
because our engine is spawned per install and needs nothing running beforehand. "The shop is up" and
"you can install" are now the same event, so one toast covers both.

### Every toast carries our icon

The artwork form existed since the notification code was written, but only as an opt-in test
(`?icon=1`) that was never made the default. Measured on this console:
`sceKernelSendNotificationRequest` returns `0` **with and without** the icon, so there was no reason
left to send unbranded toasts. `notify()`, `notifyf()` and `notify_sync()` all brand now.

**It is the ICON, not the LOGO.** `web/assets/icon0.png` is the square 512×512 mark;
`web/assets/logo.png` is the wide wordmark and would be cropped to nothing in a square notification
slot. `notify_icon()` falls back to a plain toast when the file is not on disk yet, so a console
still extracting `web/` on first boot gets a plain notification rather than a broken one.

### Messages that had gone stale

- *"Installing X — Reading it from the drive, this takes a few minutes"* described the old lane. A
  USB install is a hand-off now: **"Handing X to the console — it installs from the drive itself"**.
- *"Network busy — could not start"* said nothing actionable. Now: **"Another copy may already be
  running — check Payload Manager"**, which is what it almost always is.
- *"Open the tile, or the browser at 127.0.0.1:8710"* → **"No need to load it again — open it from
  Media"**.

### The console keeps its own install log

The PC got `pms.log` in 3.25.3 and it paid for itself immediately. The console had nothing: with the
PC switched off, or when the spawned installer was involved, there was no record at all — only
whatever toast happened to be on screen for four seconds.

`/data/pkg-mutant-shop/install.log`, readable at **`GET /api/engine/log`**. Same rules as the power
watchdog's log and for the same reasons: append-only, `O_SYNC` so a crash or a power cut cannot
swallow the last lines, trimmed only at startup (never mid-install), bounded at 512 KB.

Live, two queued installs:

```
1787672997.847  install: requested (async)  uri=http://10.0.0.76:8710/library/Riptide-GP2-CUSA02365.pkg
1787672997.899  install: installer spawned  rc=0
1787672999.905  install: verdict  {"ok":true,"rc":"0x00000000","content_id":"EP0786-CUSA02365_00-…"}
1787672999.952  install: cleaned up - ready for the next one
1787673006.139  install: requested (async)  uri=…Castle-Crashers-Remastered-CUSA14409.pkg
1787673006.198  install: installer spawned  rc=0
1787673008.239  install: verdict  {"ok":true,"rc":"0x00000000","content_id":"UP2015-CUSA14409_00-…"}
1787673008.267  install: cleaned up - ready for the next one
```

Complete cycles, both lanes covered — the async endpoint the companion drives *and* the blocking
one the USB lane uses. The verdict is logged once per job rather than on every poll.

---

## [3.28.0] — 2026-08-25 · "A game installed with etaHEN entirely dead" `[VERIFIED]`

### The test

`etaHEN Critical ser` (pid 370) killed first — it restarts the Utility daemon, so that order
matters — then `etaHEN Utility Daem` (438). Every etaHEN port closed:

```
:12800 closed   :9090 closed   :1337 closed   :9081 closed   :9028 closed
:8084  OPEN (Payload Manager)  :8710 OPEN (ours)
```

Then Riptide GP2 installed through the app, 8.7 s, `submitting → promoting → "Ready to play"`:

```
bgft #237  status=1036  title='PKG MUTANT SHOP'  107806720/107806720  err=0x00000000
/mnt/ext1/user/app/CUSA02365/  app.pkg 107806720 + app.pbm + app.pbm.backup + app.json + app.xml
```

That `bgft.db` was read through **our own `/api/fs/read`**, because etaHEN's FTP was dead too.

### Why it works — Arsenal and etaHEN were only ever gateways

Neither provides fake-signed package support. **kstuff** does, and Payload Manager autoloads it
independently:

```
/data/pldmgr/autoload.txt:  !5000 / nanodns.elf / ftpsrv.elf / kstuff-lite_v1.10.elf / shadowmountplus.elf
```

etaHEN is not in that list. Three independent confirmations, from the **running 2.6B binary**, not
just the 2.5B source:

- **Byepervisor (etaHEN's own fake-sign machinery) never runs on 12.70.** At va `0x13c40`:
  `cmp DWORD PTR [rbp-0x14],0x3000000` / `jae` — it jumps clean over the Byepervisor call. This
  console reports `12700001`.
- **etaHEN does not even load kstuff here.** 2.6B skips its own copy when
  `sceKernelMprotect(buf,100,PROT_RWX)==0` — i.e. when kstuff is already live. The running
  processes are `kstuff.elf` (pids 95, 265), *not* `kstuff`, which is the name etaHEN's
  `elfldr_spawn` would give it.
- **`patchShellCore()` and `pause_resume_kstuff()` are no-ops on 12.70** — their firmware tables
  stop at 8.20 and 10.60. The console's own `/data/etaHEN/etaHEN.log` line 8 reads
  `Unsupported firmware`.

Corroborated by the timeline: etaHEN first reached this console on **2026-08-19**, but fake-signed
games installed here on **2026-08-03** through Elf Arsenal.

### What still pointed at etaHEN in OUR code, now gone

- **The console-local (USB) lane** required `port_open(12800)` and POSTed to whatever owned it. It
  now calls `spawn_install_wait()` — our engine — so a package on a USB stick installs with the PC
  off and no third-party daemon anywhere.
- **"Install ready" meant "something is listening on :12800".** `/api/health` now reports
  `engine: "pms-spawn"` and `engine_ready`, which is Payload Manager being reachable — the only
  thing our engine actually needs. `dpi_*` is kept so an older UI does not break.

### One condition NOT yet tested — stated plainly

etaHEN's **bootstrapper** makes boot-time changes that outlive its processes: `/system_ex` and
`/system` remounted read-write via nullfs `MNT_UPDATE`, `/update/PS5UPDATE.PUP` unlinked and
`/update` force-unmounted (`bootstrapper/source/main.cpp:1140-1160`). Nothing reverts them, so they
persisted through this test — etaHEN had already booted before it was killed.

A console that **never runs etaHEN at all** would therefore have read-only `/system` and
`/system_ex` and a mounted `/update`. Nothing on the PKG install path writes to those, so this is
very likely irrelevant — but it is untested, and "installs work with etaHEN killed after boot" is
not the same claim as "installs work on a console that never loaded it". That test needs a reboot.

### Our own deploy tooling was still on etaHEN too

Killing etaHEN broke deploy_elf.py — it pushed the ELF over FTP on :1337, which is etaHEN's.
A deploy tool that needs the thing you are removing is a funny kind of independence.

Both deploy paths now prefer our own /api/fs/write and keep FTP only as a recovery route for a
console whose shop ELF is not running. Measured with etaHEN dead: **42 MB in 2.5 s**, against ~6 s
over FTP. deploy.py app does the same for the UI bundle.

### The connection light was lying in the other direction

With etaHEN gone the companion reported connected: false while installs worked perfectly,
because "install ready" still meant "something is listening on :12800". Readiness now asks OUR
engine: engine: "pms-spawn", engine_ready = Payload Manager reachable, which is the only
thing it needs. dpi_* is untouched underneath for anything still reading it, and the UI status
line says *install-ready (our engine)* instead of *connected (DPI off)*.

### Where independence stands

| | |
|---|---|
| Install engine (games, updates, DLC) | **ours** |
| Console-local / USB installs | **ours** |
| File access on the console | **ours** (`/api/fs/*`, FTP only as fallback) |
| Fake-signed package support | kstuff, autoloaded — never was etaHEN's |
| `:12800` / `:9090` / klog | nothing of ours calls them |

etaHEN is **not in autoload**, so it stays gone across a reboot unless deliberately loaded. Our ELF
still ships it (`payload_bundle.h`, port 0 = ship, never auto-start) at
`/data/pkg-mutant-shop/payloads/etaHEN.elf` if it is ever wanted back.

---

## [3.27.0] — 2026-08-25 · "Queue hygiene that matches how our engine actually works, and our logo on the toast" `[VERIFIED]`

### Multi-install: what actually needs cleaning, and what does not

The third-party host needed `/cleartmp` between queued jobs because it is a **long-lived daemon**:
it accumulates scratch files, and it wedges after a heavy install so the next job has to reload it
first. That is why the queue grew `_dirty_hosts`, `recover_dpi()` and a cleanup step.

**Our engine has neither problem, by construction.** Each install is a fresh process that makes one
call and exits. There is no daemon to wedge, no port to unstick, and nothing accumulates. So the
honest equivalent of "clean the port between installs" is not a port at all — it is the two
handover files and a guarantee that two installs are never in flight at once:

- **`/api/engine/spawn-cleanup`** removes `installer-req.txt` and `installer-res.json` and releases
  the busy latch. The companion calls it after **every** spawn install, success or failure, so the
  next job in the queue cannot possibly read the previous one's request or verdict.
- **`/api/engine/spawn-status`** reports whether an install is in flight.
- **A busy latch** rejects a second concurrent spawn with `409`. Both installs would otherwise share
  one request file and the second would install whatever the first asked for. The latch **expires
  after 600 s** so a spawned process that died without writing a result can never block the queue
  permanently.
- The companion waits for `busy` to clear before handing over, on top of the existing
  per-console serialisation.

**Verified — three back to back, queued together:**

```
 0.6s Castle Crashers Remastered  submitting → transferring → promoting → playable   11.1s
11.1s Riptide GP2                 submitting → transferring → promoting → playable    9.2s
20.3s inFAMOUS Second Son JOS DLC submitting → promoting → "Update installed"         5.6s

#234  1036  title='PKG MUTANT SHOP'  CASTLECRASHERSNA   227540992/227540992
#235  1036  title='PKG MUTANT SHOP'  RIPTIDEGP2PS4001   107806720/107806720
#236  1026  title='PKG MUTANT SHOP'  UNLOCKVESTJOS000     1048576/1048576

after the queue:  busy=false · handover files: NONE · etaHEN log lines: 0
```

### Our logo on the console's own notification

`sceKernelSendNotificationRequest` takes an icon URI, so the toast can carry our artwork instead of
the generic system glyph.

**It is the ICON, not the LOGO** — and the distinction matters. `web/assets/icon0.png` is the square
512×512 mark; `web/assets/logo.png` is the wide wordmark and would be cropped to nothing in a square
notification slot. The shop ELF already extracts `web/` on every boot, so the icon is always present
and always matches the running build; the installer checks `access()` before setting it, so a missing
file degrades to a plain toast rather than a broken one.

The wording changed too. It now names the game and says what is happening **right now**:

```
  PKG MUTANT SHOP
  Castle Crashers Remastered is downloading now
```

Not "installed" — the console posts its own *"Ready to play"* when the download actually finishes.
Claiming completion at hand-off is the same lie this project has removed everywhere else.

`MetaInfo.content_name` now carries the real game name as well, so the console's own Downloads entry
reads properly instead of showing our app's name for every job.

### Also

- **Removed `/data/pkg-mutant-shop/allow-diagnostics`**, which was left enabled on the console after
  the credential experiments. The install-capable diagnostics are gated again.

### Where etaHEN stands now

Nothing on the install path uses it. It is the FTP **fallback** behind `fs_*` (ours preferred, 6–7×
faster), and it still owns `:12800`, `:9090` and the klog port — front doors nothing of ours calls.
Whether those can simply be switched off depends on what etaHEN provides *passively*, which is under
investigation separately.

---

## [3.26.3] — 2026-08-25 · "Spawn mode is the default, and the log has no etaHEN in it" `[VERIFIED]`

`dpi.mode` is now **`"spawn"`**. Every install the app performs — base games and add-ons — goes
through our own engine. **Zero etaHEN contact across an entire session log.**

### The queue no longer babysits a daemon we do not use

Flipping the config was the small part. The queue's pre-flight was written around a third-party
daemon and keyed on `mode != "pms"`, so `"spawn"` inherited all of it: `ensure_dpi_host()` reviving
whatever owns `:12800`, `ensure_dpi_ready()` probing it, `_dirty_hosts` wedge-healing,
`ensure_install_host_safe()` rewriting its config, `last_install_via()` reading Elf Arsenal's
`last-install.json`, and `dpi_cleanup()` poking etaHEN's `/cleartmp` after every job.

All of it is now behind one predicate, `ours = mode in ("pms", "spawn")`. Running that machinery in
a mode whose entire purpose is not needing etaHEN would have made our own lane depend on etaHEN
being up.

Two call sites had to be gated, not one — the first pass missed the `if installed:` branch, and the
log proved it: `[dpi-cleanup] SUCCESS: Deleted 0 temporary files.` kept appearing after installs
that never touched etaHEN.

### Verified end to end, through the app's own queue

```
Castle Crashers Remastered   submitting → transferring 2→54% → promoting → playable   11.5s
Riptide GP2                  submitting → transferring 8→79% → promoting → playable    9.3s
inFAMOUS Second Son (DLC)    submitting → promoting → "Update installed"                6.5s
```

Both games were deleted beforehand. The proof is the `bgft.db` `title` column — the only field that
records **which lane ran**:

```
#231  status=1036  title='PKG MUTANT SHOP'  UP2015-CUSA14409_00-CASTLECRASHERSNA  227540992/227540992
#232  status=1036  title='PKG MUTANT SHOP'  EP0786-CUSA02365_00-RIPTIDEGP2PS4001  107806720/107806720
#233  status=1026  title='PKG MUTANT SHOP'  UP9000-CUSA00223_00-UNLOCKVESTEXIST0    1048576/1048576
```

`1036` = full title install, `1026` = add-on. The add-on is registered in `addcont.db`. Five files
on disk for each game at the exact package size. And:

```
grep -icE "dpi-cleanup|dpi-reload|12800|etahen"  pms.log   ->   0
```

### A test that lied, and why it is worth writing down

The first run after the flip reported `playable` for both games — and the bgft rows said
**`etaHEN DPIv2`**. The frozen exe reads `config.json` from **beside itself**
(`C:\Users\<user>\Desktop\PKG MUTANT SHOP\`), not from `companion/config.json` in the repo. The flip
had gone into the repo copy, which the running app never reads.

Two green "Ready to play" rows, and the lane was unchanged. This is exactly why the `title` column
gets checked instead of the queue state: **the queue reporting success says nothing about which
engine produced it.**

### Still etaHEN's

Nothing on the install path. It remains the FTP fallback behind `fs_*` (ours is preferred and
measured 6–7× faster), and it still owns `:12800`, `:9090` and the klog port — front-end services
we do not need but have not yet replaced. Turning etaHEN off entirely is now a question of those
ports, not of installing.

---

## [3.26.0] — 2026-08-25 · "SOLVED: our own engine installs base games" `[VERIFIED]`

**`0x80B2116F` is caused by calling `sceAppInstUtilInstallByPackage` from a payload INJECTED into a
hijacked host process. The identical call from a freshly SPAWNED process succeeds — with our own
ordinary homebrew authid.** Credentials were never the issue.

### How it was cornered

**1. The arguments were eliminated by a control nobody had run.** The same local path
`/user/data/tmp/pms_riptide_CUSA02365.pkg` was handed to both callers within the same minute. Ours
returned `0x80B2116F`; etaHEN returned `SUCCESS`. (etaHEN's DPI v2 sets `arg1.uri = url_value`
verbatim with no validation, so POSTing a local path as the `url` field makes it perform *exactly*
our call.) Identical bytes in, different result out.

**2. The entire ucred was eliminated.** A new read-only `/api/engine/procdiff` dumped every readable
process field for both. The differences were:

| field | ours | etaHEN util (succeeds) |
|---|---|---|
| `authid` | `0x4801000000000013` | `0x4800000000000006` |
| `ruid/svuid/rgid/svgid` | 0 | 1 |
| `jaildir` | `0xffffb84801d82ee0` | `0x0` |
| `caps` | `ff × 16` | `00000000701c004000ff0000000000c0` |
| `attrs` | *identical* | *identical* |

Matching **all of them at once** — including clearing `jaildir` and adopting etaHEN's exact
capability mask — still returned `0x80B2116F`.

**3. A spawned process succeeds.** `pms-installer.elf` (`ps5-app/onconsole/installer_probe.c`) does
nothing but the same call. Loaded by Payload Manager so it becomes its own process, it returned
`rc = 0x00000000` — while running as `0x4801000000000013`, the plain homebrew authid.

### Proven on a genuinely absent base game

`Castle Crashers Remastered` (CUSA14409, 227,540,992 B), deleted beforehand, installed from a plain
HTTP URL served by our own companion:

```
bgft #222   title='PKG MUTANT SHOP'   status=1036   227540992/227540992   err=0x00000000
/mnt/ext1/user/app/CUSA14409/   app.pkg 227540992 + app.pbm + app.pbm.backup + app.json + app.xml
/user/appmeta/CUSA14409/        icon0.png 262171, pic0/pic1.png 1189365 …  (real artwork)
```

**status 1036 = full title install.** Every previous `PKG MUTANT SHOP` bgft row in this console's
history was 1026 — an add-on. This is the first base game our own engine has ever installed, and
the console fetched it over HTTP from us, exactly as it does for etaHEN.

### What this retires

- **The authid theory** (3.24.7). Our authid was the ordinary homebrew one and it worked.
- **The "content-selective, therefore not process-global" argument** that a verification pass used
  in 3.25.4 to kill the spawned-process idea — and which this changelog repeated. **It was wrong.**
  A process-global property that ShellCore consults *only* when building the APP actor produces
  exactly the add-ons-work/games-fail split. The argument is retired; do not reuse it.
- Every argument-level theory: struct sizes, `content_type`/`content_platform`, the big-app
  announce, URI form, target presence.

### The lane, and why it is shaped this way

`GET /api/engine/install-spawn?uri=<http url or local path>` →
`GET /api/engine/spawn-result` for the verdict.

The ELF writes the URI to `/data/pkg-mutant-shop/installer-req.txt`, clears any previous verdict,
extracts `pms-installer.elf` **straight from the bytes embedded in itself** (rather than trusting
`payload_bootstrap`, whose thread waits out the user-configurable autostart delay and so has not
run on a fresh boot), mirrors it into Payload Manager's own payload directory — because
`/loadpayload` resolves by **basename** against that directory, so a full path elsewhere can
silently run an older copy — and asks Payload Manager to spawn it.

The protocol is files on purpose: no port, no parser, nothing to wedge.

`Ps5Bridge.install_spawn()` drives it from the companion and is selected by `dpi.mode = "spawn"`.
`ok` means the console accepted the package and created a BGFT task; completion is still proven
from `bgft.db`, exactly as for etaHEN. A refusal is pre-BGFT, so nothing is left half-queued.

Verified through the companion:

```
Ps5Bridge.install_spawn(http://10.0.0.76:8710/library/Castle-Crashers-Remastered-CUSA14409.pkg)
  -> ok=True  host=pms-spawn  via=spawned-process  rc=0x00000000
     content_id=UP2015-CUSA14409_00-CASTLECRASHERSNA
```

### On the notifications

This also settles the screenshots. **"Castle Crashers Remastered — Ready to play"** with the box
art is ShellCore/BGFT's own toast, emitted because the `subtype=6` task exists — there is no code
in etaHEN that produces it, and we get it for our own installs. The only line that was ever etaHEN's
is its debug toast; ours now says `PKG MUTANT SHOP — Install task created`.

### Still to do for full independence

The install *call* is ours. Serving our own `:12800`/`:9090` DPI front-ends and a klog port is
front-end work on top of a lane that already works, and `dpi.mode` still defaults to `"v2"` until
the spawn lane has more hardware time behind it.

---

## [3.25.4] — 2026-08-25 · "etaHEN's install call is our install call" `[VERIFIED]`

### We read etaHEN's source. The call is byte-for-byte ours.

Fetched from `github.com/etaHEN/etaHEN` (GPLv3; this project is GPLv3, so licence-compatible
reuse). **Note the public tree self-identifies as 2.5B and its firmware tables stop at 8.20 — it is
older than the binary on this console.**

`Source Code/util/include/common_utils.h:54-88` versus our `server.c`:

| | etaHEN | ours |
|---|---|---|
| `MetaInfo` | 6 × `const char*` = **48 B** | identical |
| `SceAppInstallPkgInfo` | `content_id[0x30]; int content_type; int content_platform` = **56 B** | identical |
| `PlayGoInfo` | `language_t[30]` + `playgo_scenario_id_t[64]` + `content_id_t[64]` + `long[810]` = **9984 B** | identical |

And `DirectPKGInstaller.cpp:1159-1204`, its DPI v2 path — the one our companion drives:

```c
MetaInfo arg1 = { .uri = "", .ex_uri = "", .playgo_scenario_id = "",
                  .content_id = "", .content_name = "etaHEN DPIv2", .icon_url = "" };
arg1.uri = url_value;                                  // the http URL, straight through
memset(&arg3, 0, sizeof(arg3));                        // PlayGoInfo zeroed
install_result = sceAppInstUtilInstallByPackage(&arg1, &pkg_info, &arg3);
```

That is our call, argument for argument. Confirmed on top of that:

- **etaHEN has no base-game branch at all.** One unconditional call handles game, patch and add-on.
  There is no `subtype` anywhere, no content-type discrimination, no special case.
- **etaHEN never touches an app-slot API.** No `GetPrimaryAppSlot`, no
  `sceAppInstUtilGetAppEmptySlot`, no `applyPatchPrimarySlot` — those lines in the klog are
  ShellCore's own internals, not the caller's.
- **On FW 12.70 etaHEN's ShellCore patcher applies nothing** — its firmware table stops at 8.20. So
  no ShellCore patch can be the differentiator.
- It leaves `SceAppInstallPkgInfo` **uninitialised** where we `memset` it — already eliminated by
  the 18-combination sweep in 3.25.1.
- `set_proc_authid(getpid(), DEBUG_AUTHID)` is `0x4800000000000006` — exactly the value measured
  and A/B'd on hardware in 3.24.7, both per-call and set before IPC init.

**So the difference is not in the call.** 63 candidate differences were extracted from the source
and adversarially checked; **2 survived**, and the reason the other 61 died is a single observation
that is worth more than any of them:

> **The failure is content-selective.** Add-ons succeed and base games fail **in the same process,
> on the same thread, through the same code path, differing only in the URI string.** Anything
> process-global — init order, thread identity, linked library surface, `sceNetCtlInit`, authid,
> process ancestry, `elfldr_spawn` versus payload-injection — would poison **both** equally. It
> cannot select `subtype=6` and spare `subtype=7`.

That retires the whole class of environment theories at once, including the spawned-process one this
entry originally recommended. Two corollaries fell out while checking it: etaHEN's own DPI v2 calls
`InstallByPackage` on a libmicrohttpd per-connection worker (`MHD_USE_THREAD_PER_CONNECTION`), *not*
the thread that called `Initialize` — so thread affinity is demonstrably not part of the contract;
and our own add-on successes prove our lazy `sceAppInstUtilInitialize()` produces a fully working
installer session that reaches and completes pre-allocation.

The gate is on the **callee** side: whatever ShellCore consults for the APP actor and not for the AC
actor — the application-slot resolution the add-on path never touches.

**The two survivors**, both cheap and testable in one build: `MetaInfo.content_name` (etaHEN sends
`"etaHEN DPIv2"`, we send `"PKG MUTANT SHOP"` — weak, but the one argument never varied, and
plausibly content-selective since an app entry needs a display name and an add-on does not), and
preloading `libSceNetCtl`/`libSceSysmodule`/`libSceNet`/`libSceVideoOut` plus `sceNetCtlInit()`
(rated mechanistically dead for the netctl half, since ShellCore already fetched the package in its
own process — free to add, low expectation).

### The notifications, settled

The **"Castle Crashers Remastered — Ready to play"** toast with the game's artwork is **not
etaHEN's**. There is no code anywhere in etaHEN that emits it. It is **ShellCore/BGFT's own**,
produced as a side effect of the `subtype=6` task existing. We already get it today, because our
installs go through BGFT.

The only thing on screen that belongs to etaHEN is its debug toast, `[etaHEN] DPI: Direct install
console Task started for <url>` — emitted by `notify()` right after the call returns
(`DirectPKGInstaller.cpp:1205`). When our own engine creates the task, that line becomes ours and
the console's own notifications are unchanged.

---

### Audit items closed this build

**[11] A four-minute stopwatch was issuing verdicts.** After the last byte landed, the confirm loop
failed the job at 240 s with *"Console rejected this package — delivered in full, but it won't
install (bad dump)"* — an accusation about the file, produced by a clock. A large title can spend
far longer than that copying and decrypting, and a brief FTP hiccup makes `installed_titles()`
return `None`, which reads as "not there yet". It now waits on **evidence**: it keeps going while
the console's own bgft row is still changing, fails immediately on a terminal non-success status
(`BGFT_FAILED`, e.g. the `1021 / 0x80B21104` observed here), and otherwise gives up only after
30 minutes of no movement — reporting *that*, not a guess about the dump.

**[32] `installed.json` was unlocked read-modify-write.** Two workers finishing together each read
the same list; the second write lost the first title and the badge reverted. Now guarded by an
`RLock` with the re-read **inside** the lock. Proven: 200 concurrent `remember_installed` calls,
**0 lost**. It also no longer assumes the file's shape — a bare list, `{"installed": null}`, or
unparseable junk each used to raise inside the outer `try` and silently drop the write.

**[15] A null or wrongly-typed config section bricked startup.** `POST /api/config` deep-merges an
unvalidated body, so `"companion": null` or `"port": "eight thousand"` produced a `TypeError`
before anything was logged — in the frozen exe, a window that closes with no message. `coerce_config()`
now restores any top-level section that is not the right shape, coerces `companion.port`, and says
what it repaired. Applied on load **and** on the live-update path.

**[27] / [30] One byte-counter per library file, shared by every console.** "Install to all
consoles" on one game: both jobs credited and read the same `transfers[key]`. Console A at 40 GB,
console B's hand-off resets it to 0 — A's stall detector starts counting while B displays A's
percentage and drops into "promoting" the moment A finishes. Two jobs, one number, both wrong. The
counter is now bucketed by the requesting console's IP, with the bare-key entry kept as the
aggregate because that is what a peer companion polls via `/api/served/<key>`.

### Verified on hardware

`Riptide GP2` reinstalled through the app on this build: `submitting → 8% → 28% → 33% → promoting →
playable` in 5.0 s, five files at `/mnt/ext1/user/app/CUSA02365/` with `app.pkg` at exactly
107,806,720, and `/api/served/` still reporting the aggregate correctly for peers.

---

## [3.25.3] — 2026-08-24 · "Eighteen call sites down to four, and the exe finally keeps a log" `[VERIFIED]`

### FTP is no longer a dependency — it is a fallback

Every console read and write now goes through **our own on-console server first**, with the
third-party FTP kept underneath as a fallback. FTP appears in exactly **four** places in the
companion — inside `fs_read`, `fs_list`, `fs_write` and `fs_mkdir` — down from 18 scattered call
sites. Converted this round:

`ensure_install_host_safe` · `last_install_error` · `last_install_via` · `register_path` ·
`ensure_register_path` · `installed_app_pkg` · `cheat_library_status` · `sync_cheat_library` ·
`console_backups` · `console_icon` · `console_cheats` · **and the mount lane's upload**.

New primitives: `fs_mkdir`, and `fs_text` (most callers were decoding the same small config or
json by hand). `fs_write` now takes a file object plus `size` so urllib streams it straight off
disk — the difference between being usable for a 90 GB game and only for small files.

**Both transports, every converted path, measured on hardware — identical results:**

| | ours | FTP | |
|---|---|---|---|
| `console_apps` | 1.07 s | 6.61 s | 136 titles both |
| `console_ps4_appmeta` | 0.84 s | 4.44 s | 55 PS4 titles both |
| `install_job_row` | 0.02 s | 0.17 s | same row |
| `console_backups` | 0.11 s | 0.33 s | 52 backups both |
| `cheat_library_status` | 0.22 s | 0.52 s | reachable both |
| 40 MB streamed upload | 2.57 s (16.3 MB/s) | 6.05 s (6.9 MB/s) | byte-identical both |

**Both transports are now atomic.** The FTP branch of `fs_write` writes `<name>.part` and renames,
matching what the ELF already did. Writing straight to the final name leaves a partial container in
ShadowMount's scan folder, where it looks exactly like a real game — half a game that mounts, or
refuses to, for no visible reason.

### A bug this found, in the code it replaced

Cancelling a mount upload **did not cancel it**. `fs_write` caught the progress reader's
`_Cancelled` in its blanket `except Exception`, concluded our transport was broken, marked it dead
for 20 s, retried the whole transfer over FTP, and finally returned `False` — so the queue reported
*"Upload failed"* for a job the user had deliberately cancelled, **and left a `.part` file behind in
a watched folder**. A cancel is not a transport failure. Both branches now re-raise it, and the FTP
branch deletes its own `.part` on the way out.

Verified on hardware, cancelling a 40 MB upload at 4 MB:

```
our file API : _Cancelled propagated in 0.26s · transport still trusted · no files left
FTP fallback : _Cancelled propagated in 2.61s · no files left
```

### [16] A corrupt config.json used to overwrite itself with defaults

`config.json` truncated by a power cut or an editor crash was silently replaced by `DEFAULT_CONFIG`
in memory. The defaults point at `192.168.1.50`, so the console was unreachable, so `main()` took
its auto-discover branch — **and called `save_config()`, writing the defaults over the user's
file.** Library paths, sources and console list gone, with nothing said.

`load_config()` now records that the file could not be parsed, keeps a timestamped copy
(`config.json.unreadable-YYYYmmdd-HHMMSS`), and `save_config()` **refuses to write** while that
flag is set unless called with `force=True`. `/api/health` carries `config_unreadable` so the UI
can say so rather than quietly running on defaults.

Proven by feeding it a truncated file: flagged unreadable · ran on defaults · save refused · **the
user's file byte-intact** · copy kept · `force=True` still works.

### [17] The shipped exe kept no log at all

Every diagnostic here is a `print()`, and the exe is built `--noconsole`: stdout is a null device.
So when an install failed, the host's real reply — the SCE error code, which FTP port answered,
whether a reload was even attempted — was printed and discarded. The user had a one-line UI message
and no way to learn anything else.

There is now `pms.log` beside the exe: timestamped, flushed per line (a crash must not swallow the
last lines), rotating one generation at 2 MB. `print` is *wrapped* rather than replaced at every
call site, so ~200 existing diagnostics became useful immediately and stdout still works from
source. Confirmed writing on first boot.

### End to end, on hardware

`Castle Crashers Remastered` installed through the app's own button on the fully converted stack:

```
0.0s submitting · 7.1s transferring 2% · 9.2s 28% · 10.7s 83% · 11.4s promoting · 12.1s playable
/mnt/ext1/user/app/CUSA14409 → app.pkg 227,540,992 (= the exact package size) + the four sidecars
installed titles 136 → 137
```

Real byte progress the whole way, and the "Ready to play" was a bgft-confirmed verdict.

### What still uses etaHEN

**One thing: installing a base game.** Add-ons already install through our own engine; every file
operation is now ours with FTP only as a fallback. Once you are satisfied with the fallback path,
etaHEN's FTP can be switched off entirely without the app noticing.

---

## [3.25.1] — 2026-08-24 · "Our own filesystem, and the last caller-side theory dies" `[VERIFIED]`

### The app can now read and write the console without anybody else's FTP

Every console read in the companion went through FTP — **18 call sites** — and FTP on this console
belongs to etaHEN (`:1337`) or to `ftpsrv` (`:2121`). Neither is ours. That was a hard dependency on
software we do not ship, for the sake of reading files on a machine where **our own payload already
runs as root with an HTTP server on `:8710`**.

The ELF gained a filesystem API: `GET /api/fs/read|list|stat|mkdir|delete` and a **streamed**
`POST /api/fs/write`. The write is handled straight off the accept loop, before the generic POST
path, because that one reads a body into an 8 KB stack buffer — fine for JSON, useless for a game.
It writes `<path>.part` and renames on success, so a broken transfer can never be mistaken for a
finished file — and in ShadowMount's watch folders can never be auto-mounted half-written.

The companion gained `fs_read` / `fs_list` / `fs_write` / `_pull_db`, which **try our own server
first and fall back to FTP**, remembering which answered. Nothing regresses if the ELF is not
loaded: the old path is still underneath, unchanged.

Converted so far — the four database pulls (`app.db`, `bgft.db` ×2, `addcont.db`) and the two
directory consumers (`console_ps4_appmeta`, `title_has_mount_link`). Those are the install-confirm
and install-detection paths, i.e. the ones that run constantly.

**Measured on hardware, both transports, identical answers:**

```
bgft.db read        HTTP 0.011s   FTP 0.079s     7.3x   byte-identical (sha256)
install_job_row     HTTP 0.027s   FTP 0.070s     2.6x   same row
PS4 appmeta scan    HTTP 0.91s    FTP 6.19s      6.8x   same 54 titles
24 MB streamed write         1.15s  (21.8 MB/s)  byte-identical round trip, no .part left behind
```

It is faster because FTP pays a connect, a login and a data-channel setup for **every single
fetch**, and the confirm loop re-reads `bgft.db` every few seconds for the whole of an install.

On security this is a **smaller** exposure, not a new one: etaHEN's FTP already grants any host on
the network anonymous root access to the entire filesystem, with no origin check and no way to turn
it off without losing the install host. These routes sit behind the origin guard from 3.24.8 — and
once the remaining writers move over, etaHEN's FTP can simply be switched off.

**Still on FTP:** the mount-lane upload, the cheat sync, and `console_backups`. `fs_write` already
accepts a file object plus `size` so it streams off disk rather than buffering, which is what those
need; they are the next conversion, not a blocker.

### A bug this introduced, and caught

Routing the four DB pulls through `_pull_db` left three call sites doing `self._drop_tmp(tmp)` with
`tmp = None` on the failure path. `os.remove(None)` raises **TypeError**, which `except OSError`
does not catch — so a momentary console hiccup would have propagated out of `install_job_row`
instead of returning `None`. `_drop_tmp` is now None-tolerant and the redundant calls are gone.

Converting `console_ps4_appmeta` also left its tail reading `p[4]`, a variable from the deleted FTP
parse. The resulting `NameError` was swallowed by the function's own `except`, so **PS4 detection
silently returned zero titles** — on both transports, which is exactly why "same answer as FTP"
looked like a pass. Caught because 0 PS4 titles is absurd on a console with 54 of them. It now
reads `e["size"]` from the listing and finds all 54, on both transports.

### The last caller-side hypothesis is dead too

3.24.7 eliminated the caller's *identity and context* for the base-game blocker (credentials, both
before and after IPC init; the big-app announce; target presence; storage; a missing Sony call).
What remained was the caller's *arguments*: `SceAppInstallPkgInfo{content_id, content_type,
content_platform}` has always been passed as **all zeros**, on the assumption that it is purely an
OUT parameter. Zero is proven to work for an add-on. Nobody had ever varied it for an app.

Swept 18 combinations of `content_type` × `content_platform` against an **absent** base game
(Riptide GP2, deleted from the console for the purpose):

```
0/0 1/0 2/0 3/0 4/0 5/0 6/0 7/0 8/0 0/1 1/1 2/1 0/2 1/2 2/2 6/1 6/2 7/1
  -> 0x80B2116F on every single one, content_id empty on every single one
```

Uniformly negative. The struct fields are OUT-only in practice. **Caller-side is now genuinely
exhausted** — identity, context and arguments. The remaining question is entirely on the callee
side, and the honest next step is reading etaHEN's GPLv3 source around `DbgInstall` / app-slot
resolution rather than another A/B from here.

### End-to-end regression, on hardware

`Riptide GP2` reinstalled through the app's own Install button on the new code:
`playable / 100% / "Ready to play"`, five files at `/mnt/ext1/user/app/CUSA02365/`
(`app.pkg` 107,806,720 = the exact package size, plus `app.pbm`, `app.pbm.backup`, `app.json`,
`app.xml`), and `installed_titles` back to 136 including it. The `/api/engine/*` diagnostics were
switched back off afterwards and verified 403.

---

## [3.24.9] — 2026-08-24 · "Buttons that discard what the server said" `[VERIFIED]`

More of the 48-agent audit worked through. Everything here is a defect that changed what the user
saw or lost their work — no refactors.

### "Clear finished" deleted the queue

It kept a whitelist (`RUNNING` + `queued`) and deleted everything else — which includes every
**held** job. Build a queue with ＋ Queue (all held), press *Clear finished* to tidy one failed row,
and the entire batch is gone server-side. It is worse right after a wedge, where `_pause_pending`
deliberately holds every pending install: the one button offered to tidy up is the one that throws
the batch away. It now drops only genuinely terminal states, which is what the button says.

`"reloading"` was in the same blast radius and is now also in `RUNNING` — a reloading task has a
worker inside `ensure_dpi_ready()` for up to 75 s, and leaving it out let `_claim()` start another
job past `max_parallel`.

### A multi-part install wrote several HTTP responses into one socket

`_install()` recurses into itself once per part, and **every one of those calls wrote a complete
HTTP response**. A 3-part release wrote four responses to a single request: the client reads the
first — part 1's — and the rest are garbage on the wire. The `{"multi_part":true,"parts":3}` reply
that was supposed to be the answer went into a socket nobody was reading.

The recursion now collects instead of writing (`_json` mutes and returns its payload), so exactly
one response is emitted — and each part's real result is finally honoured. Previously a part that
was **refused** or **already installed** was counted as queued regardless; now a refusal reports
which part failed and why, and a release whose parts are all present reports that instead of
queueing nothing and claiming success.

### A held job kept the address the PC had when it was queued

Queue ten games with ＋ Queue in the evening; overnight DHCP moves the PC, or Windows switches from
Ethernet to Wi-Fi. In the morning every job hands etaHEN a URL on an address that no longer exists.
Jobs that point at us are now flagged `relan` and their URL is rebuilt **at submit time** with
`companion_ip_for(bridge.ip)`, logging the change when it happens.

Same class of bug in `start_pc_register_thread`: it announced one `lan_ip()` to every console,
while the install URL used `companion_ip_for()` per console. On a dual-homed PC (Ethernet on the
console's subnet, Wi-Fi holding the default route) the console was told to reach us on an address
it may have no route to — and the console's own UI then pointed at the wrong PC while installs
worked fine. Now resolved per console, with the same helper.

### Reinstall could never reinstall

*Reinstall base* — both the drawer button and the ⋯ menu item — hit the server's already-installed
guard and came back as a green "already installed" toast with the button relabelled. There was no
way to reinstall a short or corrupt game from the UI at all. The server has implemented `force` as
the documented override the whole time and nothing passed it; both deliberate-reinstall entry
points now do. (Install and ＋ Queue are unchanged — they still refuse duplicates.)

### The UI threw away every explanation the server sent

Only `base_not_installed` was translated; everything else rendered as `"Install failed: " + code`.
So a split release with a part missing produced *"Install failed: missing_part"* while the server
had already written a full sentence saying which part and why. The toast now prefers the server's
own `message`.

Related: a multi-part install said "Sent to PS5 · Game" and hid `"Queued 3 parts"`, so the extra
amber held rows and the *Start queue (N)* button arrived unexplained. It now reports the parts.

### A fully paused queue animated as "N installing"

A wedge holds every pending install; the dock counted them as live and spun an amber pill reading
"3 installing" while the console was doing nothing at all, each row telling the user to press Start.
`held` is now split out of `live` and reads "N waiting to start", not spinning.

### The version tag hid a stale UI

`#verTag` showed whichever of (companion, this page) was **newer**. A 3.24.2 UI talking to a 3.24.9
companion therefore displayed 3.24.9 and looked current — the exact situation worth seeing, hidden.
It now always shows this page's version, and on a mismatch appends `(PC vX)` in the warning colour
with a tooltip saying which side to update.

### `deploy.py app --companion` has never survived a reload

`extract_web()` rewrites every bundled file to `WEB_ROOT` on each ELF boot — including `config.js`,
the one file there that is *configuration* rather than build output. So the companion URL that flag
writes was truncated back to the shipped comment on every load and `window.PMS_API` was always
undefined. `config.js` is now shipped only when it is missing or empty.

Proven on hardware: wrote a marker into `config.js`, reloaded the ELF, and the marker was still
there afterwards. (Left at the auto-discovery default deliberately — a hardcoded PC address here is
the same failure the two fixes above just removed.)

### Also

- `recover_dpi()`'s on-console fallback read `cfg["server"]["port"]`, a key that **does not exist**.
  It has always fallen through to the 8710 default, which happened to match — until anyone sets
  `console.shop_port`, at which point every other `_shop` call follows it and this one alone does
  not. Now reads the same key as everything else.
- Two wedge messages named **"Elf Arsenal / ps5-dpi-v2"**, payloads this build does not ship, so the
  user went looking in Payload Manager for software that is not there and never tried etaHEN, which
  is the actual fix. Both rewritten to name the real host and the real action.

---

## [3.24.8] — 2026-08-24 · "The console stops taking orders from strangers" `[VERIFIED]`

### The on-console server had no idea who was talking to it

It answers on `INADDR_ANY:8710` with `Access-Control-Allow-Origin: *`, **as root**, and some of its
routes install packages and write process memory. The PC companion has had an origin guard since it
grew a web UI; this end never did, so any page open in any browser on the network could drive it.

`request_origin_ok()` now mirrors the companion's rule exactly, applied in the accept loop before
anything is dispatched. The test is **not** "same origin" — the console serves its UI to itself
while the data comes from a PC on a different address, and that *is* the architecture. The test is
whether the requesting page has a DNS hostname at all: every legitimate caller is a private-range
IP literal or loopback, and a hostile public site always has a name. A request with **neither**
header is allowed on purpose — that is curl, the companion, and our own tooling, none of which a
remote page can forge.

Verified on hardware:

```
no Origin (the companion)          -> 200
Origin: http://10.0.0.99:8710      -> 200   (the console's own UI)
Origin: http://10.0.0.76:8710      -> 200   (the PC's UI, cross-origin by design)
Origin: https://evil.example.com   -> 403
```

### Four routes that install packages are now opt-in

`/api/engine/install-inproc`, `install-local`, `install-dir` and `diag` call `libSceAppInstUtil`
directly. Nothing in the UI and nothing in the companion calls them — they are hand-fired
diagnostics, and `diag` performs **two real installs** of the same package. They stayed reachable
by anyone on the LAN purely because nothing had ever said otherwise.

They now require `/data/pkg-mutant-shop/allow-diagnostics` to exist, and answer
`403 {"ok":false,"error":"engine diagnostics are off …"}` when it does not. **The product's own
lane (`/api/engine/install-url`) and every read-only route are not gated** — verified:
`/api/engine/authid` and `/api/engine/job` still answer normally.

### Console-local progress was the constant 50

`/api/install/status` reported `pct = 50` for the entire install. On a 90 GB package from a USB
stick that is twenty minutes of a bar that never moves — indistinguishable from a hang, and exactly
what makes someone power-cycle a console mid-write.

We genuinely cannot know the figure there: etaHEN does the fetching and reports nothing back. So
the honest answer is **unknown**, not a number that looks like measurement. `pct` is now `-1` for
"in flight, unmeasurable", and the queue row draws a sweeping indeterminate bar labelled *working*
instead of a stalled percentage. (The PC companion **does** know — it reads real byte counts out of
`bgft.db` — and its rows are unchanged.)

### A console that goes away no longer destroys the queue

Ten games queued, the PS5 reboots after the third: every remaining job cost about two seconds to
discover the console was gone, so the whole queue burned down to `error` in under half a minute and
each one needed re-queueing by hand. The offline branch now **holds** and pauses the rest of that
console's queue, exactly as the wedge path has since B8 — being switched off is no different, and
far more likely.

### A transient etaHEN restart no longer holds the queue for good

`ensure_dpi_ready()` allowed about eleven seconds in total (one 5 s probe plus `recover_dpi`'s
single 6 s check) before declaring the lane wedged. etaHEN rebinds `:12800` on its own retry loop,
so a DPI v2 toggle, an etaHEN restart, or a momentarily busy console exceeds that easily — and the
queue then held permanently, with nothing to release it. An unattended overnight run of eight games
stopped after the first. It now waits up to 75 s for the lane to come back on its own, reporting
`ready (came back on its own after Ns)`, before reloading anything. `recover_dpi`'s own success
path has always looped this way.

---

## [3.24.7] — 2026-08-24 · "The A/B nobody had, and the end of the authid theory" `[VERIFIED]`

### The measurement

`Riptide GP2` (`EP0786-CUSA02365_00`, 107.8 MB) was installed **twice on this console nine minutes
apart** — once by our in-process engine (failed) and once by etaHEN through the app's own Install
button (succeeded, bgft #213 status 1036, 2798 ms). Both klog traces captured live on `:9081` and
preserved at `research/klog-authid-ab-2026-08-24.txt`.

Before this, the only "working install" capture in the repo was an **add-on** —
`grep -o "subtype=[0-9]*"` on `klog-working-install-2026-08-23.txt` returns `3 × subtype=7`. Nobody
had ever watched etaHEN install a base game, so every conclusion about base games had been drawn
from a trace that did not contain one.

The two traces are **byte-identical** through `GetRawContentInfo 0x0`, `applyPatchPrimarySlot
0x80a30004`, `AppPrepareOverwriteByPackage 0x0`, `GetPrimaryAppSlot 0x80a3000e`, `DbgInstall begin`
and the storage enqueue. They diverge on exactly one line: etaHEN reaches
`[DbgInstall] Staring Pre-allocation transfer` and gets a `[type=1, subtype=6]` BGFT task; we
reserve **zero bytes** (`m.2 free` unchanged across enqueue) and get `0x80B2116F`.

Our successful add-on installs log `Staring Pre-allocation transfer on NativeAcPackageActor`. The
base-game line names no actor. **We can build the add-on actor and not the app one, and the refusal
lands exactly there.**

### Two hypotheses closed

**"The title is already installed, so it takes the overwrite branch"** — dead. Riptide had been
deleted since the last test, so this attempt fired at an **absent** title.
`AppPrepareOverwriteByPackage` is taken anyway and returns `0x0`, and the failure is identical.
Presence is irrelevant; that call is just the APP path.

**"We are calling with the wrong authid"** — dead, and it was the last open caller-side idea
(3.24.0: *"We have never tried `...006`. That is the next experiment."*). It was first **measured**
rather than assumed, with a new read-only probe:

| pid | process | authid |
|---|---|---|
| 381 | **etaHEN Utility Daemon** — the one whose base-game installs work | **`0x4800000000000006`** |
| 374 | shadowmountplus.elf | `0x4800000000000006` |
| 59 | SceShellCore | `0x4800000000000010` |
| 370 / 261 / 87 / 265 | etaHEN critical, pldmgr, elfldr, kstuff | `0x480000001000000e` |
| — | **us** | `0x4801000000000013` |

The guess was exactly right. Then it was tried twice, because one of the two ways is not a real
test: `sceAppInstUtilInitialize()` establishes the installer IPC channel, so a credential swapped
*after* that may never reach ShellCore at all.

1. per-call swap, restored afterwards → `0x80B2116F`
2. **whole process, set at boot before any IPC exists** (fresh pid, `GET /api/engine/authid`
   confirmed `0x4800000000000006`) → `0x80B2116F`, **klog line-for-line identical**

Same credential as the process that succeeds, same everything ShellCore derives (`getAppId` →
`0xffffffff` for both), same refusal. Caller-side explanations are now exhausted: package format,
target presence, the big-app announce, storage, a missing Sony call, and credentials have all been
eliminated by experiment. The remaining candidate is the **callee** — etaHEN patches the kernel and
ShellCore, and the difference may be a patch that only helps while etaHEN is itself the caller.

**Practical consequence, unchanged and now firmly evidenced:** our own engine is production-ready
for **add-ons** (updates and DLC, PS4 and PS5, in-process, no etaHEN) and **cannot install base
games**. etaHEN stays for base games.

### Harness added — all default-off or read-only

- `GET /api/engine/authid[?pid=N]` — reads a process's authid. Writes nothing, calls nothing.
- `GET /api/engine/install-inproc&authid=0x…` — runs one call under another credential and
  **always** restores ours, on every path.
- `/data/pkg-mutant-shop/authid` — read once by `jb_escalate_pid()` at startup. Absent or
  unparseable means the long-standing `0x4801000000000013`, so a console that has never heard of
  this behaves exactly as before. The file was removed at the end of the experiment and the app
  verified back on its normal credential.

### Also in this build

- **`_run()` re-drives only after a genuine reload.** `recover_dpi()` returns
  `(True, "etaHEN install host is already answering")` because etaHEN cannot be restarted from
  here — and the two re-drive paths took that as a successful heal and **re-POSTed the package**.
  A timed-out or reset hand-off may well have reached etaHEN, so that is the duplicate-bgft-job
  hazard this project has already been bitten by. The proactive path had the guard; the two that
  actually re-submit did not. Now they share `_really_reloaded()` and hold instead.
- **An unreadable bgft snapshot no longer proves an install.** `install_job_row()` returned `None`
  both for "no row" and "could not read bgft.db", and with a `None` baseline the add-on freshness
  test (`row != before`) is passed instantly by any pre-existing completed row — so a DLC could be
  called installed three seconds after hand-off on the strength of the base game's old row. The
  snapshot now retries three times and records `_bgft_before_ok`; without a baseline the bgft test
  is skipped entirely and the job falls through to an honest timeout.
- **Console DB pulls no longer share a filename.** `bgft.db`, `addcont.db` and `app.db` were each
  fetched to one fixed path per console, so the confirm loop, the library scan and the UI could
  truncate a file another thread was mid-read on — `database disk image is malformed`, swallowed,
  and read as "not installed yet". Each pull now uses `mkstemp` and removes its own file, including
  on the FTP-failure path (which the fixed-name version could afford to leak and this one cannot).
- The `?bigapp=0` note in the 3.24.1–3.24.3 entry described the parameter's original rationale.
  It has since been A/B'd and **ruled out** — `?bigapp=0` returns the same `0x80B2116F` with an
  identical trace.

### Verified end to end on hardware

The app's own install lane was recorded state-by-state during the live etaHEN install:

```
0.0s  submitting    0%   Handing off to PS5
4.1s  transferring  8%   Downloading 8%
5.2s  promoting    90%   Installing on PS5
9.3s  playable    100%   Ready to play
```

"Ready to play" was a real verdict, not a timer: bgft #213 reached status 1036 at
107806720/107806720 and the console's installed-title count went 136 → 137.

---

## [3.24.5] — 2026-08-24 · "Buttons that cannot work, and a verdict handed down one second in" `[WIRED]`

Findings from a 48-agent audit that traced **every** path in the app capable of installing a
package — the companion's HTTP dispatch and all seven queue lanes, every endpoint on the on-console
ELF, every fetch in the UI, and every config switch that changes routing. 40 paths were enumerated
and each non-etaHEN or claimed-unreachable one was then re-checked by an adversary told to refute
it. The routing conclusion is in 3.24.4; these are the defects that came out with it.

### The ⟳ "Reload install engine" button was a 404

`web/index.html` calls `api("/api/dpi/reload")`. **There is no such route on the companion.** The
GET dispatcher fell through to `_static()` and answered `404 not found`, so the toast read
"Reload failed" every time, on every console, since the button shipped — while three separate
install-failure messages instruct the user to press it ("Reload the install engine (⟳ in the
queue) and try it again").

`GET`/`POST /api/dpi/reload` now exist and run the same recovery the queue runs by itself. On
etaHEN there is nothing to relaunch (its DPI lives inside the daemon that also serves FTP and
klog), so the button does the one thing that *is* correct there — `POST /cleartmp` — and then
reports the state of the lane. It does not say "reloaded" when nothing was reloaded.

### The ELF guessed Arsenal's protocol whenever it could not identify the host

`dpi_host_is_etahen()` returned `0` for three different situations — connect failed, read failed,
and "the page says something else" — and the caller then spoke **Arsenal's** JSON API to whatever
was on `:12800`. A busy etaHEN that missed the 4-second sniff got a `POST /api/install`, an
endpoint it does not have; the connection is reset and the user is told *"no reply from the Elf
Arsenal install daemon"* about a console running etaHEN perfectly well.

It is now `dpi_host_kind()` → `DPI_ETAHEN` / `DPI_ARSENAL` / `DPI_UNKNOWN`, and UNKNOWN **refuses**
before opening the install socket, so nothing can be half-queued. The companion has refused on
UNKNOWN since 3.20.0; the two ends finally agree.

### "Installed" one second after handing the package over

The console-local lane (a PKG already on a USB stick / the console's own storage) called
`dpi_install_url()` and treated its return as the verdict. Against etaHEN that return arrives in
about a second — `SUCCESS: Task started` — and means **queued**, not installed. The install then
runs for minutes and can still fail on a bad dump, no space, or a crashed installer queue. The
console posted a PS5 notification saying *"X installed — It is ready on your home screen"*, and the
queue row went green, before a single byte had been fetched.

`/api/install/status` now carries `accepted_only`, the notification says what actually happened
("X is installing — the console is downloading it now"), and the companion waits for the same
proof the download lane demands: for an add-on or an already-registered title, **a bgft row that is
not the one snapshotted at handoff, fully transferred, with a terminal status (1026/1036)**; for a
brand-new title, **the game's own `app.pkg` at full size**. A timeout is reported as a timeout —
not as success, and not as a bad package.

### A disk-full download reported success

`http_download` called `write_all()`, which returns `void` and swallows a failed write. With `/data`
full, every write was dropped while the byte counter went on climbing, `have` reached the expected
size, the transfer "completed", and a **truncated PKG** was handed to the installer. A new
`write_all_checked()` is used on the file path only (the socket paths keep the forgiving version,
where a dropped client is not worth unwinding) and the transfer now fails with
`could not write to storage at N bytes (disk full?)`.

### Cancel did not cancel

Two halves, both wrong. A job cancelled while it sat in the queue only got a **flag** — nothing
looked at it until well after the handoff, so the worker still claimed it, still ran the preflight,
still POSTed the URL, the console really installed it, and the row then said "Canceled". `_run()`
now checks the flag before any lane can do anything. And `"submitted"` was treated as a *terminal*
state, so `cancel()` returned `False` for the one state that most needs it and the ✕ did nothing at
all, silently. A queued/held/submitted task is now marked cancelled immediately; a running one still
gets the flag, because its lane has to unwind its own transfer.

### A "submitted" job spun forever and offered no way out

`submitted` means the console was handed the package and then stopped reporting. The dock counted
it as **live**, so the pill spun that game's name forever, the count stayed at 1, and "All done"
never appeared. There was no retry button (the condition covered only `error`/`canceled`) and the ✕
was refused above. It is now excluded from `live`, shown in the warn colour as "N needs a look",
reads "… — no progress reported", and gets both buttons. `cancelTask` also surfaces a refusal
instead of swallowing it.

### Also

- The frozen exe does not carry the 48 MB cheat library (the ELF embeds all 7022 files and writes
  them itself). `sync_cheat_library()` said "no bundled cheat library at C:\…\_MEI…\cheats", which
  reads like a broken install; it now explains that the console already has them.
- `TOOLCHAIN.md` and the overhaul roadmap's invariant #1 still described **Arsenal's** protocol
  (`POST /api/install {"url":…}`, `res ∈ {0,ok,true}`) as *the* install contract. Following that
  literally now breaks installs; both now state the sniff-then-speak rule and both protocols.

### Known, deliberately not changed

- **The ELF's `/api/engine/*` endpoints bypass etaHEN and are reachable from anywhere on the LAN**
  (`:8710` binds `INADDR_ANY`, no auth). No UI or companion caller exists — they are hand-fired
  diagnostics — but `install-inproc`, `install-local`, `install-dir` and `diag` all call
  `libSceAppInstUtil` directly, and `diag` performs two real installs. Worth a decision.
- **`POST /api/config` deep-merges an unvalidated body and persists it**, so a single request can
  set `dpi.mode` to `pms`/`ezremote`/`v1` and move the whole fleet off etaHEN permanently. The
  origin guard is the only thing in front of it.
- The two `PKG-MUTANT-SHOP.exe` processes are **not** two instances — PyInstaller's one-file
  bootloader plus its child (verified by parent PID). There is still no single-instance guard.


---

## [3.24.4] — 2026-08-24 · "The mount lane was dialling a port nobody was on, and calling it success" `[VERIFIED]`

Everything here was found by probing the running console rather than by reading the code, and every
claim below was measured on 10.0.0.99 before it was fixed.

### The mount lane could not connect, and answered that by faking the whole transfer

`_run_mount` — the lane that puts a PS5 backup (`.ffpfsc`, `.ffpfs`, unpacked folders) onto the
console — was the **last place in the codebase still dialling `ftp_port` straight from config**:

```python
ftp = ftplib.FTP()
ftp.connect(bridge.ip, bridge.c["ftp_port"], timeout=15)     # 2121, from config.json
```

Every other console read (app.db, bgft.db, addcont.db, the cheat sync, the install proof) goes
through `Ps5Bridge._ftp()`, which tries **2121 then 1337** and remembers which answered. It has to:
Elf Arsenal's `ftpsrv` served 2121, **etaHEN's own FTP serves 1337**, and Arsenal is no longer
bundled. Measured on this console:

```
2121 -> ConnectionRefusedError
1337 -> 220 FTP server 1.0A for PS5 by LM.
```

So with a stock `config.json` the mount lane's connect **always failed**. That alone would be a bug.
What it did next is the real one — the `except` branch ran a **fake transfer**:

```python
except Exception:
    self._set(t, simulated=True, state="transferring", msg="Offline — simulated mount")
    ...  animate pct 0 -> 90 -> 100 ...
    self._set(t, state="playable", pct=100, msg="Deployed to ShadowMount (simulated)")
```

`state="playable"` at `pct=100`. In the queue that renders as **"Installed · est."** — a green,
finished-looking job for a game that never left the PC. That is the exact lie removed from the
install lane in 3.18.2 and again in its `console_offline` branch, left standing here.

Both halves fixed: the lane now uses `bridge._ftp(timeout=15)` like everything else, and an
unreachable FTP is reported as `state="error"` with `fail_reason="ftp_unreachable"`, naming the
ports it tried. **Nothing is animated to 100% any more.**

### ShadowMount was probed on elfldr's port — in the two endpoints that matter

3.24.x corrected `9021 -> 10101` in `payload_bundle.h`, `/api/helpers` and the boot notification,
having established that **9021 is elfldr, the ELF loader, which is always bound** — so probing it
reported ShadowMount "active" unconditionally. It missed `/api/sources` and `/api/health`, and those
are the two the settings panel actually reads, so the LED stayed permanently, wrongly green.

There is now a single `SMP_API_PORT` constant (10101 — the port ShadowMountPlus announces in its own
log) and no literals left to miss. The PC side had the wrong number too: `DEFAULT_CONFIG`,
`helper_status()`, `config.json` and two spots in `index.html` all defaulted to 9021.

### FTP: three more consumers that only knew about 2121

- `discover_ps5()` — the LAN sweep that finds the console after an IP change scanned the configured
  FTP port only, so a console running etaHEN was findable **solely** by its DPI port, and invisible
  whenever the install host happened to be down. Now scans 2121, 1337 and the DPI port.
- `deploy.py` — `deploy.py app` is how the on-console UI gets updated. It connected to the
  configured port and gave up, so a console on etaHEN **could not be deployed to at all**; it
  printed "upload error" and the PS5 went on serving the old page. `ftp_login()` now walks the same
  fallback list and says which port answered.
- `doctor.py` — reported "FTP :2121 no route" on a perfectly healthy console. It now probes both and
  prints the live port, plus what config claims when they differ.

### The port a service is actually on is reported, not assumed

`ftp_port` is new in `/api/health` (both servers) and in the `/api/helpers` and `/api/sources` helper
blocks. `helper_status()` now passes the console's own `shadowmount_port` / `install_host_port`
through instead of overlaying config on top of them, and surfaces `etahen_ipc`. The UI prints those
values instead of the `:2121` and `:9021` constants it used to hardcode. Additive keys only.

### Verified live after the change

```
ftp_ok            -> True, sticky port 1337
_ftp()            -> 220 FTP server 1.0A for PS5 by LM.
helper_status()   -> ftp_port 1337 · shadowmount_port 10101 · etahen_ipc true · source "console"
dpi_host_kind()   -> etahen
```

### Install routing, re-confirmed end to end

Traced rather than trusted: **every install of every kind goes to etaHEN.** `dpi.mode="v2"` +
`dpi.host="auto"` → `dpi_host_kind()` sniffs `:12800` (it answers `<title>etaHEN DPIv2</title>`) →
`_install_etahen()` → `POST /upload`. The UI has exactly one install entry point,
`POST /api/install`. Packages already on the console take the console-local lane, and the ELF's
`dpi_install_url()` sniffs the same port and speaks the same multipart protocol to the same daemon.
`UNKNOWN` refuses rather than guessing Arsenal's protocol.

---

## [3.24.1] – [3.24.3] — 2026-08-23 · reconstructed `[VERIFIED]`

Three version bumps shipped without changelog entries. There is no VCS in this tree, so the split
between .1, .2 and .3 is not recoverable; this is the combined content, reconstructed by diffing the
`2026-08-23_2100` backup (3.23.0) against the tree and subtracting what 3.24.0 already documents.
All of it is in `ps5-app/onconsole/` — the Python and the UI changed only their version stamps.

- **ShadowMount is on 10101, not 9021.** It announces its own listener
  (`[API] HTTP/JSON ready: http://127.0.0.1:10101/api/v1`). 9021 is elfldr, always running. Because
  that number is *also* the "is it already up?" test in `payload_bundle.h`, the check was permanently
  true and **we never actually auto-started ShadowMount** — it only ever ran because the user
  autoloads it from Payload Manager. Corrected in `payload_bundle.h`, `/api/helpers` and the boot
  notification. (`/api/sources` and `/api/health` were missed — see 3.24.4.)
- **etaHEN's jailbreak IPC port is reported, never spoken to.** `/api/helpers` gained
  `etahen_ipc_open` / `etahen_ipc_port` (9028). Upstream documents it as localhost-only; on this
  build it answers from the LAN too. It is a channel that jailbreaks processes and its command set
  is unknown, so we report it and stop there.
- **`?bigapp=0` A/B probe on the in-process installer.** `install_by_package_inproc` unconditionally
  called `sceSystemServiceGetAppIdOfRunningBigApp()` first, on the strength of a comment claiming
  the working reference implementation does the same. Reading etaHEN's GPLv3 source shows it does
  **not**: `DirectPKGInstaller.cpp` never calls it; the symbol is used in its daemon/jailbreak code
  only. It is now a parameter (`install_by_package_inproc_ex`) because it is the leading suspect for
  why subtype=7 (add-on) BGFT tasks can be created but subtype=6 (game) ones cannot — announcing a
  running big app immediately before an APP install plausibly steers ShellCore into
  `applyPatchPrimarySlot`, the call that errors in the failing trace and that an add-on never
  touches. **Default unchanged (`announce_bigapp = 1`)**; only the probe passes 0.
- **Helper autostart waits for the port instead of trusting Payload Manager.** "started" used to
  mean only that Payload Manager *accepted* the request. It now polls the helper's port for up to
  12 s and reports `started` / `started-but-port-silent` / `launch-failed` / `write-failed`, so the
  status is observed rather than assumed — every downstream diagnosis had been resting on that claim.
- **`autostart_wait_until_ready()` runs once per boot.** It is called from both the tile path and the
  helper path, and without a guard the full user-configured delay was served *again* on the second
  call — a console with a missing tile waited it out twice before any helper was touched.

---

## [3.24.0] — 2026-08-23 · "The console does tell us, and PlayGo is the installer" `[VERIFIED]`

Two long-standing beliefs in this codebase were tested and both were wrong.

### The console DOES announce suspend — we were never listening

`server.c` asserted, in a comment, that *"a PS5 payload gets no suspend notification"*. **False.**
The firmware publishes power state into four **named kernel event flags** any process can open by
name, and the proof was already on this console: `shadowmountplus.elf`, **a payload we ship
ourselves**, imports `sceKernelOpenEventFlag`/`PollEventFlag` and logs
`[SHELLFLAG] monitor started (4/4 flags opened)`.

Added `power_watchdog()` — **observe only**. It opens `SceSystemStateMgrInfo`,
`SceSystemStateMgrStatus`, `SceLncUtilSystemStatus`, `SceShellCoreUtilAppFocus` and polls them
every 200 ms, logging timestamped deltas and state transitions to a ring buffer readable at
`GET /api/power`. Live on first boot: **4/4 opened**, `SceSystemStateMgrInfo = 0x00000003000003e8`
→ `STATE=WORKING(1000) trigger=0x0003` — byte-identical to ShadowMount's own reading.

It stops nothing and writes nothing, on purpose. Its only job is to measure the one number nobody
has: the **runway** between the first pre-suspend edge and power-cut. Acting on that edge before
the runway is known is a coin flip with a kernel panic on the losing side, and the manual button
already works.

Two rules are load-bearing and documented in the code: poll with **wait-mode OR and clear
nothing** (a clear-mode would consume power-transition bits out from under ShellUI mid-shutdown),
and **a negative return means the out-pattern was not written** (treating it as zero invents
transitions that never happened).

API shape taken from ShadowMountPlus (GPL-3.0, `src/sm_shellcore_flags.c`); this project is GPLv3.

### The install blocker is decoded — and we are calling the RIGHT API

`0x80B2116F` — unsolved since the engine manual was written — decodes via etaHEN's own embedded
error table to **`SCE_PLAYGO_ERROR_CORE_INVALID_SLOT`**. Not a credentials error. The manual's
§2.2 conclusion ("we are not blocked on privilege") was right; the search was just looking in the
wrong place.

Then we watched a **real install happen**, passively, by attaching to etaHEN's klog on `:9081`
while a DLC installed end to end, and captured what the system does internally:

```
scePlayGoCoreGetRawContentInfo(uri = http://.../X.pkg)  -> 0x00000000
[PlayGoCore][Uninstall]     (<content id>)              -> 0x00000000
[PlayGoCore][DbgInstall]    (<content id>)              -> 0x00000000
     BGFT Task ... [type=1,subtype=7,slot=00] : registered
[PlayGoCore][CreateRequest] (<content id>, 0)           -> 0x00000002
[PlayGoCore][RequestInstall](#2, <content id>)          -> 0x00000000
     prepare -> transfer -> install, state=7 error=0x0
```

**CORRECTED SAME DAY.** The first reading of this trace was that etaHEN drives PlayGoCore
directly and we call the wrong API. That is **wrong**. etaHEN's daemon imports exactly two
installer symbols — `sceAppInstUtilInstallByPackage` and `sceAppInstUtilInitialize` — and its own
GPLv3 source calls the first at `DirectPKGInstaller.cpp:1204`. The PlayGoCore chain above is the
**interior** of that one call, logged by the ShellCore side of the IPC. No PlayGoCore library even
exists on the console.

So we call the correct and only public entry point, and the structs we pass match etaHEN's byte for
byte — including the zeroed `PlayGoInfo`, which etaHEN also zeroes and still succeeds with.

**The discriminator is the caller's environment.** The process that succeeds runs at authid
`0x4800000000000006`; we use `0x4801000000000013` in-process or borrow `0x4800000000000010`. We
have never tried `...006`. That is the next experiment.

One thing that makes this safe to iterate on: `0x80B2116F` is a **pre-BGFT refusal** — no task is
registered, so a failed attempt cannot wedge the install lane, dirty `app.db`, or leave a
`bgft.db` row.

Full trace preserved at `research/klog-working-install-2026-08-23.txt`, analysed in
`research/install-call-chain.md`. Verified the observed install really landed: `bgft.db` 1026,
error 0x00000000, 1376256/1376256.

### Fixed — ShadowMount auto-start has never actually worked

The bundle entry used port **9021** both as ShadowMount's health probe and as its
"is it already running?" de-duplication test. **9021 is elfldr**, the ELF loader, which is always
running — so the check was permanently true and we never launched ShadowMount. It only ever ran
because the user autoloads it from Payload Manager.

ShadowMount announces its real listener in its own log:
`[API] HTTP/JSON ready: http://127.0.0.1:10101/api/v1 (v1)`. Now 10101 everywhere — the bundle
de-dup, `/api/helpers`, and the ready toast, which was reporting a green light for the wrong
process.

### Other

- **Helper start is verified, not assumed.** "started" used to mean only that Payload Manager
  accepted the request. It now waits up to ~12 s for the helper's port to answer and reports
  `started-but-port-silent` when it does not.
- **The autostart delay is served once per boot.** It was called from two paths with no guard, so
  a console whose tile is missing waited the full user delay twice before any helper was touched.
- **`/api/helpers` reports `etahen_ipc_open`.** etaHEN's jailbreak IPC on `:9028` is documented
  upstream as localhost-only; on this build it answers from the whole LAN (measured from another
  machine, with 9021 and 10101 refusing as controls). We report it and never speak to it — the
  command set is unknown and it is a channel that jailbreaks processes.

### Not done, deliberately

Acting on the pre-suspend edge (A2) waits for A1's measurement. And etaHEN's own mechanism — a raw
14-byte inline detour on a function inside SceShellUI — is **refused outright**: a second
uncoordinated patcher in the same process as etaHEN's live hooks, running while ShellUI tears down
for rest, is the highest-panic-risk idea surveyed, and it buys nothing the event flags do not.

---

## [3.23.0] — 2026-08-22 · "Stop believing the comments, measure the console" `[VERIFIED]`

A second measured pass: 8 parallel audits over the whole app, 115 findings, the top 8 put through
adversarial verification. Four survived, four were refuted — including two of the loudest.

### Fixed — a 2.09 s dead-port probe on card taps

`CheatRunner.alive()` had no cache. CheatRunner's `:9999` is not running, and a refused TCP connect
costs the full ~2.03 s on Windows. Every `/api/mods/<tid>` for a title with **no** local cheat file
paid it, and so did `/api/health` once every ten minutes when `running_title()`'s negative memo
expired.

Measured: `/api/mods/CUSA00050` **2090 ms → 33 ms**. A title that *does* have mods (`CUSA00503`)
still returns all 38.

The cache is keyed off the **result**, not an exception — `_call()` swallows every error and
returns `{"ok": False, "unreachable": True}`, so a `try/except` memo would never have armed. It is
a short symmetric TTL rather than a long negative memo, so someone who starts CheatRunner is picked
up within 15 s instead of being told it is offline for ten minutes.

### Fixed — "Fix install-port conflicts" stopped the two things it must not

`CONFLICT_PROCS` listed `nanodns.elf`, `ftpsrv.elf` and `klogsrv` as Elf Arsenal's leftovers. They
are not. `/data/pldmgr/autoload.txt` on this console reads
`!5000 / nanodns.elf / ftpsrv.elf / kstuff-lite_v1.10.elf / shadowmountplus.elf` — they are Payload
Manager autoload entries and come back every boot. **This repo already recorded that correction in
a previous changelog entry and it was never propagated to the code.**

On a healthy console those two were the *only* matches, so the button stopped exactly what cannot
contend for `:12800` and nothing that can. `ftpsrv.elf` owns `:2121` — the FTP this app reads
`app.db` and `bgft.db` over and deploys the ELF through.

The same tuple also answered "is Arsenal running?", so `_arsenal_running()` returned **True** on an
Arsenal-free console. Every install then ran the full Arsenal preflight: an FTP fetch plus two
refused `:6969` connects, **~4.1 s of dead time per install**. Verified: old tuple matched
`['ftpsrv.elf','nanodns.elf']` → True; new tuple matches nothing → False, and the sweep is now a
genuine no-op with both payloads left running.

### Fixed — the rest-mode text described a console that no longer exists

The behaviour is **unchanged and deliberately so** (see below). Three strings were wrong: the
home-page tooltip and the `confirm()` dialog both named Elf Arsenal and GarlicSaves — neither of
which runs here — and both **omitted etaHEN, which the sweep does stop**, taking the install lane
and FTP with it. The Settings tooltip was already correct and was left alone.

### Why etaHEN stays in `REST_STOP`

etaHEN ships its own rest-mode switches — `Util_rest_kill`, `Game_rest_kill`,
`Rest_Mode_Delay_Seconds`, `disable_toolbox_auto_start_for_rest_mode` in `/data/etaHEN/config.ini`
— and self-heals its daemons. That proves upstream treats killing them across a suspend as a
*supported* mode. **It does not prove leaving them alive is safe**, and nobody has tested that on
12.70.

The asymmetry decides it: being wrong costs a kernel panic on suspend, the exact failure that
already cost a console rebuild. Being conservative costs one reload from Payload Manager — which
the user already performs every boot, because etaHEN ships with port 0 and is never auto-started.
The controlled experiment that would settle it (`/api/rest/prepare?self=0` with `"etahen"`
temporarily removed, then rest and wake) is recorded in the code comment.

### Performance

- **Drawer banner two-staged.** `.dbanner` is 160 px tall and was loading the 512×512 original —
  461–537 KB, ~1 MB of decoded RGBA — synchronously on every card tap. It now paints from the
  thumbnail already decoded in the card the user just tapped, and swaps in the original only once
  it has decoded. The `<img>` is reused instead of rebuilt, so reopening a title re-decodes nothing.
- **Queue rows no longer rebuilt while the drawer is shut.** `renderQueue()` runs every 1.2 s
  during an install and rebuilt every row each time, visible or not. The pill, ring and count still
  update; `dockShow()` paints the rows on open.
- **`/api/health` probes memoised for 5 s.** Every poll opened fresh TCP connections to the console
  for `ping()` and `ftp_ok()`, every 6 s, from every client — landing on the console's
  single-threaded accept loop while it serves the page being scrolled. Memoised in the *handler*,
  not in `ping()`/`ftp_ok()`, so the install preflight still gets a live answer.
- **`:6969` pokes gated on the port being open** — 2.03 s each, result discarded, on a console with
  no Arsenal.
- **`/api/network/scan`** no longer blocks on a 254-thread sweep; bounded to 2.5 s.

### Security

- **Origin guard.** Both servers answer `Access-Control-Allow-Origin: *` with no auth — fine for
  someone already on the LAN, where Payload Manager on `:8084` will load an arbitrary
  kernel-privileged ELF for anyone who asks. What is *not* fine is a page on the public internet:
  any site the user visits, in any browser on the network, could fire requests at the companion.
  The discriminator is not "same origin" — the console legitimately calls this PC cross-origin —
  it is whether the origin is a **private-range IP literal at all**. Verified: `https://evil.com`
  → **403**, `http://10.0.0.99:8710` → 200, no-Origin (curl) → 200. Applied to every POST and to
  `GET /api/rest/prepare`, which a hidden `<img>` could otherwise have fired.
- **`esc()` now escapes single quotes.** It is used inside single-quoted attributes; a game name or
  peer string containing `'` could break out.
- **Peer-supplied art URLs validated** before reaching an `img src` — `icon_url`/`thumb_url` come
  from another machine over the LAN. Anything that is not a plain http(s) URL falls back to our own
  `/icon/` path.

### Refuted — measured and deliberately NOT done

- **`/api/mem/write` as "critical".** The premise was that it is the primitive the cheat engine is
  built on. It is not: repo-wide there are **zero callers** — the engine reaches `mem_write()`
  in-process. Gating it while `:8084` loads arbitrary kernel payloads reduces attacker capability
  by zero.
- **`/api/engine/install-url` arbitrary write.** Its `dest` parameter has **no producer anywhere in
  the shipped system**, and the basename is already sanitised. Moving it to POST would break the
  install lane for every companion not updated in lockstep — including the 3.21.3 host on the LAN.
- **Removing `"etahen"` from `REST_STOP`** — see above.
- **The 13-endpoint unauthenticated-GET wall** — the right shape *if* ever done, but one change
  touching thirteen live paths on a daily-driver console, for a threat model `:8084` already blows
  open.

---

## [3.22.1] — 2026-08-22 · "The console was never using the thumbnails" `[VERIFIED]`

A measured performance pass on the PS5 browser: eight parallel audits, six findings put through
adversarial verification, five refuted. The headline is not a clever optimisation — it is that the
optimisation shipped in 3.21.0 never reached the console at all.

### The thing that mattered — covers were still full-size PNGs

**The PS5 does not use the console's API.** The page is served by the console, then `resolveApi()`
reads `on_console:true`, calls `/api/companion`, and re-points `API` at a **PC companion**. From
that moment every `/api/*` call and every cover comes from a PC over the LAN.

The console's own `/api/library` sets `thumb_url` on 112 of 113 titles. The PC companion's
`/api/library` set it on **0 of 113** — `build_library()` never added the field, and the peer-merge
branch copied `icon_url` while dropping the `thumb_url` the peer advertised. `iconUrl()` prefers
`thumb_url`, found none, fell past `icon_url`, and landed on the full-size PNG. Both halves looked
correct in isolation, which is why this survived two releases.

Measured over a 20-title sample on the live host:

| | per cover | whole library |
|---|---|---|
| before (PNG) | 370,335 B | 39.2 MB |
| after (WebP) | 19,973 B | 2.11 MB |
| | **18.5x** | |

Decoded bitmap held in the DOM for a ~50-card window drops from ~50 MB (512x512x4) to ~19.5 MB
(320x320x4). That is the memory pressure that made covers evict and re-decode while scrolling.

`iconUrl()` now also **derives** `/thumb/<TID>.webp` from the API host when a companion is too old
to advertise it — verified live against a 3.21.1 peer, which served a 7,466-byte WebP for a title
whose `/api/library` carried no `thumb_url`. Never against the console, which has no `/thumb` route
(verified: 404).

### Covers step down instead of vanishing

`onerror="this.remove()"` meant one miss — no WebP support, no `/thumb` route, a thumbnail that
failed to generate — silently deleted the artwork and left a letter. Now: thumbnail → full-size
original → initials.

### WebP is detected, not assumed

WebP arrived in Safari 14 / WebKit 610. Nothing observable from outside the console proves the PS5
browser is newer, and being wrong would blank every cover. A 68-byte 2x2 WebP is decoded at boot and
`iconUrl()` skips `thumb_url` entirely if it fails, repainting the grid once. The probe image was
round-trip tested before shipping.

### Scrolling inside a panel ran the grid virtualizer

`virtOnScroll` is bound to the **document** in the capture phase, because scroll does not bubble —
so it also fired for scrolling inside the detail panel, and `virtRender()` then read `scrollTop`,
`offsetTop` and `clientHeight`, forcing a synchronous layout every frame of a scroll that cannot
have moved the grid. It now returns immediately while an overlay is open, and resyncs on close.

### Other measured fixes

- **`/api/sources` took 760 ms** to return 564 bytes: it probed ShadowMount's port 9021 from the
  LAN, and that port binds **loopback-only**, so the probe could only ever burn its full timeout —
  and the on-console answer overwrote the value anyway. The two probes that can succeed now run in
  parallel. Measured **760 ms → 12-28 ms**, on the boot path.
- **JSON responses gzip** when the client asks: `/api/library` **186,878 → 21,342 B** (8.8x). This
  is on the real LAN wire; the page shell is not, so the console C server is deliberately left
  alone (and `Content-Encoding` would collide with the install lane's 206/Range responses).
- **Cover art now carries a validator.** `/icon` was `max-age=86400` with no ETag, so every drawer
  icon was re-fetched in full daily. Both `/icon` and `/thumb` are now
  `public, max-age=604800, immutable` with an ETag; revalidation returns **304, 0 bytes**.
- **A 40px-blur `box-shadow` was animated on card hover** — repainting a region larger than the card
  once per card as the cursor sweeps a row. Removed from the transition; it snaps.
- **`content-visibility:auto` was still on the cover `<img>`**, eleven lines under the comment
  explaining why it had been removed from `.card` for making covers flash. Gone.
- **Every grid render built the window twice.** The 10px scrollbar made the probe card measure
  ~2px taller than the settled grid, so `VIRT.remeasure` fired and rebuilt everything one frame
  later. Fixed at the cause with `scrollbar-gutter:stable` + `overflow-y:scroll`, not with a
  threshold — the delta was measured at 1.81px at 1920 but 2.14px at 1280, so any threshold would
  have let the bug survive at the smaller size.
- **Four intervals ran forever**, whether or not anyone was looking. They now pause on
  `visibilitychange` and re-arm with an immediate refresh; storage polling drops 15s → 60s on the
  console. The queue poll's cadence is untouched and still only fires while a task is live.
- **The drawer banner** decoded a 512x512 PNG synchronously on every card tap; now `decoding="async"`.

### Automatic device profile

`PROFILE` uses three separate signals, each only for what it can prove: `onConsole` (a server fact
from `/api/health`, captured before `resolveApi` clears it), `ps5` (UA — cosmetic gating only), and
`webp` (a real decode test). On the console, `backdrop-filter` is dropped from the two drawer
buttons — they sit over the banner while the drawer runs a transform+opacity transition, so the blur
was recomposited every frame of every open and close.

### Fixed — the console could not rank companions

A companion's version arrived **only** inside a federation document read during a library merge, so
a warm peer cache meant it never arrived. `/api/pcs` reported every PC as version `""`, `ver_cmp()`
had nothing to compare, and selection fell back to "most recently seen" — which is how the console
picked an *older* companion right after a restart. The PC now states its version on every
registration. Verified: `{'10.0.0.76': '3.22.1'}` → chosen.

### Deliberately NOT done (each was measured and rejected)

- **gzip on the console C server.** The PS5 loads the shell over **loopback** — compression saves
  nothing there, and `Content-Encoding` is incompatible with the `206`/`Accept-Ranges` responses the
  install lane depends on.
- **Lowering `VIRT.pad`.** Measured: pad 4→1 moved steady-scroll median only 2.8 → 2.3 ms while
  cutting the recycle overlap budget from 4339 px to 1972 px against a 6410 px range — dropping
  routine flicks into the full-teardown path, which is the visible cover-reloading this release is
  trying to eliminate.
- **Threading the console's accept loop, or keep-alive on it.** The loop is an implicit lock across
  every API handler, and `sceAppInstUtilInitialize` must run exactly once. A persistent connection
  pins it — that failure already took the whole API down once, which is why the 8s `SO_RCVTIMEO`
  exists.
- **Pointing `iconUrl()`'s final fallback at `/thumb`.** The console has no such route (verified
  404); it would blank every cover whenever no PC is reachable.

---

## [3.21.3] — 2026-08-22 · "A PS5 DLC is a PKG, not a backup" `[VERIFIED]`

Grouping PS5 add-ons onto their game's card (3.21.2) exposed two bugs that had never been reachable
before, because before it no PS5 add-on was ever offered for install.

### Fixed — an add-on inherited its game's container format and was routed as a backup

`is_backup_item(item, game)` falls back to **the game's** format when the item does not state one.
That is right for the base item and wrong for everything else. A PS5 game arrives as a `.ffpfsc`
ShadowMount container, so the moment its updates and DLC were grouped onto that card, every one of
them inherited `ffpfsc` from the parent and was declared a backup.

They were then sent down the mount/peer-delegate lane, which files a task as `kind:"backup"` — and
that cost them their real kind. The install watcher saw `kind:"base"`, took the base-game branch,
and waited for a full-size `app.pkg` that a 1 MB DLC can never produce. After the 240-second
timeout it reported:

> Console rejected this package — delivered in full, but it won't install (bad dump)

**The install had actually succeeded, completely.** Measured on Borderlands 4's *Firehawk's Finery*:
`bgft.db` status `1026`, `error_code 0x00000000`, `1114112/1114112` bytes, and `addcont.db` holding
the registration under its real name. Only the report was wrong — the user would have concluded PS5
DLC installs were broken and reinstalled them repeatedly.

An add-on is a PKG. Always. `is_backup_item()` now says so before consulting the parent game.
Verified against the old implementation side by side: **exactly two verdicts change** (PS5 DLC and
PS5 update), and PS4 base/update/DLC and every backup container are bit-for-bit unchanged.

After: lane `install`, source `peer`, **`playable` in 15 seconds** instead of four minutes to a
false failure. Confirmed on device with a second, freshly installed DLC (*Ornate Order Pack*,
`1026`, `1245184/1245184`, registered).

### Fixed — every PS5 package was reported as corrupt

`pkg_completeness()` tested only PS4's fPKG magic `7F 43 4E 54` (`CNT`). A PS5 package is a
different container with magic `7F 46 49 48` (`FIH`), so all of them came back
`{"complete": false, "confident": true, "reason": "bad PKG magic (not a .pkg)"}`.

`confident: true` is the level that is allowed to **block** an install. It did no harm only because
`integrity.on_local_corrupt` defaults to `"warn"` — set it to `"block"`, which the setting exists to
allow, and not one PS5 add-on would install. The verdict also decorated the false error above.

PS5 packages now return **inconclusive** (`complete: true, confident: false`), which is the honest
answer: the PS4 offsets this function reads mean nothing in that container, so it must not pretend to
judge it. Verified on a real file of each type — PS5 inconclusive, PS4 still fully validated.

---

## [3.21.2] — 2026-08-22 · "The peer add-on table was silently full" `[VERIFIED]`

PS5 games showed one card each (3.21.1's companion fix worked), but on the console **not one of the
54 PS5 cards carried a single update or DLC**, while PS4 cards showed theirs normally.

### Fixed — `PEER_ADDON_MAX` was a hard 32 and dropped the rest without a word

Add-ons a PC holds for a game the console already has are parked in `g_peer_addons[]` and spliced
into the card after the peer block is built. That array was a fixed 32 entries, and the write site
simply skipped anything past it — no log, no error, no truncation marker.

Peers are merged one after another. The first peer alone held **46** PS4 titles carrying a patch or
DLC. It filled the table, and every title merged after it got nothing. The PC holding the PS5 games
is merged second, so *all four* PS5 titles with add-ons landed past the end. Measured: **50 slots
needed, 32 available, 18 dropped.**

It reads as a PS5 grouping bug and is not one — it is a capacity limit that happens to fall on
whatever is merged last.

The table now grows on demand. Nothing is allocated until a peer actually has add-ons, and a
`PEER_ADDON_HARD_MAX` of 1024 exists only so a broken peer cannot make the console allocate without
bound.

After, on the console: PS5 **0 → 1 update and 4 DLC**; PS4 **33 → 44** cards with updates and
**12 → 13** with DLC — those PS4 add-ons had been dropped too. Card counts unchanged (59 PS4 / 54
PS5), zero duplicates, library JSON still valid.

---

## [3.21.1] — 2026-08-22 · "Scrolling stops throwing the library away" `[VERIFIED]`

3.21.0 made console scrolling **worse** — covers visibly reloaded on the smallest scroll. That was a
regression I introduced, and it came from fixing the wrong layer.

### Fixed — the grid was rebuilt from scratch on every row crossed

`virtRender()` did `grid.innerHTML=""` and rebuilt the whole window each time the visible range
changed. Every card *and every `<img>` inside it* was destroyed and recreated, so a nudge of the
scroll threw away 24-112 images and asked for them all again — cached or not, a fresh `<img>` still
costs a fetch check, a decode and a paint.

3.21.0 then cut `VIRT.pad` from 6 rows to 2. That reduced how many cards were built, but it also made
the window *tighter*, so the full rebuild fired far more often. Fewer cards, rebuilt constantly —
worse, not better.

The window shifts contiguously while scrolling, so the overlap between old and new is nearly total.
`virtRender()` now **trims the ends and splices in only what is genuinely new**; existing cards, and
the decoded images inside them, are never touched. A jump with no overlap (search, filter, a fling)
still falls back to a clean rebuild. `pad` is back to 4 now that a shift is cheap.

Measured on the console, scrolling two rows: **36 of 36 images survived, 12 new** — previously all
36 were destroyed and recreated.

`content-visibility:auto` was also removed from cards. It skips rendering offscreen and then has to
paint from nothing on entry, which reads as flashing. With recycling, offscreen cards simply are not
in the DOM — strictly better than asking the browser to skip them.

### Added — card covers are thumbnails, not 512x512 originals

The source art is 512x512 PNG averaging ~280 KB, which every browser decodes to a **full 1.0 MB of
RGBA** — for a tile drawn about 220 px wide. Thirty visible cards meant thirty megabytes of decoded
bitmap. That, not rendering, is what the console was struggling with.

The companion now generates a 320 px WebP once per title (`/thumb/<TID>.webp`, cached in
`.cache/thumbs`, Pillow was already bundled) and advertises `thumb_url` over federation. Measured
across six real covers: **1,929,928 -> 105,166 bytes, 18.4x smaller**, ~0.04 s to generate each,
generated once and then served from cache. Cards use the thumbnail; the detail panel still gets the
full-resolution original.

The console has no image library, so it cannot resize its own art. It now points `thumb_url` at the
best companion (`best_pc_base()`, ranked newest-build-first exactly like `/api/companion`), which
generates the thumbnail on demand — pulling the icon off the console over FTP first if it has never
seen that title. Verified: 112 of 113 console titles now carry a thumbnail URL.

### Fixed — a blanket cache header froze the UI at the old version

3.21.0 gave every response `max-age=604800`. That included `index.html`, so loading a new ELF left
the console running the **old UI for a week** — the browser never asked again. Caught immediately:
the console kept reporting `pad:2` and full-size icons after a build that had neither.

`send_file()` now picks by content type: images and fonts keep
`public, max-age=604800, immutable`; everything else gets `no-cache, must-revalidate`. The ETag means
an unchanged shell still costs only a 304. Verified live — `index.html` revalidates,
`assets/logo.png` and `/icon/*.png` stay immutable.

## [3.21.0] — 2026-08-22 · "PS5 games get one card, and the console stops rendering the whole library" `[VERIFIED]`

### Fixed — every PS5 update and DLC had its own library card

A PS5 game showed up several times: the real game, plus extra cards named after content ids like
`UP1001- 00-FIREHAWKSFINERY0`. Across the whole PS5 set: **0 entries had updates, 0 had dlc**, while
PS4 had 44 and 15. Two independent causes, found by reading bytes rather than guessing:

**1. `parse_pkg()` returns `None` for every PS5 package.** A PS4 fPKG starts with `7F 43 4E 54`
(`CNT`); a PS5 package starts with **`7F 46 49 48`** (`FIH`) — a different container. The
entry-table reader bails at the magic check, so there is no `param.sfo`, no `CATEGORY`, no `TITLE`
and no version. (Its metadata is not recoverable another way either: the SFO magic appears nowhere
in the first 4 MB of four different PS5 packages.) With no CATEGORY the item fell to
`filename_fallback()`, which hard-defaults to `kind="base"` and names the card after the content id.

`filename_fallback()` now classifies PPSA titles from the filename, which is deterministic:
a version triple (`-v01-000-000`) means an **update**; anything else is **dlc**, because a PS5 game
arrives as a ShadowMount `.ffpfsc` backup and never as a `.pkg`. **This function is only ever
reached when `parse_pkg` fails**, which for PS4 essentially never happens — and the new branch is
gated on `PPSA`, so a CUSA title cannot enter it.

**2. The game and its add-ons were built into two different lists.** `.pkg` items are grouped by
title into `by_title` (`lane:"install"`); `.ffpfsc` backups are appended separately from `mounts`
(`lane:"mount"`). A PS5 game with a backup *and* add-on packages therefore produced **two cards for
one title id**. They now merge: the backup becomes the card's base, the packages stay as the
updates/DLC they now classify as, and the card keeps the real game name. Duplicate identical
containers (the same backup reaching us twice) are dropped.

Verified before changing anything that **no PS4 title has this split** — 4 titles were affected, all
PS5 — so the merge cannot move the PS4 path. Confirmed after: PS4 is byte-identical at 59 entries,
base 56, updates 44, dlc 15, update_only 3, zero duplicate cards.

End-to-end on a synthetic library mirroring the real filenames — **4 games, 4 cards**:

    PPSA01494  Borderlands4              base=backup  + 3 dlc
    PPSA07064  Lords of the Fallen       base=backup  + 1 dlc
    PPSA15656  Disney Epic Mickey        base=backup  + 1 update + 1 dlc
    CUSA14409  Castle Crashers (PS4)     base + update, lane=install     <- unchanged

### Fixed — the console rendered far more of the library than it showed

Scrolling lagged on the PS5 while the PC was fine. Three multipliers stacked:

- **`VIRT.pad` is measured in ROWS**, so the overscan multiplied by the column count: 6 rows × 7
  columns is ~112 cards built for ~21 visible. Now 2 rows — measured on the console: **24 cards per
  window instead of 48 here, and ~5× fewer on a TV grid**.
- **Four `backdrop-filter: blur()` regions per card** (`.plat`, `.badge`, `.tid`, `.sz`) — roughly
  450 blur regions per window, the most expensive single property on this GPU. Removed from cards
  (backgrounds darkened slightly so they stay readable); panels and buttons keep theirs, there are
  only a handful on screen. Measured after: **0 backdrop-filter regions on cards**.
- **Covers were eager and uncached.** Every card image is now `loading="lazy"` and
  `decoding="async"`, and cards carry `content-visibility:auto` + `contain`, so an offscreen card
  costs no layout or paint.

### Fixed — the console re-downloaded every cover on every rebuild

`send_file()` sent **no `Cache-Control`, no `ETag`, no `Last-Modified`**, so a browser had no
validator and reused nothing — each grid rebuild refetched every ~360 KB cover through an accept
loop that is **single-threaded** and shared with the install engine and the cheat engine. It now
sends `Cache-Control: public, max-age=604800` and a size+mtime `ETag`. Verified live on
`/assets/logo.png` and `/icon/<TID>.png`.

`build_library_json()` also hardcoded `"has_icon":true` for **every** app.db title without ever
stat()ing the file. The UI only emits an `<img>` when that is set, so any title without art fired a
request that 404s — one per card, through the same serial loop. It now calls `icon_exists()`.

### Note — Casita must run 3.21.0 for the PS5 grouping to appear on the console

All the PS5 games and their add-ons live on the second PC. The console renders whichever companion's
library it is given, so the grouping fix has to run **there**. The console prefers the newest
companion (v3.20.3, I30), so once Casita is updated it takes effect immediately.

## [3.20.3] — 2026-08-22 · "The console was driving an old PC, and the DPI never got cleaned" `[VERIFIED]`

Three separate faults behind *"second install says dpi wedged"*, *"it activated Elf Arsenal again"*
and *"the version shows 1.1.1, then 3.19.0, then 3.20.2"*. They share one root: **the console was
using the wrong companion.**

### Fixed — the console picked its companion by luck, and could pick an OLD build

`/api/companion` sorted the registered PCs by `last_ms` — most recently seen. Every companion
re-announces every 8 s, so which one the console used was effectively a coin flip. With two PCs on
the LAN it was picking **Casita on 3.19.0** over this PC on 3.20.2:

    {"urls":["http://10.0.0.72:8710","http://10.0.0.76:8710"],"url":"http://10.0.0.72:8710"}

That is not cosmetic. Driving an old companion means driving an old **engine** — and 3.19.0 still
has the Elf-Arsenal-shaped readiness probe (so every install reads **"dpi wedged"**) and the
hardcoded `payload_path("arsenal")` recovery (which **relaunches Elf Arsenal**). Pressing Install on
the PS5 therefore resurrected Arsenal and restarted the :12800 port war. Every symptom reported,
from one cause.

Companions are now ranked by **version first** (`ver_cmp()`, numeric per component — a string
compare puts "19" after "2"), then by recency. The version is captured from each peer's
`/api/federation` into `pcpeer_t.ver`, and `/api/pcs` now reports `name` and `version` so it is
visible rather than guessable. Verified live: with Casita on 3.19.0 and this PC on 3.20.3, the
console now returns `"url":"http://10.0.0.76:8710"`.

**Casita still needs updating by hand** — we have no self-update path and no file access to it.
Until then the ranking keeps it out of the way.

### Added — post-install DPI cleanup, the etaHEN equivalent of the Arsenal mechanism

Elf Arsenal had no cleanup endpoint; the mechanism there is `_dirty_hosts`, which makes the *next*
install reload the daemon first (I22). etaHEN cannot be reloaded — its DPI lives inside the daemon
that also serves FTP and klog — but it exposes **`POST /cleartmp`**, which drops what the transfer
left behind (`SUCCESS: Deleted N temporary files.`).

`Ps5Bridge.dpi_cleanup()` now runs at **both** job-completion sites, success or failure, so a queued
run starts each job on a clean daemon instead of inheriting the last one's leftovers. Because it
leaves the host genuinely clean, the dirty mark is cleared rather than forcing a pointless reload
attempt before every subsequent install.

### Fixed — the console-local lane still spoke Arsenal's protocol

`dpi_install_url()` in the ELF POSTed Arsenal JSON to `127.0.0.1:12800` unconditionally. Against
etaHEN that endpoint does not exist, so every console-local (USB / already-on-console) install died
with **"no reply from the install daemon"** — reproduced on hardware. It now sniffs the host the
same way the companion does and speaks multipart `/upload` to etaHEN. Its success test was also a
bare `strstr(reply,"ok")`, which matched the word "ok" anywhere in an *error* body; it now requires
`SUCCESS` (etaHEN) or a real `res:0` (Arsenal).

After the fix the same install returns the console's genuine verdict instead of a transport lie:
`SCE_NP_DRM_CONTENT_ERROR_UNSUPPORTED (0x80F00003)` — correct, because that title is a ShadowMount
`.ffpfsc` container and etaHEN's DPI installs `.pkg` only.

### Fixed — the version shown was three different numbers

`v1.1.1` was a hardcoded literal in the markup, only corrected once `/api/health` answered — and
that answer came from whichever companion was picked, which could be an old one. So the console
showed 1.1.1, then 3.19.0, then the real version. The tag is now painted from the build's own
`APP_VERSION` on `DOMContentLoaded`, and health can only *raise* it, never replace it with an older
companion's number (`verNewer()`).

### Verified on hardware

- **Three consecutive install → uninstall → install cycles of the same title: all `playable`.**
- **A queue of four installs: all `playable`**, with four clean bgft rows landing inside 45 s —
  `CUSA02365 107806720`, `CUSA14409 227540992` ×2, `CUSA11740 532217856`, every one `status 1036`.
- Install host stayed `ready=True live=live` throughout; Elf Arsenal never started.
- Console UI: one version everywhere (`verTag`, `APP_VERSION` and health all 3.20.3), 113 games,
  48 cards.

## [3.20.2] — 2026-08-22 · "Nothing happened when you pressed Install — because nothing was there" `[VERIFIED]`

Reported as *"pressed install and nothing happened — not downloading, not installing"*, with the
conclusion that the etaHEN engine could not work and we should revert to Elf Arsenal.

**The engine was never the problem.** Two separate UI/serving bugs made the library invisible, so
there was no card to press. Both are fixed; no engine code changed. etaHEN's capability was proven
before and after by sha256.

### Fixed — the grid virtualiser could render zero cards, silently

`renderGrid()` measures the card grid using a **single probe card**, and that measurement is
unreliable: a lone card in an `auto-fill` grid makes `getComputedStyle` report **one column**, and a
card whose cover has not sized yet measures ~46 px. Observed live: `VIRT.cols = 1`,
`VIRT.cardH = 60` against a real grid of 6 columns × 325 px.

Those numbers drive the visible window, and they drove it negative — `last = -6`. Then:

- `renderGrid()` clears `innerHTML` before calling `virtRender()`;
- `virtRender()` computed an empty window and **cached it** in `VIRT.first/last`;
- its memo, `if(first===VIRT.first&&last===VIRT.last) return;`, then short-circuited every later
  call — while the grid was empty.

Result: a grid with correct padding (6840 px of it) and **zero cards**. No exception, no
"no titles match your filters" banner, `state.games = 112`. The library simply was not there, and
neither was any Install button.

Three defects, three fixes: an empty window while games exist now falls back to a first screenful;
the memo will not short-circuit while the grid is empty; and the metrics are re-measured from a
**real** card on the next animation frame, re-rendering if they changed. Verified: 48 cards,
`cols=6 cardH=325`, correct through scrolling to the bottom and back.

### Fixed — the console's `/api/library` emitted invalid JSON

Separately, the on-console UI showed an empty library because `GET /api/library` returned a **parse
error at byte 12987**:

    "peer_url":"http://10.0.0.76:8710/li],"cheats":[]}

`merge_pc_library()` accumulated each add-on into a fixed `char bkt[3][2200]` behind a 600-byte
headroom guard — but one item can exceed 900 bytes (two 400-char escaped install keys plus the
URL), so it passed the guard and `snprintf` truncated **mid-value**. Worse, `bn[bi]++` ran even when
nothing had been written, so the comma logic still counted the item.

Items are now built whole and appended only if they fit entirely; `bn[bi]` counts only what was
really written; the bucket is 8192 (Jump Force alone has 21 DLC). `peer_addon_t.upd/.dlc` had the
same hazard — they are spliced verbatim into the document, so a truncated copy breaks the JSON for
*every* title — raised to 4096 and the copy now refuses to truncate rather than corrupt.

The main record append was already correct: it drops a whole title rather than write a partial one.

### Verified end to end, from the UI, on hardware

Clean console, etaHEN alone on :12800, no Elf Arsenal:

- Clicked the real **Install base** button in the drawer → `"Sent ✓ · in queue"`, queue badge 1,
  pill showing the game name. POST body carried
  `drive/mode/console/version/content_id/size/force` exactly as the UI builds it.
- `queued → submitting → transferring → promoting → playable`.
- **sha256 of the console's `app.pkg` vs the PC source: `84871d37…`, identical.** bgft
  `1036  227540992/227540992  err 0x00000000`, fresh timestamp; app.db registered; integrity `ok`.
- Console UI after the JSON fix: **110 games, 48 cards**, grid healthy, no horizontal overflow.
- Per-second process monitoring across the whole install: **not one process started or stopped.**
  Elf Arsenal never came back.

### Answering the question directly

No, we do not need to go back to 3.19.0, and etaHEN does have every capability required: it accepts
a URL, downloads it itself, installs through Sony's BGFT, and produces a byte-perfect result — the
hash proves it. What failed was our UI, twice, in ways that looked exactly like an engine failure.

## [3.20.1] — 2026-08-22 · "We were starting a kernel daemon nobody asked for" `[VERIFIED]`

A PS4 title installed under 3.20.0 (Castle Crashers) passed **every** check we have and then
crashed the game *and the console* on launch. A full audit of both lanes followed, then three
forensic measurements on the recovered console.

**The forensics eliminated two of the three candidates outright.** `bgft.db` held a single row for
`CUSA14409` at `2026-08-22T03:30:50Z`, `status 1036`, `227540992/227540992`, `err 0x00000000` — so
the install was genuinely real and complete (not our gate reading the previous install's files), and
there was exactly one submission (no duplicate job). What remained was the environment.

**And the "install does not work / it started Elf Arsenal again" report is now fully explained and
reproduced.** On 3.20.0, pressing Install ran this chain:

    dpi_install_ready()  POSTs a junk URL to /api/install  ->  etaHEN has no such endpoint
      -> probe always False  ->  "⏸ dpi wedged"
      -> recovery runs  ->  pm.payload_path("arsenal")  ->  WE LAUNCH ELF ARSENAL
      -> garlic / dpi.elf / tile-autoinst / klogsrv return, dpiv2 fights etaHEN for :12800

Both links are broken in 3.20.1, and it was verified live: with per-second process monitoring, a
full base-game install and a UI-shaped DLC install each completed without a single process starting
or stopping on the console.

### Fixed — 3.20.0 auto-started etaHEN, changing the console's jailbreak layer

3.20.0 gave etaHEN `port 12800` in the payload bundle, so our ELF launched it on every boot whenever
the port was free. **This console's own boot chain does not run etaHEN** — the user's
`autoload.txt` is `pldmgr → kstuff_lite → shadowmountplus`, with `etaHEN-2.6B.bin` present in the
folder and pointedly unlisted. etaHEN patches the kernel and ShellCore, so we were putting a second
kernel-touching daemon next to kstuff_lite that had never been part of a working boot — and the
first fake-signed PS4 title launched afterwards panicked the console.

A wrong byte in `app.pkg` makes a *game* crash. Panicking the whole console on launch of a
fake-signed title points at the fpkg/fself patch layer, which is exactly what changed.

**etaHEN is back to `port 0` — carried, never auto-started.** It still ships inside the ELF and is
written to `/data/pkg-mutant-shop/payloads/` every boot so the file is always the build we tested
against, but starting it is the user's decision. Changing the jailbreak layer must never be a side
effect of installing our app.

### Fixed — `HOST_UNKNOWN` silently meant "Elf Arsenal"

`Ps5Bridge.install()` fell through to Arsenal's JSON protocol whenever the host could not be
identified — and `dpi_host_kind()` returns UNKNOWN *precisely* when two hosts are fighting over
:12800 or one is restarting, which was the incident state. Sending Arsenal's request to etaHEN
resets the connection, and the substring classifier then read "reset" as *wedged* and re-POSTed.

Now the probe is re-run with `ttl=0` immediately before dispatch (the cached answer is up to 20 s
stale and the port changes hands inside that window), and UNKNOWN **refuses**: nothing was queued,
so the task is held with `dpi_host_unknown` and a message naming the fix.

### Fixed — four more Arsenal assumptions that survived the switch

- **`recover_dpi()` reported success having reloaded nothing.** With etaHEN it cannot reload
  anything and just confirms the host answers — but callers treated that as "healed", cleared the
  `_dirty_hosts` mark (making I22 a set that fills and empties with no effect) and re-POSTed on a
  false premise. The dirty mark is now only cleared on a genuine reload.
- **`last_install_error()` still read Arsenal's `last-install.json`** with no host check and no
  freshness filter — the same trap `last_install_via()` was already fixed for. It could report an
  hours-old Arsenal error as the verdict on an etaHEN install.
- **`ensure_install_host_safe()` (I21) was keyed on port ownership, not on Arsenal being alive.**
  It returned "ok" the moment etaHEN answered :12800 — on the reasoning that Arsenal "is not even
  running", which is exactly what was false during the incident. It now checks the process list.
- **etaHEN's `queued: false` was set and never read.** etaHEN validates a URL *before* queueing, so
  a rejection provably created no bgft job — the one case where a clean re-POST is safe, and the
  duplicate-job hazard behind invariant I4 does not apply. One retry is now taken.

### Fixed — a stale full-size install could satisfy the base-game gate

The base gate is "title in `app.db` **and** `app.pkg` ≥98%". For a title that was *already*
registered with a full-size `app.pkg`, both halves can be satisfied by the **previous** install's
files — `_bgft_before` was snapshotted for every job but only compared on the add-on branch. When
`was_registered` is true the gate now also demands a `bgft` row that differs from that snapshot and
is terminal. A genuinely new title needs no such proof: `app.pkg` appearing at full size where there
was nothing *is* the new evidence.

### Verified on hardware, 3.20.1, clean console

Console: etaHEN alone on :12800 (user-started), kstuff, ShadowMount, pldmgr, elfldr. Zero Arsenal.

- **Castle Crashers, 217 MB, base game** — `queued → submitting → transferring → promoting →
  playable` in 17 s. `app.pkg` mtime fresh, all five files, `bgft 1036 … err 0x00000000` timestamped
  to the minute, and — for the first time — **sha256 of the console's `app.pkg` hashed against the
  PC source: `84871d37f59a33ad6a67a4580342011f798b92a8d4c4c8c8bb255ae4dea8964f`, identical.** That
  is the only check that proves the bytes, and it is now part of the routine.
- **A DLC through the exact UI request shape** (drive/mode/console/version/content_id/force) —
  `addcont.db` 6 → 7.
- **PS5 mount lane untouched**: 34/34 ShadowMount backups registered, 0 missing.
- Cheat library `shipped=0` — all 7 022 already present, so the ELF correctly wrote nothing.
- All 12 PC and console endpoints 200. `app.db` integrity `ok`, 109 games.
- etaHEN's log: **no bind errors at all.**

`POST /api/hosts/cleanup` was exercised for real and stopped `garlic-savemgr`, `dpi.elf`,
`nanodns.elf`, `ftpsrv.elf`, `klogsrv`, `tile-autoinst` while leaving etaHEN and ShadowMount up.

Elf Arsenal is now also **disabled in Payload Manager** — its payload renamed to
`elf-arsenal.elf.disabled`, so it cannot be launched by hand or by accident. Reversible by renaming
it back. Note `nanodns` and `ftpsrv` come from `/data/pldmgr/autoload.txt`, **not** from Arsenal —
that attribution was wrong earlier.

### Not changed, deliberately

The audit's second candidate is that our proof is size-and-existence only — never content, never
freshness. That is true and worth fixing, but candidate A and candidate B demand *different* fixes,
and a confident wrong fix is the worst outcome here. **Three measurements settle it** on the
recovered console, before any further change: the `last_updated` on `CUSA14409`'s `bgft` rows (a
2026-08-21 timestamp means the install was real and the fault is at launch; 2026-08-03 means the
proof was stale; more than one row means a duplicate submission), the file **mtimes** under
`/mnt/ext1/user/app/CUSA14409/`, and a sha256 of the console's `app.pkg` against the PC's.

The source package is not in doubt: `pkg_completeness` reports it complete and confident, and that
exact file installed through Arsenal on 2026-08-03 and was confirmed playable.

## [3.20.0] — 2026-08-22 · "etaHEN can drive the install engine, and the two hosts stop fighting" `[VERIFIED]`

### Added — etaHEN 2.6B as a first-class install host

:12800 can now be served by **either** Elf Arsenal or etaHEN, and the app detects which and speaks
the right protocol. They are not interchangeable — this was measured on hardware, not assumed:

| | Elf Arsenal | etaHEN 2.6B |
|---|---|---|
| endpoint | `POST /api/install`, JSON | `POST /upload`, multipart field `url` |
| success | `{"res":"0"}` | `SUCCESS: Direct install console Task started…` |
| failure | **queues, then rejects** | `FAILED: … SCE_PLAYGO_ERROR_…404_NOT_FOUND, code 0x80B22404` |
| bad URL | leaves a parked bgft job | **validates first — creates no bgft row at all** |
| HEAD / | answers | resets the connection |

Sending one the other's request is not a soft failure: etaHEN has no `/api/install` and simply
resets (measured `http=000` in 0.03 s).

etaHEN is the better host for us. It reports the console's real Sony error instead of a generic
rejection, and because it validates *before* queueing it cannot produce the duplicate-bgft-job
hazard that makes an Arsenal rejection un-retryable (invariant I4).

- `dpi_host_kind()` — detects the owner of :12800 from its own page, cached 20 s. `dpi.host` in
  config pins it to `arsenal`/`etahen`; default `auto`.
- `_install_etahen()` — the multipart client, parsing the real `SCE_*` code out of a FAILED reply.
- `dpi_install_ready()` — etaHEN has no `/api/install`, so the Arsenal junk-POST probe reported a
  healthy etaHEN as wedged. It now asks for the page instead. Deliberately **not** `/upload`: with
  etaHEN that endpoint is a real, logged install attempt, not a probe.
- `dpi_live()` — same problem, same fix. etaHEN does not implement HEAD, so the HEAD probe read
  `wedged` on a perfectly healthy host. GET for etaHEN, HEAD for Arsenal.
- `ensure_install_host_safe()` — Arsenal-only, now returns immediately for etaHEN (8 ms instead of
  an FTP round-trip). etaHEN has no dashboard tile of its own to re-queue, so it cannot hit that bug.
- `ensure_dpi_host()` — will no longer launch Elf Arsenal when etaHEN already serves. Doing so
  started a second DPI v2 that could never bind, plus nanodns, garlic and the tile auto-installer.
- `recover_dpi()` — etaHEN's DPI v2 lives inside its Utility daemon, which also serves FTP and klog.
  Killing that to "reload the installer" would take those down with it and there is no separate
  payload to relaunch, so it now reports honestly instead of doing damage.

### Fixed — the stale-`via` hazard the host switch created

`last_install_via()` reads Elf Arsenal's `last-install.json`. etaHEN never writes that file, so with
etaHEN serving it still returned `"sceAppInstUtilInstallByPackage(url)"` from an Arsenal install
hours earlier — a stale verdict on an unrelated install. It now returns nothing when the host is not
Arsenal, and takes a `since` timestamp so a record older than the current hand-off is ignored.

### Fixed — Arsenal and etaHEN were fighting over :12800 forever

etaHEN's util-daemon log was **2 MB of one repeating pair of lines**:

    Notify: [etaHEN] DPIv2 error: bind | Address already in use
    Serving on http://10.0.0.99:12800 (eth0)

Arsenal's `dpiv2.elf` had the port, so etaHEN retried in a tight loop indefinitely. And it is not
recoverable in the other direction either: once etaHEN's `DPI_v2` is on, its retry loop reclaims the
port the instant Arsenal's dpiv2 dies — verified by reloading Arsenal 15 times in a row, etaHEN won
every time. **They cannot coexist; exactly one install host may run.**

`payload_bundle.h` now carries an explicit `port` per bundled payload and `payload_bootstrap()`
honours it, replacing the old `name == "shadowmount" ? 9021 : 12800` ternary. `port == 0` means
*ship the file, never auto-start it*, so we can never start a second install host.

### Changed — Elf Arsenal is no longer bundled; etaHEN is the install host

Arsenal and etaHEN both serve DPI v2 on :12800 and **cannot coexist**. Running both is what put a
wall of `DPIv2 error: bind | Address already in use` notifications on the TV and grew a 2 MB log.
So Arsenal is out of the payload bundle entirely.

The bundle is now `shadowmount` (:9021) and `etahen` (:12800), each carrying the port it serves.
`payload_bootstrap()` starts a payload only when its port is free, so if etaHEN is already running
from its own app we leave that instance completely alone — verified live:
`{"bundled":2,"status":"shadowmount=already-running,etahen=already-running"}`.

Dropping Arsenal also drops what it dragged in: `nanodns`, `garlic-savemgr`, `dpi.elf` and the tile
auto-installer that crashes SceShellCore's job queue (the thing `ensure_install_host_safe()` exists
to defuse).

- **etaHEN.elf** (4 763 312 B) bundled and auto-started when nothing owns :12800.
- **shadowmountplus.elf** refreshed 1 688 528 → 1 746 608 B.
- **elf-arsenal.elf** removed from the repo and the ELF (−4.7 MB).

**FTP is no longer assumed to be on 2121.** Arsenal's `ftpsrv` served that; etaHEN's FTP is on 1337.
The ELF now probes with `ftp_live_port()` (2121, then 1337, else 0) everywhere it used to hardcode
2121 — `/api/health`, `/api/helpers`, `/api/devices` and the readiness toast.

### Added — the cheat library ships INSIDE the ELF

7 022 files (30.7 MB: json 1994, mc4 2142, shn 1763, patches 376, xml 369, xml_orbis 369,
xml_prospero 9) are embedded in the payload itself, so installing the app is the only thing anyone
has to do — no separate download, nothing to copy to the console.

`gen_cheat_bundle.py` packs every file into one `cheats.pack` and emits a table of
`(path, offset, length)`, pulled in with a **single `.incbin`**. It is deliberately not shaped like
`gen_web_bundle.py`, which emits octal C string literals: that is fine for 881 KB of UI but would
turn 30.7 MB into roughly 120 MB of C source. The packer runs in 0.57 s.

`cheat_bundle_extract()` writes them on boot and **skips anything already on disk**, so the first
boot lays the library down and every later boot costs one `access()` per entry and writes nothing.
Verified: deleted 40 files across four folders, reloaded the ELF, and it reported
`shipped=40` with all 7 022 present again.

ELF: 8 987 424 → 41 880 456 B. That is the deliberate cost of self-containment — the loader maps
the payload into RAM, so the library is carried in memory to avoid ever asking the user to fetch it.

The library is also bundled in the exe (+10.8 MB) and `start_cheat_sync_thread()` pushes anything a
console is missing over FTP — incremental, time-budgeted to 90 s so it can never monopolise the link
installs depend on. That is the fallback for a console running an older ELF; the embedded copy is
the primary path. `GET /api/cheats/library` reports local vs console per folder,
`POST /api/cheats/sync` runs it.

### Added — it will not reinstall what is already there

Reinstalling is never harmless: for a base game the console **skips the re-pull and keeps the old
files** (the `stale_install` trap), and for an add-on it is a wasted transfer. Each kind is recorded
in a different place, so each needs its own proof:

| kind | proof it is already installed |
|---|---|
| base game | registered in `app.db` **and** its own `app.pkg` on disk at ≥98% |
| update / patch | `app.db` `APP_VER` already at or past the package version |
| DLC | its `content_id` present in `addcont.db` |

`installed_addons()` reads `addcont.db` — the only record a DLC leaves, since a DLC never appears in
`app.db` and never gets an `app.pkg`. Nothing read that file before.

The check runs before anything is queued and answers `{"ok":true,"skipped":true,
"reason":"already_installed","message":…}`; the UI shows the message and marks the button
*Already installed*. A failed lookup never blocks an install. `force:true` overrides.

Verified live — all three skip paths, and a genuinely new package still installing:

    DLC already in addcont.db  -> "this add-on is already installed on PS5"
    update already applied     -> "KNACK 2 is already at version 01.01"
    base game present          -> "CUSA13529 is already installed on PS5 — delete it there first…"
    a NEW DLC                  -> queued -> transferring -> promoting -> playable, addcont.db 5 -> 6

### Fixed — FTP no longer assumes Arsenal, and a button to clear the port fight

Dropping Elf Arsenal takes its `ftpsrv` (:2121) with it, and **every** read of console state goes
over FTP — app.db, bgft.db, addcont.db, the install proof, the cheat sync. `Ps5Bridge._ftp()` and
`ftp_ok()` now try the configured port and fall back to the other of 2121/1337, remembering which
answered. Verified: etaHEN's :1337 serves all three databases, `LIST`, and `STOR` writes, and the
41.9 MB ELF was deployed over it in 5.9 s.

**`POST /api/hosts/cleanup`** and a *Fix install-port conflicts* button in Settings stop Elf Arsenal
and the services it leaves behind (`dpiv2`, `dpi`, `nanodns`, `ftpsrv`, `tile-autoinst`,
`garlic-savemgr`, `klogsrv`) while **leaving etaHEN and ShadowMount running**. That is a different
job from the rest-mode sweep, which stands everything down for suspend.

It matches specific process names only. Payload Manager reports **every** loaded payload as
`payload.elf`, including our own ELF — killing by that name kills the app. (Learned live: an ad-hoc
sweep took our own ELF down with Arsenal's orphans. The app's own `REST_STOP` never had this bug —
it is an explicit allow-list and `rest_scan` already excludes `getpid()`.)

`REST_STOP` gains `etahen`/`eta-hen`/`eta_hen`, `onion_daemon`, `onion_util` — etaHEN is now the
install host and is exactly the long-lived homebrew that made suspend panic. `REST_KEEP` gains
`onion_elfldr` explicitly: loaders must never be stopped or the console is stranded.

### Fixed — arbitrary file read on the PC companion `[SECURITY]`

`_static()` is the catch-all for every unmatched GET, and on Windows
`os.path.join(WEB_DIR, "C:/Windows/win.ini")` **discards `WEB_DIR` entirely** because the drive
letter makes the path absolute — so the `".."` guard never saw it. The companion binds `0.0.0.0`,
so anything on the LAN could read any file on the PC. Verified before the fix: `GET
/C:/Windows/win.ini` → **HTTP 200 with the file body**.

Now the joined path is resolved with `realpath()` and required to sit inside `WEB_DIR`, which covers
drive letters, UNC paths and symlinks in one check. After: `400`, while `/`, `/index.html`,
`/assets/logo.png` and `/config.js` all still serve normally.

### Fixed — one idle TCP connection froze the whole console API `[SECURITY]` `[RELIABILITY]`

The console's accept loop is single-threaded and its `read()` had no timeout, so a client that
connected and sent **nothing** blocked it indefinitely — taking the UI, the install engine and the
cheat engine down with it. A dead browser tab or a port scan was enough. Measured: `/api/health` OK
in 0.01 s, one idle socket opened, then timeouts for as long as it was held, recovering the instant
it closed.

Accepted sockets now get `SO_RCVTIMEO` 8 s and `SO_SNDTIMEO` 30 s. The long transfers are unaffected
— `/pkgfile/` is handed to a worker thread with its own socket before this read.

### Verified on hardware

etaHEN serving :12800, six inFAMOUS Second Son DLC installed through the full app path —
`addcont.db` 0 → 6 rows with real content ids, fresh `1026` bgft rows each time. `dpi_state` reads
`live`, `/api/dpi/status` `ready:true`, etaHEN's log flat at 0 bytes/8 s (the bind-loop is gone),
`app.db` integrity `ok`. Every PC and console endpoint answering 200.

Then a full base game, deleted first and reinstalled through the app with etaHEN serving and Elf
Arsenal stopped: **Castle Crashers, 217 MB, `queued → submitting → transferring → promoting →
playable` in 21 s**, `app.pkg` byte-exact at 227 540 992, all five files present, bgft `1036`,
`app.db` integrity `ok` — and a second attempt correctly refused with *"already installed"*.


## [3.19.0] — 2026-08-05 · "The install host has its own fallback, and it was lying to us" `[VERIFIED]`

### Fixed — an add-on could report "Ready to play" having installed nothing at all

Reported as *"installing a DLC or update sometimes fails with HTTP 400"*. Reproduced on hardware and
root-caused; it is not the drive, and it is not a 400 the app ever sees.

**What actually happens.** Elf Arsenal runs its own fallback ladder that we do not control. When
`sceAppInstUtilInstallByPackage` fails it silently retries with `sceAppInstUtilAppInstallPkg` — the
metadata-only call — and still answers `ok:true`. Measured, same package, twice in a row:

    attempt 1   reloading ⟳ payload launched      via: sceAppInstUtilAppInstallPkg      contentId: ""
    attempt 2   reloading ⟳ stopping wedged DPI   via: sceAppInstUtilInstallByPackage   contentId: UP5245-...

Attempt 1 stamped `APP_VER 01.22` into `app.db` for Subnautica, wrote **no `bgft.db` row**, moved
**zero bytes** — and the app said *Ready to play*. It is transient: it happens on a daemon that was
just reloaded, which is why it looked random. The retry installed for real.

Two of our own checks let it through:

- **`last_install_error()` never read `via`.** It looked at `error` and `pending` only, so the
  fallback was invisible. Now `last_install_via()` reads it and **anything that is not
  `InstallByPackage` means nothing was installed** — the task is failed, the host is marked dirty,
  and it is retried once on a freshly reloaded daemon before giving up with `metadata_only`.
- **The add-on proof gate was structurally incapable of failing.** It compared the *base game's*
  `app.pkg` against 98% of the *add-on's* size — for an 8.1 MB update that is 2.9 GB vs 8.3 MB, true
  before the add-on has done anything. An add-on now needs **its own new completed `bgft.db` row**:
  a row that differs from the snapshot taken before hand-off, fully transferred, with a terminal
  status. `1036` = a full title install, `1026` = a patch/update install.

Also: a failed add-on no longer tells you to delete the game. It was falling into the
`stale_install` branch — whose advice is *"delete it on the console and install again"* — because
the parent title is, of course, already registered. New `addon_not_confirmed` says the base game was
left alone. And `remember_installed()` is no longer called for an add-on; that file tracks titles.

Verified on hardware at 3.19.0: two inFAMOUS Second Son DLC installed through the new gate,
`via: InstallByPackage`, and `addcont.db` went 0 → 2 rows with the real content ids.

### Not changed — install destination, and why

Also asked for: pick the destination drive for PS4 PKG installs in the app. **There is no mechanism
to do it.** `MetaInfo` (`server.c:4799`) has no destination field, the DPI POST body is literally
`{"url": …}`, and not one of the six `sceAppInstUtil*` symbols we hold names a device, location or
target. `-lSceRegMgr` is linked but never called — the RegMgr strings in our ELF come from the
embedded Arsenal binary, not our code — and the registry key id for Installation Location exists
nowhere in this repo. Guessing one is a blind write to an unknown system setting with uid 0.

What was tested instead: **add-ons are not drive-bound.** Four add-ons, two per drive, with the
console's own Installation Location set to Internal throughout:

| title | drive | `via` | patch row |
|---|---|---|---|
| PAW Patrol `CUSA52195` | internal | InstallByPackage | `1026` ✓ |
| Castle Crashers `CUSA14409` | internal | InstallByPackage | `1026` ✓ |
| KNACK 2 `CUSA08014` | **ext1** | InstallByPackage | `1026` ✓ |
| Subnautica `CUSA13529` | **ext1** | AppInstallPkg → retry → InstallByPackage | `1026` ✓ |

Two extended-storage add-ons installed with the console defaulting to internal. Sony's installer
does follow the base game. Base PKG installs still follow the console's own setting.

### Changed — the queue is a pill in the header, not a bar across the bottom

It was docked to the bottom of the window at `position:fixed`, overlaying the library, and it grew
taller as jobs were added — so the more there was to watch, the less library you could see. `main`
carried 84px of bottom padding purely to clear it.

Now it is a compact pill in the middle of the header with a **conic-gradient progress ring**, and
the list is a floating drawer that opens on hover and pins on click. The drawer is out of flow, so
the grid never reflows. Same information, same ids, same handlers — `#dock`, `#dockhead`,
`#dockbody`, `#qn`, `#startQueue`, `#reloadDpi`, `#clearDone` all unchanged.

The ring is one CSS variable, so an update is a paint and never a layout — it ticks every 1.2s
while installs are live. It reads: idle · sweeping while transferring · spinning when the percentage
is not yet meaningful or the engine is self-healing · amber held · red failed · green done. The
label shows the game name for one job and *"N installing"* for several.

### Changed — filter rows stacked, search narrowed, console picker hidden

The two chip rows sat side by side and squeezed the storage tiles into a wrapping strip. Stacked
into one column they take 366px instead of ~900px at 1600px wide, and `#drives` gets the rest —
measured 1130px, four tiles on one line. Chips and the sort select are tighter; the search box went
from `max-width:420px` to `flex:0 1 260px`.

The console picker is hidden while there is only one console (it was a dropdown with one entry
sitting next to a status pill that already names the console). It reappears automatically for a
second console. `state.console` is still set exactly as before, so every install POST targets the
same console it always did.

## [Verified] — 2026-08-03 · 3.18.2 proven end-to-end on hardware `[VERIFIED]`

No code changed. This entry records the hardware verification of 3.18.2 after the Y2JB re-run, and
one deployment trap found along the way that had been silently serving an old build.

### The console was running 3.18.0, not 3.18.2 — and nothing said so

The Desktop `.elf` was 3.18.2. The new build was uploaded to
`/data/pkg-mutant-shop/payloads/PKG-MUTANT-SHOP.elf` and loaded with
`GET :8084/loadpayload:<that exact path>`, which answered `OK`.
`GET :8710/api/health` then reported **`"version":"3.18.0"`**.

**Payload Manager resolves `/loadpayload:` by BASENAME against its own registry**
(`/data/pldmgr/payloads/<NAME>/<NAME>.elf`), not by the path given. Its registered copy was a stale
3.18.0 — 8 971 112 B against our 8 971 040 B. 3.18.0 is the build with the AppInstallPkg fallback
that produced the two unplayable titles and took the console down.

`SHOP_VERSION` is compile-time only, with no runtime override, so `/api/health` cannot report a
version the binary does not contain. That makes it the authoritative check — and it is the only
reason this was caught. **A version on the Desktop is not the version on the console.**

Deploy procedure that actually works:

1. back up `/data/pldmgr/payloads/PKG-MUTANT-SHOP/PKG-MUTANT-SHOP.elf`, then overwrite it
2. `GET :8710/api/quit` — our own port-handover endpoint; poll until `:8710` drops
3. `GET :8084/loadpayload:/data/pldmgr/payloads/PKG-MUTANT-SHOP/PKG-MUTANT-SHOP.elf`
4. `GET :8710/api/health` and confirm the version **before testing anything**

Still open: nothing in this repo writes to `/data/pldmgr/payloads/`, so step 1 is manual.

### Install lane verified — two games, back to back, no wedge

Lane: PC serves the PKG on `:8710` → `POST :12800/api/install` (Elf Arsenal DPI v2) →
`sceAppInstUtilInstallByPackage(url)` → Sony's BGFT.

| | Riptide GP2 `CUSA02365` | Castle Crashers `CUSA14409` |
|---|---|---|
| `app.pkg` | 107 806 720 / 107 806 720 | 227 540 992 / 227 540 992 |
| `app.pbm` · `.backup` · `app.json` · `app.xml` | all present | all present |
| `bgft.db` `tbl_downloads.status` | **1036** | **1036** |
| Arsenal `last-install.json` | `{"ok":true,"result":0,"via":"sceAppInstUtilInstallByPackage(url)"}` | same |
| `app.db` | registered, correct `metaDataPath` + `contentId` | same |

Both landed on `/mnt/ext1/user/app/<TID>/`. The second install was caught mid-flight:

    [  0s] state=submitting  "Handing off to PS5"
    [  4s] state=promoting   bgft status=1009  219 283 456 / 227 540 992
    [  8s] state=playable    bgft status=1036  227 540 992 / 227 540 992

**`tbl_downloads.status` 1009 → 1036 is the signal that drives the PS5's own top-right toasts**
(Downloading → Installing → Ready to play, with box art). Watch that table to prove the
notifications fired, rather than relying on anyone having been looking at the screen.

`dpi_install_ready()` stayed `true` after both installs — the wedge that used to follow the first
install did not occur. `dpiv2.elf`, `dpi.elf`, `garlic-savemgr`, `shadowmountplus.elf` and
`kstuff.elf` all survived. `app.db` `PRAGMA integrity_check` = `ok`.

Both titles were confirmed playable by the user afterwards.

### Corrected — `app_install_all` is not the registration switch it was recorded as

The Y2JB re-run reset `/data/shadowmount/config.ini` to the stock all-commented template. Its own
runtime log then showed `app_install_all=0`, while **all 22 `.ffpfsc` backups on
`/mnt/ext1/homebrew` were registered, 0 missing.**

The template claims the value is *"forced to 1 on FW 12.00 and newer"*; the log disagrees. Earlier
notes said to force it to 1 to fix registration. On this ShadowMount build registration works at
`0`, so **count actual registrations before changing it** — the value alone proves nothing either way.
It was left untouched.

### Housekeeping

- Both broken registrations from 3.18.0 (`CUSA02365`, `CUSA14409`) were already gone; the DB rebuild
  cleared them. `/user/app/` and `/mnt/ext1/user/app/` held no leftovers for either.
- Removed two stale cached PKGs from `/data/pkg-mutant-shop/install/` after confirming both matched
  the PC library byte-for-byte. **`Star-Wars-Racer-Revenge-LUA-C0RE-EXPLOIT.pkg` (582 549 504 B) was
  kept** — it exists only on the console; the library entry showing it is our own console-merge, not
  a PC copy. Deleting it would lose it.
- The engine is now documented in full in **`MUTANT PKG ENGINE.md`** — the rebuild manual. Repair the
  engine from that document, not from memory or from this changelog's older entries.

## [3.18.2] — 2026-08-02 · "'Ready to play' now requires the game to actually be there"

### Fixed — the check that let an unplayable install be reported as finished
For a title that was not already registered, the queue accepted **presence in `app.db` alone** as
proof of a completed install:

    if tid in installed_titles():
        if not was_registered:
            installed = True        # <- app.db row is enough

A metadata-only registration produces exactly that row, with no game data behind it. So the app
could show *"Ready to play"* for a title that crashes the console on launch — which is precisely
what happened with the two titles installed by 3.18.0.

Every install, new or repeat, now has to show the game's own **`app.pkg` on disk** at 98% or better
(`/user/app/<TID>/` or `/mnt/ext1/user/app/<TID>/`) before it is called installed. The app is no
longer capable of reporting a data-less registration as a success.

## [3.18.1] — 2026-08-02 · "Reverting 3.18.0 — AppInstallPkg does not install a game"

**3.18.0 was wrong and it broke a console.** It made `sceAppInstUtilAppInstallPkg` a fallback in the
game install path. That call registers metadata and no game data: the title appears installed, the
tile shows, and launching it takes the console down hard enough to need Y2JB re-run. Two titles were
installed that way and both are unplayable.

The evidence was already in this file, in three places — including inside 0.19.0's own **Fixed**
section, which I quoted the *other* half of:

    0.19.0  "sceAppInstUtilAppInstallPkg registers metadata only - it creates /user/appmeta/<TID>
             and no game data, which is what produced dashboard tiles that failed with
             'Cannot start the game'. It is no longer used as an install path."
    2.6.0   "The USB path called sceAppInstUtilAppInstallPkg, which only registers metadata... the
             tile then fails with 'Cannot start the game'."
    3.9.4   "hands off through dpi_install_url() - NOT sceAppInstUtilAppInstallPkg, which only
             writes metadata and is what produces an unplayable tile."

### Reverted
- **The AppInstallPkg fallback is gone from the game path.** It remains only for our own deeplink
  tile, which has no data to install. A real game must go through `InstallByPackage` / BGFT — which
  is also the only path that produces the PS5's own *Downloading → Installing → Ready to play*
  notifications with the game's artwork.
- **`dpi.mode` is back to `v2`**: PKG served on the LAN -> `POST :12800/api/install` -> Elf Arsenal
  DPI v2. That is the mechanism with the strongest repeated verification in this changelog.

### Recorded so it is not lost again
A real install lives at **`/mnt/ext1/user/app/<TID>/`** (Extended Storage, `contentLocation=2`) with
`app.pkg` + `app.pbm` + `app.pbm.backup` + `app.json` + `app.xml`. `/user/appmeta/<TID>` alone proves
nothing — that is exactly what a metadata-only registration leaves behind. Always check BOTH roots,
and check for `app.pkg`, before calling anything installed.

## [3.18.0] — 2026-08-02 · "Our own install engine is switched back on"

Installs were broken by two regressions away from what **0.19.0 — "Our own install engine — Elf
Arsenal no longer required"** had already built and verified. Both are undone. Nothing new was
invented here; this restores what worked.

### Regression 1 — the install lane had been switched back to Elf Arsenal
0.19.0 set `dpi.mode` to **`pms`** so installs run on our own engine. The default in `server.py` had
reverted to `"v2"`, and both `config.json` files with it, so every install was being handed to
Arsenal's DPI daemon — which queues onto the console's BGFT installer queue, the queue Arsenal's own
tile re-install crashes. Our engine sat idle and unused the whole time.

Back to `pms`, in the code default and both configs. **Our engine never enters that queue at all** —
0.19.0 noted it as a limitation ("installs do not appear in the PS5 download list"), and it is
exactly why it keeps working when the queue is wedged.

### Regression 2 — the engine's final step used the call that cannot work
0.19.0: *"register it with `sceAppInstUtilAppInstallPkg`, **the one call that works from this
process***". That had been replaced with `install_full()` (`InstallByPackage`) alone, on the belief
that AppInstallPkg "only registers metadata". It answers **0x80B2116F** every time, so every install
died after a perfect 100% download.

The ladder is restored — InstallByPackage first, **AppInstallPkg as the fallback** — which is what
Arsenal does too, and for the same reason: InstallByPackage is unreliable for everyone. The
"metadata only" reading was wrong because game data lands in *managed storage*, which is not
browsable over FTP; an empty `/user/app/<TID>` proves nothing.

### Added — the engine now proves the install instead of trusting a return code
After the ladder it reads the console's own title list back and waits for the title to appear.
`rc = 0` with no registration is reported as a failure, not a success, and the message says which
call was used:

    done  100%  107,806,720/107,806,720  rc=0x00000000  "Installed via AppInstallPkg"

### Verified end to end on hardware
**Riptide GP2 (CUSA02365)** — downloaded 107,806,720 bytes to console storage, installed, and
registered:

    app.db    CUSA02365 · "Riptide GP2" · /user/appmeta/CUSA02365 · categoryType 61440
    appmeta   icon0.png, icon0.dds, pic0/pic1, snd0.at9, shareparam.json, save_data.png
    library   on_console = true

## [3.17.1] — 2026-08-02 · "Two ways the queue could lie about an install"

Both found by an end-to-end audit of the install lane and confirmed in the source.

### Fixed — with the console OFF, the app reported a game as installed. Permanently.
If the PS5 was unreachable the lane animated progress to 100%, said **"Installed (simulated)"** and
then called `remember_installed()`, writing the title into `installed.json` for good. The library
then showed a game as installed on a console that had never received it. The demo animation is gone;
an unreachable console is now an honest error that installs nothing and remembers nothing.

### Fixed — a transfer that died part-way hung that console's queue forever
The stall detector was `stalled = stalled + 1 if pct == 0 else 0` — it only counted while progress
was **zero**. The moment a single percent landed it reset, so a transfer that stopped at 3% held the
worker open indefinitely, and because installs are serialised per console that console's entire
queue was blocked with no way out but restarting the app.

It now tracks real byte progress: no NEW bytes for 90s is a stall at any point in the transfer, and
a part-way stall reports the percentage it died at instead of pretending it is still going.

## [3.17.0] — 2026-08-02 · "The install host was crashing the console's installer queue"

### Root cause — found in Elf Arsenal's own source
Installs were accepted, queued, and then transferred **zero bytes, forever**. It was never our
package, our file server, or the DPI daemon. Elf Arsenal ships:

    static atomic_int g_tile_autoinstall     = 1;   /* default on */
    static atomic_int g_tile_skip_if_installed = 0; /* default: always reinstall */

so ~30s after every start it re-queues its own 1.5 MB dashboard tile — even when that tile is
already installed. An earlier build guarded against this unconditionally, and the comment on that
removed guard says exactly what it costs:

    // Skip if PSPS69691 tile is already installed - avoids triggering
    // AppPrepareOverwriteByPackage every boot, which crashes SceShellCore's
    // SceAppInstallerJobQueue via an uncaught std::out_of_range.

Upstream deleted that guard and demoted it to an opt-in flag that defaults OFF. Once the job queue
is crashed, every install afterwards sits at `transferred = 0`. That is why this used to work and
then stopped: the behaviour changed in the install host, not here.

Proof from the console's own `bgft.db` — the **same job, same content id, same size**:

    completed  item 3   19:44:24   transferred 1,572,864 / 1,572,864
    stuck      item 37  23:01:52   transferred         0 / 1,572,864

### Fixed — the app now protects the install host from itself
`Ps5Bridge.ensure_install_host_safe()` runs before every hand-off: it sets `tile_autoinstall=0` and
`tile_skip_installed=1` in `/data/elf-arsenal/config.ini` (survives a reload) and applies the same
through the live API at `:6969/api/state` (takes effect immediately). Idempotent — a no-op once
correct, and it never overwrites a config it failed to read.

### Fixed — one Install was creating THREE install jobs
The DPI retry policy said a `res != ok` reply meant "nothing was queued, safe to retry". It does
not. The daemon queues the install the moment it accepts the POST, polls app.db for up to 90s, and
only then answers `{"res":"-1","error":"install failed"}` — so a rejection means *queued and did not
finish*. Retrying re-submitted the same package: measured on hardware, one Install produced **three
`bgft.db` jobs for the same content id within 36 seconds**, all stuck at 0, duplicates blocking each
other.

A rejection is now terminal, and the read timeout went from 15s to 120s so the daemon's real verdict
is actually seen instead of being guessed at. Verified after the fix: `jobs_for_title = 1` for the
whole run, where it had been 3.

### Note
Both fixes stop this recurring. The queue that is *already* crashed lives inside SceShellCore and
only comes back when SceShellCore does.

## [3.16.1] — 2026-08-02 · "The tile installs; and an install failure now says what really happened"

### The dashboard tile installs, live, with no reboot
`sceAppInstUtilAppInstallPkg` returned **0x00000000** and the system installer created
`/user/appmeta/PKGM00001/{param.json,icon0.png,icon0.dds}` itself — the `.dds` is generated by
ShellCore, which is the proof it went through the real installer.

    metaDataPath   /user/appmeta/PKGM00001
    categoryType   65536    dispLocation 188     -> Media apps
    deeplinkUri    http://127.0.0.1:8710/        -> works with every PC off

Re-running answers `already registered`; reloading the ELF leaves `PKGM00001 rows = 1`. The package
is embedded in the ELF (`tile_bundle.h`, `.incbin`) and built by our own recovered LibProsperoPkg
toolchain — 1,324,322 bytes against the original's 1,324,320.

**The earlier `0x80A40029` was never the package.** It was the console's damaged app database: the
jailbreak was only half-working while `appinfo.db-corrupt` was latched. After Y2JB was re-run and the
database rebuilt, the identical ELF installed the identical package first try. If AppInstUtil answers
`0x80A4xxxx`, suspect console health before suspecting the package.

### Fixed — "DPI daemon wedged" was the wrong answer to almost every install failure
The queue had exactly one explanation for a failed install and used it for all of them. Elf Arsenal
writes the real reason to `/data/elf-arsenal/last-install.json`, and it is usually something else
entirely:

    "error": "install queued but CUSA02365 did not appear in app.db after 90s",
    "via":   "sceAppInstUtilInstallByPackage(url)",
    "contentId": "EP0786-CUSA02365_00-RIPTIDEGP2PS4001"

That content id was read out of **our** package, so the file was fetched, parsed and accepted — the
transport was never the problem. The console's own install queue simply never ran the job.
`Ps5Bridge.last_install_error()` now reads that file and the hold message says what actually
happened, instead of sending someone to reload a daemon that is working perfectly.

### Diagnosed — the console's install queue is jammed (not an app fault)
`/system_data/priv/mms/bgft.db` shows `download_inprogress = 4`, with four jobs parked at status
1000: Star Wars (CUSA03474), Castle Crashers (CUSA14409), Riptide GP2 (CUSA02365) and **Elf Arsenal's
own tile (PSPS69691)**. Every `InstallByPackage` job now stacks up behind them. Arsenal cannot install
its own tile either, which is why its tile never came back after the database rebuild.

Our tile was unaffected because `AppInstallPkg` registers metadata and never enters that queue.
Clearing the stuck entries from the console's Downloads screen is what frees it.

## [3.16.0] — 2026-08-02 · "The tile, the rest-mode sweep, and a heap overflow that was corrupting memory"

### Found — `sceAppInstUtilAppInstallTitleDir` does not exist on FW 12.70
The dashboard tile could not be registered, and no amount of fixing the call would have helped.
Probed live against the console's own module list:

    sceAppInstUtilInitialize          0x80035e5d0   OK
    sceAppInstUtilAppInstallPkg       0x80035ea60   OK
    sceAppInstUtilAppInstallAll       0x80035ea80   OK
    sceAppInstUtilAppInstallTitleDir  0x0           NOT EXPORTED (by name AND by NID)

New endpoint `GET /api/engine/sym?name=&nid=&mod=` does this lookup on demand — a NID is a hash, so
a wrong one is indistinguishable from a missing function, which is what made this invisible before.
Resolution now also tries `kernel_dynlib_dlsym()` by real symbol name; that is how `AppInstallAll`
and `AppUnInstall` became available (the latter had been hardcoded to 0, so patches could never be
reverted through it).

### Fixed — the tile is back, and now survives the PC being off
`/user` was wiped when the console was set up fresh, taking `/user/app/PKGM00001` and
`/user/appmeta/PKGM00001` with it, while boot-time registration had been removed on the assumption
the tile was "already installed". Registered again via the repo's own `register_tile.py` (pristine
on-console backups kept, `PRAGMA integrity_check` on both databases, atomic RNFR/RNTO replace,
`--restore` available). Verified in `app.db`:

    titleId PKGM00001 · titleName "PKG MUTANT SHOP" · categoryType 65536
    pprDeeplinkUri  http://127.0.0.1:8710/
    pprHubAppUri    psmediahub:main?titleId=PKGM00001      <- what puts it in Media apps

The deeplink points at **127.0.0.1**, not at a PC. The old build's eboot had a specific LAN address
compiled into it, so the tile was dead whenever that machine was off. Both working homebrew tiles on
this console (Elf Arsenal, Homebrew Launcher) are localhost deeplink tiles with no eboot at all —
this now matches them.

`GET /api/tile/status` and `/api/tile/install` were added, and boot restores the tile only when it is
genuinely missing (the unconditional version is what used to re-create dashboard entries). It never
toasts a failure — on this firmware the on-console attempt cannot succeed, and an error popup on
every boot is worse than silence.

### Fixed — a heap overflow in the peer library merge
`*len += snprintf(...)` accumulated snprintf's **untruncated** return against a 6000-byte headroom
guard, while one record's worst case is ~8.2 KB. Once `*len` passed `*cap`, the size_t subtraction
`*cap - *len` wrapped to a huge number, the guard passed, and the next record wrote past the end of
the heap block with an effectively unlimited size. One PC with long game names was enough, and this
runs on every peer merge. Now: reserve 16 KB, write into the real space, advance only by what landed,
and drop a record that still will not fit rather than carry half of it.

### Fixed — four threads with no stack size
PS5 default worker stacks are tiny; five threads already set 256 KB with comments saying so. The four
that did not were the riskiest: the download/install worker, the InstallByPackage worker, the toast
thread, and **the boot bootstrap — the one that runs on every wake**, while the console is still
assembling itself.

### Fixed — the rest-mode sweep believed a helper that lies
Payload Manager answers `{"ok":true,"message":"Killed"}` whether or not the process died. Verified on
hardware: **`dpi.elf` survives three consecutive kills with the same pid**, while every other payload
dies on the first. The sweep counted "Payload Manager replied" as success, so the app could say
*"Rest mode should be safe now"* with homebrew still running into suspend.

Now it asks Payload Manager, sends `SIGKILL` itself as root, re-reads the process list, and repeats up
to three passes. What it reports is what is genuinely gone — `failed[]` means still running.
`tile-autoinst` and `klogsrv` were added to the stop list: after the console rebuild they were the
only homebrew still alive through suspend, and klogsrv holds a kernel log socket open. `kstuff` is
still deliberately never touched.

The TV toast and the app's own exit are now conditional on the sweep actually succeeding; both used
to fire even when Payload Manager never answered and nothing was stopped. A rest request during an
active download or install is refused (`force=1` overrides) — it used to `_exit(0)` mid-write, and a
move writes its final filename straight into ShadowMount's watch folder, so an interrupted one left a
half-written game to be auto-mounted on the next boot.

## [3.15.0] — 2026-07-31 · "A PKG can no longer arrive with a name the installer cannot fetch"

### Fixed — "DPI host answered but rejected this PKG — HTTP 500"
The install lane hands the console's DPI daemon a URL to the package. A filename carrying spaces or
brackets is not fetchable, and the daemon answers a bare `HTTP 500: Error` — **after** the queue has
accepted the job, so the failure surfaces at the worst possible moment:

    Cars 3 Driven to Win (CUSA07083) - [DLPSGAME.COM].pkg     -> HTTP 500

Packages are now renamed the moment they are seen, so a name that cannot work never reaches the
installer at all:

    Cars 3 Driven to Win (CUSA07083) - [DLPSGAME.COM].pkg
      -> Cars-3-Driven-to-Win-CUSA07083.pkg

The new name is built from the package's **own param.sfo** — real title, title id, version — not from
the old filename, so site stamps and release-group tags fall away while the game keeps its actual
name. Everything in the library was renamed on the first scan:

| before | after |
|---|---|
| `Cars 3 Driven to Win (CUSA07083) - [DLPSGAME.COM].pkg` | `Cars-3-Driven-to-Win-CUSA07083.pkg` |
| `Castle.Crashers.Remastered_CUSA14409_v1.00_[6.72]_OPOISSO893.pkg` | `Castle-Crashers-Remastered-CUSA14409.pkg` |
| `Castle.Crashers.Remastered_CUSA14409_v1.04_[6.72]_OPOISSO893.pkg` | `Castle-Crashers-Remastered-CUSA14409-UPDATE-v01.04.pkg` |
| `CUSA03173-Game-PRELUDE-[DLPSGAME.COM].pkg` | `Bloodborne-CUSA03173.pkg` |
| `CUSA03173_v1.09_1080p_60fps-[DLPSGAME.COM].pkg` | `Bloodborne-CUSA03173-UPDATE-v01.09.pkg` |
| `CUSA03173-DLC-PS4-[DLPSGAME.COM].pkg` | `Bloodborne-The-Old-Hunters-CUSA00900-DLC-v01.00-SPEXPANSIONDLC03.pkg` |

An update or DLC keeps its kind and version in the name on purpose: it sits in the same folder as the
base and must not collapse onto its filename — the library really does hold Castle Crashers as both a
227 MB base and a 10 MB update.

### It runs by itself
The folder watcher already rescans whenever the library changes, and the rename now happens at the top
of every scan — so a package copied into the games folder is fixed *before* it is listed, let alone
queued. A name that is already clean is skipped without even being opened, which is also what keeps
the watcher (which fires on name changes) from looping.

### Deliberately narrow
It touches **only** `.pkg` files inside the configured library folders. PS5 backups, the console, the
download/mount/install lanes and the cheat engine are not involved. Three guards before any rename:
a file modified in the last 15 seconds is left alone (still copying), a file a live queue job is
holding is skipped, and an existing name is never overwritten — a collision gets a numeric suffix.
`install_key` is rebuilt on every scan and never persisted, so renaming cannot orphan anything.

## [3.14.1] — 2026-07-31 · "Corrected against the real patch engines, before a single byte was written"

3.14.0 derived the patch format from the 376-file library. That got the value encodings right and
**two things wrong**. Both were found by reading the source of the engines that actually consume
these files — `ps-patch-system` (illusionyy's current PS4+PS5 engine, the only one implementing
`ImageBase`), GoldHEN's `game_patch`, etaHEN, and the shadPS4 emulator.

### Fixed — every PS4-style patch would have been written 0x400000 bytes too high
`Address` **already includes the load base**. The offset is `Address - (ImageBase, or 0x400000)`,
not `Address`. All four implementations agree:

    ps-patch-system  resolve_addr() = mapbase + (addr - base),  NO_ASLR_ADDR = 0x00400000
    etaHEN           PatchBaseAddresss = (ImageBaseAddr == 0 ? NO_ASLR_ADDR_PS4 : ImageBaseAddr)
    GoldHEN          addr_real = g_module_base + (addr_real - NO_ASLR_ADDR)
    shadPS4          std::stoi(patch.offsetStr, 0, 16) - 0x400000

The library says the same thing once you know to ask: of **19,167** addresses in files with no
`ImageBase`, the smallest is **0x401c22** and **not one** is below 0x400000. Image-relative
addresses would be scattered below it. Reading them as relative was self-consistent and wrong —
the corpus could not disprove it, only the reference sources could.

### Fixed — utf8/utf16 wrote one byte too many
The reference writes exactly `s.size()` bytes. 3.14.0 appended a NUL, which would have clobbered the
byte after the string. The library's utf8 lines *do* sit `len+1` apart — but that is where the
original data already had its terminator, not something the writer adds. A neat inference from data
that happened to be false.

### Added — `float64`, and signed decimals
`float64` is in the reference switch and was missing here (8-byte IEEE-754 LE). Scalar types now read
hex unsigned and decimal signed, matching `stoull` vs `stoll`, so `-1` means all-ones instead of
failing.

### Re-verified
All 376 files re-parsed on the console against the corrected rule: **0 errors, 0 mismatches,
19,223 applicable + 550 unsupported = 19,773 lines.**

Nothing had been applied to a game at 3.14.0 — no title has a version-matching patch, so the bug
never reached memory.

## [3.14.0] — 2026-07-31 · "The 376-file patch library is no longer invisible"

### New — game patches actually work
`"patches"` had been a hardcoded empty array in every response, so the whole migrated patch library
was dead weight and the panel's patch rows never rendered. The engine now reads the real format:

    <Patch>
      <TitleID><ID>PPSA01339</ID>…</TitleID>          up to 25 title ids share one file
      <Metadata Title= Name= Note= Author= AppVer= [ImageBase=]>
        <PatchList><Line Type="bytes" Address="0x0059ea79" Value="e9c2f41a01"/>

Each `<Metadata>` is one user-visible patch, and a single file carries up to **59** of them — usually
the same patch rebuilt for different game versions.

New endpoints on the console: `GET /api/patch/list`, `/api/patch/apply`, `/api/patch/revert`, and
`POST /api/mods/<tid>/apply`. Patches also appear in `/api/mods/<tid>` alongside cheats.

### The encodings were derived from the library, not assumed
`Line` has **ten** types, not one. The rules were read off the real data and then confirmed against
three independent sources:

| type | rule | how it was established |
|---|---|---|
| `bytes` | raw hex, verbatim | unambiguous |
| `byte` / `bytes16` / `bytes32` / `bytes64` | value, **little-endian** | "Resolution Patch (900p)" writes `0x00000640`/`0x00000384` = **1600**/**900**; the 720p variant writes **1280**/**720**. Verbatim those digits would be 1074003968. |
| `float32` | decimal → IEEE-754 LE | `0.016666667` is 1/60 — a 60 fps frame time. And `bytes32 0x3fe38e39` is IEEE-754 for 1.7777778, in a patch named "16:9 Aspect Ratio". |
| `utf8` | raw bytes **+ NUL** | in CUSA00547 the strings sit exactly len+1 apart — `CROW` at …63, `DENGEKI` at …68. Five of five agree. |
| `utf16` | UTF-16LE, Latin-1 widened | matches all three sources |
| `mask`, `mask_jump32` | **not supported** | `Address` holds a byte *signature*, not an address — it needs a code scan |

`ImageBase` appears in only 8 of 376 files: when present `Address` is absolute-in-image and the
offset is `Address - ImageBase`; when absent it is already image-relative.

### Verified against the entire library, on the console
Every one of the **376 files / 1567 patches / 19,773 lines** was parsed by the console and compared
against an independently written reference:

    errors 0   mismatches 0   applicable 19,223   unsupported 550   total 19,773

**550 lines (18% of patches) use mask signatures and are reported as unsupported rather than quietly
dropped** — a patch that can only be half-applied says so instead of looking whole.

### Two real bugs the testing caught
**`xml_attr` scans forward without a bound**, so a patch inherited the *next* patch's attributes. On
hardware the first patch of a two-patch file picked up the second one's `ImageBase`, every address
then compared as out of range, and a perfectly good patch reported `lines=0`. Attribute lookups are
now bound to the opening tag.

**`PATCH_MAX_VAL` was 256, and one line needs 970.** The first survey measured the length of the
*Value text*; a `utf8` line in CUSA13233 expands to 970 bytes when encoded — 8x the "obvious"
reading of the same corpus. Caps are now measured by encoding every line, not by eyeballing.

### Safety — a patch has no "off" bytes, so the version IS the check
A cheat is gated on memory already holding the documented opposite bytes. A patch has no such
baseline, so instead: the game must be the one running, **`AppVer` must match the installed version**,
every address is range-checked, the **original bytes are saved before writing** so a patch can be
undone, and every write is read back and compared.

Dark Souls Remastered is exactly why the version gate exists: the library holds a "Restore Debug
Camera" patch built for **01.03** while **01.00** is installed. It is listed, described, and refused.

### Fixed — the panel forced the one case the check exists to stop
The Apply button sent `force:!info.compatible` — forcing *precisely when the versions did not match*.
It never forces now. Rows show what a patch does, who wrote it, which version it targets and how many
edits it makes, and the button explains in words why it is unavailable.

Also: a title with patches but **no** cheat file used to return early with `patches:[]` and show
"No cheat file for this title" — Dark Souls is exactly that case. Both engines now report its patch.

### Unchanged
Downloads, mount/install and the PS4 PKG path were not touched. Cheat toggling is unaffected; the POST
`apply` action, previously a silent alias for `toggle`, now means "apply a patch" — which is what the
only caller in the UI always meant by it.

## [3.13.0] — 2026-07-31 · "The Mutant Cheat Engine was never broken — the PC just refused to ask it"

### Fixed — "CheatRunner is not running on the PS5 (:9999)" when toggling a mod
The engine was fine. Toggling a mod from the PC went through `POST /api/mods/<tid>/toggle`, and that
handler opened with:

    cr = b.cheats
    if not cr.alive():
        return 503 cheatrunner_offline

That gate ran **before anything else**, for all four actions. Twenty lines below it sat a complete
implementation on our own engine (`mutant_running` + `mutant_apply`) that never touched CheatRunner —
and was unreachable, because the gate returned first. So the mods panel could **read** live on/off
state through our engine on the GET, and then refuse to **change** it on the POST, blaming a service
this project deliberately removed.

Proven side by side against DOOM Eternal (PPSA01981) running at pid 183:

| call | before |
|---|---|
| `POST 10.0.0.99:8710/api/mods/PPSA01981/toggle` (console ELF) | `ok:true … entries=1 written=1 skipped=0 failed=0` |
| `POST 10.0.0.76:8710/api/mods/PPSA01981/toggle` (PC companion) | `ok:false, "CheatRunner is not running on the PS5 (:9999)."` |

The console's own ELF already implemented **all four** actions — select, toggle, apply, disable-all —
with expect-gated writes and the on-screen toast. The companion now asks it, through one new method
`Ps5Bridge.mods_action()`, instead of keeping a second copy of the engine that could drift. Our engine
goes first, exactly like the GET path; CheatRunner is finally what the comment above it always claimed
it was — a last resort, never a requirement. It is consulted only when our engine reports it has no
cheat file for the title *and* CheatRunner happens to be running.

### Verified live, in-game
DOOM Eternal running, all four mods driven **from the PC**:

    toggle Inf Ammo            ON   -> entries=2 written=2 skipped=0 failed=0
    toggle Inf Hp And Armor    ON   -> entries=1 written=1 skipped=0 failed=0
    toggle Inf Jumps           ON   -> entries=1 written=1 skipped=0 failed=0
    toggle Inf Upgrade Points  ON   -> entries=2 written=2 skipped=0 failed=0
    toggle Inf Jumps           OFF  -> entries=1 written=1 skipped=0 failed=0
    disable-all                     -> disabled=4 failed=0
    re-enable all four              -> 6 entries written, 0 failed

State read back from the game's live memory after every step, and the ELF stayed healthy throughout.

### Fixed — the mods panel reported success it had not checked
`Disable all mods` toasted **"All mods disabled"** unconditionally: it never looked at the response.
Now that the engine can legitimately answer `game_not_running`, that would have been a straight lie.
It now reports the refusal, and on success says how many mods it actually reverted.

A failed toggle also showed only the error *code*. Both write paths now prefer the engine's own
sentence — "Launch the game first — cheats are written into its live memory", or its refusal to write
because the game's code does not match the cheat file — which is the part that tells you what to do.

### Unchanged
Download, mount/install and PS4 PKG paths were not touched. The engine's safety model is exactly as
it was: every entry is gated on memory already holding the documented opposite bytes, mismatches are
refused rather than forced, and `force` stays opt-in.

## [3.12.0] — 2026-07-31 · "PS5 games install. It was one setting."

### Solved — PS5 backups now register, with no console restart
Last night's conclusion was that the console had to be rebooted. **That was wrong**, and this is the
correction. The whole failure came down to a single ShadowMount setting.

ShadowMount does two separate things to a backup: it **mounts** the container, then it **registers**
the title. Mounting always worked. Registering always failed:

    [REG] internal AppInstallTitleDir bridge unavailable
    NOTIFY: Register failed: Evergate (PPSA01885)

That "internal bridge" is a patch ShadowMount writes into SceShellCore — and on this console it is
never installed at all. Its own startup log says so, every single boot:

    [SHELLCORE] unexpected prologue: launchApp at 0x1563c660
    [SHELLCORE] lifecycle hooks unavailable; stock behavior kept

So the direct per-title register route **cannot** work here. ShadowMount has a second route, and its
own documentation says this firmware is supposed to be on it already:

    # Use batch registration for newly staged titles:
    # 1/true/yes/on  -> submit queued installs through sceAppInstUtilAppInstallAll
    # 0/false/no/off -> register each title directly
    # Default: 0 on FW below 12.00, forced to 1 on FW 12.00 and newer

The console is on **12.70** — but its runtime config dump read `app_install_all=0`. That "forced to 1"
does not happen in 1.7alpha4, and the key was not in the config file, so ShadowMount used the broken
route on a firmware where it can never succeed.

Setting `app_install_all=1` and restarting ShadowMount, against the **same file already sitting on the
console**:

    [REG] Prepared for batch install: Evergate (PPSA01885)
    [REG] Batch install request contains 1 title(s):
    NOTIFY: Batch install queued (1):
    [REG] Installed: Evergate (PPSA01885)
    NOTIFY: installed game PPSA01885

`/user/appmeta/PPSA01885` came out complete — `param.json`, `icon0.png`, `pic0/1/2` — which is exactly
what was missing when the PS5's own notification said **"Unknown"** with no artwork.

No reboot. No jailbreak lost. One line of config.

### The app now fixes this itself, before it costs you an hour
A mount-lane transfer checks the console's register route **before** sending a byte, and repairs the
config if it is wrong. It only rewrites after reading the file in full, preserves the original text
byte for byte, and appends the key rather than reformatting — the fakelib excludes, kstuff rules and
scan paths in that file are yours and are left alone. Once correct it is a no-op.

### Fixed — "ready to play" for a game that was not there
`title_is_mounted()` now requires **both** signals, because each one alone has already lied:

| signal | how it lied |
|---|---|
| `mount.lnk` alone | written *before* the register attempt — passed for Evergate, which the console had never registered |
| app.db alone | a registration outlives its data — Little Nightmares III sits in app.db at 75 MB of metadata with **no container on the console at all** |

Little Nightmares III is the clean proof: `/user/app/PPSA05144` holds only `icon0.png` and `sce_sys`
— no `mount.lnk` — while app.db still lists it. Waiting on app.db alone would have passed instantly,
on a tile that crashes. Checked against every title on the console:

    17 working games   in app.db + mount.lnk  -> ready
    PPSA05144          in app.db, no mount.lnk -> correctly rejected

18/18 correct.

### Unchanged
The PS4 PKG path through Elf Arsenal DPI v2, the Mutant Cheat Engine, downloads, patches and mods are
untouched. Verified after the change: 17/17 PS5 games still mounted, PS4 titles' `app.pkg` intact.

## [3.11.0] — 2026-07-31 · "The PS5 side is a console fault, and the app now says so"

### What is actually wrong — proven, not guessed
PS5 backups reach the console perfectly and still do not work. ShadowMount's own log says why:

    [ACTION] Installing: Evergate (PPSA01885)
    NOTIFY:  Installing: Evergate (PPSA01885)...
    [LINK]   nullfs mounted: /mnt/shadowmnt/PPSA01885_... -> /system_ex/app/PPSA01885
    [LINK]   mount.lnk created / mount_img.lnk created ... layers=2
    [REG]    internal AppInstallTitleDir bridge unavailable
    NOTIFY:  Register failed: Evergate (PPSA01885)

The container mounts — **both layers, pfsc and the inner exfat** — and the links are written. Only the
final **register** step fails, because the console's `AppInstallTitleDir` bridge is unavailable. A
title that mounts but never registers is exactly the broken tile that crashes on launch.

**It is not this app.** Tested five ways, all with the same result:

| test | register bridge |
|---|---|
| our ELF stopped completely, ShadowMount reloaded | unavailable |
| Elf Arsenal reloaded (fresh `dpiv2`), ShadowMount restarted | unavailable |
| DPI processes stopped, ShadowMount restarted | unavailable |
| `tile-autoinst` **and** DPI stopped, ShadowMount started first | unavailable |
| everything running as normal | unavailable |

And the delivery itself is provably correct: content **byte-identical** (SHA-256 over five 8 MB chunks
including head and tail), permissions and ownership identical to the 16 backups that do work
(`-rwxrwxrwx 0 0`), correct folder, correct size.

The bridge works at boot — which is why all 16 existing games mount at startup — and goes down during
a session. **Only a console restart clears it.**

### Fixed — the app claimed "Deployed" for a game the console had rejected
The mount lane reported success as soon as the file was delivered. Delivery is not the finish line;
ShadowMount still has to mount *and register*. It now waits for the title to appear in the console's
`app.db` — the thing that only follows a successful register — and if that never happens it says:

> Copied to /mnt/ext1/homebrew, but the PS5 did not finish mounting it. ShadowMount can mount the
> image and still fail to register the title — restart the console, then it will pick it up from the
> same file.

A first attempt at this checked for `mount.lnk` and was **wrong**: the log above shows `mount.lnk` is
written *before* the register attempt, so it passed for a title the console never registered. The
check now uses `app.db`, verified against the failing case.

### Established — Elf Arsenal cannot install a PS5 backup
Handing Arsenal a `.ffpfsc` exactly the way a PS4 PKG is handed over:

    POST 10.0.0.99:12800/api/install {"url": ".../[PS5] PPSA01885 - Evergate.ffpfsc"}
    -> {"res":"-1","error":"install failed"}

Its own UI confirms it: **`.pkg` is the only extension anywhere in Arsenal's 500 KB `app.js`** — no
`.ffpfsc`, `.ffpfs`, `.ffpkg` or `.exfat`. Its installer is PKG-only.

This also explains the remembered "Download/Install Started" toast for a PS5 game: **before 3.6.1 a
backup held on another PC was forced onto lane `install` and submitted to the DPI daemon.** That
produced Arsenal's toast — and then failed, because Arsenal cannot install a backup. The message was
real; the install never was. FTP to `/mnt/<drive>/homebrew` has always been the correct route, and it
is what the app does.

### Unchanged and re-verified — the PS4 path
Re-tested after every payload restart in this session: Castle Crashers 0.23 GB through Elf Arsenal
DPI v2 → `app.pkg` **227,540,992 bytes = source, byte-identical**, full `app.pbm`/`app.json`/`app.xml`
set, registered in `app.db`. Untouched by any of the above.

### Console left as found
All payloads restored to the same set and all **16/16** PS5 games still mounted: Elf Arsenal
(`dpiv2`, `dpi`, `ftpsrv`, `nanodns`), `shadowmountplus`, `tile-autoinst`, `garlic-savemgr`,
`kstuff`, `pldmgr`, `elfldr`, and our own ELF. DPI live, FTP up.

### What to do next
**Restart the console, then transfer a PS5 game before doing any PKG install.** If it registers, the
bridge is confirmed as session-lifetime state and the app can warn before wasting a big transfer.
The Evergate container is already in `/mnt/ext1/homebrew`, so it should be picked up on boot with no
further transfer.

## [3.10.1] — 2026-07-30 · "ShadowMount never sees a half-written game again"

### Fixed — PS5 backups mounted while they were still uploading
This is why PS5 games installed "successfully" and then crashed the console, and why their
notification said **Unknown** with no artwork.

ShadowMountPlus is an **auto-mounter**: it watches its scan folder and mounts whatever appears —
including a file still being written. The upload wrote straight to the final `.ffpfsc` name, so the
mounter could grab the container at 30% and mount an incomplete image. The metadata is not readable
yet, hence "Unknown" and no icon; the game then crashes on launch because most of it is not there.
The transfer would finish afterwards and the file would look perfect, which is why nothing seemed
wrong when checked later.

Uploads now go to `<name>.part` and are renamed only once every byte is across. The mounter sees
nothing until the file is complete and correct. Verified live, watching both sides during a
transfer:

    transferring   0%   scanfolder=Evergate.ffpfsc.part   mounted=no
    transferring  42%   scanfolder=Evergate.ffpfsc.part   mounted=no
    transferring  84%   scanfolder=Evergate.ffpfsc.part   mounted=no
    playable     100%   scanfolder=Evergate.ffpfsc        mounted=no
    playable     100%   scanfolder=Evergate.ffpfsc        mounted=[sce_sys, icon0.png, mount.lnk, mount_img.lnk]

The rename also clears any stale copy first, and `RNFR`/`RNTO`/`DELE` were probed on the console's
FTP before relying on them.

### Fixed — large PS5 transfers died partway
Little Nightmares III (17.5 GB) failed at **23%** — 4,066,902,016 bytes — with "FTP push failed:
timed out". `ftp.connect(timeout=5)` and **ftplib gives the data socket that same timeout**, so any
stall longer than five seconds killed the transfer. The console pauses for exactly that while it
flushes gigabytes to extended storage. A 2 GB game happened to squeak through; a 17.5 GB one never
could — which is why only the big titles failed. The data timeout is now 900s.

### Verified end to end on hardware

| | transfer | mount | appmeta |
|---|---|---|---|
| **Evergate** (local, 1.98 GB) | 1,983,840,256 = local, byte-identical | `mount.lnk`, `mount_img.lnk`, `sce_sys`, `icon0.png` | full |
| **Little Nightmares III** (on Casita, 17.5 GB, delegated) | 17,484,021,760 = source, byte-identical | `mount.lnk`, `mount_img.lnk`, `sce_sys`, `icon0.png` | full |
| **Castle Crashers** (PS4 PKG → DPI v2) | `app.pkg` 227,540,992 = source | — | registered, patched to 01.04 |

No `.part` leftovers, both queues empty, DPI still `live` after the run, all three machines on 3.10.1.

`appmeta` being populated is what gives the PS5 notification the real game name and artwork — the
"Unknown" toast was the symptom of mounting before it existed.

## [3.10.0] — 2026-07-30 · "It only says installed when the game is actually there"

### Fixed — the app reported "Ready to play" for an install that never happened
Castle Crashers showed as installed and then crashed the console on launch. The install itself was
routed correctly; **the confirmation was lying.**

A finished install was decided by one thing: the title appearing in the console's `app.db`. That is
sound for a game the console did not have — but it is worthless for one it already had. As recorded
back in July, a title that is **already registered** makes the installer skip the download entirely
and keep the existing files: it reads the package header, sees the title registered, and refuses to
re-pull. Our check then found the id in `app.db` — put there by the *old* install — and called it
ready. A broken tile went on the dashboard, and launching it took the console down.

Confirmation is now evidence-based:

* **The state before the handoff is recorded.** If the title was already registered, its presence in
  `app.db` afterwards proves nothing and is no longer accepted on its own.
* **A reinstall must show its data on disk.** `installed_app_pkg()` reads the real
  `/user/app/<TID>/app.pkg` (or `/mnt/ext<n>/user/app/<TID>/app.pkg`) over FTP and requires it to be
  at least 98% of the package size. Metadata without data — the exact shape of the tile that crashes
  — can no longer pass.
* **A first-time install is also checked**, but only to catch a *visibly short* file (<90%). If the
  path cannot be read at all the result is left alone, so nothing that works today starts failing.
* **The dead end is named.** When a leftover blocks the re-pull the message is now
  *"Already installed on the PS5, so it was not replaced — the old copy is still there and may be
  broken. Delete it on the console (Options → Delete), then install again."* — the documented remedy,
  instead of a false success.

### Fixed — the repo config pointed installs at the wrong port
`config.json` in the source tree had `consoles[].dpi_port: 9040` (the ezremote port) while
`dpi.mode` is `v2` (12800). Running from source would have handed every install to a port with
nothing listening. The deployed copy was already correct; both now agree.

### Audited — the whole download/install path, end to end
Traced rather than assumed, in order:

| lane | condition | destination |
|---|---|---|
| **MOUNT** | local backup container | FTP → `/mnt/<drive>/homebrew` → ShadowMount mounts it |
| **CONSOLE-LOCAL** | package already on the PS5 | served over HTTP → DPI v2 |
| **PEER** | game on another PC | that PC's URL as the source, or the mount delegated to it |
| **INSTALL** | PKG in the library | served on the LAN → `POST :12800/api/install` → **Elf Arsenal DPI v2** |

`dpi.mode = v2` in the live config, so every PKG goes to Elf Arsenal. The console's own local install
hands off through `dpi_install_url()` — **not** `sceAppInstUtilAppInstallPkg`, which only writes
metadata and is what produces an unplayable tile.

The self-healing around the handoff is intact and unchanged: `ensure_dpi_host` brings the host up on
demand, a host marked dirty by the previous install is reloaded before the next one
(`recover_dpi`), `ensure_dpi_ready` runs before every hand-off, a wedged daemon is reloaded once and
the task re-driven, and if that fails the queue pauses and says so.

### Still to verify on hardware
The console is being re-jailbroken, so none of this could be tested live. What was verified statically:
every lane's routing, the DPI port and mode, the confirmation logic, and that both artifacts build.

**Castle Crashers is almost certainly still broken on the console.** Delete it there
(Options → Delete) before reinstalling — with a leftover in place the console will not re-pull it,
which is the very situation this release stops misreporting.

## [3.9.5] — 2026-07-30 · "Start means start, from any device"

### Fixed — "Waiting in Casita's queue — start it there"
That prompt should never have existed and was not asked for. It came from a flaw in how 3.9.0
delegated a peer backup: the other PC was contacted **when the game was queued**, and this queue's
mode was passed along with it. So adding a game with "+ Queue" created a *held* job on the other
machine, and pressing Start here released only the local mirror — leaving the app telling you to go
and start it on the other PC.

Delegation now happens when the task **runs**, not when it is queued, and the other PC is always
asked with `mode: "now"`. Queue behaviour stays entirely local: your "+ Queue" holds it here, Start
releases it here, and everything else follows automatically. Which device you press the button on
never matters again.

Two more consequences of the old design, also fixed:

* **No orphans.** Because nothing is sent until the task runs, cancelling a queued game leaves
  nothing behind on the other machine. (One such orphan — a held *Demon's Souls* — was found stranded
  on Casita from the previous behaviour and cleared.)
* **A held job over there is released, not reported.** If the other PC's queue ever holds the job for
  its own reasons, it is started rather than turned into a message you have to act on.

The fallback got better too: if the other PC cannot take it (asleep, busy, older build), this machine
now pulls the file and sends it on itself instead of stopping — slower, still fully automatic.

### Verified end to end, live
    queue a peer PS5 backup here   -> Casita's queue: EMPTY (nothing sent yet)
    press Start                    -> Casita job created and RUNNING immediately
    progress here                  -> "Uploading 2% (on Casita)"   — mirrored, no prompt
    cancel here                    -> "Canceled on Casita", both queues clean

The partial file the cancelled test left in `/mnt/ext1/homebrew` was removed, so ShadowMount has
nothing half-written to pick up.

## [3.9.4] — 2026-07-30 · "Installing from a PC works on the PS5 again"

### Fixed — the PS5 said "only packages already on the PS5 can be installed"
That message comes from the **console's own** `/api/install`, and seeing it means the app was talking
to the console instead of to a PC. The console cannot fetch a game off a PC, so every install died
there. Two faults, both mine, stacked:

**1. `/api/health` on a PC took 2.07s, and the console gives each candidate 1.5s.**
The whole 2 seconds was one dead call: `running_title()` still asked **CheatRunner** — removed from
this project long ago — and a refused connection costs ~2.07s on Windows. Our own engine already
answers that question in ~15ms. Because health was slower than the budget, the console skipped
*every* PC, found no companion, and fell back to its own API.

Its client swallows errors and returns a dict rather than raising, so the first attempt at a memo
never tripped — it now keys off the *result*. **Health: 2.07s → 0.004s.** The console's per-candidate
budget also went 1.5s → 4s, so a momentarily slow PC is not discarded.

**2. The console only ever offered ONE PC.** `/api/companion` returned `g_pc_url`, a legacy single
slot holding whichever machine announced itself last — even though the multi-PC registry has known
about all of them since 3.1.0. If that one machine happened to be asleep, installing was impossible
with other PCs wide awake. It now returns **every** known PC, most-recently-seen first, and the UI
tries them in order.

The app also re-checks every 20s while it is running on its own API, so a PC that wakes up after the
page loaded is picked up without a reload.

### Verified end to end, by installing — not by reading code

Simulating exactly what the PS5 does — page served by the console, ask for companions, try each
inside the UI budget, then install through the winner:

    STEP 1  on_console = True
    STEP 2  candidates: ['http://10.0.0.76:8710', 'http://10.0.0.72:8710']
    STEP 3  -> 10.0.0.76 OK in 0.048s   (the other one was down; it fell through)
    STEP 4  PS4 PKG   -> lane=install, source=companion-lan  (goes to Elf Arsenal / DPI v2)
    STEP 5  PS5 backup-> lane=mount,   dest=/mnt/usb3/homebrew  (the drive that was picked)

And with real transfers earlier in this session:

* **PS4 PKG** — Castle Crashers base, 0.23 GB: downloaded, handed to DPI v2, **"Ready to play"**,
  console installed count 23 → 24.
* **PS5 backup** — Evergate `.ffpfsc`, 1.98 GB: uploaded to `/mnt/ext1/homebrew`,
  **"Deployed — ShadowMount picks it up on the next scan"**.
* **On another PC** — routes to that PC's URL as the source (`source: peer`), or delegates the mount
  to the machine holding the file.

The DPI self-healing between installs is untouched and still in place: `ensure_dpi_host` on demand,
proactive `recover_dpi` for a host marked dirty, then `ensure_dpi_ready` before every handoff, with
the queue pausing and saying so if a reload genuinely fails.

### Note on "base_not_installed"
Seen twice while testing and correct both times — the base really had been uninstalled on the console
between attempts. It is not related to any of the above.

## [3.9.1] — 2026-07-30 · "The drive picker is a destination list again"

### Fixed — the install destination list lost the USB drives
My fault, and it came from 3.4.0. The storage bar was changed to show only what is *actually holding
games* — which was right for the bar, but the drive picker used that same list as its fallback. Two
different questions, one list:

* **the bar** answers "what is holding games right now" — and since 3.4.0 it also lists the PCs
* **the picker** answers "where on the console can this go"

So when the fallback was used the picker offered whatever happened to be populated — internal and
extended only — and could even offer **another PC** as an install destination. Every USB was gone.

The picker no longer looks at the bar at all. `ps5Destinations()` returns the fixed set of console
destinations (internal, ext0, ext1, usb0–usb7) and merges in real free space and labels when the
console reports them. Verified: a PS5 backup panel lists **all 11 destinations** again, and choosing
`usb0` produces `dest: /mnt/usb0/homebrew`.

The console's `/api/devices` now reports every destination too, each flagged `detected`, instead of
only the ones currently mounted — it was offering two choices where the PCs offered eleven, so a
stick you were about to plug in could not be selected at all. On the console those show as
"not detected" so the choice is informed; on a PC the list behaves exactly as it always did.

### Verified — the download and install system is intact, end to end, with real transfers
Because the report was that it was broken, it was tested by actually installing, not by reading code:

| path | result |
|---|---|
| **PS4 PKG, local** — Castle Crashers base, 0.23 GB | downloaded and installed, **"Ready to play"**; console installed count 23 → 24 |
| **PS5 backup, local** — Evergate `.ffpfsc`, 1.98 GB | uploaded to `/mnt/ext1/homebrew`, **"Deployed — ShadowMount picks it up"** |
| **PS4 PKG, on another PC** | queues `lane=install` with that PC's URL as the source |
| **PS5 backup, on another PC** | delegates to the owner, which runs its own mount lane |

One thing that looked like a bug and was not: installing the Castle Crashers *update* was refused with
`base_not_installed`. That was correct — the base had been uninstalled during testing. Once the base
was installed the refusal stopped.

### Also fixed — the actions (⋯) menu
Covered in 3.9.0 but worth restating, since "it does nothing" is what a menu drawn off-screen looks
like: it is now placed by measurement and moved to `<body>`, because the drawer's `transform` was
making a `position:fixed` child resolve against the panel instead of the viewport.

## [3.9.0] — 2026-07-30 · "A PS5 game goes to the PS5, not to the other PC"

### Fixed — installing a peer backup copied it between PCs instead of sending it to the console
Asking for Demon's Souls on the internal SSD moved it from Casita to ASUS-LAP. The mount system was
never broken — a **local** backup still takes the working MOUNT lane, and that path is untouched —
but 3.7.0's `peer-mount` lane pulled a remote container onto this PC first and only then pushed it to
the console. Two transfers of a 100GB file, and the visible half was the wrong one.

The PC that **owns** the file can send it to the console itself, so it does. The install request is now
handed to that machine, which runs its own already-working mount lane straight into
`/mnt/<drive>/homebrew` on the drive you picked. One transfer, machine to machine, never through the
PC you happen to be sitting at.

Progress is mirrored locally by a `peer-delegate` task, so you watch it finish in the app you started
it from instead of having to open the other PC's. Cancelling here cancels it there. If the owner is
unreachable or on an older build, it falls back to the previous copy-then-send rather than failing.

Verified with a held job so nothing moved: asking ASUS-LAP to install Elden Ring to the internal SSD
put `lane=mount, state=held, drive=internal` on **Casita**, with only a `peer-delegate` mirror here —
then both were cancelled and both queues came back empty with nothing transferred.

### Added — choose which PC a copy goes to
The PC-to-PC copy is the alternative action, and it always copied to whichever machine you had the app
open on. It now asks which PC should end up with it, listing every online peer except the one that
already has it, and hands the job to that machine to run. `/api/transfer` takes a `to` and relays.

### Fixed — the actions (⋯) menu opened off the top of the screen
Same root cause as the hover tips: it was anchored to its button and clipped by the panel, with no way
for CSS to know it was about to leave the screen. It now places itself by measurement — above the
button by preference, below when there is no room, clamped horizontally.

One extra trap found on the way: the drawer animates with `transform: matrix(0.975, …)`, and **any**
transform on an ancestor makes a `position:fixed` child resolve against that ancestor rather than the
viewport — so a fixed menu inside the panel still landed in the wrong place. The menu is moved to
`<body>` when opened, where nothing can capture it. Verified at five extreme button positions
(each corner and centre): **zero off-screen**, and in the real panel it sits above the button, right
edges aligned.

## [3.8.0] — 2026-07-30 · "Helpers wait for the console to finish waking up"

### Fixed — waking from rest crashed intermittently
Stopping the homebrew before rest made suspend reliable, but the PS5 auto-starts payloads again on
wake — and that is where it was still crashing, sometimes.

The cause was timing, not any single payload. On wake the whole chain comes up at once, and our
bootstrap asked Payload Manager to load two more payloads **the instant our process began** — while
the shell, the network stack and Payload Manager itself were still assembling. Sometimes the system
was far enough along to survive it; sometimes it was not. That is exactly the shape of a fault that
happens "sometimes".

The helper bootstrap now waits for the console to actually be ready. **Nothing about what gets
launched, or in what order, changed — only when.** Three gates, each individually bounded so a
console that never reports itself ready still ends up with its helpers running:

1. **A delay you can set** — default **30s**, adjustable 0–600s.
2. **Payload Manager answering `/version`** (up to 90s). We load *through* it, so launching before it
   is up could never have worked anyway.
3. **A user signed in** (up to 90s), via `sceUserServiceGetLoginUserIdList` — the closest thing the
   system offers to "the PS5 has finished starting".

Then a short settle, and the existing launch logic runs untouched. The autostart switch is re-checked
after the wait, since it is now long enough that you may have turned it off in the meantime.

### Added — Settings › Network › "Start helpers: after …"
Immediately / 15s / 30s / 45s / 60s / 90s / 2 min, alongside the Helpers toggle, and hidden when the
helpers are not set to auto-start at all. Works from the PC, the PS5 or a phone — the companion relay
now forwards the whole query string rather than just `on`, which would otherwise have dropped the new
setting silently.

Verified live on the console: read `30`, set `60`, `9999` clamped to `600`, restored to `30`, and the
same through the PC relay and through the UI control itself. The gated bootstrap then reported
`shadowmount=already-running, arsenal=already-running` — it waited, checked, and correctly relaunched
nothing.

### Checked — the rest-mode stop list against the console's real process names
With the console up, Payload Manager's own process list was read and every name cross-checked against
the allow-list. All six homebrew helpers match under their real names — `garlic-savemgr`, `dpi.elf`,
`dpiv2.elf`, `nanodns.elf`, `ftpsrv.elf`, `shadowmountplus.elf` — and, most importantly,
**`kstuff.elf` is left alone**: that is the jailbreak itself, and stopping it would be far worse than
the crash being chased. `klogsrv` and `tile-autoinst` are also left running; rest mode is already
reliable without touching them, so they stay.

Also confirmed our own process appears as `payload.elf` (pid 323 this boot) — the sweep skips it by
pid and exits last, rather than by name.

## [3.7.1] — 2026-07-29 · "The console can pick the drive too"

### Fixed — the drive picker worked on the PCs but not on the PS5
3.7.0 gave the mount lane to peer backups in the *companion* merge only; the console's own merge still
hardcoded `lane:"install"`, and the picker is gated on the mount lane. So a `.ffpfs` on Casita offered
all 11 destinations from a PC and only *"Installs to: console default"* on the console.

The console's merge now takes the lane from the container type, the same way the companion does —
verified live: `Elden Ring lane=mount`, `Marvel's Spider-Man 2 lane=mount`, while PKG titles stay
`lane=install`.

### Fixed — non-ASCII in a title arrived as literal escape text
`Star Wars™: Racer Revenge™` displayed on the console as **`Star Warsu2122: Racer Revengeu2122`**.
Python's json writes that ™ as `\u2122`; `json_str_after()` dropped the backslash without decoding the
escape, leaving the four hex digits behind as text. It now decodes `\uXXXX` to UTF-8 (and `\n`, `\t`,
`\"`, `\\`, `\/` properly), and a malformed `\u` is swallowed whole instead of leaking its digits.

`json_escape()` was the other half: it dropped **every** byte >= 0x80, so real characters were stripped
out of real titles along with the mojibake it was aiming at. It now passes valid UTF-8 through and drops
only genuinely broken bytes — so `Bloodborne™` keeps its ™, while invalid bytes still disappear.

Both functions also parse cheat mod files, so both changes were proved on the PC first: **21/21 cases**,
covering escaped quotes in mod names, Windows paths, truncated sequences and lone continuation bytes.

### Added — artwork is fetched the moment a backup lands
A backup container carries no icon; the console only creates `/user/appmeta/<id>/icon0.png` once the
title is actually there. After a successful install/mount the companion now watches for that icon for a
minute and rescans as soon as it appears, so the card picks up its real art straight away instead of
keeping its initials until something else happens to trigger a fetch. Once cached it is advertised to
every other device as `icon_url`, exactly as for any other title.

### Why Elden Ring shows "ER" until you mount it
The sharing pipeline is already complete, and was verified end to end: the PC pulls artwork from the
console's appmeta over FTP (a real 304KB PNG for Evergate), advertises `icon_url` to peers, and the
console picks peer artwork up the same way. What is missing is the *source* — `PPSA04610` has never been
on the console, so no `icon0.png` exists on any device, and the artwork inside the container is not
reachable: the first 4MB of that 109GB `.ffpfs` is a sparse PFS superblock, 99.9% zeros, with no
filenames and no PNG data in it. Mount it once and the icon, version and region all fill in by
themselves — nothing else is needed.

## [3.7.0] — 2026-07-29 · "Every backup container behaves like .ffpfsc"

### Fixed — a backup on another PC had no drive picker
`.ffpfsc` was never actually privileged in the scanner: local backups all share one `MOUNT_EXTS` path
with no per-format branching, so a local `.ffpfs` or `.exfat` already behaved identically. The real
split was **local vs remote** — the peer merge forced `lane:"install"` on everything, and the drive
picker is gated on `lane === "mount"`. That is why Elden Ring offered only *"Installs to: console
default"*.

A backup is a backup wherever it sits, so it now keeps the mount lane and with it the picker. Detection
(`is_backup_item`) checks the kind, the format from either side, **and** the filename — so it holds for
a peer on an older build that reports none of them. 19/19 cases verified: every container in
`MOUNT_EXTS` (`.ffpfsc .ffpfs .ffpkg .ffpfsx .fpkg .exfat .iso .img`) is treated alike, and no PKG is
mistaken for one.

Verified side by side — Elden Ring (`.ffpfs`, on Casita) now matches Evergate (`.ffpfsc`, local):
**picker enabled with all 11 destinations**, button reads **Mount backup**, format reads **FFPFS**.

### Added — the peer→mount lane, so that picker does something
The action had to exist before offering it. A remote backup cannot go through the PKG installer, and
the mount lane needs the file locally. The new `peer-mount` lane runs the two proven halves in order:
pull the container into this library exactly as the PC-to-PC copy does, then send it to
`/mnt/<drive>/homebrew` exactly as a local backup does. **Neither `_run_pc_copy` nor `_run_mount` was
modified** — a failure in either reports itself and stops.

### Added — games stored as a FOLDER
An unpacked dump is now listed like any other backup: `format: folder`, mount lane, drive picker.
Recognised by structure (`eboot.bin` / `sce_sys` / `param.json` / `param.sfo` / `sce_module`), never by
name, so ordinary directories are left alone; a claimed folder is pruned from the walk so its contents
do not come back as loose packages; and its size is the sum of its contents rather than the directory
entry. Sending one recreates the tree over FTP — added as its own branch so the single-file push every
container has always used is untouched. Verified on a synthetic tree: the game folder found and named,
`sce_sys`, `random` and `NotAGame` all correctly ignored.

Peer transfer of a folder is *not* included: `/library/<key>` streams a single file, and a folder would
need an archive stream. Local folders work fully.

### Fixed — the console labelled every backup "ffpfsc"
`build_library_json()` hardcoded that string, so an installed title backed by an `.ffpfs` or `.exfat`
described itself as the wrong container. It now reports the real extension.

### Not a bug — Elden Ring has no artwork, version or region
Worth stating plainly since it looks like one. Artwork for a backup is fetched from the console's
`/user/appmeta/<title id>/icon0.png`, and version and region come from `app.db` — **both exist only
once a title has been installed**. Evergate has all three because it *is* installed (`PPSA01885` is in
app.db); Elden Ring is not (`PPSA04610` absent), and an `.ffpfs` container carries no extractable
metadata of its own. Nothing is being dropped — there is no source yet. Mount it once and the icon,
version and region fill in by themselves.

### Unchanged
PS4 packages still install to the console default with the picker locked, exactly as before —
re-verified on Castle Crashers and Bloodborne after these changes.

## [3.6.1] — 2026-07-29 · "Backup formats read properly, and a badge that fell out of its corner"

### Fixed — a badge landed at the bottom-left of the cover, sliced in half
`.tip{position:relative}` sits later in the stylesheet than `.badge{position:absolute}` and has the
**same specificity**, so it silently won. Any badge that also carried a tip lost its absolute
positioning, flowed as inline content inside the cover, and was clipped at the bottom edge. That is
exactly the three that were affected — *Patch only* on add-ons, the source-PC name (**Casita** on the
Elden Ring card), and the drive name on removable media. Every other badge was fine because it has no
tip.

`.tip` existed only to anchor the old `:after` tooltip. Since 3.6.0 the tooltip is a fixed-position
element on `<body>`, so the rule was already dead weight — removing it fixes the badge outright rather
than papering over it with a higher-specificity override.

### Removed — the "Patch only" tag on add-on cards
It sits under **Add-ons** already and the panel spells out that the base game is missing, so the tag
said nothing the card did not. Add-on cards now show platform, id, size and name — nothing else.

### Fixed — ShadowMount backups arrived from another PC with no information
A `.ffpfs` held on another PC showed **FORMAT —** and no version, because neither field ever crossed
the network: `federation_self` did not advertise `format`, and the merge did not read it. Both ends
now carry the container type (per title *and* per file) and the version, so a backup on Casita reads
as **FFPFS** on every device instead of an anonymous package. Same fix in the console's own merge,
which was hardcoding `"format":"PKG"` for every peer title.

`lane` is deliberately **not** taken from the peer. It selects the action the panel offers, and
pulling a backup across the network is a different job from mounting one that is already local —
changing it was not needed to fix the missing information.

### Fixed — a JSON null could be read back as the next field's name
Found while wiring the version above. `json_str_after()` stepped past the key and then scanned ahead
for the next quote, so `"version":null,"items":[...]` returned **"items"** as the version. It now steps
over exactly `:` and requires a quoted string, giving nothing for null / numbers / objects / arrays.

This function is also what the **cheat engine** parses mod files with, so the change was proved on the
PC first: 13/13 cases, including escaped quotes inside names (`Inf \"HP\"`) and the exact document a
peer sends. Every previously-correct read is unchanged — only null-valued fields differ, and returning
nothing there is strictly better than returning another key's name.

### Fixed — backup names carried their own plumbing
A backup container has no `param.sfo`, so its card name is its filename — which read
*"Elden Ring - PPSA04610-app"*, repeating the id that already has its own field. Platform tags,
version tags in brackets, the title id and a trailing `-app` / `-patch` marker are now stripped:

    Elden Ring - PPSA04610-app.ffpfs        ->  Elden Ring
    [PS5] PPSA01885 - Evergate.ffpfsc       ->  Evergate
    DOOM Eternal - PPSA01981-app.exfat      ->  DOOM Eternal

The marker strip is anchored to the end **and** requires the hyphen the backup tools actually write.
An earlier looser version turned *"The Last Game"* into *"The Last"* — 11/11 cases correct now,
including titles that genuinely end in "Game" and files that are nothing but an id.

### Fixed — three different lists of which formats count as a backup
The library scanner accepted eight extensions, the console accepted three, and the console-backup
scanner four. A stick holding an `.exfat`, `.iso`, `.ffpfsx` or `.fpkg` backup was therefore listed by
one scan and ignored by the next. All three now use one list — `.ffpfsc .ffpfs .ffpkg .ffpfsx .fpkg
.exfat .iso .img` — via a single `is_backup_ext()` on the console side.

### Known limitation, unchanged by this release
Installing a **backup that lives on another PC** is not wired: a `.ffpfs` cannot go through the PKG
installer, and the mount lane requires the file to be on this machine. The card and panel now describe
it correctly; the action does not yet exist. Copy it over with the PC-to-PC move first, then mount it.

## [3.6.0] — 2026-07-29 · "Tips that stay on screen, and cleaner game cards"

### Fixed — hover tips were being cut off
A tip on a header button was drawn *above* it, which on the console put it behind the browser's own
top bar; tips inside the game panel and the Settings blocks were sliced by the panel edge.

The cause was structural: the tip was a CSS `:after` pseudo-element on each button. A pseudo-element
is **clipped by any ancestor that scrolls or hides overflow**, and CSS cannot know a tip is about to
run off the screen — so no amount of tweaking `top`/`right` could fix it everywhere. There was
already a per-panel override trying to work around exactly this for one case.

Replaced with a single fixed-position element on `<body>`, placed by measurement:

- **Prefers above**, and **flips below** when there is no room up there — which is what rescues the
  header buttons.
- **Clamps horizontally** so a control near either edge still shows its whole tip.
- **Fixed positioning escapes every container**, so no panel can clip it — the per-panel special
  case is gone rather than multiplied.
- Hides on scroll, resize and click, since a tip is anchored to where the control *was*.

No markup changed: it works by delegation on `data-tip`, so all 21 existing tips are covered and any
new one is automatic. Touch behaviour is unchanged (still suppressed, where hover never happens).

Verified at 1920×1020 across every visible tip on the main page, the whole Settings panel and an open
game panel: **14/14 fully on screen, zero clipped.** The two header buttons now place their tip
*below* (y=84 and y=82 instead of off-screen above); dock and Settings controls place theirs above.

### Changed — title id and size moved onto the artwork
They had a text row of their own under the game name, costing every card a line. They are now tags
along the bottom of the cover — id bottom-left, size bottom-right — in the **same style as the PS4 /
PS5 tag above**, inset by the same 8px, and **white** so they read against any cover.

The bottom text row is gone entirely. The card's text block drops from **73px to 51px** — 22px back
per card, across every card in the grid, add-ons included (they all come from the same builder).
The platform tag and the status badge are untouched: same position, same behaviour.

## [3.5.0] — 2026-07-29 · "Rest mode confirmed fixed, and a slimmer header"

### Confirmed on hardware — rest mode works
*Stop all homebrew before rest mode* was pressed, confirmed, and the console **suspended without a
panic**. The diagnosis holds: our app was never the cause — the other loaded payloads were. Elf
Arsenal (with the `dpiv2` / `nanoDNS` / `ftpsrv` processes it leaves behind), GarlicSaves and
ShadowMount are not part of the console's sleep/wake cycle, and stopping them makes suspend safe.

### Added — the shutdown is one press from the main page
A power icon sits beside the settings gear. It runs **exactly** the same action as the Settings
button — both call one shared function, so the shortcut can never drift from the original. The
Settings control is untouched.

It appears only when a console is actually reachable, following the health check that already runs
every few seconds rather than probing on its own, and turns red on hover so it reads as an off
switch rather than another grey icon.

### Fixed — a dead band across the top of every page
`logo.png` was **508×360 carrying only 489×177 of artwork** — 81px of transparent space above it and
102px below. Over half the image was nothing, and that nothing was being rendered as a full-width
empty band above and below the logo on every device.

The file is cropped to its artwork and the CSS heights are set to the exact size that artwork was
already drawing at (132 × 177/360 = 64.9px → **65px**, and 176 → 86px, 215 → 105px for the larger
breakpoints). **The logo renders at an identical size — 186.3px wide before and after.** Nothing was
shrunk, resized, or re-styled; only the emptiness is gone. The original is kept as
`assets/logo.original.png`.

### Changed — the library summary moved up beside the byline
*🎮 26 titles · 📂 C:\Mutant Games · 🔀 2 sources* had a row of its own above the grid. It now sits on
the same line as **By XavyProd v3.5.0**, right-aligned so its edge lands exactly on the settings
icon's edge. Same text, same size, one row fewer.

Measured at 1920×1020: header **222px → 141px**, and the first row of games moved from y=363 to
**y=250 — 113px of the page reclaimed** with nothing removed. Verified at 1920, 1366 and 375 wide:
no overlap, no horizontal overflow, and on a phone the summary drops below the search field instead
of wedging itself between the icons and the input.

## [3.4.0] — 2026-07-29 · "PCs find each other on their own, and the storage bar tells the truth"

### Fixed — PCs could not see each other unless the PS5 was up
Two companions on the same network, both running, both reachable — and neither found the other.
Everything went through the console, so with the PS5 off there was no cross-PC library at all, and
**moving a game between PCs depended on a console that has nothing to do with it**.

The cause was a timeout, not a network problem. Discovery probes the port, then asks the responder
to identify itself via `/api/federation` — with **2.5s** allowed. That reply is not a ping: the peer
builds a library listing to produce it. Casita answered in **3.006s**. Every sweep saw the open port,
timed out identifying it, and threw the peer away. The console never had this problem because the
ELF allows 6s, which is exactly why it only ever worked *through* the PS5.

Fixed at both ends:

- **We answer instantly now.** `/api/federation` was spending its entire 3s probing Payload Manager
  on the console — so with the PS5 asleep, *we* were the peer nobody could identify in time. The
  liveness value is cached, and the identity reply never probes at all. **3.02s → 0.010s.**
- **Discovery waits long enough** (8s) for a peer that is still on an older build, with one deadline
  for the whole sweep instead of four seconds per thread.
- **Peer-of-peer adoption.** Every companion advertises `known_pcs`; each PC now adopts them, so one
  machine finding another is enough for the whole set to converge.
- **A background keepalive.** Discovery only ever ran as a side effect of a request, so two idle PCs
  never met. A full sweep now runs at startup and every 5 minutes, with a cheap re-check of known
  machines every 20s in between.

Verified with the PS5 offline: **Casita found, online, 3 titles**; merged library **7 titles** with
correct host attribution; and a PC→PC move enqueued from Casita to this PC's folder with no console
involved at all.

### Fixed — the main page listed every storage slot, all of them empty
Whenever the console was unreachable the bar emitted **one tile per configured drive** — Internal,
EXT0, EXT1, USB0 through USB7 — each reading "0 games". Eleven tiles for storage that either was not
attached or held nothing.

A place now earns its tile by holding something. No console: no console tiles. No extended drive
attached: no Extended Storage tile (the console used to emit one from a failed `statvfs`). Nothing
anywhere yet: one honest line saying so, instead of a row of placeholders.

### Added — the PCs are on the storage bar
They are where the games live and what the console installs from, so they belong beside the drives:

    💻 ASUS-LAP (this PC)   14 GB   4 games
    💻 Casita               51 GB   3 games · 10.0.0.72

Real title counts and real sizes, on both the PC app and the console. A peer on an older build sends
no byte total, so its size is added up from the files it advertises rather than showing an empty tile.

### Fixed — storage was read once and never again
`loadStorage()` ran a single time at boot, *before* the API was even resolved. A drive that filled up
later, or a PC that came online, could not appear without a page reload. It now re-reads once the API
is settled and stays current, which is what makes a machine show up on its own.

## [3.3.0] — 2026-07-29 · "Stop all homebrew before rest mode"

### The rest-mode test result that changes everything
Shutting **only this app** down and then resting the console **still panicked**. That rules our
process out as the cause — which is the single most useful thing learned so far. The remaining
difference between a healthy console and this one is the *other* loaded payloads: **Elf Arsenal**
(plus the `dpiv2`, `nanoDNS` and `ftpsrv` processes it spawns, which outlive it), **GarlicSaves**,
and **ShadowMount**. None of them is part of the console's sleep/wake cycle either.

### Changed — the button now stops all of it, not just us
*Settings → Network → **Stop all homebrew before rest mode***.

It asks Payload Manager for the process list, stops each payload by name, and shuts this app down
last. Payload Manager itself keeps running, so everything can be loaded again **without
re-jailbreaking**.

Two deliberate design choices:

- **An allow-list, never "kill everything reported."** That list may well include system processes,
  and getting it wrong would take the console down harder than the crash being chased. Only names
  matching Arsenal / garlic / dpiv2 / nanodns / ftpsrv / shadowmount are stopped, and `pldmgr` and
  `elfldr` are protected outright.
- **Whatever is left is reported back.** The UI lists what is still running after the sweep, so the
  real process names can be read off a live console and the list widened precisely — rather than
  spending another 50-minute recovery on a guess.

### Added — it works from the PC and from a phone
The companion relays `/api/rest/prepare` to the console, the same way it relays the Helpers toggle.
You are about to walk away and rest the console, so pressing this from the PC beats having to open
the app on the PS5 first. Both console controls hide themselves when no PS5 is reachable.

`GET /api/rest/prepare?self=0` stops the helpers but leaves this app running — useful for checking
the sweep from a PC without losing the app in the same breath.

### Also fixed — the PC app crawled whenever the PS5 was off
Directly relevant, because this button's whole job is to leave you with a console that is *not*
answering. With the PS5 off or resting, the companion was making calls that could only ever time
out, one after another:

| with the console off | before | after |
|---|---|---|
| `/api/health` | 13.8s | **1.8s** |
| `/api/library` | 25.0s | **0.005s** |

Three causes, each fixed by asking first whether the console is even there:

- `running_title()` cost **two 6-second timeouts** on every health check. It is now only consulted
  when the console actually answered.
- `console_usb_packages()` allowed **20 seconds** for a console busy scanning sticks — right when it
  is there, entirely wrong when it is not. Skipped unless the console is reachable.
- `console_apps()` paid the full FTP timeout again on every call in the same request. A short
  10-second memo of the failure stops the retry storm; a success clears it immediately.

None of this changes behaviour when the console is up: every guard is the reachability signal the
response already reports. The library still lists the PC's own games, correctly marked
`console_reachable: false`.

### If it still panics
Then no app is responsible, and the remaining suspect is the jailbreak state itself — kstuff /
Y2JB's kernel patches, which no app-level change can undo. That would mean rest mode is simply not
safe on this setup until the jailbreak is reloaded, and the honest workaround is to power the
console off rather than rest it. The report from this button is what tells the two apart.

## [3.2.2] — 2026-07-29 · "The Helpers toggle works from the PC too"

### Fixed — "Could not change it" when pressing Helpers in the PC app
Helper auto-start is a **console** setting, and `/api/payloads/autostart` only ever existed on the
ELF. Pressing the button in the PC companion hit a **404**, so it failed every time — while the exact
same button worked on the PS5. Since the point of the toggle is A/B testing the rest-mode crash, it
has to work from wherever you happen to be sitting.

The companion now relays the call to the console, the same way it already relays cheat rescans and
mods. The button behaves identically on the PC, on the PS5, and on a phone.

Two smaller things went with it:

- **The button hides itself when no console is reachable.** There is nothing to toggle without a PS5,
  and a control that can only fail is worse than no control.
- **Failures say why.** It reported a flat *"Could not change it"* for every cause; it now passes
  through what actually went wrong (`no console configured`, `console did not answer: …`).

## [3.2.1] — 2026-07-29 · "The UI could not parse, so nothing loaded anywhere"

### Fixed — the app was blank on every device
A `confirm()` message added in 3.2.0 ended up with **real newlines inside a double-quoted
string** instead of `
` escapes. That is a syntax error, so the browser discarded the entire
script block — and the whole UI is one block. Both artifacts embed the same `web/index.html`,
so one bad token took out the PS5 app and the PC app at the same time.

What made it hard to see: **nothing looked wrong from outside**. Both servers stayed healthy and
returned `200` with the full page; the API answered normally. Only the page's script was dead.

### Added — the build now refuses to ship a UI that will not parse
`tools/check_web.py` runs before either artifact is packaged, from **both** `build-wsl.sh` (ELF)
and `PKG-MUTANT-SHOP.spec` (EXE), so it cannot be skipped by building one and not the other.
It parses the page with `node --check` when node is present, and always runs a scanner that flags
a string literal left open at end of line — the exact damage a generated edit causes. Quotes inside
comments, template literals and regex literals do not trip it. A failure aborts the build.

Verified after the fix: 25 cards render, filters read *All · Installed · Not installed · Updates ·
Add-ons (1)*, no console errors, and both devices serve a byte-identical page. The Mutant Cheat
Engine is untouched — 1954 json / 1760 shn / 1421 mc4 / 377 patches indexed, Bloodborne matching at
exact version — and download, install and queue endpoints all answer.

## [3.2.0] — 2026-07-29 · "Patches and DLC that live on a PC, and rest-mode safety"

### Fixed — a game's patch or DLC sitting on a PC never appeared on the PS5
The console merged each PC's library by title id and **skipped any title it already had**.
That is exactly backwards for add-ons: the console *does* have Bloodborne, which is precisely why
the v1.09 patch on the other PC mattered — and it was thrown away with the rest of the entry.
The same skip hid Castle Crashers' v1.04.

Peer titles the console does not have were also collapsed to a **single file**: the merge read
`items[0]` and gave the whole entry one kind, so a title a PC holds as base + update + DLC arrived
as one card with one file.

Both are fixed in the console's merge:

- every entry in the peer's `items[]` is read and bucketed into **base / updates / dlc**
- a title the console already has now gets the peer's add-ons **spliced into its existing card**
  instead of being dropped
- an add-on with no base stays an add-on (`update_only`), so it lands under **Add-ons** rather than
  posing as a full game
- the same patch held by two PCs is listed once

Verified live: Bloodborne now shows `update 01.09` from **Casita**, Castle Crashers Remastered shows
`update 01.04` from **ASUS-LAP**, and *Bloodborne The Old Hunters* is an Add-on, not a game card.

### Fixed — the console only ever found PCs that already knew its address
A PC announces itself every 8s, but a PC with no console configured never announces, so it stayed
invisible even when another PC could see it. Each companion now advertises `known_pcs`, and the
console registers those too — **one PC finding the console is enough for all of them**. No subnet
sweep on the console and no extra requests: the list rides along in the federation document already
being fetched.

### Fixed — Devices showed "0 titles" for every machine
`/api/network` on the console sent an empty `counts` block. It now reports the count from each PC's
own federation document and its own card count. Peers are named in the source list too, with titles.

---

### Rest mode — what changed, and what is still unproven

**The honest summary: the cause is not confirmed yet, and this release does not claim to fix it.**
What it does is remove our app's continuous background network activity and add a clean way out.

**What was found.** The app had *no* suspend or resume handling of any kind — nothing in it knows the
console is going to sleep, and a loaded payload is not part of the system's sleep/wake cycle, so
nothing quiesces it. On top of that it was doing steady network work: **every** `/api/library` and
`/api/storage` request opened a fresh TCP connection to **each** registered PC with a 6-second
timeout, and the PCs announce themselves every 8 seconds. Threads sitting in socket calls while the
network stack is torn down is the classic shape of a suspend-time fault.

**What was changed.**

- **Peer library snapshot** — the merged peer library is cached for 20s. A library request no longer
  opens sockets to every PC; the same answer is reused. Far less to be caught mid-flight at suspend.
- **Resume detection** — a watchdog compares `CLOCK_REALTIME` against `CLOCK_MONOTONIC`. Wall clock
  running ahead of monotonic means the console slept through it, so everything derived from the old
  network state is dropped and a notification confirms the app is awake.
- **Deactivate before rest mode** — Settings → Network now has a one-tap clean shutdown for the
  console. No API tells a payload that rest is coming, so a deliberate control is the truthful answer.
- **Helpers: auto-start / off** — the app launches **ShadowMount** and **Elf Arsenal** (which itself
  spawns garlic, nanoDNS on :53, and ftpsrv). Those are third-party payloads doing their own
  continuous network work and are the leading suspects. The toggle turns that auto-start off so the
  two cases can be told apart.

**How to find the actual cause** (each test is one rest cycle):

1. Settings → **Helpers: off**, reload the app, rest the console.
   *Survives* → the helpers are the cause, not us. *Crashes* → keep going.
2. Load the app, **do not open the UI**, rest the console.
   *Survives* → it is request traffic. *Crashes* → it is the payload merely being resident.
3. **Deactivate before rest mode**, then rest.
   *Survives* → confirms it is a live payload at suspend, and the shutdown habit is the workaround.

## [3.1.1] — 2026-07-28 · "Progress for games coming from another PC"

### Fixed — installing from a peer PC showed no progress
A game pulled from another PC sat at 0%, then jumped to **"submitted est."** while installing
perfectly in the background. Not a display glitch — the task genuinely had **no progress source**:

- `local_progress` was false (the source is the peer's name, not `companion-lan`), and
- `progress_url` stayed `None`, because it was only set for a *configured mirror*
  (`is_companion(src)`), and a **discovered peer** is not one.

With neither, `measurable` was false, so the worker submitted the install and reported
*"Sent to PS5 (progress not measurable for this source)"*. A game from the local library was
byte-counted normally, which is why one title behaved and the other did not.

The peer is the machine actually serving the bytes, so it knows exactly how many have gone out —
that is what `/api/served/<key>` reports. The peer lane now carries a `base_url`, and the
progress wiring accepts a peer as well as a configured mirror:

    peer_url   http://10.0.0.76:8710/library/<key>
    served_url http://10.0.0.76:8710/api/served/<key>

### Fixed — the queue hid the reason
`submitted` rendered as the bare state name, so a task that was explaining itself just looked
stuck. Queue rows now show the message, which is where the explanation lived all along.

Nothing in the install path itself changed: the same lane, the same daemon, the same files.
Only how progress is measured and reported.

---

## [3.1.0] — 2026-07-28 · "Mutant Multi Companion Engine"

Every device now shows the **same** library, with the same categories and the same artwork,
regardless of which machine you happen to be looking at.

    before          ASUS-LAP 25 titles, no Add-ons | Casita 26, Add-ons(1) | PS5 26, no Add-ons
    after           all three: 26 titles, Add-ons(1), identical contents

### Fixed — cover art never crossed the network
Artwork lives on whichever PC holds the game, and nothing advertised it, so every remote title
fell back to drawing initials (the "CC" / "ZA" / "BT" tiles). Peers now advertise `has_icon` and
an `icon_url` pointing at their own `/icon/<tid>.png`, the merge carries it, and a device with
no artwork of its own **borrows the peer's**. This was not the virtualization — that only
decides which cards exist, never what they contain.

### Fixed — the console turned every peer title into a fake "game"
The console's merge emitted everything as a `base` entry, so a DLC or update from a PC became a
game card and the **Add-ons category could never appear on the PS5 at all**. It now carries the
item's kind into the right bucket and preserves `update_only`, so an add-on stays an add-on
everywhere.

### Fixed — Settings said no PCs were found, while merging their libraries
`/api/network` only existed on the companion. The console has it now, so the Devices panel
lists the machines it is actually talking to — and it learns their **names** from what they
advertise instead of showing bare addresses.

### Fixed — "1 source · auto-picked" however many PCs were connected
The source list only counted download mirrors, never the other machines supplying games. Peers
are sources now, on both the companion and the console:

    console sources: this PS5 · ASUS-LAP · Casita

### Note
A PC only advertises artwork once it is running **3.1.0** — update every machine, or titles
coming from an older one still show initials.

---

## [3.0.0] — 2026-07-28 · "One library, every device"

### Fixed — the console only ever knew about ONE PC
`g_pc_url` was a **single slot**. Every companion announces itself to the console, so the
second PC to start simply overwrote the first — which is exactly why the library changed
depending on which computer you had open.

The console now keeps **every** PC that announces itself (`/api/pcs` lists them), asks each one
what it holds, and merges the lot into its library. A PC that is asleep or off is skipped, so
nothing depends on any particular machine being awake.

    PCs known to the console: 10.0.0.76 (ASUS-LAP) + 10.0.0.72 (Casita)
    PS5 library: console 23 · ASUS-LAP 1 · Casita 1   (the rest dedupe — already installed)

### Fixed — add-ons were invisible from other devices
`federation_self` only advertised titles that had a **base** game, so an update or DLC sitting
alone on one PC was never sent to the others — which is why the Add-ons category appeared on
Casita and nowhere else. Every installable file is now advertised with its kind, and the merge
puts each one in the right place: a peer's DLC lands in that game's **DLC** section, and an
add-on with no base stays an add-on. The category is therefore the same on every device.

### Changed — filter order
**All · Installed · Not installed · Updates · Add-ons**

### Added — virtualized grid
Only the cards near the viewport exist as DOM nodes; spacers above and below keep the scrollbar
honest. Below 60 titles it stays off, because virtualization has its own cost and a small
library is faster without it. Measured with 400 titles: **90 nodes instead of 400**, window
tracking correctly while scrolling (`[0,47] → [42,131]`, `padding-top: 2447px`).
- Row height and column count are measured once and the window computed arithmetically — no
  per-card measurement, which would defeat the purpose.
- It binds to the **real scrolling container** (`<main>`), not the window: this page does not
  scroll the document, so a window listener would never fire.
- Detection tests the overflow **style**, not whether the element happens to overflow at that
  instant — during a rebuild the container is briefly empty and would resolve to the wrong node.
- The scroll handler schedules with `requestAnimationFrame` **and** a timer guard: some embedded
  browsers throttle rAF to nothing when not compositing, which would silently stop the grid
  updating mid-scroll.

---

## [2.9.0] — 2026-07-28 · "Smooth on the console, and PC to PC"

### Fixed — the PS5 showed FTP off and everything disconnected
A real bug, and console-only. `refreshHealth()` handled the on-console case with an **early
return placed before any status was painted**, so every LED kept its initial "off" and every
row kept its "-" — while the services were running fine. The PC never hit it.

The console also reported two **hardcoded** booleans (`ftp_online:true`, `dpi_online:false`)
instead of looking. It now probes FTP (2121), the install engine (12800) and ShadowMount (9021)
on loopback and reports what is actually there, plus its real LAN address.

`/api/sources` on the console had none of the fields the header bar reads, which is why it said
"(none configured) · 0 sources". It now reports the console itself as the source, with the
folders it really scans.

### Changed — helper startup is framed instead of mysterious
The burst of `Debug:` toasts on the first run after a boot comes from the helper payloads
themselves (garlic, nanoDNS, ftpsrv, the install host) as they start. **Nothing in our process
can suppress another program's notifications** — so instead:
- We now say what is about to happen *before* launching them, and post **one** summary
  afterwards: `Mounts on · Installs on · FTP on`.
- The duplicate generic "ready" toast is gone — the summary replaces it.
- `GET /api/payloads/autostart?on=0` stops the shop launching helpers at all, so no third-party
  startup messages appear. Start them yourself from Payload Manager when you want them.
It only happens once per boot because on later runs the ports are already busy and nothing is
launched — that behaviour is unchanged.

### Added — copy a game from one PC to another
A peer already serves its library over HTTP with byte ranges (that is how the console installs
from it), so pulling a copy onto this machine is the same fetch written into the library folder.
**⋯ → Copy from &lt;PC&gt;** on any title that lives on another PC.
- Downloads to a `.part` file and renames only when complete, so an interrupted copy can never
  be mistaken for a finished game.
- Resumes with a Range request if a partial is already there, and verifies the final size.
- Runs as a normal queue job, so it shows progress and can be cancelled.

### Performance — the parts that actually apply to a console browser
- **The grid no longer rebuilds unless it changed.** It was torn down and re-created on every
  health tick. A cheap signature guards it, and a no-op render now leaves the DOM completely
  untouched — verified in-browser.
- **Cards are built in a DocumentFragment**, so 25 cards cost one layout and one paint instead
  of 25 of each.
- **Search is debounced** (140ms) — it used to filter and rebuild on every keystroke.
- **Stale-while-revalidate:** the last library is kept in localStorage and painted instantly on
  open, then replaced when the real one arrives, instead of staring at an empty grid.

Not applicable and deliberately not done: pgvector, SQL/vector search, quantization, connection
pooling, edge/object caching. This is a stdlib-only companion plus a C payload serving one HTML
file to one console over LAN — there is no database, no CDN and no embeddings, and adding them
would break the self-contained single-EXE design.

---

## [2.8.0] — 2026-07-27 · "Add-ons know their place"

### Fixed — the version flashed 1.1.1 on every load
The UI carried a hardcoded `APP_VERSION` used until `/api/health` answered, and it had been
left at an old number. Both artifacts bundle `web/` directly, so `tools/stamp_version.py` now
writes the real version into the page at build time from one source (`companion/server.py`),
and `build-wsl.sh` runs it. It also warns if the EXE and ELF versions ever disagree.

### Fixed — DLC and patches appeared as their own games
An update or DLC whose base game is nowhere — not in the library, not on the console — was
getting a full game card next to the real title, showing the ADD-ON's ids as if they were the
game's. Those are now hidden from the grid. An **Add-ons (n)** chip appears when any exist so
nothing is silently swallowed, and opening one says plainly which title it belongs to, with its
own id and content id visible — so a file that is actually for another region or release is
obvious instead of quietly wrong. When the base game arrives they attach automatically, because
grouping was always by title id.

### Added — multi-part packages install as one thing
Some releases ship split across `Part 1` / `Part 2` (or `pt2`, `[1 of 3]`, `.part2`, `Disc 2`).
Installing one piece leaves the title broken. Parts are now recognised as a **single library
entry** carrying an ordered `parts` list, and installing it queues every piece **in order** —
the queue already serialises per console, so they install one after another. Works the same
from a PC library, a peer PC or a USB stick, because expansion happens before the lane is
chosen.
- A release with a **gap** in its numbering is refused with an explanation rather than
  installing a knowingly incomplete set.
- Unit-checked against real names: `v1.04` and `[6.72]` in existing filenames are **not**
  mistaken for part numbers.

### Fixed — a freshly applied patch took several refreshes to show
`console_apps()` caches installed titles for 30s, so the panel kept reporting a patch as
pending until the cache aged out. Any job reaching "playable" now invalidates that cache and
bumps the library generation, so the UI updates as soon as the install lands.

### Added — cheats say which ones actually work on your build
Compatibility was per-FILE; it is now per-MOD. When the game is running, a mod whose bytes match
neither its ON nor its OFF state targets code this build does not have — it can never apply. Those
are greyed out and labelled **N/A** with the reason, instead of letting you press them and get a
refusal. Where the file does not match the installed build, the note now lists which versions
your library *does* have cheats for (`/api/cheat/list` and `/api/mods` both report `versions`).

---

## [2.7.0] — 2026-07-27 · "More than one PC, and your phone"

### Added — every PC on the network is one library
A second computer only has to **run the app**. It is found on the LAN automatically, and its
games join this library — no pairing, no URLs to type.

- **Stable identity.** Each install writes a device id into its config once and keeps it. A
  hostname is neither unique nor stable, so peers key on the id: rename the PC or let DHCP
  change its address and it is still recognised as the same machine, not a duplicate entry.
- **Discovery** sweeps the /24 for the companion port, then asks each responder to identify
  itself, so a library is only ever merged from something that really is one of ours. It runs
  in the background — a library request never waits on a scan.
- **One card per game, however many PCs have it.** Titles are merged by title id and carry
  `hosts` — who has a copy. Verified with two libraries sharing Castle Crashers: one card,
  `hosts=['ASUS-LAP','DESKTOP-PS5RIG']`, no duplicate.
- **Peer-only games install directly from the PC that has them.** The console fetches from that
  machine, so nothing is proxied through this one and the transfer does not depend on this PC
  staying awake. This is the piece that was missing: `peer_url` was being produced and never
  consumed, so installing a peer title returned *"unknown install_key"*.
- Panels show where a game lives (**Located on**, **Also on**, **Source PC**), and a card for a
  title that only exists elsewhere is badged with that PC's name.

So one machine can hold the PS4 library and another the PS5 library — or both hold both — and
the console sees a single merged catalogue either way.

### Added — control it from a phone
The companion already listened on all interfaces; what was missing was the address and the
ergonomics. **Settings → Devices on this network** shows the URL to open on a phone, this PC,
and every peer found. On a phone the mod toggles are now **62×43** instead of 50×35 (44px is
the minimum a finger reliably hits), and hover tooltips are suppressed on touch, where they
can never be shown and only get in the way. No desktop layout changed.

### Fixed
- `/api/devices` was already taken — it returns **storage drives**, and the drive selector
  depends on it. The new machine list is `/api/network`; adding it under the old name would
  have silently shadowed the drive list and broken install destinations.
- Settings scrolled again once the Devices block was added (columns 420 vs 659). Rebalanced to
  **552 / 425** and tightened the row spacing: **no scrolling**, verified at 720p.

### Not done yet
Moving a game *between two PCs* is not implemented — right now a peer's games are installed to
the console, not copied PC-to-PC. Say the word and that is the next piece.

---

## [2.6.1] — 2026-07-27 · "+ Queue actually queues"

### Fixed — queueing a USB package installed it immediately
The console-local lane I added in 2.6.0 called the installer straight from the request handler
and ignored `mode: "queued"` entirely, so **+ Queue** behaved exactly like **Install**.

It is a proper queue lane now, the same as every other install: `+ Queue` adds it **held**
("Held — press Start"), and it only runs when the queue is started. The job appears in the
queue with live progress, and cancel works while it is held.

Reused the existing `submitting` / `promoting` states rather than inventing an `installing`
one — a new name would have fallen outside the `RUNNING` set, quietly breaking both
"Clear done" and the parallel-install limit.

### Fixed — the console behaved differently from the PC
With the companion off, the on-console app had no queue at all (`/api/queue` always returned
an empty list), so the same button meant two different things depending on whether the PC was
running. The console now holds, starts, cancels and clears the same way, over both GET and
POST — the UI drives the queue with POST, which the console previously answered with
"not supported on console".

---

## [2.6.0] — 2026-07-27 · "Installing from a stick, properly"

### Fixed — a USB install produced an unplayable game
The USB path called `sceAppInstUtilAppInstallPkg`, which **only registers metadata**. It
returns in seconds, creates a dashboard tile, and leaves no game data — the tile then fails
with *"Cannot start the game"*. The download pipeline has warned about exactly this in a code
comment for months; the USB path I added simply used the wrong call.

A package already on the console now goes through the **same pipeline as a downloaded one**:
it is served over HTTP and handed to the install daemon, which fetches and installs it for
real. Verified on-device — `/mnt/ext1/user/app/CUSA03474/app.pkg` is **582,549,504 bytes**,
the complete package, with the same file layout (`app.pkg`, `app.pbm`, `app.json`, `app.xml`)
as a known-good install.

Three things had to be right for that to work:

- **A Range-capable file server.** The daemon HEADs for the size, then fetches with byte
  ranges. `/pkgfile/…` answers `HEAD`, `200` and `206 Partial Content` with `Content-Range`.
  Paths are restricted to package/backup files on drives we scan; traversal and anything else
  is refused (verified: `/etc/passwd`, `../../etc/shadow` and a `.html` all rejected).
- **Clean URLs.** The daemon **cannot fetch a percent-encoded URL**. A real name like
  `Star Wars Racer Revenge - LUA C0RE EXPLOIT - UP1082-CUSA03474.pkg` came back
  `{"res":"-1","error":"install failed"}`, while the identical request with a clean name
  returned `{"res":"0"}`. Packages are now served under a short opaque token
  (`/pkgfile/<n>.pkg`) — the same trick the PC path already used with `/library/<key>`.
- **Off the main thread.** The daemon installs **synchronously**: the POST does not return
  until it finishes. Running that on the accept loop froze the whole app — UI, cheat engine
  and even `/api/installed` stopped answering mid-install. Both the transfer and the install
  now run on their own threads, with `GET /api/install/status` for progress.

With the companion running, `local:` install keys are forwarded to the console instead of
being looked up in the PC file registry.

### Fixed — no notifications appeared on the PS5
Every toast had been silent since the app icon was added. The shell **accepts** the icon form
(`rc = 0`) but does not render it on this firmware — so the app reported success while nothing
appeared, which is the worst possible failure mode. All notifications are back on the plain
form, byte-for-byte what was confirmed working on-device.

The icon variant is kept behind `GET /api/notify?icon=1` so it can be compared on the TV
without putting the working path at risk again.

---

## [2.5.1] — 2026-07-27 · "The toggle that killed the app"

### Fixed — applying a cheat crashed the whole on-console app
Toggling a mod took the server down: the browser reported *"remote end closed the
connection"*, every later request failed, and no notification could appear because the
process was gone. The cause was a printf argument list, not the cheat engine:

```c
notifyf("%s is %s\n%s%d memory patch%s applied",   /* declares 5: %s %s %s %d %s */
        what, "ON", title, " - ", wr, "es");       /* passes 6                    */
```

`%d` consumed the `" - "` **pointer**, and the following `%s` then received the integer
`wr` and dereferenced it — a segfault inside `vsnprintf`. It only fired when a write
actually landed (`rc > 0`), which is why the same line survived every earlier test: those
runs returned `skipped=1` and took the "already in that state" branch instead.

The memory engine was never at fault. `mem_read`, `mem_write`, the page-walk, expect-gating
and address resolution were all working the entire time.

Both occurrences fixed, and **`notifyf` is now declared `__attribute__((format(printf,1,2)))`**
so the compiler rejects this class of bug instead of letting it reach the console. Every
formatted call in the file was audited: **191 checked, 0 mismatches.**

### Fixed — installing from a USB stick said "unknown install key"
With the companion running, the UI posts installs to the PC, which looked the key up in its
own file registry. A `local:` key points at a file already on the console, so it was never
going to be there. Those now route to the console app, which installs from the stick with
the same call it uses standalone (`lane: "console-local"`). Keys that genuinely are unknown
still return `unknown install_key` exactly as before.

### Verified after the fix
- Doom Eternal running (pid 724): 4 mods listed with live state, `compatible: true`.
- Toggle through the companion — the exact path that crashed — returns `written=1`, the app
  stays up, and re-reading the game's memory confirms the new state.
- Notifications deliver with `rc=0`, icon included.

---

## [2.5.0] — 2026-07-27 · "Removable media, told honestly"

### Fixed — a package on a USB stick claimed to be installed
`/api/installed` was built by scraping every `"title_id"` out of the library JSON, so a package
merely *sitting* on a stick came back as installed: green INSTALLED badge, "✓ Installed" in the
panel, "Reinstall base" as the action. It now comes straight from `app.db`, which only knows
about titles the console has actually registered.

### Fixed — the USB game was filed under "PC"
`isOnPC()` meant "has an installable copy", which a USB package also has. It now means what it
says — a copy in the **PC library folder** — and removable media is its own thing. Added a
**USB filter chip**, so the four categories are All / PS4 / PS5 / PC / USB.

### Added — PS4 and PS5 packages are no longer treated the same
They genuinely differ, and the app now says so instead of guessing:

| On removable media | What it is | Can it run there? |
|---|---|---|
| `.pkg` | a package to install | **No** — a PS4 title must be installed first |
| `.ffpfsc` / `.ffpfs` / `.ffpkg` | a ShadowMount backup | **Yes** — ShadowMount scans USB roots and `<drive>/homebrew` |

Each entry now carries the drive it is on (`USB 0`, `Extended Storage`, …), its real path, and
whether it runs in place. The card badge shows the **drive** rather than a bogus install state,
and the panel gained **Located on**, **Runs from here** and **Path** rows.

### Added — move a game between drives
A PS5 backup runs from wherever it sits, so "installing" one really means putting it on the
drive you want. **⋯ → Move to another drive…** lists every mounted drive with its free space and
copies on the console — no PC involved. It **copies, verifies the size, and only then deletes
the original**; a half-copied 90 GB game that had already removed its source would be
unrecoverable. Destinations are the paths ShadowMount actually scans (read from its own
`config.ini`), so a moved game is picked up with no further setup. Progress and completion are
reported by toast; `GET /api/move/status` has the live percentage.

### Fixed — duplicated games and phantom drives
Scanning drives for backups listed every `ext1` game a **second** time, next to the copy already
read from app.db (41 entries instead of 23). Removable entries are now skipped when the title is
already registered. Separately, an unmounted `/mnt/usbN` still stats fine because it resolves to
the parent filesystem, which listed **six drives that do not exist** with 0 GB free; a drive now
has to be its own filesystem (`st_dev`) to count. Result: **23 games, no duplicates, 3 real drives.**

### Changed — settings fit on one screen
The panel used a two-column auto-flow, which dropped both tall blocks into the same column and
forced scrolling. It now uses explicit columns, grouped as **Console · Services · Games library**
and **Cheat & mod library · Download sources · About**, with denser rows and long paths
shortened rather than wrapped. `[VERIFIED]` in a browser: content height equals the visible
height — **no scrolling** — with the two columns at 420 px and 442 px.
The "I own the content I install" checkbox is removed.

---

## [2.4.0] — 2026-07-27 · "The console app stands on its own"

The on-console app depended on the PC for things it could do itself. With the companion
switched off it showed title IDs instead of names, no versions, no cheats and dead settings
rows. All of it is fixed at the source. `[VERIFIED]` end to end with the PC companion closed.

### Fixed — the console could not read its own library
It built the library from **file names** (`/mnt/ext1/homebrew/*.ffpfsc`, `/user/appmeta/CUSA*`),
which is why panels showed `CUSA08692` as the game name with no region, size or version.

Real data lives in `app.db`, which is SQLite — the companion reads it with Python, the ELF had
no way to. It does now: a **read-only single-table SQLite scanner** (~240 lines: b-tree walk,
record decoding, overflow-page chains, column lookup parsed from the CREATE TABLE statement so
a firmware change cannot silently shift reads onto the wrong field). No writes, no journal, no
locking, no SQL. **22 titles now read with correct names, versions, regions, sizes and install
locations with the PC off** — identical to what the companion reports.

### Fixed — cheats were unreachable on the console
The UI is one codebase served from both the PC and the console, but the console answered only
its own `/api/cheat/*` names. The panel calls `/api/mods/<TID>`, which fell through to the
catch-all `{}` and rendered **"Can't reach the PS5 mod engine"** — while the engine was running
right there. The console now answers the same endpoint names as the companion:
`/api/mods/<TID>`, `/api/cheats/paths`, `/api/cheats/rescan`, `/api/sources`, `/api/consoles`,
`/api/queue`, `/api/config`.

**The server was also GET-only**, so every cheat toggle got a `405`. It now handles POST with a
correctly-drained body (`Content-Length` is authoritative — a short read here is the same bug
that once hung the PC side): `/api/mods/<TID>/toggle | apply | select | disable-all`.

### Fixed — settings showed dashes for services that were running
Those rows were filled in by the companion. The console now probes **its own ports** on
loopback (ShadowMount 9021, install host 12800, FTP 2121) and reports its real LAN address, so
`ftp://<ps5-ip>:2121` and the cheat folder paths are shown on the console itself.

### Fixed — loading the payload twice said nothing
`notify()` hands off to a detached thread; the "already running" path called it and then
returned from `main()`, killing that thread before it could deliver. Exit-path toasts are now
sent **synchronously** (`notify_sync`). The message says which it was and where to go.
Confirmed separately that the icon request is fine: the kernel returns **rc=0** for the
extended struct, and there is now a fallback to the smaller layout if it ever refuses.

### Added — install from USB with no PC at all
`/mnt/usb0..7` (root and `/pkg`) are scanned for packages, and `POST /api/install` installs one
straight from the stick through the installer already proven to work in this process. Packages
without a CUSA/PPSA id are skipped, so the PS3 packages on the same stick are not offered as
installable PS5 games. **The companion now shows the console's USB packages too** — matched by
title id so a stick copy never duplicates a title already listed.

### Removed
CheatRunner, completely: `/data/cheatrunner`, its Payload Manager copy and the Desktop ELF —
after verifying our own library was intact and that neither artifact depends on it (the ELF
never references port 9999; the companion falls back cleanly). Its process keeps running until
the next reboot, but it has no data left to serve. **The `CHTR09999` home tile is a system
registration and is not removed by deleting files** — delete it from the PS5 home screen if you
want it gone.

---

## [2.3.0] — 2026-07-27 · "Every game reads completely"

### Fixed — PS5 titles reported no version at all
PS4 and PS5 do not store the version in the same field, and we only ever read the PS4 one:

| Platform | Field | Example |
|---|---|---|
| PS4 (CUSA) | `APP_VER` | `01.04` |
| PS5 (PPSA) | **`CONTENT_VERSION`** | `01.011.000` |

Every `PPSA` title therefore came back blank, so the cheat engine could never confirm a match
and every PS5 panel showed no version. **Version coverage went 0/19 → 19/19 on PS5.**
Compatibility is now genuinely verified rather than assumed: 8 of 10 titles with cheats match
exactly, and the two that do not are real mismatches — DRAGON BALL: Sparking! ZERO (game
`02.000.017` vs cheats `01.000.000`) and Dark Souls Remastered (`01.00` vs `01.03`).

### Fixed — game panels were not equally complete
Whole sections silently vanished when a title had no copy in the PC library folder, which made
console-only games (nearly all the PS5 ones) look half-empty next to PS4 games. Every panel now
renders the same sections — **Details · Base game · Updates & Patches · DLC · On console ·
Mods & Patches** — with an honest explanation where a section is empty instead of no section.
`Base game` for a console-only title is deliberately **information only, with no install
control**, so nothing in that branch can reach the install path.

`Details → Version` now prefers what is actually installed over the version of the PKG sitting
in the library: a patched game reports `01.04` while its base PKG still says `01.00`, and the
installed one is the truth the cheat engine matches against.

### Added — notifications carry our icon and say something useful
The shell's notification request has icon fields we were not using. Toasts now show the app's
own artwork (`icon0.png`, already extracted to the console at boot) and describe what happened:
- `Inf Ammo is ON` / `DOOM Eternal · 2 memory patches applied`
- `Inf Ammo was already ON` / `Nothing needed changing`
- `Inf Ammo not applied` / `The game's code does not match this cheat file — it is probably
  built for another version. Nothing was written.`

### Added — cheat files are found automatically, wherever you put them
- Drop files in **`/data/pkg-mutant-shop/cheats/incoming`** (reachable at
  `ftp://<ps5-ip>:2121`) and they are filed automatically. A **`/cheats` folder on any
  plugged-in USB stick** is picked up too, and never deleted from the stick.
- Sorted by **what is inside the file, not its name** — a `.shn` saved as `.json`, or a `.mc4`
  saved as `.txt`, still lands correctly. `.mc4` is only accepted if it actually decrypts to
  Trainer XML. Unrecognised files are left alone rather than guessed at.
- `GET /api/cheat/paths` lists every watched location; **Settings → Cheat & mod library** shows
  them with a **Rescan cheats** button (`GET /api/cheat/rescan`) so new files are picked up
  without reloading the payload.

### Removed
- The old CheatRunner cheat and patch directories, after verifying our copies were complete and
  byte-identical (1954 json + 1760 shn + 1421 mc4 + 377 patches). CheatRunner's own app files
  were left alone — removing those is a separate decision.

### Performance
Measured rather than assumed: a cheat-file lookup takes **17 ms** against a **13 ms** bare
health-check baseline, so the per-request directory scan is already at network cost. No index
was added — it would have been complexity for nothing, and scanning fresh each time is what
makes a newly dropped file usable immediately.

---

## [2.2.0] — 2026-07-27 · "Every cheat format, and our own library"

Mutant Cheat Engine now reads **all three** cheat formats and keeps its library in our own
directory. `[VERIFIED]` on-device: 120 files parsed on the PS5 with zero failures.

### Added — .shn and .mc4 support
- **`.shn`** is a plain-XML "Trainer" document. **`.mc4`** is the same XML, base64-encoded and
  AES-256-CBC encrypted. Both convert to the JSON document the engine already parses, so the
  working JSON path is reused verbatim rather than duplicated.
- **AES-256-CBC decryption written from scratch** (decrypt only — we never write cheat files).
  The S-box is generated at runtime instead of carried as a literal table.
- Verified by converting `.shn` and `.mc4` for a title that also ships a `.json`: all three
  produce a **byte-identical** document.
- **This unlocked 5 more of the installed games** — Hogwarts Legacy (11 mods), Dying Light 2
  (15), Crash Bandicoot 4 (7), Crimson Desert, Dragon Ball: Sparking! ZERO — all of which
  previously reported no cheats because only `.json` was readable.

### Added — our own library directory
- The ELF creates **`/data/pkg-mutant-shop/cheats/{json,shn,mc4,patches}`** at boot and migrates
  the old CheatRunner tree into it on a background thread: **5135 cheat files + 377 patches**.
- The source directory is **left intact**. The copy costs nothing and means a failed migration
  can never lose someone's cheats; lookups fall back to it while the copy is still running.
- `GET /api/cheat/library` reports counts per format and migration status.

### Fixed
- **Toggling a mod no longer redraws the panel.** It rebuilt the whole drawer on every click,
  which read as a flash. Conflicts still change which mods can be toggled, so state is still
  re-read after every write — it now patches the existing rows in place. Verified with a
  MutationObserver: **0 rebuilds**, and the row survives as the same DOM node.
- **Limits were below what the real library contains.** Measured across a 400-file sample:
  74 mods in the largest file (cap was 64 — the rest were silently dropped), 41 entries in the
  largest mod, and a 520-byte patch (cap was 256). Now 256 / 128 / 1024. Entry arrays moved to
  the heap; at the new size they would have blown the thread stack.
- `hex2bytes` accepts the `90-90-90-90` separators these formats use. JSON files contain none,
  so that path is bit-for-bit unchanged.

### Safety
- `.shn`/`.mc4` offsets are ambiguous — most image-relative, a few absolute, with nothing in the
  file reliably saying which. We resolve it by **reading both candidates** and keeping whichever
  already holds a state the file documents. Relative is probed first because a raw absolute
  offset can land on GPU/MMIO and hang the game on a mere read. Entries whose ValueOff is one
  repeated byte (a zero-filled code cave — 38% of real entries) cannot discriminate, so the mode
  is decided from a mod's distinctive entries and applied to the rest.
- If nothing is conclusive we use relative and let expect-gating decide: a wrong guess **fails
  closed** rather than writing over unrecognised code. JSON addressing is untouched.

---

## [2.1.2] — 2026-07-27 · "The mods panel actually renders"

The real reason cheats never showed. `[VERIFIED]` end-to-end in a browser against running Doom.

### Fixed
- **The whole mods panel crashed whenever it tried to show a note.** `cheatNote()` returns HTML
  *text* — it is assigned to `innerHTML` everywhere else — but four call sites passed it to
  `body.appendChild()`, which throws `TypeError: parameter 1 is not of type 'Node'`. The panel's
  `.catch()` swallowed the error and printed **"Could not read mods."**, so a backend that was
  returning four mods correctly looked like a title with no cheats.
  This was latent from the start: before 2.1.1 the `!compatible` note tripped it, and after
  2.1.1 the new "version can't be confirmed" note tripped it in exactly the same way.
  Notes now go through `addNote()`, which uses `insertAdjacentHTML` — the string/Node mismatch
  cannot recur. Audited every other `appendChild` call site; `cheatNote` was the only offender.

### Verified
- Doom Eternal (PPSA01981, running): panel lists 4 mods with live state, toggles enabled.
- Toggling **Inf Jumps** from the UI and re-reading the console's memory returns `on`.

---

## [2.1.1] — 2026-07-26 · "Cheat search that doesn't give up"

Fixes a title with cheats reporting **"no cheats"**. Doom (PPSA01981) was the case that exposed
it, but every PS5 title was affected. `[VERIFIED]` on-device against a running game.

### Fixed
- **An unknown game version was treated as a version mismatch.** PS5 (`PPSA…`) titles often
  expose no `APP_VER`, so `installed_version` came back empty and every cheat file was flagged
  incompatible. Not knowing a version is not the same as knowing it's wrong — the mismatch
  warning now only fires when we can actually prove a mismatch. The per-write safety check
  (memory must already hold the expected bytes) is unchanged and still gates every write.
- **Mods could only be found for the game currently running.** For any other title the request
  fell through to CheatRunner, and if that wasn't loaded the panel dead-ended with
  "CheatRunner is not running". Our engine now resolves cheat files for **any** title.
- **The installed version was never sent to the file picker**, so `/api/cheat/running` could
  never hit its exact-match branch and always reported `other version`. It's passed now —
  Doom resolves as `exact version`.
- **Empty-state text still named CheatRunner**, a dependency we removed in 2.0.0.

### Added
- `GET /api/cheat/find?title=<TID>[&version=]` (on-console ELF) — the cheat file we'd use for a
  title, running or not. Same precedence as before: exact version → generic `<TID>.json` →
  any other version. Title ids are character-filtered before being echoed into JSON.
- Mods for a **non-running** game are now listed read-only, with toggles disabled and a tooltip
  explaining the game must be launched (writes go into its live memory).
- Compatibility banner has three states instead of two: **Matched** (verified),
  **Loaded** (console reports no version — can't confirm), **Version mismatch** (proven).

---

## [2.0.0] — 2026-07-26 · "Mutant Cheat Engine — our own"

Cheats, mods and patches now run on **our own engine**. Nothing in the running-game path
depends on a third-party service any more.

### Added — the engine (in the on-console ELF)
- **Memory I/O without ptrace** — the game is never stopped. Reads via `mdbg_copyout`. Writes
  can't use `mdbg_copyin` (broken on FW 8.20+), so we resolve the address ourselves:
  `proc → p_vmspace → pmap → {pm_pml4, pm_cr3}`, derive the direct-map base from their
  difference, walk PML4→PDP→PD→PT, then `kernel_copyin` to the physical page. This bypasses
  page protection, which is required — cheats patch read-only code pages.
- **Cheat parser** — hand-rolled scan of the real schema (`mods[].memory[]` with
  `offset`/`on`/`off`; offsets are hex without `0x`, applied at `eboot_base + offset`).
  No JSON library, so the payload stays dependency-free.
- **Apply / revert with a hard safety gate.** Before writing `on` it verifies memory currently
  holds `off` (and vice versa). Code that doesn't match what the cheat expects is **refused,
  not forced** — a cheat built for another build cannot corrupt unrelated memory. Entries
  already in the target state are skipped. `force=1` is opt-in. Every call reports
  `entries/written/skipped/failed`.
- **Our own running-game discovery** — `sceSystemServiceGetAppIdOfRunningBigApp()` for the app
  id, `sysctl KERN_PROC` (ki_pid at +72) matched against `sceKernelGetAppInfo` for the pid, and
  `kernel_dynlib_mapbase_addr(pid, 0)` for the image base instead of assuming `0x400000`.
  Verified to agree exactly with the reference implementation.
- Endpoints: `/api/cheat/running`, `/api/cheat/list`, `/api/cheat/apply`, `/api/mem/read`,
  `/api/mem/write` (the last gated by `expect=`).

### Fixed
- **Notifications never rendered.** `sceNotificationSend` with a JSON payload returns 0 and
  draws nothing. The shell only renders `sceKernelSendNotificationRequest` with a flat struct
  (45 reserved bytes + message), sent on a detached thread. Toasts now appear on the TV —
  mod on/off and patch-applied included.
- **Mods always showed "off".** The engine reports `state`/`label`
  (`"on"|"off"|"conflict"`) plus `canToggle`/`conflictsWith` — not `on`/`enabled`. Reading the
  wrong fields made every mod look disabled even when active. Conflicts are now surfaced, with
  the conflicting mod named in the tooltip.
- A cheat file must be **selected** before its state is meaningful; this is now automatic.
- The running game sorts to the front of the library with a **Running** badge, and returns to
  its normal position when it stops.

### Verified on-device (Castle Crashers CUSA14409, pid 437, base 0x400000)
`engine=mutant` · discovery matches · `apply mod0 ON → entries=2 written=2 skipped=0 failed=0`
· `revert → written=2` · TV toasts shown.

### Licence
The engine is derived from CheatRunner (GPL-3.0), which the project owner authorised. At
release PKG MUTANT SHOP must ship under **GPLv3 with source**.

### Not yet
`.shn` / `.mc4` cheat formats. Titles that are not running still browse through CheatRunner
(applying to a stopped game is meaningless), so it remains a fallback rather than a dependency.

---

## [1.3.0] — 2026-07-26 · "Installs work, unattended"

Confirmed on-device: games download from the PC and install to the PS5 as **playable titles**,
multiple in a row, with nothing loaded or reloaded by hand.

### Fixed — the mistake that caused ~12 rounds of false diagnosis
- **Installed games live in `/mnt/ext1/user/app/<TID>/` (Extended Storage), not `/user/app/`.**
  A missing `/user/app/<TID>` was being treated as proof that installs were fake. It wasn't — it
  was the wrong path for this console. Real installs verified: `app.pkg` at 4.05 GB / 7.27 GB plus
  `app.pbm`, `app.pbm.backup`, `app.json`, `app.xml`. Always check BOTH locations
  (`contentLocation=2` in app.db means the title is on ext storage).
- **`sceAppInstUtilAppInstallPkg` registers metadata only** — it creates `/user/appmeta/<TID>` and
  no game data, which is what produced dashboard tiles that failed with "Cannot start the game".
  It is no longer used as an install path.

### Added
- **The app starts the install host itself** (`Ps5Bridge.ensure_dpi_host()`): if the install
  daemon isn't listening when a game is queued, the app launches it via Payload Manager and waits
  until the install lane actually answers. No manual payload loading.
- Combined with the existing wedge recovery, a queue of several games now runs start-to-finish
  unattended: host started on demand, daemon healed between installs, smallest-first ordering.
- Reclaimed ~15 GB on the console: the old local download cache under
  `/data/pkg-mutant-shop/install/` is not used by this path (the host fetches the PKG itself).

### Verified end-to-end
Install host NOT running → queued Marvel + Dark Souls → app started the host → both installed
unattended → both **playable**, both confirmed with full data on ext storage.

### Still open (enhancement, not a blocker)
Our own in-process installer still gets `0x80B2116F` from `sceAppInstUtilInstallByPackage`
(12 hypotheses eliminated). The app owns the download, queue, UI, console app, auto-start and
auto-recovery; only the final install call is delegated. See the INSTALL BLOCKER note for the
full investigation, the directory-install recipe, and two hard "do not repeat" hazards.

---

## [0.19.0] — 2026-07-26 · "Our own install engine — Elf Arsenal no longer required"

PKG MUTANT SHOP now downloads and installs packages **entirely by itself**. No DPI daemon, no
Elf Arsenal, nothing to wedge — the failure mode 0.18.0 recovered from can no longer occur,
because the component that wedged is gone from the path.

### What we learned first (from Arsenal's source, which is GPLv3)
Their code is **GPLv3+**, so none of it is used here — only the *mechanism*, which is fact:
API names, ABI struct layouts, error codes, and the order they try things in. Our implementation
is our own, and the app stays free of copyleft.

- **DPI v2 (:12800) installs nothing.** It is a thin HTTP shim that forwards to Arsenal's main
  process on `127.0.0.1:6969` and blocks with a **600-second** timeout. *That* is what the "wedge"
  always was — a synchronous install holding the socket, not a corrupted daemon.
- **Their reliability is a fallback ladder, not privilege.** Verified live: the same URL installed
  via `InstallByPackage` once, then minutes later fell back to download-then-`AppInstallPkg` (102s).
  `InstallByPackage` is unreliable for them too.
- **0x80B2116F was never a credentials problem.** Dumping ucred with `/api/cred?pid=N` showed our
  server is byte-identical to Arsenal's privileged processes — authid `0x4801000000000013`, caps all
  `ff`, attrs `00 00 00 80 …`. (So `attrs[3] |= 0x80` is right; setting `attrs[0]` too was a wrong
  guess, now reverted.)

### Added — the engine
- **`GET /api/engine/install-url?url=&name=&dest=`** — we stream the PKG to console storage
  ourselves, then register it with `sceAppInstUtilAppInstallPkg`, the one call that works from this
  process (it is what installs our own tile). Runs on a worker thread; the request returns at once.
- **`GET /api/engine/job`** — live `state / pct / done / total / content_id`, so progress is real
  bytes measured on the console instead of inferred.
- **`GET /api/engine/install-local?path=`** — install a PKG already on the console (USB → internal).
  Fully offline: no PC, no network, no Arsenal.
- **Resumable downloads.** `Range` resume, and the remote size is checked with a `HEAD` *before*
  resuming — asking for bytes past EOF on an already-complete file earns an HTTP 416 that looks
  exactly like a failure. 416 is also handled as "already complete".
- Content id is read from the PKG header (offset `0x40`), and `/data/…` is rewritten to
  `/user/data/…` because the install service is sandboxed and cannot see `/data`.
- Companion `dpi.mode` now defaults to **`pms`** and routes installs to our engine.

### Fixed
- **Premature "Ready to play".** The engine keeps the last job's result until a new one starts, so
  the queue read a *previous* completed job as its own and marked fresh downloads finished within
  seconds. Jobs now carry an **id**; the companion only trusts progress matching the id it was
  given, and waits for a free engine instead of being refused.
- Engine failures surface immediately instead of sitting through the generic 90-second
  "console isn't pulling" timeout.
- **Don't link `-lSceAppInstUtil`** in a payload: it isn't mapped into a freshly spawned process, so
  the loader drops the ELF before `main()` — the process appears in the list and executes nothing.
  The SPRX is loaded and its NIDs resolved at runtime instead.
- On-console builds self-replace via `/api/quit` (a reloaded ELF used to exit with "Already running",
  leaving the *old* code serving :8710).

### Verified on-device (FW 12.70, both titles deleted first)
- **DARK SOULS 7.27 GB** — downloaded in 183 s (~40 MB/s), installed `rc=0x00000000`,
  contentId `UP0700-CUSA08692_00-DARKSOULSHD00000`, and `CUSA08692` appeared in the installed list.
- **MARVEL 4.05 GB** — back-to-back immediately after, 103 s, `rc=0x00000000`,
  contentId `UP0102-CUSA04622_00-UMVC3FULLGAME000`. No reload between them.

### Known limits
- **Plain HTTP only.** That is what the companion serves; HTTPS would need a TLS stack in the
  payload. Remote `https://` sources are not supported by our engine yet.
- Installs do not appear in the PS5 download list (that is a property of `AppInstallPkg`).
- One package at a time; the queue serialises and waits.

---

## [0.18.0] — 2026-07-25 · "The install engine heals itself"

Multi-install is now **hands-free**. 0.17.3 could only *pause* on a wedged DPI daemon and ask for a manual
payload reload; the app now performs that reload itself, on both sides, and resumes the queue.

### How the reload was found (on-device research)
- The DPI's API is exactly **one endpoint** — `POST /api/install`. Everything else 404s, so there is **no
  in-band reset**. Relaunching the payload is the only real fix.
- **Payload Manager (pldmgr v0.5.0) is LAN-reachable on `:8084`** and exposes `/processes_list`,
  `/process_kill?pid=N`, `/list_payloads`, `/loadpayload:<path>` — the same route it uses itself
  (`"[PLDMGR] Sending ELF to local loader" / "Sent %zu bytes to loader"`, i.e. raw ELF bytes to elfldr).
- The ELF loader (`:3232`, and `:9021` on localhost) **only honours 127.0.0.1** — a push from the PC is
  accepted then RST. That is why the on-console path exists.
- **The install daemon is `dpiv2.elf`, a process of its own.** Relaunching elf-arsenal alone does *not*
  replace it: verified that a relaunch cycled `dpi.elf`/`ftpsrv`/`nanodns` while `dpiv2` kept its pid and
  `:12800` never dropped — the old instance still owns the port, so a fresh one cannot bind.
  **Killing it first is what makes the reload real.**
- The DPI v2 server is **compiled into `elf-arsenal.elf`** (its `dpiv2`/`12800`/`/api/install` strings live in
  the main binary; there is no separate payload). So while we depend on Arsenal, any DPI restart means
  relaunching Arsenal — a silent, DPI-only reload is not possible without our own engine.

### Added
- **Automatic DPI recovery — no user interaction.** `PayloadManager` + `Ps5Bridge.recover_dpi()`: kill the
  process holding the install port, wait for it to free, relaunch the payload, then wait for readiness.
  Measured **~4.6 s** end to end from the PC and **~3.6 s** on-console. `[VERIFIED]`
- **A readiness probe that tells the truth.** A bound port proves nothing — the wedged daemon still accepts
  sockets and still answers HEAD in ~14 ms. `dpi_install_ready()` POSTs a deliberately un-fetchable URL:
  healthy rejects it in ~15 ms (`{"res":"-1","error":"install failed"}`), wedged never answers. Nothing is
  ever queued or installed by the probe. `[VERIFIED]`
- **Self-healing queue.** Before each handoff the worker checks the install lane (~15 ms when healthy) and
  reloads only if genuinely wedged, so a healthy console is never disturbed. On a wedge mid-install it
  reloads and re-drives the same task once (`_healed`, so it can never loop). Holding the queue is now only
  the last resort, when recovery itself fails. `[VERIFIED]`
- **Works with the PC off** — the on-console ELF (v1.2.0) does the same thing for offline flows (USB →
  internal): `GET /api/dpi/reload`, `/api/dpi/status`, `/api/dpi/probe`. It drives Payload Manager over
  localhost, and falls back to pushing the payload straight into the localhost ELF loader. `[VERIFIED]`
- `POST|GET /api/dpi/reload` and `/api/dpi/status` on the companion (both verbs, so one UI call works whether
  the page is served by the PC or the PS5), plus a **⟳ button** in the queue dock for a manual reload.
- Queue shows a distinct amber-pulse **"reloading"** row while the engine is being restarted.

### Fixed
- **A dead engine no longer fakes a successful install.** `online = bridge.ping()` tests the *DPI port*, so a
  dead daemon sent the task down the offline branch and "installed" it as a **simulation** — reporting
  success for something that never happened. Now, if the console answers on FTP but the install port is
  dead, the engine is started first and the real path is used. Caught by the end-to-end test. `[VERIFIED]`
- **On-console upgrades actually take effect.** A newly loaded build used to hit "Already running" and exit,
  leaving the *old* code serving `:8710`. The new build now asks the running one to stand down
  (`/api/quit`) and takes the port. `[VERIFIED]`
- `/api/dpi/reload` hung until the client timed out: `do_POST` already reads the body, and the handler read
  it a second time, blocking on bytes that never come. `[VERIFIED]`

### Verified on-device
- Engine killed dead → install queued → **`reloading` → `submitting` → real `Downloading 4%` in 7.6 s**, no
  interaction, and not a simulation.
- Marvel (4.05 GB) installed → **`playable`**; Dark Souls (7.27 GB) queued behind it installed back-to-back.
- DPI measured **ready in 5 ms immediately after a 4 GB install** — the wedge is intermittent, not
  guaranteed, which is exactly why the cheap probe-then-heal design beats reloading after every install.

---

## [0.17.3] — 2026-07-25 · "Graceful wedge handling + reopen fix"

### Fixed
- **Panel reopen crash** — the 2-column restructure moved the action bar (`.dfoot`) inside `#dbody`; the next
  open's `innerHTML=""` destroyed it, so `openDrawer` threw on a null element and the panel wouldn't reopen
  until refresh. Now the action bar is rescued out of `#dbody` before the clear. `[VERIFIED]`
- **Multi-install wedge — now graceful.** Diagnosed on-device: the Elf Arsenal DPI daemon on 12.70 wedges its
  *install machinery* (AppInstUtil) after one heavy install and does NOT recover on its own — HEAD returns 400
  in 14 ms, but `POST /api/install` hangs 15 s. It can only be reset by reloading the payload on the PS5 (we
  can't do that from the PC: on-console loader + a blocked kernel-cred write). So instead of cascade-failing
  every queued game, a wedge now **HOLDS** the failed task + all pending installs (`Queue._pause_pending`) with
  a clear *"⏸ reload the DPI payload, then ▶ Start"* prompt (amber). Reload Elf Arsenal once, press ▶ Start,
  and the whole queue retries small-first. Verified: queue DS+Marvel → first wedges → both settle **held**, not
  errored. `[VERIFIED]`
- Confirmed the B5 health HEAD-probe is safe (doesn't wedge the daemon — it answers HEAD with a fast 400).

---

## [0.17.2] — 2026-07-25 · "Read the REAL console data + 2-column panel"

Second on-device test: Marvel installed, then the DPI daemon **wedged** and Dark Souls failed — B5 correctly
detected it ("daemon is wedged, reload"). Plus a batch of data/layout fixes driven by inspecting the real
`app.db`.

### Fixed / changed
- **Sizes/format/region are now REAL.** Pulled `app.db` and confirmed `tbl_contentinfo.size` == `AppInfoJson
  #_size` (it is NOT bogus — the small numbers were genuine stub installs). More importantly, most titles are
  **ShadowMount backups**, and the backup scan was only looking at one path. Now `console_backups` scans **all**
  real locations (`/data/homebrew`, `/mnt/ext0|ext1/homebrew`, `/mnt/usb0-7/homebrew`), so games show their real
  **backup size + `.ffpfsc` format** (Crash 4 22.6 GB, Crimson Desert 95 GB, DOOM 89 GB…). `console_apps` now
  also reads `contentId` → **region** and `contentLocation` → location. This was the real "not connected" bug.
- **2-column detail panel** (per request): **Details** (+ Base/Updates/DLC) on the **left**; **Actions bar
  (Install to ▾ · Install · ＋Queue · ⋯) + On console + Mods & Patches** on the **right**. Storage selector is
  now compact (in the right bar), panels are tighter (less wasted space), single→multi handled with a grid.
- **Wedge is now recoverable** — failed queue rows get a **↻ Retry** button (reload the DPI payload on the PS5,
  press retry; small-first ordering keeps it after anything running). The daemon wedging after one heavy
  install is an Elf Arsenal/12.70 limitation we can't remove, but the queue now pauses gracefully instead of
  dead-failing.
- Cards show real sizes again (reverted the earlier "—"). Both artifacts rebuilt to the Desktop; install path
  verified byte-perfect.

---

## [0.17.1] — 2026-07-25 · "Multi-install fix + card/panel polish (first on-device test)"

First real on-device test of the ELF: queue worked, Dark Souls installed perfectly, but Marvel (first in
the smallest-first queue) hit `HTTPError 400` immediately. Root-caused + fixed, plus the requested card/panel
fixes. `[VERIFIED]` on the PC; multi-install to re-test on-device.

### Fixed
- **Multi-install 400 (regression from 0.17.0's H1).** A freshly-loaded DPI daemon's FIRST POST transiently
  returns 400 before it settles; my H1 change had made *any* response non-retryable, so the first queued
  game failed. Corrected the retry policy in `_install_http`: **a 400 / rejection / connection-refused means
  nothing was queued → safe to retry**; only a read-timeout/reset (install maybe in flight) is not retried.
  So the cold-start 400 now recovers and both games install. `[VERIFIED build]`
- **Wrong size on cards.** `tbl_contentinfo.size` is a bogus metadata value; `build_library` now reports the
  real size only when we truly have it (PC pkg or ShadowMount backup) and `size_known:false` otherwise, so
  the UI shows "—" instead of a wrong number. PC-installable + backup titles show correct sizes.
- **Cards decluttered** — the 📍 install-location moved off the card (it stays in the panel).
- **Panel enriched** — added Version, File, Updates count, DLC count cells; nicer Format label (e.g.
  "Installed app" for DPI-installed titles instead of "—"); all values HTML-escaped.
- **PS5 panel perf** — dropped the CSS multi-column detail layout (`columns:2` reflowed slowly on PS5
  WebKit → single column) and deferred the FTP-backed cheat read until after the drawer opens, so the panel
  opens snappily.
- Both artifacts rebuilt + delivered to the Desktop (`PKG-MUTANT-SHOP.exe` / `.elf`); install path verified
  byte-perfect (Range @5 GiB = HTTP 206, identical).

---

## [0.17.0] — 2026-07-25 · "The overhaul — 12 batches, install path untouched"

A large, blueprint-driven feature overhaul (13-agent audit+design pass → `ROADMAP-OVERHAUL.md`). Every new
feature defaults to today's behavior; the working install handoff (`{"url":url}` → Elf Arsenal DPI v2 →
confirm) was never altered. All PC-verified against the live server; on-device items flagged. `[WIRED]`

### Added / changed
- **B1 Frontend safety + PS5-WebKit compat** — `hsl()` comma syntax (card covers render on WebKit), HTML-
  escaping everywhere (`esc()`), 2-arg `classList.toggle`→explicit, `closest`/`remove` polyfills,
  `-webkit-backdrop-filter`; **Circle/Back history sentinel** (closes the open panel, not the app);
  "Reinstall base" no-op fixed; install button no longer sticks on "Queued…". `[VERIFIED]`
- **B2 Install-pipeline hardening** — H1: `_install_http` retries **only** on connection-refused (a read
  timeout/response is final → no double-submit); L3 atomic JSON writes (`_atomic_write_json`); L4 FTP leak.
- **B3 UI foundation** — connection **LEDs** (Companion/LAN/FTP/DPI), dynamic version from `/api/health`,
  About section (real data), restyled ownership checkbox.
- **B4 Perf/cleanup/auto-rescan** — debounced **auto-rescan** (add/remove picked up ~10s), atomic scan swap
  (no 404 window), tunable 1 MiB serve buffer (Range still byte-perfect at 5 GiB), `sweep_temp_dbs`,
  `library_gen`→UI auto-refresh. `[VERIFIED]`
- **B5 Honest install-ready** — `dpi_live()` HEAD probe → `dpi_state` live/wedged/down (real daemon answers
  HEAD); DPI LED 3-state. `dpi_online` kept tied to reachability so a non-HEAD daemon can't false-blank it.
- **B6 Storage & devices** — `/api/devices` (PC drives + PS5 internal/ext0/ext1/usb0-7), the picker now
  offers **all destinations** (the "only ext1" fix); `/api/library/prepare` builds `Games/PS4|PS5`.
- **B7 Integrity** — `pkg_meta.pkg_completeness()` (pfs image fits EOF + high-entropy tail), pre-handoff
  verify (default **warn**, never blocks), `/api/verify/<key>`, ⋯-menu "Verify integrity".
- **B8 Smart queue** — install-now vs **＋Queue** (held), **▶ Start queue** releases smallest-first.
- **B9 Multi-PC federation** — `/api/federation` self-advertise + merged/deduped library (default off).
- **B10 i18n** — 7 languages (EN/ES/PT/FR/DE/JA + Arabic **RTL**), live switcher, localStorage persist.
- **B11 Multi-interface networking** — `companion_ip_for(target)` picks the PC IP on the console's subnet
  (Wi-Fi + Ethernet); single-NIC byte-identical.
- **B12 Clean reinstall** — ⋯-menu Clean-reinstall/Delete via a **guided** flow (gated `capabilities.uninstall`).
- **Ship — both artifacts rebuilt** so the actual workflow (load the ELF, run the EXE) carries everything:
  - **EXE** (PC companion): rebuilt with PyInstaller and installed to the Desktop path you run
    (`PKG-MUTANT-SHOP.NEW.exe`; old one kept as `.bak`), verified serving the new UI. `[VERIFIED]`
  - **ELF** (on-console): the **entire web UI is now embedded in the ELF** (`gen_web_bundle.py` →
    `web_bundle.h`, ~806 KB) and **self-extracted to `WEB_ROOT` on boot** — so loading the updated ELF
    alone delivers the new UI to the PS5, no separate push. On-console `/api/health` now reports `version`.
    Rebuilt via WSL prospero-clang; ELF 140 KB → 926 KB with the UI embedded, verified to contain
    `langSel`/`queueBtn`/`data-i18n`. `[WIRED]` (on-device load pending)
  - `deploy.py` WEB_ROOT gap also fixed as a fallback path.

### Needs on-device / your input
- **Load the rebuilt ELF** (`ps5-app/onconsole/pms-onconsole.elf`) via your payload loader — the PS5 gets the
  new UI automatically (self-extract). Then: on-device regression gate (Marvel + DS); confirm Circle-back +
  install-to-USB on the TV; B12 auto-uninstall NID/creds (else guided-manual stays). Deferred: B2 M2/M3/M4/L2,
  meta-cache, remote pre-fetch staging.

---

## [0.16.2] — 2026-07-24 · "Dark Souls 0% wasn't a decline — the DPI daemon was wedged"

Second real install after Marvel. **Ultimate Marvel vs. Capcom 3 installed fine; Dark Souls: Remastered stuck
at 0%** with *"Install engine declined it — is Elf Arsenal loaded on the PS5?"*. Chased it end-to-end with
direct probes from the companion box.

### Root cause (proven, not guessed)
- Companion side is healthy: after rescan the PKG serves **HTTP 200, 7,274,168,320 bytes, `Accept-Ranges: bytes`**.
- Port **12800 accepts TCP** (so `ping()`/"install-ready" go green), **but `POST /api/install` never returns a
  single byte** — the connection is aborted after a multi-second wait. The **DPI daemon is wedged**, almost
  certainly hung after the preceding Marvel install (payload instability on 12.70). `bridge.install()`'s three
  timeouts were being reported as "declined."
- **Not** the filename. Marvel is `…CUSA04622.ByCisso.pkg` (dots), Dark Souls was `[CUSA08692][BASE]-[DARKSOULS RE].pkg`
  (spaces + `[ ]`) — a plausible red herring, but the direct probe shows the daemon never even reads the URL. We
  percent-encode correctly (`_file_url`/registry key both `quote()`), and the clean-named file serves fine.

### Fixed
- **Failure reporting now names the actual mode.** `Queue._run` no longer prints the generic
  *"is Elf Arsenal loaded?"* for every failure. It inspects how `install()` failed and distinguishes three cases:
  **(a) port not listening** → "isn't listening — is the DPI payload loaded?"; **(b) port open but the request
  timed out/aborted** → *"accepts connections but isn't responding — the daemon is wedged; reload the DPI payload,
  then retry"* (the case we hit); **(c) a real rejection payload** → surfaces the daemon's `error`/`res`, plus a
  filename hint only if the URL actually contains spaces/brackets. Logs `[install] fail name=… url=… res=…` to the
  companion console and stores `detail` on the task. `[WIRED]`

### Housekeeping
- Renamed the library file to `DARKSOULS-REMASTERED-CUSA08692.pkg` (URL-safe). Not the fix, but good hygiene —
  artwork/title come from the PKG's param.sfo (`CUSA08692`), so the tile is unaffected.

### Known gap / next
- **`ping()` is not a liveness check.** A bare TCP connect passes against a wedged daemon, so the header can read
  "install-ready" when installs will actually time out. Next: make the DPI health check issue a real lightweight
  request with a short timeout (cached) so "install-ready" means *the daemon answered*, not *the port is open*. `[STUB]`

---

## [0.16.1] — 2026-07-22 · "The missing UserService init"

On-device re-test: shop payload showed *"Opening the shop…"* but the browser never opened; tile installer showed
no message at all. Root-caused both against the SDK samples.

### Fixed
- **Browser never launched** — `main.c` never called `sceUserServiceInitialize(0)` before
  `sceSystemServiceLaunchWebBrowser`. The SDK's `samples/browser/main.c` always initializes UserService first
  (in a constructor). Added it as the first line of `main()`, linked `-lSceUserService`, and now the launch
  **return code is reported on-screen** (`Browser launch failed: 0x…`) for both auto-open and the `open`
  command — so the next test is conclusive either way. `[WIRED]`
- **Tile installer showed nothing** — I had added `sceUserServiceInitialize(0)` *before* the first `notify()`;
  the SDK `install_app` sample never calls it, and it appears to fault in payload context before anything can
  display. Removed it; `notify("Installing tile…")` is now the literal first statement. `[WIRED]`

---

## [0.16.0] — 2026-07-22 · "Make it OPEN, and tell me why the tile failed"

On-device testing (real PS5, FW 12.70): the notification payload **works** (toast confirmed on TV). But the
tile "did nothing" and the shop never opened. This release fixes both — and makes the tile installer explain
itself instead of failing silently.

### Fixed
- **Shop now opens on load.** `ps5-app/payload/main.c` (`pkg-mutant-shop.elf`) previously only *notified* on
  load and opened the browser only on a TCP command. It now auto-calls `sceSystemServiceLaunchWebBrowser(PMS_URL)`
  on load (`PMS_AUTOOPEN=1`), so loading the payload opens PKG MUTANT SHOP. `[WIRED]` (browser-launch itself
  is what we're validating on-device).
- **Double title in toasts.** The companion prepended "PKG MUTANT SHOP\n" while the payload's toast already
  shows it as the sub-message → "PKG MUTANT SHOP / PKG MUTANT SHOP …". Companion now sends clean text
  (`web/index.html`: test-notify + mod ON/OFF). `[VERIFIED]` (companion side)

### Added — tile debugging
- **Self-diagnosing installer** `ps5-app/tile/tile-install.c` replaces the silent SDK sample. It now reports
  on-screen at every step: `Installing tile…` → checks the FTP'd files exist (`No app files on console — run
  deploy-tile.py first`, the #1 cause of "nothing happened") → exact `Register failed: 0x….` codes →
  `Tile installed!`. Links `-lSceNotification`. `[WIRED]`
- **Correct install ordering.** `deploy-tile.py` no longer pre-uploads the system-side `param.json`; the
  installer writes it to `/system_ex/app/PKGM00001/sce_sys/param.json` **after** registration (matches the SDK
  `install_app` sample's `test` target exactly). `[WIRED]`
- **"Open shop on PS5" button** (`web/index.html`) + `POST /api/open-ps5` + `Ps5Bridge.open_shop()` — sends the
  `open` command to the loaded payload so you can test the browser-launch from your PC without reloading a
  payload. `[WIRED]`

### Messages
- On-screen text is now customizable via build defines (`PMS_MSG_OPENING`, `PMS_MSG_READY`, `PMS_AUTOOPEN`).

---

## [0.15.0] — 2026-07-22 · "A dashboard TILE with your logo"

Built a real, fake-signed **homebrew app** — a tile on the PS5 dashboard with the user's logo that opens the
app. Same no-sudo WSL toolchain.

### Added — `ps5-app/tile/`
- **`PKGM00001/eboot.bin`** — fake-signed app executable that calls `sceSystemServiceLaunchWebBrowser(PMS_URL)`
  → launching the tile opens PKG MUTANT SHOP in the PS5 browser. Compiled dynamically, then ELF type patched
  `ET_DYN → ET_SCE_DYNAMIC` (what `make_fself` + the PS5 app loader require) and fake-signed with `make_fself.py`.
- **`install-tile-payload.elf`** — registers the app via `sceAppInstUtilAppInstallTitleDir` → `/user/app/` (the
  PS5's own installer; no etaHEN).
- **`PKGM00001/icon0.png`** (your logo) + `sce_sys/param.json[.system]` (titleName "PKG MUTANT SHOP").
- **`build-wsl.sh`** (reproducible, no sudo) and **`deploy-tile.py`** (FTPs the app files, `MTRW` +
  `/system_ex/app` + `/user/app`, then you run the payload via Payload Manager).

### Honest note
- Tile + icon + install path are solid (built on the SDK's `install_app` sample). The eboot's actual
  browser-launch is untested until first on-device run — if it needs a tweak, the toolchain is right here.

### Toolchain proven for both remaining asks
- Notifications ✅ (0.14) · dashboard tile ✅ (0.15) · **our own PKG installer** (`sceAppInstUtilInstallByPackage`)
  is the same pattern — next.

---

## [0.14.0] — 2026-07-22 · "THE .ELF IS REAL — compiled a PS5 payload"

After many turns of "I can't compile a PS5 `.elf` from Windows" — **I compiled one.** Using the user's WSL2 +
a user-space clang-18 (no sudo), the ps5-payload-dev SDK built our payload.

### Added
- **`ps5-app/payload/pkg-mutant-shop.elf`** — a real, compiled PS5 payload (113 KB, `ELF 64-bit LSB pie
  executable x86-64`, the correct PS5 loader format). On load it shows a notification and can open the app in the
  PS5 browser; it listens on **:9099** for `notify <text>` / `open <url>` from the companion.
- **`build-wsl.sh`** — fully reproducible, **NO-SUDO** build: fetches the ps5-payload-dev SDK + clang-18 `.deb`s
  into `~/` (via `apt-get download` + `dpkg-deb -x`), shims the SDK's `llvm-config` at the user-space clang, and
  compiles with `prospero-clang`. Verified: rebuilds the `.elf` cleanly from one command.
- Rewrote `main.c` against the **real** PS5 APIs discovered in the SDK samples: `sceNotificationSend(0xFE,…)`
  (a JSON toast) and `sceSystemServiceLaunchWebBrowser(url,0)`.

### How this unlocks the rest
The same toolchain now compiles the SDK's **`install_app`** sample (`sceAppInstUtilAppInstallTitleDir` →
`/user/app/` = a **dashboard tile**) and **PKG install** (`sceAppInstUtil…` = our own installer, no etaHEN).
Those are the next builds — the compile wall is gone.

---

## [0.13.0] — 2026-07-22 · "Install your way — Send-to-PS5 for the Debug Installer"

Explored the user's console to answer "how do I install on Y2JB, not etaHEN?". Their Payload Manager repo has
**no standalone ps5-dpi-v2** — DPI v2 there comes from **etaHEN**. So added a second install path that uses their
**proven Debug Package Installer**, no USB and no DPI host.

### Added
- **"📥 Send to PS5"** (drawer) — FTPs the PKG onto the console (`install.ftp_dest`, default `/data/pkg`) so you
  install it with **Debug Settings → Install Package Files**. New `via:"ftp"` install mode → `sendpkg` queue lane
  (reuses the mount FTP-push with real progress). No DPI, no USB. Verified: endpoint safe, button renders on PC PKGs.
- Config `install.ftp_dest`.

### Findings (from live FTP exploration of the console)
- Payload Manager repo payloads available: kstuff-lite, ShadowMountPlus, **websrv**, shsrv, ps5debug-NG, elfldr,
  ps5-app-dumper, **etaHEN**, … (no standalone ps5-dpi-v2). Autoload: nanodns + kstuff-lite 1.09 + ftpsrv + shadowmount.
- **`websrv` is available** → the realistic path to run our web UI as an on-console homebrew app (vs. the browser).
- Confirmed the app reads LIVE state: installed count tracked 45→44→45 as UMvC3 was deleted/reinstalled during testing.

---

## [0.12.0] — 2026-07-22 · "The payload + PS5 notifications"

### Added
- **PS5 payload source** (`ps5-app/payload/`: `main.c`, `Makefile`, `README.md`) — a real ELF for the
  ps5-payload-dev SDK that shows an **on-screen notification** on load and listens on **:9099** for the app to
  push live "mod X enabled on <game>" notifications (what CheatRunner does). Includes the mod-apply framework
  (memory-write engine) as the documented next step. **You compile it with the SDK (WSL/Linux) — I can't from
  the laptop; honest build steps in the README.**
- **Companion → payload notifications** — `Ps5Bridge.notify()` + `POST /api/notify`. Toggling a mod now pushes a
  notification to the PS5 (once the payload is loaded); Settings has a **"📣 Test PS5 notification"** button.
  Verified: endpoint works (returns `ok:false` until the payload is running). Config `console.notify_port`.
- **Install-test readiness verified** — the new PC-folder PKG **Ultimate Marvel vs. Capcom 3 (CUSA04622)** parses
  correctly, shows "Not installed" (you deleted it from the console), and the companion serves the 4.05 GB file
  with Range/HEAD. Only blocker: launch the DPI host on the console.

---

## [0.11.0] — 2026-07-21 · "Real sizes, real storage, honest connection"

Fixed the bugs from the first real look at the running app. The numbers were wrong because the app.db `size`
is a bogus metadata value — the **real sizes are the ShadowMount `.ffpfsc` backup files** on the external SSD.

### Fixed (real data bugs)
- **Game sizes were wildly wrong** (NBA 2K26 showed 18 GB, Hogwarts 0.8 GB…). `Ps5Bridge.console_backups()` now
  scans the ShadowMount folders (`/mnt/ext1/homebrew` + configured paths) and uses the **real `.ffpfsc` file
  size**, matched by title-id. **Verified live: NBA 2K26 125 GB, Hogwarts 124 GB, Crimson Desert 95 GB, DOOM 89 GB.**
- **Storage was wrong** → now real used per drive from real sizes. **Verified: Internal 8 GB / Extended Storage
  843 GB (19 games).** (Total/free still shows "—" honestly — needs a partition query.)
- **"PS5 offline" was misleading** — it only checked the DPI port (install service, which you hadn't launched).
  `/api/health` now reports `ftp_online` + `dpi_online` separately; the pill shows **"connected (DPI off)"** when
  FTP works, so you know it's really talking to the console. **Verified: connected via FTP.**
- **Card badges clipped/overlapped** on narrow cards → `white-space:nowrap` + max-width + dark backdrop for
  readability over art. **Verified: consistent 70 px "Installed" badges.**

### Added
- **Format + PS5 location in the drawer** — e.g. Hogwarts → `124.1 GB · FFPFSC · 📂 /mnt/ext1/homebrew/[PS5]
  PPSA01593 - Hogwarts Legacy.ffpfsc`. Answers "what format / where on the PS5 / how big".
- **Open games folder** (Settings) — opens your `C:/PS4 PKG GAMES` folder in Explorer to manage the PC side.
- Settings now shows **FTP (data)** and **DPI v2 (install)** status separately.

### Researched (for the apply-engine)
- Studied `notmaj0r/CheatRunner`: cheats are **runtime memory writes** (game must be running), matched by
  **title-id + version** (`CUSA_01.09.json` exact → generic → wrong-version fallback), **crash-suspect flagging**,
  and an on-screen PS5 notification — all **on-console** behavior. This is the spec for our own apply-engine.

### Still on-console (needs the ps5-payload-dev SDK — can't compile from the laptop)
- The **native PS5 app / `.elf`**, **on-screen notifications**, and **mod *apply*** (writing game memory) all need
  a compiled payload. Mod *reading/parsing/matching* is done; *applying* is the payload. Offered as the next build.

---

## [0.10.0] — 2026-07-21 · "Truly connected — reads the whole console"

The app was reading detection from the PS5 but only *showing* the PC folder, so it looked local. Now it reads
and shows the **entire console** — every game, its real art, where it's installed, its real mods, and real
storage. All validated live.

### Added — the app now reads everything from the PS5
- **Console library merged in** (`build_library` + `Ps5Bridge.console_apps`, cached 30 s): all installed games
  from `tbl_contentinfo` are shown, merged/deduped with the PC folder. **Verified: 23 games.**
- **Real box art for every game** — lazy-fetches each game's `icon0.png` from the console (`icon0Info` path,
  `/user/appmeta/…` or `/user/app/…/sce_sys/…`) and serves it; initials render underneath as graceful fallback.
  **Verified: all 23 cards show real 512×512 art.** This fixes the "banner not visible" report.
- **Where each game is installed** — `contentLocation` → Internal SSD / Extended Storage, shown as a 📍 badge
  on the card + a chip + an "On console" section in the drawer. **Verified: Dark Souls @Internal, most @Extended.**
- **Real storage per drive** (`build_storage`) — used space = sum of installed game sizes by location.
  **Verified: Internal 8.0 GB (4 games), Extended 27.8 GB (19 games).** (Total/free still needs an on-device
  query → shown honestly, not faked.)
- **Real mod names + activation toggles** — the cheat reader now downloads + parses the JSON mod files to show
  actual mod names/types/version. **Verified: DOOM Eternal → Inf Ammo / Inf HP+Armor / Inf Jumps / Inf Upgrade
  Points; Dark Souls → 1 Hit Kill + Godmode / Max Souls.** Toggles are honest (see boundary below).
- **PS5 discovery** (`/api/discover` + Settings "🔍 Find PS5") — scans the LAN /24 for the FTP/DPI ports so you
  can re-point at the console when its IP changes; one click fills the IP field.
- **Decoupled from Cheat Runner** — the mod library path is configurable (`cheats.root`); we read the data
  files directly over FTP, never depending on the Cheat Runner service.

### Honest boundary
- **Mod *reading* is fully real; mod *applying* is not wired.** Applying a mod means writing its on/off bytes
  into the running game's memory — that needs our own on-console apply-engine (a payload), the same
  SDK/on-device class of work as the native app. The toggles read real and say so; the apply-engine is the
  next on-console build.

---

## [0.9.0] — 2026-07-21 · "LIVE on-device — real detection & real cheats"

First live session on the console (PS5 @ 10.0.0.99, ethernet). Confirmed real paths by FTP discovery and turned
two more assumptions into **validated, on-device-real** features.

### Fixed (real bugs found on hardware)
- **`doctor.py` crashed** on `lib.is_demo` (renamed to `is_empty` in 0.7.0 but not updated here). Fixed —
  found on the very first live run.
- **Install-detection was querying the wrong (PS4) tables** → returned nothing on PS5. The real PS5 12.70
  `app.db` lists installed titles in **`tbl_contentinfo`** (PS4-style `tbl_appinfo` lives in `appinfo.db`).
  Rewrote the query. **VERIFIED LIVE: `/api/installed` read 45 titles straight from the console over FTP,
  Dark Souls (CUSA08692) correctly detected.**
- **`shadowmount.scan_path` default was `/data/homebrew`, which doesn't exist on this console.** Your real
  ShadowMount config scans **`/mnt/ext1/homebrew`** (external SSD) — corrected in `config.json`.

### Added — Cheats are now REAL (Cheat Runner integration)
- `Ps5Bridge.console_cheats()` + `GET /api/cheats/<title_id>` read Cheat Runner over FTP
  (`/data/cheatrunner/cheats/{json,shn,mc4}` and `patches/{xml_orbis,xml_prospero,xml}`), matched by title-id.
- The drawer's **Cheats & Patches** tab now lists the real files for a title. **VERIFIED LIVE: Dark Souls shows
  `CUSA08692_01.03.json`, `.shn`, patch `CUSA08692.xml`; DOOM Eternal / Hogwarts / Castle Crashers also resolve.**

### On-device facts confirmed (see project memory + TOOLCHAIN.md)
- FTP `ftpsrv.elf` on :2121 ✓ · **DPI v2 :12800 NOT running** (launch Elf Arsenal / ps5-dpi-v2 for the PKG-install test).
- Tools present in `/data`: `pldmgr` (Payload Manager, with a payload repository), `shadowmount`,
  `GameCompressor`, `cheatrunner` (HTTP API on :9999).
- App DB confirmed at `/system_data/priv/mms/app.db`; cheat naming `<TITLEID>_<VER>.json`.

### Still pending (needs the DPI host launched)
- Real PKG **install** through DPI :12800 (the one remaining core path to validate live).

---

## [0.8.0] — 2026-07-21 · "Audit — correctness, performance & every-device views"

Full review pass. Found and fixed real defects (one that would have broken installs), sped things up, and made
the views correct on **PC, phone, and the PS5's TV** with controller navigation.

### Fixed (correctness — found in audit)
- **HTTP `HEAD` was unimplemented → 501.** Install clients (`AppInstUtil`) often HEAD the PKG for size /
  `Accept-Ranges` before downloading; a 501 could abort the install. Added `do_HEAD` for `/library/` and
  `/icon/` (returns `Content-Length` + `Accept-Ranges`, no body). `[VERIFIED: HEAD → 200]`
- **Suffix `Range` (`bytes=-N`) served the wrong bytes** (first N instead of last N). Fixed the range parser.
  `[VERIFIED: bytes=-100 → 206, exactly the last 100]`

### Performance
- **Fleet status pings all consoles in parallel** (was N × ping-timeout, serial). `[VERIFIED]`
- **Icons no longer re-extracted every scan** — skip when the cached PNG already exists (big win for large libraries).

### Device views (the ask: "perfect on each device")
- **Full responsive tiers:** phone (2-col ≤380px, wrapping header, full-width search, ≥42px touch targets),
  tablet, desktop, and **TV / PS5 (≥1600px): overscan-safe 44px padding + larger cards & text for couch viewing**;
  4K tier too. Verified at 375px and 1920×1080 — no horizontal overflow at any size. `[VERIFIED in-browser]`
- **Controller / keyboard navigation:** cards are focusable (`tabindex`/`role=button`, Enter/Space to open) and
  every interactive element gets a matte-yellow **`:focus-visible`** ring — usable with the DualSense on the TV.
- **Accessibility:** `role`/`aria-label` on cards, `alt` on the logo, focus rings only for keyboard/controller
  (pointer users stay clean).

### Polish
- **Clear-finished** button (🧹) in the queue dock (wired to `/api/queue/clear`).
- **Title count** in the source bar ("🎮 N titles").

---

## [0.7.0] — 2026-07-21 · "Honest Data — no fakes"

Stripped every piece of fabricated data. The app now shows **real data or an honest empty state — never
invented content**. Front, back, and settings all reflect actual state.

### Removed (fakes)
- **Demo/sample games** (Neon Drift, Hollow Sky, Ashfall, …) — deleted from both `web/index.html` and
  `server.py`. No more fabricated titles, ever. `[VERIFIED: only the real DARK SOULS shows]`
- **Fake storage reads** — the hardcoded drive usage bars (Internal 62%, USB0 28%, …) are gone. Storage now
  shows real free space **or an honest "—"** via `/api/storage` (`free:null` until an on-device query is
  wired — never a made-up number). `[VERIFIED]`
- **Fake "% free"** in the install drive-selector — replaced with plain drive labels.

### Changed
- Empty states are explicit and honest: opened as a bare file → "Interface preview. Run the companion…";
  companion up but no library → "No PKGs found. Set library.local_paths… then Rescan"; companion down →
  "Companion not reachable." The grid's "no match" text only appears when filters hide real titles.
- `/api/library` & `/api/rescan` now report `empty` (was `demo`); `is_demo` → `is_empty` throughout.
- New `/api/storage` (honest drive list) and `drives` config key.
- **Version fix:** `server.py` VERSION was stuck at 0.5.0 through the 0.6.0 work — now correctly **0.7.0**.

### Confirmed correct (PS4 PKG install location)
- PS4 PKGs go through the **INSTALL lane → DPI/AppInstUtil → real system install** (same as the Debug Package
  Installer), **not** the homebrew folder. They're detected by reading the console **app database**. Only PS5
  **backups** use the homebrew folder (MOUNT lane → ShadowMount). The two-lane split already does this right;
  nothing in the app pushes PKGs to homebrew.

---

## [0.6.0] — 2026-07-21 · "Correct for Y2JB 12.70 · Mount Lane"

Re-verified the app against the **current FW 12.70 toolchain**, made it tool-agnostic, and added the
ShadowMount "mount lane" for compressed backups.

### Fixed / corrected (accuracy for Y2JB 12.70)
- **DPI v2 is no longer described as etaHEN-only.** On 12.70 the `:12800/api/install` service comes from
  **Elf Arsenal** or **`ps5-dpi-v2`** (works FW 4.03–13.20+, handles the 11.00+ authid transparently); etaHEN
  is just one option on older FW. Corrected in `doctor.py`, `deploy.py`, and docs. The app already targeted the
  right standard API, so no behavior change — just correct guidance.
- **ELF-loader honesty:** the default loader is **localhost-only** (security) — PC→console ELF injection needs a
  LAN-enabled loader or on-console **Payload Manager**; FTP deploy + DPI install are unaffected. Documented.
- New **[TOOLCHAIN.md](TOOLCHAIN.md)** — authoritative map of the 12.70 stack (Y2JB/P2JB, autoloader / Payload
  Manager, DPI v2 hosts, Kstuff Lite, ShadowMountPlus, Game Compressor): roles, ports, boot order, and how the
  app uses each. Every assumption is a configurable default.

### Added — Mount lane (ShadowMount + Game Compressor)
- Scan recognizes **backup formats** `.ffpfsc` (Game-Compressor compressed PFS), `.exfat`, `.ffpkg`, `.ffpfs`
  as a **mount lane** (`lane:"mount"`, `platform:"BACKUP"`), distinct from PKG install. `[VERIFIED]`
- Installing a backup **FTPs it to the ShadowMount scan folder** (`shadowmount.scan_path`, default
  `/data/homebrew`) with real byte-progress via the FTP callback; ShadowMount auto-mounts on next scan. Offline
  → labeled simulation. `[VERIFIED routing; live FTP confirmed on-device]`
- UI: **MOUNT** badge, "Backup (ShadowMount)" section, **Mount** / **Mount backup** actions. `[VERIFIED in-browser]`
- Config: `shadowmount.scan_path`.

### Note
- etaHEN references in *older* changelog/roadmap entries are kept as dated history; TOOLCHAIN.md is the current
  source of truth.

---

## [0.5.0] — 2026-07-21 · "Native App & Branding"

Real branding + a real, no-toolchain path to an **installed PS5 app with your icon** (not the browser), plus
a self-bootstrapping PC app.

### Added
- **Official branding** — your icon + logo integrated. Header mark = your icon (128px, verified loaded),
  favicon (64px), 512×512 `icon0.png` for the PS5 app. Assets in `web/assets/`, referenced by **relative
  path** so they work when companion-served, from `file://`, and bundled on the console. `[VERIFIED in-browser]`
- **PS5 homebrew-app package** (`ps5-app/`): `homebrew.js` manifest + `sce_sys/icon0.png` — the "launch from
  the homebrew launcher with your icon, no browser" app (Route A). `[VERIFIED bundle]`
- **`deploy.py`** — real deploy tool. `payload <elf>` injects an ELF to the PS5 loader port (verified:
  4096-byte send/receive echo); `app` bundles icon+manifest+UI+assets, writes `config.js` with your companion
  URL, FTP-uploads to `/data/homebrew/PKG_MUTANT_SHOP/`; `check` tests loader+FTP reachability. `[VERIFIED sender+bundler]`
- **Configurable API base** — UI reads `window.PMS_API` (the PS5 app's `config.js`) so the on-console app
  talks to your companion box; same-origin when companion-served; demo from a bare file. `[VERIFIED]`
- **Self-bootstrapping PC app** — `bootstrap.cmd` auto-installs the only dependency (Python via winget) then
  launches; `build_exe.cmd` makes a single-file `PKG-MUTANT-SHOP.exe` (bundles Python+UI, zero deps on the
  target PC; companion is now `sys.frozen`-aware). **No DLLs/drivers needed — stdlib-only by design.**
- **BUILD-PS5-APP.md** — honest two-route guide (homebrew-launcher app now; system-dashboard fpkg via SDK).

### Honest boundary
- A *loadable* fake-signed **fpkg** (system games-dashboard icon, Route B) needs the **ps5-payload-dev SDK** +
  on-device signing — that compile step isn't done from a laptop, and I won't fake a finished binary.
  Everything around it (icon, manifest, UI, bundling, FTP deploy, ELF injection) is built and tested; Route A
  needs no toolchain.

---

## [0.4.0] — 2026-07-21 · "Fleet, Parallel & Integrity"

No stubs left for later — the full PC↔PS5 pipeline is coded and the real online path was proven end-to-end
against a simulated console (byte progress climbed 0→100% → promote). PS5 WebKit compatibility hardened.

### Added
- **Multi-console fleet.** `consoles: [...]` in config; header **console picker** with **"All consoles"**
  fan-out. `Fleet`/`Ps5Bridge` per console; `/api/consoles` reports each console's online state. `[VERIFIED]`
- **Parallel queue** (`queue.max_parallel`, default 2) with **per-console serialization** (safe default; flip
  `allow_parallel_per_console` if firmware allows). Multiple workers, claim-under-lock. `[VERIFIED]`
- **Unified REAL progress.** Companion-hosted files → local byte-count; a **remote companion source** →
  polled via its `/api/served/<key>`. Proven end-to-end with a fake PS5 (real % + promote). `[VERIFIED]`
- **SHA-256 integrity.** `/api/hash/<key>` (streamed, cached by path+size+mtime); per-file **🔒 Verify**
  button in the drawer; hashes surface in manifests. `[VERIFIED wiring]`
- **Per-title manifest** `/api/manifest/<title_id>` (files, sizes, kinds, sha if computed). `[VERIFIED]`
- **`/api/served/<key>`** so any companion reports live bytes for remote-progress polling. `[VERIFIED]`
- **Settings** now shows the live **Sources** ranking (🟢/🔴 + latency) and **Consoles** fleet. `[VERIFIED]`
- **One-click launchers** `start.cmd` / `start.sh`; `sources.json.example`; **SETUP-REMOTE.md**
  (Tailscale "from anywhere", multi-console, mirrors).

### Fixed / hardened (before-they-happen)
- **Key-normalization bug:** `file_registry`/`install_key` are URL-quoted, but `_serve_library` + `served`/
  `hash` used the unquoted form → live LAN progress would read the wrong key on the console. Unified via
  `registry_key()`. Caught + fixed via the fake-PS5 test. `[VERIFIED]`
- **PS5 WebKit compatibility:** replaced `inset:0` → explicit `top/right/bottom/left`, and `aspect-ratio`
  → `padding-top` ratio box (confirmed 1.33 in-browser) so cards render on older console WebKit. `[VERIFIED]`
- Config loader now strips **all** `_`-prefixed doc keys (not just `_comment`).
- Deployment requirement surfaced by testing: companion must bind `0.0.0.0` (default) for the console to reach it.

---

## [0.3.0] — 2026-07-21 · "Source Engine"

Multi-mirror intelligence: the app auto-detects where each game can come from and picks the fastest reachable
source, with the LAN companion always available as a fallback. Full design in `ARCHITECTURE.md`.

### Added
- **`sources.py` — intelligent source engine.** Probes candidate mirrors concurrently (reachability + latency,
  30 s cache), ranks them (local → lowest-latency → priority), resolves the best URL for a file, fails over.
  Verified: ranks a live LAN host above a dead cloud host and picks it. `[VERIFIED]`
- **Source types** `local · lan · http · tunnel · cloud`, each `{name,type,base_url,priority,auth?}`.
- **Multi-source config** (`sources: []`) + an implicit always-on `companion-lan` mirror. `config.example.json`
  documents the shape (home-box-over-Tailscale + your-own-cloud examples). `[VERIFIED]`
- **Install uses selection:** `/api/install` resolves the best mirror holding the file (falls back to LAN host).
  Byte-accurate progress stays real for LAN-hosted files; remote pulls are honestly marked not-locally-measurable. `[VERIFIED]`
- **`/api/sources`** returns the live probe ranking + chosen best; UI source-bar shows "N sources · auto-picked <best>". `[VERIFIED]`
- **`ARCHITECTURE.md`** — full design: components, selection algorithm, parallel/background/multi-console, the
  three "from-anywhere" options with tradeoffs, security, reliability, future parking lot.

### Notes / honesty
- Recommended "from anywhere" path is a **home box + Tailscale** (private, free, no public hosting). Cloud/VPS
  sources are supported but must be **your own private storage of content you have the right to host** — the app
  ships no catalog and no piracy sources.
- Still ahead: fleet manager (multi-console), remote-source progress reporting, manifests + SHA-256, parallel-install
  limits on one console (to confirm on device).

---

## [0.2.0] — 2026-07-21 · "Real Metadata & Real Progress"

Ripped out the biggest stubs. Verified end-to-end on the laptop against a real retail PKG
(`DARK SOULS™: REMASTERED`, CUSA08692, 7.27 GB).

### Added — now REAL (was stub/heuristic)
- **`pkg_meta.py` — real PS4 PKG / param.sfo parser.** Reads TITLE, TITLE_ID, CONTENT_ID, CATEGORY,
  APP_VER/VERSION straight from the package. `[VERIFIED against CUSA08692]`
  - Category → type is now real: `gd`=base, `gp`=update, `ac`=dlc (no more filename guessing).
  - Region derived from CONTENT_ID prefix (UP→US, EP→EU, JP→JP, HP→ASIA, KP→KR).
- **Real box art.** Extracts `icon0.png` (entry 0x1200) from the PKG and serves it at `/icon/<title_id>.png`;
  UI shows the real cover (512×512 for Dark Souls), falling back to a generated tile only when absent. `[VERIFIED]`
- **Byte-accurate transfer progress.** The companion file host counts bytes as the PS5 pulls the PKG and
  reports true % — no more simulated bar while a real transfer is happening. `[REAL]` (measured), post-download
  promote step still shown as "installing on console" until the DPI status feed is captured. `[ON-DEVICE]`
- **Real install-detection.** `/api/installed` reads installed Title-IDs from the console app DB over **FTP + SQLite**
  (`installed_titles()`), merged with a local record of what we installed. Works when the console is online;
  DB path is configurable. `[WIRED]` (needs the console to confirm path/schema)
- **Source auto-read.** `/api/sources` + the UI's source bar show exactly *what folder is being read* and *the URL
  the PS5 pulls from* ("📂 Reading from … / ⬇ PS5 pulls from …"). `[VERIFIED]`
- **`doctor.py` test kit.** `python doctor.py` = full pre-flight (config, companion, console DPI/FTP reachability,
  real library list, install URL). `python doctor.py --install <TITLE_ID>` = drive one real install and watch live
  progress. `[VERIFIED]` (diagnostics), `[WIRED]` (install path)
- Config gains `ftp.port` (2121) and `console.app_db_path`. Default `library.local_paths` → `C:/PS4 PKG GAMES`.

### Removed / fixed
- Filename-heuristic metadata (`classify`, `pretty_name`, stub `region_of`) — replaced by real parse (kept only as
  a fallback if a file can't be parsed).
- **Fake cheat toggles** — the section no longer pretends to toggle runtime patches; shows an honest empty state
  until a real cheat backend is wired (Phase 4).
- **Bug:** UI treated same-origin API base `""` as falsy, forcing demo mode even when connected to the companion.
  Now uses explicit `API===null`. `[VERIFIED fixed]`

### Still honestly pending (needs the console)
- DPI v2 live *promote/status* endpoint (download % is real; the decrypt/promote tail is not observable yet).
- Confirm app-DB path/schema on PS5 for install-detection.
- Drive targeting (internal M.2 vs USB/ext) — how DPI selects the destination.

---

## [0.1.0] — 2026-07-21 · "Foundation"

First playable foundation. Everything renders and runs on the laptop today in **demo mode**; the console-specific paths are wired and clearly marked for tonight's FTP test.

### Added
- **Project scaffold** — self-contained web front-end (`web/index.html`) + stdlib-only Python companion (`companion/server.py`). No pip installs, no CDNs, no external assets (important: the UI must load inside the PS5's offline WebKit).
- **UI shell** in the MUTANT theme (black / matte-yellow / gray): library grid, game-detail drawer with **Base / Updates / DLC / Cheats** sections, install queue with progress bars, storage/drive selector, connection status, settings. `[VERIFIED]` (renders + interacts in demo mode)
- **Demo mode** — opening `web/index.html` directly (or running the companion with no library configured) shows a fully populated, clickable store with sample titles, so the interface is reviewable with zero hardware. `[VERIFIED]`
- **Companion service** (`companion/server.py`) — serves the UI and exposes `/api/health`, `/api/config`, `/api/library`, `/api/installed`, `/api/install`, `/api/queue`, `/api/queue/<id>/cancel`. `[VERIFIED]` (local), `[WIRED]` (console bridge)
- **PS5 bridge → etaHEN DPI v2** — real submit endpoint: `POST http://<ps5-ip>:12800/api/install` with `{"url": "..."}`, expecting `{"res":"0"}`. Legacy DPI v1 (port 9090 socket) adapter stubbed. `[WIRED]`
- **Source-agnostic library index** — scans user-configured local folders for `.pkg` and merges an optional `sources.json`. You supply your own content. `[VERIFIED]` (scan), `[STUB]` (metadata: filename heuristics for now)
- **Config system** — `config.example.json` → copy to `config.json`: PS5 IP, DPI mode/ports, ShadowMount port, library paths, companion host/port.
- **Docs** — `README.md` (architecture + honest feasibility notes) and `ROADMAP.md` (validated vs. needs-device).

### Needs on-device validation (moves to next version once captured on the console)
- DPI v2 **live progress/status** endpoint — install *submission* is confirmed; the percentage/state feed (`transferring → promoting → playable`) will be captured from the console's own WebUI network trace tonight. `[STUB]`
- **Install-state detection** — reading installed title-IDs from the system app database (path/format to confirm on device). `[STUB]`
- **`param.sfo` parsing** — real Title-ID / version / region / content-type from PKG headers, replacing filename heuristics. `[STUB]`

### Notes
- Base install path is the same for PS4 FPKGs on PS5 (via AppInstUtil/DPI). PS5-native game *backups* run through ShadowMountPlus (mount, not install) — tracked as a separate lane in ROADMAP. PS4-first, per plan.
