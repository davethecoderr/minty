#!/usr/bin/env python3
"""minty network - manage connections and wifi from minty.

Uses nmcli (NetworkManager) to list connections, scan and join wifi
networks, and disconnect. Tabs switch between connections and wifi.
"""

import curses
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


def have_nmcli() -> bool:
    return shutil.which("nmcli") is not None


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


def connections() -> list[dict]:
    code, out = run_capture(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show"])
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        name, typ, dev, state = (line.split(":", 3) + ["", "", "", ""])[:4]
        rows.append({"name": name, "type": typ, "device": dev, "state": state})
    return rows


def wifi_networks() -> list[dict]:
    code, out = run_capture(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"])
    if code != 0:
        return []
    rows = []
    seen = set()
    for line in out.splitlines():
        ssid, signal, sec = (line.split(":", 2) + ["", "", ""])[:3]
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        rows.append({"ssid": ssid, "signal": signal, "security": sec})
    rows.sort(key=lambda r: int(r["signal"] or 0), reverse=True)
    return rows


def device_status() -> list[dict]:
    code, out = run_capture(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "device", "status"])
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        dev, state, conn = (line.split(":", 2) + ["", "", ""])[:3]
        rows.append({"device": dev, "state": state, "connection": conn})
    return rows


def _cli(argv):
    if len(argv) < 2 or argv[1] in ("app", "ui"):
        return 0 if run_app() else 1
    cmd, rest = argv[1], argv[2:]
    if cmd == "status":
        for d in device_status():
            mark = f"{GREEN}●{RESET}" if d["state"] == "connected" else f"{DIM}○{RESET}"
            print(f"{mark} {BOLD}{d['device']:<10}{RESET} {d['state']:<12} {d['connection']}")
        return 0
    if cmd == "wifi":
        if rest:
            return _connect(rest[0], rest[1] if len(rest) > 1 else None)
        for n in wifi_networks():
            print(f"{BOLD}{n['ssid']:<32}{RESET} {n['signal']:>3}%  {n['security']}")
        return 0
    if cmd == "connect" and rest:
        return _connect(rest[0], rest[1] if len(rest) > 1 else None)
    if cmd == "disconnect" and rest:
        return run_interactive(["nmcli", "connection", "down", rest[0]])
    if cmd == "rescan":
        return run_interactive(["nmcli", "device", "wifi", "rescan"])
    print("""usage:
  net                    open the visual network app
  net status             device status
  net wifi [ssid]        scan wifi (or connect to ssid)
  net connect <ssid> [password]
  net disconnect <name>
  net rescan             rescan for wifi networks""", file=sys.stderr)
    return 2


def _connect(ssid: str, password: str | None) -> int:
    argv = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        argv += ["password", password]
    print(f"{YELLOW}connecting to '{ssid}'...{RESET}")
    return run_interactive(argv)


class NetApp:
    def __init__(self):
        self.tab = "wifi"  # wifi | connections
        self.query = ""
        self.refresh()

    def refresh(self):
        self.wifi = wifi_networks()
        self.conns = connections()
        self.sel = 0

    def visible(self):
        if self.tab == "wifi":
            q = self.query.strip().lower()
            return [n for n in self.wifi if not q or q in n["ssid"].lower()]
        return self.conns

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

    def do_connect(self, stdscr):
        if self.tab != "wifi" or not self.visible():
            return
        n = self.visible()[self.sel]
        pw = ""
        if n["security"] and n["security"] not in ("", "--"):
            pw = self._input(stdscr, f"password for '{n['ssid']}': ") or ""
        if pw is None:
            return
        curses.endwin()
        print(f"\n{BOLD}minty: connecting to '{n['ssid']}'...{RESET}\n")
        run_interactive(["nmcli", "device", "wifi", "connect", n["ssid"]] +
                        (["password", pw] if pw else []))
        self._reenter()
        self.refresh()

    def do_disconnect(self, stdscr):
        items = self.visible()
        if not items:
            return
        target = items[self.sel]
        name = target.get("name") or target.get("ssid") or ""
        if self._confirm(stdscr, f"disconnect '{name}'? (y/N)"):
            code = run_interactive(["nmcli", "connection", "down", name])
            if code != 0:
                run_interactive(["nmcli", "device", "disconnect", target.get("device", "")])
            self.refresh()

    def do_rescan(self, stdscr):
        curses.endwin()
        print(f"\n{BOLD}minty: rescanning wifi...{RESET}\n")
        run_interactive(["nmcli", "device", "wifi", "rescan"])
        self._reenter()
        self.refresh()

    def draw(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, " minty network ", w - 1, curses.A_BOLD | curses.A_REVERSE)
        tabs = ["wifi", "connections"]
        tx = 0
        for t in tabs:
            attr = curses.A_REVERSE if self.tab == t else 0
            label = f" {t} "
            stdscr.addnstr(0, tx, label, len(label) + 2, attr)
            tx += len(label)
        stdscr.addnstr(1, 0, f" filter: {self.query or '(none)'}   "
                              f"{len(self.wifi)} networks   {len(self.conns)} connections ", w - 1, curses.A_DIM)
        items = self.visible()
        y = 3
        for idx, it in enumerate(items):
            if y >= h - 2:
                break
            attr = curses.A_REVERSE if idx == self.sel else 0
            if self.tab == "wifi":
                line = f" {it['ssid']:<30} {it['signal']:>3}%  {it['security']}"
            else:
                mark = "●" if "activ" in it["state"] else "○"
                line = f" {mark} {it['name']:<28} {it['type']:<14} {it['device']}  {it['state']}"
            stdscr.addnstr(y, 0, line.ljust(w - 1), w - 1, attr)
            y += 1
        hint = (" Enter/c connect  d disconnect  r rescan  Tab switch  "
                if self.tab == "wifi" else
                " Enter/c connect  d disconnect  Tab switch  ")
        stdscr.addnstr(h - 1, 0, hint + "type to filter  j/k move  q quit ",
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
            if key in (curses.KEY_TAB, ord("1"), ord("2")):
                self.tab = "connections" if self.tab == "wifi" else "wifi"
                self.sel = 0
            elif key in (10, 13, curses.KEY_ENTER, ord("c"), ord("C")):
                self.do_connect(stdscr)
            elif key in (ord("d"), ord("D")):
                self.do_disconnect(stdscr)
            elif key in (ord("r"), ord("R")):
                self.do_rescan(stdscr)
            elif key in (curses.KEY_DOWN, ord("j")):
                items = self.visible()
                if items:
                    self.sel = (self.sel + 1) % len(items)
            elif key in (curses.KEY_UP, ord("k")):
                items = self.visible()
                if items:
                    self.sel = (self.sel - 1) % len(items)
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
            elif 32 <= key < 127 and len(self.query) < 100:
                self.query += chr(key)


def run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = NetApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
