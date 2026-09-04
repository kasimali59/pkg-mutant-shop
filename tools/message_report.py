#!/usr/bin/env python3
"""Every word PKG MUTANT SHOP says, pulled out of the source that says it.

WHY THIS IS GENERATED AND NOT WRITTEN
-------------------------------------
The first version of this list was written by hand, and it was out of date within a day - a toast
was reworded in server.c and the table still quoted the old sentence. A hand-kept list of strings
is a second copy of the strings, and second copies drift. This reads the real ones.

  python tools/message_report.py            -> research/message-reference.html
  python tools/message_report.py --check    -> exit 1 if any message breaks the house style

HOUSE STYLE (enforced by --check)
  * console toasts: at most two lines; line 1 is the event, line 2 is what it means or what to do
  * the words "PKG MUTANT SHOP" appear only when the shop itself is the subject
  * no raw error codes, JSON, or file paths on a television
  * no bare-verb failures ("Failed", "Rescan failed") anywhere
  * no reference to software we do not ship (etaHEN, Elf Arsenal, DPI, :12800) outside the one
    control whose whole job is to remove it
"""
import argparse
import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "research", "message-reference.html")

CONSOLE_C = os.path.join(ROOT, "ps5-app", "onconsole", "server.c")
PROBE_C = os.path.join(ROOT, "ps5-app", "onconsole", "installer_probe.c")
COMPANION = os.path.join(ROOT, "companion", "server.py")
WEB = os.path.join(ROOT, "web", "index.html")

# Words that should not appear in anything a user reads. The cleanup control is the one place
# they legitimately do, so it is excluded by id rather than by pretending the rule does not exist.
BANNED = ("etahen", "elf arsenal", "dpiv2", "goldhen")
BARE_VERBS = ("failed", "error", "failed.", "rescan failed", "scan failed", "hash failed",
              "copy failed", "move failed", "verify failed", "toggle failed", "patch failed",
              "retry failed", "reload failed", "could not save")


