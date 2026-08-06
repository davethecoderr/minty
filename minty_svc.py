#!/usr/bin/env python3
"""minty services - manage systemd services from minty.

Lists services (running/failed/all), starts/stops/restarts them, enables or
disables them and shows their status. Uses systemctl and falls back to sudo
when a system unit needs root.
"""

import curses
import re
import subprocess
import sys

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
RESET = "\033[0m"

UNIT_RE = re.compile(r"^\s*(\S+\.service)\s+(\S+)\s+(\S+)\s+(\S+)(.*)$")


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


def services(state: str = "running") -> list[dict]:
    argv = ["systemctl", "list-units", "--type=service", "--all",
            "--no-pager", "--no-legend"]
    if state != "all":
        argv += ["--state=" + state]
    code, out = run_capture(argv)
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        m = UNIT_RE.match(line)
        if m:
            rows.append({
                "unit": m.group(1),
                "load": m.group(2),
                "active": m.group(3),
                "sub": m.group(4),
                "desc": m.group(5).strip(),
            })
    return rows


def sysctl(op: str, unit: str) -> int:
    """Run `systemctl <op> <unit>`, retrying with sudo on permission errors."""
    code, out = run_capture(["systemctl", op, unit])
    if code == 0:
        return 0
    low = (out or "").lower()
    if "permission denied" in low or "operation not permitted" in low or "not a root" in low:
        print(f"{YELLOW}needs root, retrying with sudo...{RESET}")
        return run_interactive(["sudo", "systemctl", op, unit])
    print(out.rstrip())
    return code


def _cli(argv):
    if len(argv) < 2 or argv[1] in ("app", "ui"):
        return 0 if run_app() else 1
    cmd, rest = argv[1], argv[2:]
    if cmd in ("list", "ls"):
        state = "all" if "--all" in rest else "running"
        pattern = next((a for a in rest if not a.startswith("-")), None)
        for s in services(state):
            if pattern and pattern not in s["unit"]:
                continue
            mark = f"{GREEN}●{RESET}" if s["sub"] == "running" else f"{RED}○{RESET}"
            print(f"{mark} {BOLD}{s['unit']}{RESET}  {s['active']}/{s['sub']}  {DIM}{s['desc']}{RESET}")
        return 0
    if cmd in ("start", "stop", "restart", "enable", "disable", "status") and rest:
        for unit in rest:
            if not unit.endswith(".service"):
                unit += ".service"
            if cmd == "status":
                return run_interactive(["systemctl", "--no-pager", "status", unit])
            code = sysctl(cmd, unit)
            if code != 0:
                return code
        return 0
    print("""usage:
  svc                    open the visual service manager
  svc list [pattern]     list running services (--all for everything)
  svc start/stop/restart/enable/disable <name>
  svc status <name>      show a service's status""", file=sys.stderr)
    return 2


class SvcApp:
    def __init__(self):
        self.mode = "running"  # running | failed | all
        self.query = ""
        self.refresh()

    def refresh(self):
        self.list = services(self.mode)
        self.sel = 0
        if self.list:
            self.sel = min(self.sel, len(self.list) - 1)

    def filtered(self):
        q = self.query.strip().lower()
        if not q:
            return self.list
        return [s for s in self.list if q in s["unit"].lower() or q in s["desc"].lower()]

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

    def _op(self, op):
        items = self.filtered()
        if not items:
            return
        unit = items[self.sel]["unit"]
        curses.endwin()
        print(f"\n{BOLD}minty: {op} {unit}...{RESET}\n")
        code = sysctl(op, unit)
        if code != 0:
            print(f"{RED}{op} failed (rc {code}).{RESET}\n")
        self._reenter()
        self.refresh()

    def do_status(self):
        items = self.filtered()
        if not items:
            return
        unit = items[self.sel]["unit"]
        curses.endwin()
        print()
        run_interactive(["systemctl", "--no-pager", "status", unit])
        self._reenter()

    def draw(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, " minty services ", w - 1, curses.A_BOLD | curses.A_REVERSE)
        info = f" view: {self.mode}   filter: {self.query or '(none)'} "
        stdscr.addnstr(1, 0, info, w - 1, curses.A_DIM)
        items = self.filtered()
        y = 3
        for idx, s in enumerate(items):
            if y >= h - 2:
                break
            attr = curses.A_REVERSE if idx == self.sel else 0
            mark = "●" if s["sub"] == "running" else "○"
            line = f" {mark} {s['unit']:<38} {s['sub']:<9} {s['desc']}"
            stdscr.addnstr(y, 0, line.ljust(w - 1), w - 1, attr)
            y += 1
        stdscr.addnstr(h - 1, 0,
                       " Enter status  s start  t stop  r restart  e enable  d disable  "
                       "a view  type to filter  q quit ",
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
            if key in (10, 13, curses.KEY_ENTER):
                self.do_status()
            elif key in (ord("s"), ord("S")):
                self._op("start")
            elif key in (ord("t"), ord("T")):
                self._op("stop")
            elif key in (ord("r"), ord("R")):
                self._op("restart")
            elif key in (ord("e"), ord("E")):
                self._op("enable")
            elif key in (ord("d"), ord("D")):
                self._op("disable")
            elif key in (ord("a"), ord("A")):
                self.mode = {"running": "failed", "failed": "all", "all": "running"}[self.mode]
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
    app = SvcApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
