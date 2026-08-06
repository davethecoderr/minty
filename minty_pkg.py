#!/usr/bin/env python3
"""minty packages - package manager for minty (pacman, paru, yay, apt).

Detects what package manager your system has and wraps it so minty can
search, install, remove, update and clean packages - both from the command
line and from a visual curses app.
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


def detect_pm() -> dict | None:
    """Return {'kind': ..., 'pm': ...} for the system's package manager."""
    for name in ("paru", "yay"):
        if shutil.which(name):
            return {"kind": "aur", "pm": name}
    if shutil.which("pacman"):
        return {"kind": "pacman", "pm": "pacman"}
    if shutil.which("apt"):
        return {"kind": "apt", "pm": "apt"}
    return None


def _argv(pm: dict, *parts) -> list[str]:
    return [pm["pm"]] + list(parts)


def _sudo(pm: dict, *parts) -> list[str]:
    if pm["kind"] == "apt" or pm["pm"] in ("paru", "yay"):
        return _argv(pm, *parts)
    return ["sudo"] + _argv(pm, *parts)


def search_cmd(pm, term):
    if pm["kind"] == "apt":
        return _argv(pm, "search", term)
    return _argv(pm, "-Ss", term)


def install_cmd(pm, pkgs):
    if pm["kind"] == "apt":
        return _sudo(pm, "install", *pkgs)
    return _sudo(pm, "-S", "--needed", *pkgs)


def remove_cmd(pm, pkgs):
    if pm["kind"] == "apt":
        return _sudo(pm, "remove", *pkgs)
    return _sudo(pm, "-Rns", *pkgs)


def update_cmd(pm):
    if pm["kind"] == "apt":
        return ["sudo", "sh", "-c", "apt update && apt upgrade -y"]
    if pm["pm"] in ("paru", "yay"):
        return [pm["pm"], "-Syu", "--noconfirm"]
    return ["sudo", "pacman", "-Syu"]


def upgrades_cmd(pm):
    if pm["kind"] == "apt":
        return _argv(pm, "list", "--upgradable")
    return _argv(pm, "-Qu")


def orphans_cmd(pm):
    if pm["kind"] == "apt":
        return ["sudo", "apt", "autoremove", "--dry-run"]
    return _argv(pm, "-Qdt")


def clean_cmd(pm):
    if pm["kind"] == "apt":
        return ["sudo", "apt", "autoremove", "-y"]
    return _sudo(pm, "-Sc")


