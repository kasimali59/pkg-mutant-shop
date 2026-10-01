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

    if fails:
        print("test_panel_rules: FAIL")
        for f in fails:
            print("   %s" % f)
        return 1
    print("test_panel_rules: OK (%d checks)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
