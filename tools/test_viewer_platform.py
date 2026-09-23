# -*- coding: utf-8 -*-
"""viewer_platform_for: which console is reading the page.

The shop's UI is ONE file. The PC serves it, the PS5 serves it, the PS4 serves it - and whichever
machine it is read on, the data comes from the PC. So the page cannot tell on its own where it is,
and it has to know: a PS4 must never be offered PS5 games, the header has to name the console the
reader is holding, and the "Game backups" row is a PS5 row that painted a red alarm light on a PS4
that was working perfectly.

The answer is the address the request came from. This is the whole of that decision, tested without
a console, a television or a network.

    python tools/test_viewer_platform.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMPANION = os.path.join(os.path.dirname(HERE), "companion")
sys.path.insert(0, COMPANION)

spec = importlib.util.spec_from_file_location("pms_srv", os.path.join(COMPANION, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["pms_srv"] = srv
try:
    spec.loader.exec_module(srv)
except SystemExit:
    pass

PS5 = {"id": "ps5", "platform": "ps5", "ip": "10.0.0.99"}
PS4 = {"id": "ps4", "platform": "ps4", "ip": "10.0.0.87"}
OLD = {"id": "ps5", "ip": "10.0.0.99"}            # a config written before PS4 support existed

results = []


def case(name, consoles, peer, expect):
    got = srv.viewer_platform_for(consoles, peer)
    ok = got == expect
    results.append((name, ok))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                           "" if ok else "   got %r want %r" % (got, expect)))


case("the PS4 asking is told it is a PS4", [PS5, PS4], "10.0.0.87", "ps4")
case("the PS5 asking is told it is a PS5", [PS5, PS4], "10.0.0.99", "ps5")
# THE DEFAULT HAS TO BE "PC", NOT "PS5". Everything device-specific keys off this string, and a PC
# wrongly answered "ps5" would be shown the console's controller hints and the console's rules
# about what it may install.
case("this PC is not a console", [PS5, PS4], "127.0.0.1", "")
case("another PC on the LAN is not a console", [PS5, PS4], "10.0.0.81", "")
case("no address at all is not a console", [PS5, PS4], "", "")
# A console that does not say what it is, is a PS5 - the same rule reconcile_consoles and the
# health handler follow, because a config from before PS4 support carries no platform field.
case("an entry with no platform reads as a PS5", [OLD], "10.0.0.99", "ps5")
case("an empty fleet answers PC", [], "10.0.0.87", "")
case("an address that matches nothing answers PC", [PS4], "10.0.0.99", "")
# Real fleets get edited by hand; a malformed entry must not take the whole answer down.
case("a junk entry is stepped over, not tripped on", [{"id": "x"}, PS4], "10.0.0.87", "ps4")

bad = [n for n, ok in results if not ok]
print("\n%d checks, %d failed" % (len(results), len(bad)))
sys.exit(1 if bad else 0)
