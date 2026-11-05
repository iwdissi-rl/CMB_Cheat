"""Command-line interface for paobao.

    bao status                      detect the Firefox login, list pending
    bao                             paste links at the prompt, run them paced
    bao run URL [URL...]            run specific passages
    bao save                        save all pending essays to ~/tcb_essays

Pacing: each passage waits a random whole-second time drawn uniformly from
[--min, --max] minutes before its quiz is taken — the "reading time" the user
configures. Defaults: 4–9 minutes.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__
from .api import SITE, Tcb, essay_markdown
from .driver import Unknown, default_firefox_binary, make_runner, new_debug_dir, run_quiz
from .firefox import site_cookies
from .links import index_pending, parse_links, resolve
from .pacing import parse_range, roll_seconds

ESSAYS_DIR = os.path.expanduser("~/tcb_essays")


def _cookies(args) -> list:
    return site_cookies(args.profile)


def _tcb(args) -> Tcb:
    return Tcb(_cookies(args))


def _read_links(args) -> list[str]:
    """Links from argv, or from the interactive paste area on stdin."""
    if args.links:
        return parse_links(" ".join(args.links))
    print("Paste Chairman's Bao links, one per line, then an empty line:")
    out: list[str] = []
    try:
        while True:
            line = input()
            if not line.strip():
                break
            out.extend(parse_links(line))
    except EOFError:
        pass
    return out


def _save_essays(data: list[dict]) -> list[str]:
    os.makedirs(ESSAYS_DIR, exist_ok=True)
    paths = []
    for a in data:
        slug = a["assignment"]["post"]["post_name"]
        fn = os.path.join(ESSAYS_DIR, f"{slug}.md")
        open(fn, "w").write(essay_markdown(a))
        paths.append(fn)
    return paths


def cmd_status(args) -> int:
    t = _tcb(args)
    if not t.logged_in:
        print("NOT LOGGED IN: open thechairmansbao.com in Firefox, log in, then retry.")
        return 2
    data = t.pending()
    if data is None:
        print("NOT LOGGED IN (or site unreachable): retry, or check the network.")
        return 2
    print(f"Logged in to {SITE} — {len(data)} outstanding assignment(s):")
    for a in data:
        A = a["assignment"]
        tag = "READING" if A["type"] == 1 else f"skip ({A['type_text']})"
        print(f"  [{tag}] {a['show_text']}  due {a['due_date_time']}")
    return 0


def cmd_save(args) -> int:
    t = _tcb(args)
    data = t.pending()
    if data is None:
        print("NOT LOGGED IN.")
        return 2
    for fn in _save_essays(data):
        print(fn)
    return 0


def cmd_run(args) -> int:
    links = _read_links(args)
    if not links:
        print("No links given.")
        return 0
    lo, hi = args.range
    t = _tcb(args)
    if not t.logged_in:
        print("NOT LOGGED IN: open thechairmansbao.com in Firefox, log in, then retry.")
        return 2
    pend = t.pending() or []
    idx = index_pending(pend)
    items = [resolve(l, idx) for l in links]

    print(f"{len(items)} link(s) to complete; reading time {lo:g}–{hi:g} min each.")
    plan = [roll_seconds(lo, hi) for _ in items]
    for it, secs in zip(items, plan):
        print(f"  {it['title']}  -> in {int(secs // 60)}m{int(secs % 60):02d}s")

    if args.dry_run:
        print("Dry run — nothing opened.")
        return 0

    debug_dir = new_debug_dir()
    runner = make_runner(_cookies(args), binary=default_firefox_binary())
    failed: list[str] = []
    try:
        for it, secs in zip(items, plan):
            label = it["title"]
            if not args.now:
                print(f"  … reading {label} ({secs / 60:.1f} min)", flush=True)
                time.sleep(secs)
            if not it["pid"]:
                print(f"SKIP {label}: no article id (link not recognised as an assigned passage)")
                continue
            try:
                sc = run_quiz(runner, SITE, it["pid"], it["aid"], debug_dir)
                scores = ", ".join(f"{k.replace('_comprahension_', '')}={v}" for k, v in sc.items())
                print(f"DONE {label}: {scores or 'submitted'}")
            except Unknown as e:
                failed.append(label)
                print(f"UNKNOWN-TYPE {label}: {e}")
            except Exception as e:  # noqa: BLE001 — network hiccups shouldn't kill the run
                failed.append(label)
                print(f"FAILED {label}: {e}")
    finally:
        runner.close()

    left = t.pending()
    if left is not None:
        print("Still outstanding:", ", ".join(x["show_text"] for x in left) or "none")
    if failed:
        print("Debug dumps in", debug_dir)
        return 3
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bao", description="The Chairman's Bao reading-assignment runner.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--profile", help="path to a Firefox profile dir (default: newest with a sessionstore)")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("status", help="detect login and list pending assignments")
    sp.add_argument("--profile")
    sp.set_defaults(func=cmd_status, links=[], range=(4.0, 9.0), dry_run=False, now=False)

    ss = sub.add_parser("save", help="save all pending essays to ~/tcb_essays")
    ss.add_argument("--profile")
    ss.set_defaults(func=cmd_save, links=[], range=(4.0, 9.0), dry_run=False, now=False)

    rp = sub.add_parser("run", help="complete the given passages, paced")
    rp.add_argument("links", nargs="*", help="Chairman's Bao passage URLs")
    rp.add_argument("--profile")
    rp.add_argument("-r", "--range", type=parse_range, default=(4.0, 9.0), metavar="MIN[-MAX]",
                    help="reading time per passage in minutes (default 4-9)")
    rp.add_argument("-n", "--dry-run", action="store_true", help="show the plan, touch no browser")
    rp.add_argument("--now", action="store_true", help="skip the pacing waits (run back to back)")
    rp.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        argv = ["run"]
    elif argv[0] not in ("status", "save", "run", "-h", "--help", "--version"):
        argv = ["run", *argv]  # bare `bao https://…` == `bao run https://…`
    args = parser.parse_args(argv)
    return args.func(args)