def _first_arg(call):
    """The text of a call's FIRST argument only, given `name(...)`.

    Taking every literal in the call swept up the arguments too, so a plural suffix written as
    `n == 1 ? "" : "s"` was concatenated onto the end of the sentence and the report quoted
    "...written to the running game - s". Stop at the first comma that is not inside brackets,
    a string, or a ternary's colon.
    """
    i = call.find("(")
    if i < 0:
        return ""
    depth, j, in_str, esc = 0, i, False, False
    while j < len(call):
        c = call[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
            if depth == 0:
                return call[i + 1:j]
        elif c == "," and depth == 1:
            return call[i + 1:j]
        j += 1
    return call[i + 1:]


def _join_c_string(text):
    """Turn a run of C string literals ("a" "b") into one string, keeping \\n as a marker."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', _first_arg(text) if "(" in text else text)
    return "".join(parts)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def console_messages():
    """Every notify()/notifyf()/notify_sync() call in the two console binaries."""
    out = []
    for path, who in ((CONSOLE_C, "shop ELF"), (PROBE_C, "installer ELF")):
        src = read(path)
        lines = src.split("\n")
        for m in re.finditer(r'\bnotify(?:f|_sync|_icon)?\s*\(', src):
            start = m.start()
            # take up to the matching close paren, so multi-line literals come along
            depth, i = 0, m.end() - 1
            while i < len(src):
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            call = src[start:i + 1]
            body = _join_c_string(call)
            if not body or body.strip() in ("%s", ""):
                continue
            lineno = src.count("\n", 0, start) + 1
            # The enclosing function, for "where". Signatures are matched at COLUMN 0 only, and a
            # multi-line signature is matched on its first line - an earlier version required the
            # opening brace on the same line, so every message inside a function whose arguments
            # wrapped was attributed to whichever function was defined above it.
            where = ""
            for j in range(lineno - 1, -1, -1):
                mm = re.match(r'^(?:static\s+)?[A-Za-z_][\w]*[\w \*]*?\b(\w+)\s*\((?!\s*\))',
                              lines[j])
                if mm and not lines[j].rstrip().endswith(";"):
                    where = mm.group(1)
                    break
            out.append({"text": body, "file": os.path.basename(path), "line": lineno,
                        "where": where or who, "who": who})
    return out


def web_messages():
    src = read(WEB)
    out = []
    for m in re.finditer(r'toast\(\s*("(?:[^"\\]|\\.)*")', src):
        lineno = src.count("\n", 0, m.start()) + 1
        out.append({"text": m.group(1)[1:-1], "file": "web/index.html", "line": lineno,
                    "where": "app toast", "who": "app"})
    # the ones built from an expression get listed by their fallback sentence instead
    for m in re.finditer(r'errText\([^,]+,\s*("(?:[^"\\]|\\.)*")\s*\)', src):
        lineno = src.count("\n", 0, m.start()) + 1
        out.append({"text": m.group(1)[1:-1], "file": "web/index.html", "line": lineno,
                    "where": "app toast (fallback)", "who": "app"})
    return out


def queue_messages():
    src = read(COMPANION)
    out = []
    for m in re.finditer(r'msg=("(?:[^"\\]|\\.)*"(?:\s*\n?\s*"(?:[^"\\]|\\.)*")*)', src):
        text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))
        if not text or text.startswith("⟳"):
            continue
        lineno = src.count("\n", 0, m.start()) + 1
        out.append({"text": text, "file": "companion/server.py", "line": lineno,
                    "where": "queue row", "who": "queue"})
    return out


def lint(rows):
    """Return a list of (row, complaint). Empty means the whole app is on style."""
    bad = []
    for r in rows:
        t = r["text"]
        low = t.lower()
        if any(b in low for b in BANNED):
            bad.append((r, "names software we do not ship"))
        if low.strip().rstrip(".") in BARE_VERBS:
            bad.append((r, "bare-verb failure - says nothing about what to do"))
        if r["who"] != "app" and t.count("\\n") > 1:
            bad.append((r, "more than two lines on a television"))
        if re.search(r'0x%08X|\{\\"', t) and "%s" not in t:
            bad.append((r, "puts a raw code or JSON on screen"))
        if r["who"] in ("shop ELF", "installer ELF") and t.startswith("PKG MUTANT SHOP\\n"):
            bad.append((r, "app name used as a title line - the icon already says who is speaking"))
    return bad


PAGE = """<title>What The Shop Says</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Newsreader:opsz,wght@6..72,400;6..72,600&display=swap">
<style>
  :root{
    --ink:#12100f; --ink-soft:#514a45; --ink-faint:#8b817a;
    --paper:#fbf9f6; --card:#ffffff; --rule:#e6e0d8;
    --mark:#b4442e; --mark-soft:#f3e3de; --good:#3f7d55; --warn:#9a6b12;
    --mono:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  }
  @media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
      --ink:#f0ebe5; --ink-soft:#b6ada5; --ink-faint:#7d746d;
      --paper:#14120f; --card:#1c1916; --rule:#2f2a25;
      --mark:#e0765c; --mark-soft:#33221d; --good:#7fb98f; --warn:#d6a54a;
    }
  }
  :root[data-theme="dark"]{
    --ink:#f0ebe5; --ink-soft:#b6ada5; --ink-faint:#7d746d;
    --paper:#14120f; --card:#1c1916; --rule:#2f2a25;
    --mark:#e0765c; --mark-soft:#33221d; --good:#7fb98f; --warn:#d6a54a;
  }
  body{background:var(--paper);color:var(--ink);margin:0;
       font:16px/1.62 Newsreader,"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;}
  .wrap{max-width:1180px;margin:0 auto;padding:48px 22px 90px}
  h1{font-size:38px;line-height:1.1;margin:0 0 6px;letter-spacing:-.02em;text-wrap:balance}
  .sub{color:var(--ink-soft);margin:0 0 34px;font-size:16px}
  h2{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--mark);
     margin:44px 0 12px;font-family:var(--mono);font-weight:600}
  .count{color:var(--ink-faint);font-weight:400;letter-spacing:.06em}
  .tablewrap{overflow-x:auto;border:1px solid var(--rule);border-radius:10px;background:var(--card)}
  table{border-collapse:collapse;width:100%;min-width:760px}
  th{font-family:var(--mono);font-size:10px;letter-spacing:.13em;text-transform:uppercase;
     color:var(--ink-faint);text-align:left;padding:11px 14px;border-bottom:1px solid var(--rule);
     font-weight:600;background:var(--card);position:sticky;top:0}
  td{padding:11px 14px;border-bottom:1px solid var(--rule);vertical-align:top}
  tr:last-child td{border-bottom:0}
  .msg{font-family:var(--mono);font-size:12.5px;line-height:1.65;white-space:pre-wrap;
       color:var(--ink);max-width:640px}
  .msg .l2{color:var(--ink-soft)}
  .msg var{color:var(--mark);font-style:normal;background:var(--mark-soft);
           padding:0 3px;border-radius:3px}
  .where{font-family:var(--mono);font-size:11px;color:var(--ink-faint);white-space:nowrap}
  .ok{color:var(--good);font-family:var(--mono);font-size:11px}
  .note{background:var(--mark-soft);border-left:3px solid var(--mark);padding:14px 18px;
        border-radius:0 8px 8px 0;margin:0 0 30px;color:var(--ink-soft);font-size:14px}
  .note b{color:var(--ink)}
  footer{margin-top:60px;padding-top:18px;border-top:1px solid var(--rule);
         color:var(--ink-faint);font-size:12.5px;font-family:var(--mono)}
