# -*- coding: utf-8 -*-
"""Translation coverage for web/index.html.   Run: python tools/i18n_report.py [--check]

WHY THIS EXISTS. The language selector shipped for a long time translating only a fraction of the
app, and nothing noticed: the dictionary was healthy, the wiring was not. A string only reaches a
translation if somebody remembers to route it through t() or a data-i18n attribute, and forgetting
is silent - the app just quietly stays English for that one label, on every device, in every
language. This turns that into a number you can look at, and with --check into a build gate.

WHAT IT CHECKS
  1. Every language has every key the app actually uses.  A missing key is not fatal at runtime -
     t() falls back to English - but it is a hole in the translation and it should be visible.
  2. Every key the app uses is DEFINED.  A typo'd key silently renders as the key itself, which
     looks like "gp_installed_to" on screen.
  3. Keys defined but never used, so the dictionary does not rot with dead weight.
  4. A rough count of user-visible text still hardcoded in the markup, as a direction of travel.

WHAT IT DELIBERATELY DOES NOT DO
  It does not try to prove a string SHOULD be translated. Game names, file names, paths, title ids
  and API values must stay exactly as they are, and no heuristic can reliably tell those from a
  label. Item 4 is a hint for a human, never a failure.
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "index.html")


def read():
    return io.open(HTML, encoding="utf-8").read()


def dict_block(s):
    """The I18N literal, from 'var I18N={' to the line that closes it."""
    i = s.find("var I18N=")
    if i < 0:
        return ""
    depth, j = 0, s.index("{", i)
    start = j
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[start:j + 1]
        j += 1
    return ""


def languages(block):
    """{code: set(keys)} for every language in the dictionary."""
    out = {}
    # each language starts at  <two spaces><code>:{  at the top level of the literal
    for m in re.finditer(r"\n\s{2}([a-z]{2}):\{", block):
        code = m.group(1)
        depth, j = 0, block.index("{", m.start())
        start = j
        while j < len(block):
            if block[j] == "{":
                depth += 1
            elif block[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = block[start:j + 1]
        out[code] = set(re.findall(r"[\{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body))
    return out


def used_keys(s):
    """Every key the app actually asks for."""
    keys = set()
    for attr in ("data-i18n", "data-i18n-ph", "data-i18n-tip", "data-i18n-aria"):
        keys |= set(re.findall(attr + r'="([^"]+)"', s))
    keys |= set(re.findall(r'[^A-Za-z_.]t\(\s*"([A-Za-z0-9_]+)"\s*\)', s))
    return keys


def hardcoded_markup(s):
    """Rough count of visible text in the static markup with no data-i18n on its element."""
    # The FIRST <script> sits in <head>, long before <body> - slicing to it produced an empty
    # string and a confident, wrong "0". Take from <body> to the LAST <script>, which is the one
    # big UI block at the end of the file.
    b = s.find("<body")
    e = s.rfind("<script")
    body = s[b:e] if (b >= 0 and e > b) else ""
    n = 0
    for m in re.finditer(r">([^<>{}]{3,60})<", body):
        txt = m.group(1).strip()
        if not txt or not re.search(r"[A-Za-z]", txt):
            continue
        if re.match(r"^[\s\d.,:/\\|-]+$", txt):
            continue
        n += 1
    return n


def main():
    check = "--check" in sys.argv
    s = read()
    block = dict_block(s)
    if not block:
        print("i18n_report: could not find the I18N dictionary")
        return 1
    langs = languages(block)
    if "en" not in langs:
        print("i18n_report: no English dictionary")
        return 1

    used = used_keys(s)
    defined_en = langs["en"]
    undefined = sorted(used - defined_en)
    unused = sorted(defined_en - used)

    print("languages : %s" % ", ".join(sorted(langs)))
    print("keys (en) : %d      used by the app: %d" % (len(defined_en), len(used)))
    print()

    # COVERAGE IS MEASURED AGAINST THE KEYS THE APP ACTUALLY USES, not every key defined.
    # The dictionary carries keys nothing asks for yet; translating those is work no one can see,
    # and counting them made a fully-translated language read as 42% complete.
    target = used & defined_en
    worst = 0
    for code in sorted(langs):
        if code == "en":
            continue
        missing = sorted(target - langs[code])
        worst = max(worst, len(missing))
        pct = 100.0 * (len(target) - len(missing)) / max(len(target), 1)
        flag = "OK " if not missing else "GAP"
        print("  [%s] %-3s %6.1f%% of the %d live keys   missing %d%s"
              % (flag, code, pct, len(target), len(missing),
                 ("  e.g. " + ", ".join(missing[:4])) if missing else ""))

    print()
    if undefined:
        print("  [BAD] %d key(s) USED but not defined - these render as the key itself:" % len(undefined))
        for k in undefined[:20]:
            print("        %s" % k)
    else:
        print("  [OK ] every key the app uses is defined")

    if unused:
        print("  [ .. ] %d defined key(s) never used (dead weight, not an error)" % len(unused))

    hard = hardcoded_markup(s)
    print("  [ .. ] roughly %d visible text nodes in the static markup (hint only, not a failure)" % hard)

    if check:
        if undefined:
            print("\nFAIL: keys used but not defined.")
            return 1
        if worst:
            print("\nFAIL: %d live key(s) missing from at least one language." % worst)
            return 1
        print("\nOK: every language covers all %d keys the app uses." % len(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
