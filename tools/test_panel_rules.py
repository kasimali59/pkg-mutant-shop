# -*- coding: utf-8 -*-
"""Gate: what the Payloads & Homebrews panel means by "Installed", and how long it believes it.

TWO DEFECTS, BOTH REPORTED BY THE OWNER AND BOTH MEASURED BEFORE BEING BELIEVED.

  * The panel said FPKGi was "Installed" on a console it had been DELETED from. The console was
    right, the companion's own /api/installed was right, and the panel was wrong - on one PC. On
    the owner's other PC, which nobody happened to be looking at, the same panel was correct.
    That difference is the whole clue: the probe cache was re-stored with a fresh timestamp on
    every request, including requests that only copied the previous values back out. The panel
    polls every three seconds and the cache expires after four, so looking at the panel kept the
    entry young for ever and the console was asked exactly once - when the panel was opened.

  * "Installed" was also being computed from every title the console's app list MENTIONS. A
    console lists titles it does not have: measured on the owner's PS4, 21 titles of which 5 are
    system entries with no data on disk, and a freshly deleted app keeps its row for a while
    before the console drops it.

Both rules are functions now, so this runs them instead of reading them.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "companion"))

os.environ.setdefault("PMS_TEST_MODE", "1")
import server as S                                              # noqa: E402

fails = []
n = 0


def ok(cond, what, detail=""):
    global n
    n += 1
    if not cond:
        fails.append("%s%s" % (what, (" - " + detail) if detail else ""))


def main():
    # ---- INSTALLED MEANS INSTALLED -------------------------------------------------------------
    apps = [
        {"title_id": "ITEM00001", "install_status": 0},      # really there
        {"title_id": "LAPY20009", "install_status": 0},      # really there
        {"title_id": "PKGI13337", "install_status": 1},      # the row survives the delete
        {"title_id": "NPXS20114", "install_status": 1},      # a system entry with no data
        {"title_id": "cusa00001", "install_status": 0},      # ids are compared upper-cased
    ]
    got = S.installed_ids(apps)
    ok(got == {"ITEM00001", "LAPY20009", "CUSA00001"},
       "only titles whose data is really in place count as installed", repr(sorted(got or [])))
    ok("PKGI13337" not in (got or set()),
       "a deleted app whose row has not gone yet is NOT installed")
    ok(S.installed_ids(None) is None,
       "no list at all stays unknown, which is not the same as nothing installed")
    ok(S.installed_ids([]) == set(), "an empty list means nothing installed")
    # Absent field: the PS5 path always sets it, but a row that somehow lacks it must not be
    # silently dropped - unknown-but-listed is far likelier to be installed than not.
    ok(S.installed_ids([{"title_id": "AAA"}]) == {"AAA"},
       "a row with no status at all is taken as installed")

    # ---- A CACHED ANSWER AGES FROM WHEN IT WAS MEASURED -----------------------------------------
    # Driven exactly as the route drives it: ask only on a miss, and write back only then.
    cache, asked = {}, []

    def poll(now):
        hit = S.probe_cache_hit(cache, "ps4|10.0.0.86", now)
        if hit is None:
            asked.append(now)
            cache["ps4|10.0.0.86"] = (now, "answer@%g" % now)
            return "answer@%g" % now
        return hit[1]

    poll(0.0)
    ok(asked == [0.0], "the first look asks the console", repr(asked))
    poll(1.0); poll(2.0); poll(3.0)          # the panel polling every three seconds
    ok(asked == [0.0], "...and a look inside the window reuses that answer", repr(asked))
    poll(4.5)
    ok(asked == [0.0, 4.5],
       "...and once the ANSWER is older than the window, the console is asked again", repr(asked))
    # THE BUG, STATED AS A TEST: polling faster than the window must not postpone the next ask.
    cache.clear(); del asked[:]
    t = 0.0
    while t < 30.0:
        poll(t)
        t += 1.0
    ok(len(asked) >= 7,
       "polling every second for 30s still re-reads the console about every 4s, not once",
       "asked %d time(s) at %s" % (len(asked), asked[:9]))

    # ---- AND THE ROUTE REALLY USES THEM ---------------------------------------------------------
    # Extracting a rule helps nothing if the caller kept its own copy.
    import io as _io
    _src = _io.open(os.path.join(ROOT, "companion", "server.py"), encoding="utf-8").read()
    _fn = _src.split("def _payloads_list(", 1)[1].split("\n    def ", 1)[0]
    ok("installed_ids(" in _fn, "the panel route asks installed_ids() for the word Installed")
    ok("probe_cache_hit(" in _fn, "...and probe_cache_hit() for whether to re-read the console")
    ok("_pc[_ck] = (_now" in _fn and "if _fresh:" in _fn,
       "...and only writes the cache back when it actually measured")

    # ---- THE RESTART WAITS FOR THE PRESSING TO STOP --------------------------------------------
    # "Update all" walks the list one row at a time and our own entry is one of those rows, so
    # restarting the instant that row succeeds cuts the chain off. Measured on this PC: the PS5 row
    # reported success and restarted, and the PS4 row already in flight died with the connection.
    # The restart is armed instead, and every further update request pushes it back.
    #
    # relaunch_self() is replaced here because the real one STARTS A SECOND COPY OF THE APP and
    # exits this process - which a test must never do, and which is also the thing being scheduled.
    import time as _t
    fired = []
    _real_relaunch = S.relaunch_self
    try:
        S.relaunch_self = lambda port, log=None: fired.append(port)
        S._relaunching = False
        S._relaunch_timer = None

        S.schedule_relaunch(8710, delay=0.6)
        _t.sleep(0.25)
        ok(not fired, "arming a restart does not restart anything yet", repr(fired))
        S.defer_relaunch(0.6)                 # another update came in
        _t.sleep(0.45)
        ok(not fired, "...and a second update pushes it back rather than letting it fire",
           repr(fired))
        _t.sleep(0.75)
        ok(fired == [8710], "...and it goes once the panel has been quiet", repr(fired))

        # ONCE, HOWEVER MANY TILES ASK. Our entry appears once per console.
        del fired[:]
        S._relaunching = False
        S._relaunch_timer = None
        S.schedule_relaunch(8710, delay=0.4)
        S.schedule_relaunch(8710, delay=0.4)
        S.schedule_relaunch(8710, delay=0.4)
        _t.sleep(0.9)
        ok(fired == [8710], "three tiles asking still restarts exactly once", repr(fired))

        # Deferring when nothing is armed must not arm one.
        del fired[:]
        S._relaunch_timer = None
        S.defer_relaunch(0.2)
        _t.sleep(0.5)
        ok(not fired, "deferring when no restart is pending does not start one", repr(fired))
    finally:
        S.relaunch_self = _real_relaunch
        try:
            if S._relaunch_timer is not None:
                S._relaunch_timer.cancel()
        except Exception:
            pass
        S._relaunch_timer = None
        S._relaunching = False

    # ---- THE REPLACEMENT IS TOLD WHO IT REPLACED ------------------------------------------------
    # The exe is a PyInstaller bundle: a bootloader parent starts the Python child that runs the
    # app, and the child exiting does not always take the parent with it. Measured after a working
    # self-update - the new build was serving and the OLD build's bootloader was still in the task
    # list holding its own image, which is <exe>.old, so the sweep could not remove it and the NEXT
    # self-update failed trying to rename over it.
    ok(S.REPLACING_FLAG == "--replacing", "the handover has a flag of its own")
    _killed = []
    _real_run = S.subprocess.run
    try:
        S.subprocess.run = lambda cmd, **kw: _killed.append(list(cmd))
        S._clear_replaced([S.AFTER_UPDATE_FLAG, "8710", S.REPLACING_FLAG, "4242,4243"])
        _pids = [c[-1] for c in _killed]
        ok(_pids == ["4242", "4243"],
           "both the build that handed over and its bootloader are cleared", repr(_killed))
        del _killed[:]
        # NOTHING NAMED, NOTHING TOUCHED. A normal start must never kill anything.
        S._clear_replaced(["--after-update", "8710"])
        ok(not _killed, "a normal start clears nothing", repr(_killed))
        del _killed[:]
        # AND NEVER OURSELVES, whatever it is told.
        S._clear_replaced([S.REPLACING_FLAG, "%d,0,-1,abc" % os.getpid()])
        ok(not _killed, "it refuses to kill its own process, or a nonsense pid", repr(_killed))
    finally:
        S.subprocess.run = _real_run

    # AND THE TWO ENDS ARE WIRED. A clear-up that is never told anything, or never called, is the
    # same as not having one - and neither end can be reached from inside this process: one starts
    # a second copy of the app, the other is the startup path itself.
    import io as _io2
    _ssrc = _io2.open(os.path.join(ROOT, "companion", "server.py"), encoding="utf-8").read()
    _rl = _ssrc.split("def relaunch_self(", 1)[1].split("\ndef ", 1)[0]
    ok("REPLACING_FLAG" in _rl and "os.getppid()" in _rl,
       "the launch names this process and its bootloader for the replacement to clear")
    _main = _ssrc.split("\ndef main(", 1)[1].split("\ndef ", 1)[0]
    ok("_clear_replaced(" in _main, "...and startup actually clears them")
    ok(_main.index("_clear_replaced(") > _main.index("_wait_for_port_release("),
       "...after waiting for the port, not before")

    # ---- AND NOTHING IS LEFT BESIDE THE APP ----------------------------------------------------
    # <exe>.old is the build that was running and <exe>.new a download that did not finish; both
    # were measured sitting next to the app after a self-update, 38 MB and 25 MB of nothing.
    import payloads as _PE
    import tempfile as _tf
    import shutil as _sh
    _d = _tf.mkdtemp()
    _real_exe = _PE.running_exe
    try:
        _e = os.path.join(_d, "PKG-MUTANT-SHOP.exe")
        for _p in (_e, _e + ".old", _e + ".new"):
            with open(_p, "wb") as f:
                f.write(b"MZ")
        _PE.running_exe = lambda: _e
        _PE.sweep_old_exe()
        ok(not os.path.exists(_e + ".old"), "the previous build is cleared away")
        ok(not os.path.exists(_e + ".new"), "...and so is a download that did not finish")
        ok(os.path.exists(_e), "...and the app itself is untouched")
    finally:
        _PE.running_exe = _real_exe
        _sh.rmtree(_d, ignore_errors=True)

    if fails:
        print("test_panel_rules: FAIL")
        for f in fails:
            print("   %s" % f)
        return 1
    print("test_panel_rules: OK (%d checks)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