def run_capture(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr
    except FileNotFoundError:
        return 127, f"command not found: {argv[0]}"


def run_interactive(argv: list[str]) -> int:
    """Run a command attached to the real terminal (for sudo prompts)."""
    try:
        proc = subprocess.run(argv)
        return proc.returncode
    except FileNotFoundError:
        print(f"command not found: {argv[0]}")
        return 127
    except KeyboardInterrupt:
        return 130


def is_installed(pm: dict, name: str) -> bool:
    if pm["kind"] == "apt":
        return False
    code, _ = run_capture([pm["pm"], "-Q", name])
    return code == 0


# --------------------------------------------------------------------------
# Search result parsing
# --------------------------------------------------------------------------

PKG_RE = re.compile(r"^([\w.-]+)/([\w@.+-]+)\s+([\S]+)(.*)$")


def parse_pkg(output: str) -> list[dict]:
    """Parse `pacman -Ss`/`yay -Ss` output into package dicts."""
    pkgs = []
    cur = None
    for line in output.splitlines():
        m = PKG_RE.match(line)
        if m:
            repo, name, version, tail = m.group(1), m.group(2), m.group(3), m.group(4)
            installed = "[installed]" in tail or (len(line.split()) > 2 and line.split()[-1] == "[installed]")
            cur = {"repo": repo, "name": name, "version": version, "desc": "", "installed": installed}
            pkgs.append(cur)
        elif cur and line.startswith(" ") and line.strip():
            cur["desc"] = (cur["desc"] + " " + line.strip()).strip()
    return pkgs


# --------------------------------------------------------------------------
# Non-visual CLI
# --------------------------------------------------------------------------

def _cli(argv):
    pm = detect_pm()
    if pm is None:
        print("no supported package manager found (pacman, yay, paru, apt)", file=sys.stderr)
        return 1
    if len(argv) < 2 or argv[1] in ("app", "ui", "menu"):
        return 0 if run_app() else 1
    cmd = argv[1]
    rest = argv[2:]

    if cmd == "manager":
        print(f"{pm['pm']} ({pm['kind']})")
        return 0
    if cmd == "search" and rest:
        code, out = run_capture(search_cmd(pm, rest[0]))
        for p in parse_pkg(out):
            mark = f"{GREEN}●{RESET} " if p["installed"] else "  "
            print(f"{mark}{BOLD}{p['repo']}/{p['name']}{RESET} {DIM}{p['version']}{RESET}")
            if p["desc"]:
                print(f"    {p['desc']}")
        return code
    if cmd == "info" and rest:
        if pm["kind"] == "apt":
            code, out = run_capture(_argv(pm, "show", rest[0]))
        else:
            code, out = run_capture([pm["pm"], "-Qi", rest[0]])
        print(out.rstrip())
        return code
    if cmd == "list":
        if pm["kind"] == "apt":
            code, out = run_capture(_argv(pm, "list", "--installed"))
        else:
            code, out = run_capture([pm["pm"], "-Q"])
        print(out.rstrip())
        return code
    if cmd in ("updates", "upgrades", "outdated"):
        code, out = run_capture(upgrades_cmd(pm))
        print(out.rstrip() or "up to date")
        return code
    if cmd == "orphans":
        code, out = run_capture(orphans_cmd(pm))
        print(out.rstrip())
        return code
    if cmd == "install" and rest:
        print(f"{YELLOW}installing: {' '.join(rest)}{RESET}")
        return run_interactive(install_cmd(pm, rest))
    if cmd == "remove" and rest:
        print(f"{YELLOW}removing: {' '.join(rest)}{RESET}")
        return run_interactive(remove_cmd(pm, rest))
    if cmd == "update":
        print(f"{YELLOW}system update via {pm['pm']}{RESET}")
        return run_interactive(update_cmd(pm))
    if cmd == "clean":
        return run_interactive(clean_cmd(pm))

    print("""usage:
  pkg                        open the visual package app
  pkg search <term>          search packages (incl. AUR with paru/yay)
  pkg info <pkg>             show installed info for a package
  pkg list                   list installed packages
  pkg updates                list available updates
  pkg orphans                list unneeded packages
  pkg install <pkg...>       install packages
  pkg remove <pkg...>        remove packages
  pkg update                 full system update
  pkg clean                  clear the package cache""", file=sys.stderr)
    return 2


# --------------------------------------------------------------------------
# Visual app
# --------------------------------------------------------------------------

class PkgApp:
    def __init__(self):
        self.pm = detect_pm()
        self.query = ""
        self.results = []
        self.sel = 0
        self.msg = "type a search term (s) - enter to search - i install - r remove - u update system"
        self.busy = False

    def do_search(self, stdscr, query):
        if not query.strip():
            return
        self.busy = True
        self.msg = f"searching for '{query}'..."
        stdscr.refresh()
        code, out = run_capture(search_cmd(self.pm, query))
        self.results = parse_pkg(out)
        self.sel = 0
        self.busy = False
        if not self.results:
            self.msg = f"no results for '{query}'"
        else:
            self.msg = f"{len(self.results)} result(s) for '{query}' - i install, r remove, s new search"

    def do_install(self, stdscr):
        if not self.results:
            return
        pkg = self.results[self.sel]["name"]
        if self._confirm(stdscr, f"install '{pkg}'? (y/N)"):
            curses.endwin()
            print(f"\n{BOLD}minty: installing {pkg}...{RESET}\n")
            run_interactive(install_cmd(self.pm, [pkg]))
            self.results[self.sel]["installed"] = True
            self._reenter()

    def do_remove(self, stdscr):
        if not self.results:
            return
        pkg = self.results[self.sel]["name"]
        if self._confirm(stdscr, f"remove '{pkg}'? (y/N)"):
            curses.endwin()
            print(f"\n{BOLD}minty: removing {pkg}...{RESET}\n")
            run_interactive(remove_cmd(self.pm, [pkg]))
            self.results[self.sel]["installed"] = False
            self._reenter()

    def do_update(self, stdscr):
        if self._confirm(stdscr, "full system update? (y/N)"):
            curses.endwin()
            print(f"\n{BOLD}minty: updating system via {self.pm['pm']}...{RESET}\n")
            run_interactive(update_cmd(self.pm))
            self._reenter()

    def do_clean(self, stdscr):
        if self._confirm(stdscr, "clean package cache? (y/N)"):
            curses.endwin()
            run_interactive(clean_cmd(self.pm))
            self._reenter()

    def do_orphans(self):
        code, out = run_capture(orphans_cmd(self.pm))
        self.results = parse_pkg(out) if self.pm["kind"] != "apt" else []
        if not self.results and out.strip():
            self.results = [{"repo": "?", "name": n, "version": "", "desc": "", "installed": True}
                            for n in out.splitlines() if n.strip()]
        self.sel = 0
        self.msg = f"{len(self.results)} orphan(s) - i installs nothing, r removes"

    def _confirm(self, stdscr, text):
        y = max(0, stdscr.getmaxyx()[0] - 1)
        stdscr.addnstr(y, 0, text, stdscr.getmaxyx()[1] - 1, curses.A_REVERSE)
        stdscr.refresh()
        key = stdscr.getch()
        return key in (ord("y"), ord("Y"))

    def _input(self, stdscr, prompt_text, initial=""):
        h, w = stdscr.getmaxyx()
        y = h - 1
        buf = list(initial)
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

    def _reenter(self):
        try:
            curses.endwin()
            curses.reset_shell_mode()
        except curses.error:
            pass

    def draw(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, " minty packages ", w - 1, curses.A_BOLD | curses.A_REVERSE)
        info = f" pm: {self.pm['pm']} ({self.pm['kind']}) "
        stdscr.addnstr(0, w - 1 - len(info), info, len(info), curses.A_DIM)
        stdscr.addnstr(1, 0, f" search: {self.query if self.query else '(none)'} ", w - 1, curses.A_DIM)
        y = 3
        for idx, p in enumerate(self.results):
            if y >= h - 2:
                break
            if idx == self.sel:
                stdscr.addnstr(y, 0, " ", 1, curses.A_REVERSE)
            mark = "●" if p.get("installed") else " "
            attr = curses.A_REVERSE if idx == self.sel else 0
            line = f" {mark} {p['repo']}/{p['name']} {p['version']}"
            stdscr.addnstr(y, 0, line.ljust(w - 1), w - 1, attr)
            if idx == self.sel and p.get("desc"):
                stdscr.addnstr(y, 1, "", 1)
            y += 1
        if self.results and self.sel < len(self.results):
            desc = self.results[self.sel].get("desc", "")
            if desc:
                stdscr.addnstr(min(h - 3, y), 0, f" {desc[:w - 2]}", w - 1, curses.A_DIM)

        if self.msg:
            stdscr.addnstr(h - 1, 0, " " + self.msg, w - 1, curses.A_DIM)
        else:
            stdscr.addnstr(h - 1, 0,
                           " s search  Enter/space install  r remove  u update  o orphans  c clean  q quit ",
                           w - 1, curses.A_DIM)

    def run(self, stdscr):
        try:
            curses.curs_set(0)
            stdscr.keypad(True)
        except curses.error:
            pass
        if self.pm is None:
            stdscr.addstr(0, 0, "no supported package manager found")
            stdscr.refresh()
            stdscr.getch()
            return
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
            if key in (ord("s"), ord("S")):
                q = self._input(stdscr, "search: ")
                if q is not None:
                    self.query = q
                    self.do_search(stdscr, q)
            elif key in (10, 13, curses.KEY_ENTER, ord(" ")):
                self.do_install(stdscr)
            elif key in (ord("i"), ord("I")):
                self.do_install(stdscr)
            elif key in (ord("r"), ord("R")):
                self.do_remove(stdscr)
            elif key in (ord("u"), ord("U")):
                self.do_update(stdscr)
            elif key in (ord("o"), ord("O")):
                self.do_orphans()
            elif key in (ord("c"), ord("C")):
                self.do_clean(stdscr)
            elif key in (curses.KEY_DOWN, ord("j")):
                if self.results:
                    self.sel = (self.sel + 1) % len(self.results)
            elif key in (curses.KEY_UP, ord("k")):
                if self.results:
                    self.sel = (self.sel - 1) % len(self.results)


def run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = PkgApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
