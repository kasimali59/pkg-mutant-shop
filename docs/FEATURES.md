# Features

Everything the app does, by area. Where a line reads like a boast it is because it was measured —
the numbers and the failure modes in here came off real hardware, not off a design document.

Legend: **PS5** · **PS4** · **PC** — where the feature lives. "Both" means both consoles.

---

## The library

* **Real metadata, real art.** Titles, versions, sizes and region come out of each package's
  `param.sfo`; the cards use the package's own `icon0.png`. Nothing is scraped and nothing is
  guessed from a filename. *(PC)*
* **Grouped by game, not by file.** Base game, updates, DLC and add-ons collapse into one title, so
  a game with six files is one card with six rows. *(all)*
* **Install state from the console**, read live: `bgft.db` plus a full-size `app.pkg` on disk. A row
  in `app.db` alone is never treated as proof — it survives a database reset and once produced 53
  phantom "installed" titles. *(all)*
* **Every drive, with free space** — internal, extended storage, and each USB — for both consoles at
  once, plus every PC in the fleet. *(all)*
* **Search, filters and sort**: installed / not installed / has an update / add-ons, by platform, by
  name or size. *(all)*
* **Fifteen languages**, RTL included, with a build gate that fails when one language is missing a
  key the app uses. *(all)*

## Installing

* **The console installs it.** Every package goes through Sony's own installer (BGFT), which fetches
  the bytes from the PC over HTTP Range. The PC never pushes a game over FTP. *(both)*
* **A spawned process, not injected code.** `sceAppInstUtilInstallByPackage` answers `0x80B2116F`
  from a payload injected into a hijacked host, and succeeds from a freshly spawned one — so the app
  ships its own tiny installer and has Payload Manager spawn it per install. *(PS5)*
* **One install per console, claimed atomically.** The queue hands out a token, not a flag, so two
  devices pressing Install at the same moment cannot both win. *(PC)*
* **A verdict that is checked both ways.** "Console rejected this package" is not believed without a
  `bgft.db` row, and neither is "it worked" — the app has been wrong in both directions and now
  reads the databases that actually decide. *(all)*
* **Integrity before hand-off**, and an out-of-space refusal only when a package fits on *no* drive
  — where it lands is the console's own *Installation Location* setting and the app does not guess
  it. *(PC)*
* **Progress you can trust**: the byte counter on the file being served is the progress bar, because
  it is the same bytes the console is reading.

## Backups and drives *(PS5)*

* Copy a backup to **any** drive the console can see, staged as `.part` — ShadowMountPlus mounts the
  instant a file appears, and a half-written game crashes the console.
* Move and delete backups, with a two-tier root boundary: a drive root is not a watch folder, and
  confusing the two is how four separate bugs got in.

## Cheats, mods and patches *(PS5 live; PS4 browse-only)*

* **Applied to the running game**, by the app's own engine — a CR3 walk to the game's memory, an
  expect-gated write, and a revert that puts the original bytes back.
* **A cheat is a code patch, not a value poke.** Nothing changes until the patched instruction runs,
  which is why the panel never claims an effect it cannot see.
* **Matched to the exact game version.** A cheat file built for 01.00 is not offered for 01.02; the
  panel has a version picker because the library genuinely differs between them.
* **Thousands of cheat and patch files ship inside the app**, so a console with no PC still has them.
* On the **PS4** the library browses but cannot be applied: that jailbreak gives a payload no way to
  write another process's memory. This was measured — `mdbg` and `ptrace` both return `EPERM`, and
  GoldHEN's syscall gateway is caller-only — and it is documented rather than papered over.

## Payloads & Homebrews *(both)*

* **Send a payload ELF to either console** — through Payload Manager on the PS5, through GoldHEN's
  loader on the PS4 — and **install homebrew packages** through the same engine the games use.
* **Identity is what is inside the file.** A payload is recognised by a marker string in its bytes,
  so renaming it changes nothing: it keeps its port, its upstream and its warnings.
* **Running is observed, never assumed.** A tile is green when a port answered, when the loader
  listed the process, or — for a UDP service like nanoDNS, which answers nothing from the LAN — when
  a bind test proves the port is taken. When nothing can be observed, the tile says so.
* **Upstream releases**, per item, with the asset that would actually replace *this* file — one
  project can ship both consoles in one release. The download is verified before anything is
  replaced, and the old file is kept as `.bak` until the new one is proven complete.
* **A jailbreak-layer payload is never started as a side effect.** Those need an explicit confirm,
  every time.
* **The app's own build is in this panel too**, and that is where its updates arrive.

## The fleet *(PC)*

* **Companions find each other on the LAN** and share their libraries; the console installs from
  whichever PC has the game.
* **Every device can press every button.** A PC that does not hold a file is not a spectator: the
  console can start a payload it carries itself, and anything else is forwarded to the companion
  that has the bytes — which then runs its *own* deploy lane rather than having bytes shipped to it.
* **Consoles are tracked, not configured.** Each one is followed by a durable id, so a console that
  changes address is still the same console, and nothing anywhere hardcodes an IP.

## On the console, with every PC switched off *(both)*

* All three artifacts embed the UI, the payload set, the catalogue and the cheat library.
* Each console serves the page itself, and the page then re-points its API at the newest companion
  that announced itself — so the same page is a full app with a PC and a working app without one.
* **A home-screen icon on both consoles.** The PS5 gets a tile; the PS4 gets a real application, and
  the payload installs and updates that icon itself, from inside the ELF, comparing versions and
  then bytes so a current icon is left alone.
* The PS4 helper **arms itself** for exactly the installed titles the library covers, because
  GoldHEN reads its plugin list only at game start.

## Housekeeping

* **Rest mode is safe.** The panic that used to follow it was other payloads, not this one; the app
  stands them down first.
* **PSN blocking** — point the console's DNS at the companion and Sony's update and telemetry hosts
  stop resolving. The app never starts, resumes or cancels a console-owned transfer.
* **Notifications that actually render.** The icon form of the PS5 toast returns success and draws
  nothing on 12.70, so the app sends the plain form.

## How it is kept honest

* **Build gates, all fatal**: the single inline UI script is parsed by Node before it can ship, every
  language is checked for every key, every user-facing string is checked against the house style, the
  cheat library and the payload catalogue are checked against their sources, and the exe is checked
  for staleness.
* **Tests that are perturbed to red.** A check that passes when the code is broken is not a check;
  when one is added here, the code it guards is deliberately broken to prove the test fails.
* **Measurements, not adjectives.** Where this file says something is faster or smaller, there is a
  number behind it in `CHANGELOG.md` or one of the design documents.