</style>
<div class="wrap">
<h1>What the shop says</h1>
<p class="sub">Every message PKG MUTANT SHOP puts in front of you &mdash; on the television and in the
app &mdash; read straight out of the code that says it.</p>
<div class="note">
  <b>How to read the messages.</b> <var>%s</var> is filled in when the message is shown: a game name,
  a count, a drive. A second line is shown underneath in grey &mdash; on the PS5 that is the smaller
  line under the title. Every console message carries the shop icon, which is why almost none of them
  say &ldquo;PKG MUTANT SHOP&rdquo;: the artwork already says who is speaking.
</div>
__BODY__
<footer>__FOOT__</footer>
</div>
"""


def fmt(text):
    parts = text.split("\\n")
    esc = [html.escape(p) for p in parts]
    esc = [re.sub(r'(%\w|%\d*\.?\d*[dsfxX]|%08X|%lld)', r'<var>\1</var>', p) for p in esc]
    if len(esc) == 1:
        return esc[0]
    return esc[0] + "\n" + '<span class="l2">' + "\n".join(esc[1:]) + "</span>"


def table(rows, cols=("Message", "Where it comes from")):
    out = ['<div class="tablewrap"><table><thead><tr>']
    out.append("<th>%s</th><th>%s</th>" % cols)
    out.append("</tr></thead><tbody>")
    for r in sorted(rows, key=lambda x: x["text"].lower()):
        out.append('<tr><td class="msg">%s</td><td class="where">%s<br>%s:%d</td></tr>'
                   % (fmt(r["text"]), html.escape(r["where"]), html.escape(r["file"]), r["line"]))
    out.append("</tbody></table></div>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any message breaks the house style")
    args = ap.parse_args()

    con = console_messages()
    web = web_messages()
    que = queue_messages()
    allrows = con + web + que

    bad = lint(allrows)
    if args.check:
        for r, why in bad:
            print("%s:%d  %s\n    %r" % (r["file"], r["line"], why, r["text"][:110]))
        print("%d message(s) checked, %d off style" % (len(allrows), len(bad)))
        return 1 if bad else 0

    body = []
    body.append('<h2>On the television <span class="count">&mdash; %d</span></h2>' % len(con))
    body.append(table(con))
    body.append('<h2>In the app <span class="count">&mdash; %d</span></h2>' % len(web))
    body.append(table(web))
    body.append('<h2>In the install queue <span class="count">&mdash; %d</span></h2>' % len(que))
    body.append(table(que))

    foot = "%d messages &middot; generated by tools/message_report.py &middot; %s" % (
        len(allrows), "off style: %d" % len(bad) if bad else "all on style")
    page = PAGE.replace("__BODY__", "\n".join(body)).replace("__FOOT__", foot)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write(page)
    print("wrote %s (%d messages, %d off style)" % (OUT, len(allrows), len(bad)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
