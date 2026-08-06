#!/usr/bin/env python3
"""minty processes - a top/htop-style process manager for minty.

Lists processes sorted by CPU or memory, lets you kill them, and can hand
off to btop/htop for the full interactive view.
"""

import curses
import os
import shutil
import subprocess
import sys

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
RESET = "\033[0m"

PS_BASE = ["ps", "-eo", "pid,user,pcpu,pmem,rss,args", "--no-headers"]


def run_capture(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr
    except FileNotFoundError:
        return 127, f"command not found: {argv[0]}"


def run_interactive(argv: list[str]) -> int:
    try:
        return subprocess.run(argv).returncode
    except FileNotFoundError:
        print(f"command not found: {argv[0]}")
        return 127
    except KeyboardInterrupt:
        return 130


def procs(sort: str = "cpu") -> list[dict]:
    code, out = run_capture(PS_BASE)
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid, user, pcpu, pmem, rss, args = parts
        try:
            rows.append({
                "pid": int(pid),
                "user": user,
                "cpu": float(pcpu),
                "mem": float(pmem),
                "rss": int(rss),
                "args": args,
            })
        except ValueError:
            continue
    if sort == "mem":
        rows.sort(key=lambda r: r["mem"], reverse=True)
    elif sort == "pid":
        rows.sort(key=lambda r: r["pid"])
    else:
        rows.sort(key=lambda r: r["cpu"], reverse=True)
    return rows


def fmt_rss(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}T"


def _cli(argv):
    if len(argv) < 2 or argv[1] in ("app", "ui"):
        return 0 if run_app() else 1
    cmd, rest = argv[1], argv[2:]
    if cmd in ("list", "ls"):
        n = 20
        if rest and rest[0].lstrip("-").isdigit():
            n = int(rest[0])
        sort = "mem" if "--mem" in rest else "cpu"
        for p in procs(sort)[:n]:
            print(f"{BOLD}{p['pid']:>7}{RESET} {p['user']:<10} "
                  f"{YELLOW}{p['cpu']:>5.1f}{RESET}% {p['mem']:>4.1f}% "
                  f"{DIM}{fmt_rss(p['rss']):>6}{RESET}  {p['args'][:80]}")
        return 0
    if cmd in ("kill",) and rest:
        for pid in rest:
            code = run_interactive(["kill", "-9", pid])
            if code != 0:
                return code
        return 0
    if cmd in ("top", "btop", "htop"):
        for name in ("btop", "htop", "top"):
            if shutil.which(name):
                return run_interactive([name])
        print("no top/htop/btop found", file=sys.stderr)
        return 1
    print("""usage:
  proc                    open the visual process manager
  proc list [n]           list top processes by cpu (--mem for memory)
  proc kill <pid...>      force kill processes
  proc top                hand off to btop/htop/top""", file=sys.stderr)
    return 2


class ProcApp:
    def __init__(self):
        self.sort = "cpu"
        self.query = ""
        self.refresh()

    def refresh(self):
        self.list = procs(self.sort)
        self.sel = 0
        if self.list:
            self.sel = min(self.sel, len(self.list) - 1)

    def filtered(self):
        q = self.query.strip().lower()
        if not q:
            return self.list
        return [p for p in self.list if q in p["args"].lower() or q == str(p["pid"])]

    def _reenter(self):
        try:
            curses.endwin()
            curses.reset_shell_mode()
        except curses.error:
            pass

    def _confirm(self, stdscr, text):
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(h - 1, 0, text, w - 1, curses.A_REVERSE)
        stdscr.refresh()
        key = stdscr.getch()
        return key in (ord("y"), ord("Y"))

    def do_kill(self, stdscr):
        items = self.filtered()
        if not items:
            return
        p = items[self.sel]
        if p["pid"] == os.getpid():
            stdscr.addnstr(0, 0, " refusing to kill minty itself ", stdscr.getmaxyx()[1] - 1, curses.A_REVERSE)
            stdscr.refresh()
            stdscr.getch()
            return
        if self._confirm(stdscr, f"force kill pid {p['pid']} ({p['args'][:40]})? (y/N)"):
            run_interactive(["kill", "-9", str(p["pid"])])
            self.refresh()

    def do_monitor(self, stdscr):
        for name in ("btop", "htop", "top"):
            if shutil.which(name):
                curses.endwin()
                print(f"\n{BOLD}launching {name}... press q inside to return{RESET}\n")
                run_interactive([name])
                self._reenter()
                self.refresh()
                return
        stdscr.addnstr(0, 0, " btop/htop not found - install one with: pkg install btop ", stdscr.getmaxyx()[1] - 1, curses.A_REVERSE)
        stdscr.refresh()
        stdscr.getch()

    def draw(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, " minty processes ", w - 1, curses.A_BOLD | curses.A_REVERSE)
        info = f" sort: {self.sort}   filter: {self.query or '(none)'} "
        stdscr.addnstr(1, 0, info, w - 1, curses.A_DIM)
        items = self.filtered()
        y = 3
        for idx, p in enumerate(items):
            if y >= h - 2:
                break
            attr = curses.A_REVERSE if idx == self.sel else 0
            line = (f" {p['pid']:>7} {p['user']:<9} {p['cpu']:>5.1f}% "
                    f"{p['mem']:>4.1f}% {fmt_rss(p['rss']):>6}  {p['args'][:70]}")
            stdscr.addnstr(y, 0, line.ljust(w - 1), w - 1, attr)
            y += 1
        stdscr.addnstr(h - 1, 0,
                       " k kill  s sort(cpu/mem)  m btop  r refresh  type to filter  j/k move  q quit ",
                       w - 1, curses.A_DIM)

    def run(self, stdscr):
        try:
            curses.curs_set(0)
            stdscr.keypad(True)
        except curses.error:
            pass
        while True:
            stdscr.erase()
            self.draw(stdscr)
            stdscr.refresh()
            try:
                key = stdscr.getch()
            except curses.error:
                key = -1
            if key in (ord("q"), ord("Q"), 27):
                return
            if key in (10, 13, ord("k"), ord("K")):
                self.do_kill(stdscr)
            elif key in (ord("s"), ord("S")):
                self.sort = {"cpu": "mem", "mem": "pid", "pid": "cpu"}[self.sort]
                self.refresh()
            elif key in (ord("m"), ord("M")):
                self.do_monitor(stdscr)
            elif key in (ord("r"), ord("R")):
                self.refresh()
            elif key in (curses.KEY_DOWN, ord("j")):
                items = self.filtered()
                if items:
                    self.sel = (self.sel + 1) % len(items)
            elif key in (curses.KEY_UP, ord("k")):
                items = self.filtered()
                if items:
                    self.sel = (self.sel - 1) % len(items)
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
            elif 32 <= key < 127 and len(self.query) < 100:
                self.query += chr(key)


def run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = ProcApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
