#!/usr/bin/env python3
"""minty tmux - a visual tmux session manager for minty.

Lists your tmux sessions and lets you create, attach to and kill them.
If tmux is not installed, minty offers to install it with your package
manager. Also works from the command line (tmux list/new/attach/kill).
"""

import curses
import os
import re
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

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_PY = os.path.join(HERE, "minty_pkg.py")


def have_tmux() -> bool:
    return shutil.which("tmux") is not None


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


def sessions() -> list[dict]:
    if not have_tmux():
        return []
    code, out = run_capture(["tmux", "ls"])
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        m = re.match(r"^(\S+): (\d+) windows", line)
        if m:
            rows.append({
                "name": m.group(1),
                "windows": int(m.group(2)),
                "created": line.partition("created ")[2],
            })
    return rows


def _cli(argv):
    if len(argv) < 2 or argv[1] in ("app", "ui"):
        return 0 if run_app() else 1
    cmd, rest = argv[1], argv[2:]
    if cmd == "install":
        print(f"{YELLOW}installing tmux...{RESET}")
        return run_interactive([sys.executable, PKG_PY, "install", "tmux"])
    if not have_tmux():
        print("tmux is not installed. try: tmux install", file=sys.stderr)
        return 1
    if cmd == "list":
        for s in sessions():
            print(f"{BOLD}{s['name']}{RESET}  {s['windows']} windows  {DIM}({s['created']}){RESET}")
        return 0
    if cmd == "new" and rest:
        name = rest[0]
        code = run_interactive(["tmux", "new-session", "-d", "-s", name])
        if code != 0:
            return code
        return run_interactive(["tmux", "attach", "-t", name])
    if cmd == "attach" and rest:
        return run_interactive(["tmux", "attach", "-t", rest[0]])
    if cmd == "kill" and rest:
        return run_interactive(["tmux", "kill-session", "-t", rest[0]])
    print("""usage:
  tmux                    open the visual session manager
  tmux list               list sessions
  tmux new <name>         create and attach to a session
  tmux attach <name>      attach to a session
  tmux kill <name>        kill a session
  tmux install            install tmux with your package manager""", file=sys.stderr)
    return 2


class TmuxApp:
    def __init__(self):
        self.missing = not have_tmux()
        self.refresh()

    def refresh(self):
        self.sessions = sessions()
        self.sel = 0
        if self.sessions:
            self.sel = min(self.sel, len(self.sessions) - 1)

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

    def _input(self, stdscr, prompt_text):
        h, w = stdscr.getmaxyx()
        y = h - 1
        buf = []
        while True:
            line = (prompt_text + "".join(buf)).ljust(w - 1)[:w - 1]
            stdscr.addnstr(y, 0, line, w - 1, curses.A_REVERSE)
            stdscr.move(y, min(w - 1, len(prompt_text) + len(buf)))
            stdscr.refresh()
            key = stdscr.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return "".join(buf).strip()
            if key in (27,):
                return None
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif 32 <= key < 127 and len(buf) < w - len(prompt_text) - 2:
                buf.append(chr(key))

    def do_attach(self, stdscr):
        if not self.sessions:
            return
        name = self.sessions[self.sel]["name"]
        curses.endwin()
        print(f"\n{BOLD}attaching to '{name}'... (leave with Ctrl+B d or 'exit'){RESET}\n")
        run_interactive(["tmux", "attach", "-t", name])
        self._reenter()
        self.refresh()

    def do_new(self, stdscr):
        name = self._input(stdscr, "new session name: ")
        if not name:
            return
        curses.endwin()
        run_interactive(["tmux", "new-session", "-d", "-s", name])
        run_interactive(["tmux", "attach", "-t", name])
        self._reenter()
        self.refresh()

    def do_kill(self, stdscr):
        if not self.sessions:
            return
        name = self.sessions[self.sel]["name"]
        if self._confirm(stdscr, f"kill session '{name}'? (y/N)"):
            run_interactive(["tmux", "kill-session", "-t", name])
            self.refresh()

    def do_install(self, stdscr):
        if self._confirm(stdscr, "tmux is not installed. install it now? (y/N)"):
            curses.endwin()
            print(f"\n{BOLD}minty: installing tmux...{RESET}\n")
            run_interactive([sys.executable, PKG_PY, "install", "tmux"])
            self._reenter()
            self.missing = not have_tmux()
            self.refresh()

    def draw(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, " minty tmux ", w - 1, curses.A_BOLD | curses.A_REVERSE)
        if self.missing:
            stdscr.addnstr(1, 0, " tmux is not installed - press i to install with your package manager ", w - 1, curses.A_DIM)
            return
        stdscr.addnstr(1, 0, f" {len(self.sessions)} session(s) ", w - 1, curses.A_DIM)
        y = 3
        for idx, s in enumerate(self.sessions):
            if y >= h - 2:
                break
            attr = curses.A_REVERSE if idx == self.sel else 0
            line = f" {s['name']:<20} {s['windows']} windows  {s['created']}"
            stdscr.addnstr(y, 0, line.ljust(w - 1), w - 1, attr)
            y += 1
        stdscr.addnstr(h - 1, 0,
                       " Enter/space/a attach   n new   x kill   j/k move   q quit ",
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
            if self.missing:
                if key in (ord("i"), ord("I"), ord("y"), ord("Y")):
                    self.do_install(stdscr)
                continue
            if key in (10, 13, curses.KEY_ENTER, ord(" "), ord("a"), ord("A")):
                self.do_attach(stdscr)
            elif key in (ord("n"), ord("N")):
                self.do_new(stdscr)
            elif key in (ord("x"), ord("X")):
                self.do_kill(stdscr)
            elif key in (curses.KEY_DOWN, ord("j")):
                if self.sessions:
                    self.sel = (self.sel + 1) % len(self.sessions)
            elif key in (curses.KEY_UP, ord("k")):
                if self.sessions:
                    self.sel = (self.sel - 1) % len(self.sessions)


def run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = TmuxApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
