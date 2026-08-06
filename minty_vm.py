#!/usr/bin/env python3
"""minty vms - create, start and manage virtual machines for minty.

Backed by libvirt/QEMU (virsh + virt-install). If the stack is missing,
minty offers to install qemu-full, libvirt and virt-install with your
package manager and enable libvirtd.
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

STACK = ["qemu-full", "libvirt", "virt-install"]


def have_virsh() -> bool:
    return shutil.which("virsh") is not None


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


def vms() -> list[dict]:
    if not have_virsh():
        return []
    code, out = run_capture(["virsh", "list", "--all"])
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        m = re.match(r"^\s*(\S+)\s+(\S+)\s+(.+)$", line)
        if m:
            rows.append({"id": m.group(1), "name": m.group(2), "state": m.group(3).strip()})
    return rows


def _cli(argv):
    if len(argv) < 2 or argv[1] in ("app", "ui"):
        return 0 if run_app() else 1
    cmd, rest = argv[1], argv[2:]
    if cmd == "install":
        return _install_stack()
    if not have_virsh():
        print("libvirt is not installed. try: vms install", file=sys.stderr)
        return 1
    if cmd == "list":
        for v in vms():
            mark = f"{GREEN}●{RESET}" if v["state"] == "running" else f"{DIM}○{RESET}"
            print(f"{mark} {BOLD}{v['name']}{RESET}  {v['state']}  {DIM}(id {v['id']}){RESET}")
        return 0
    if cmd in ("start", "stop", "reboot", "destroy") and rest:
        op = {"start": "start", "stop": "shutdown", "reboot": "reboot", "destroy": "destroy"}[cmd]
        return run_interactive(["virsh", op, rest[0]])
    if cmd in ("delete", "undefine") and rest:
        argv = ["virsh", "undefine", rest[0]]
        if "--storage" in rest:
            argv.append("--remove-all-storage")
        return run_interactive(argv)
    if cmd == "create":
        return _create_wizard()
    print("""usage:
  vms                    open the visual VM manager
  vms list               list VMs
  vms start <name>       start a VM
  vms stop <name>        graceful shutdown
  vms reboot <name>      reboot a VM
  vms destroy <name>     force stop
  vms delete <name>      delete (undefine)
  vms create             create a new VM (wizard)
  vms install            install the qemu/libvirt stack""", file=sys.stderr)
    return 2


def _install_stack() -> int:
    print(f"{YELLOW}installing qemu/libvirt: {' '.join(STACK)}{RESET}")
    code = run_interactive([sys.executable, PKG_PY, "install"] + STACK)
    if code != 0:
        print("install failed - try it manually with: pkg install qemu-full libvirt virt-install", file=sys.stderr)
        return code
    print(f"{YELLOW}enabling libvirtd service...{RESET}")
    code = run_interactive(["sudo", "systemctl", "enable", "--now", "libvirtd"])
    if code != 0:
        print("could not enable libvirtd. after the service is running, add yourself to the libvirt group:\n"
              "  sudo usermod -aG libvirt $USER  (then log out and back in)", file=sys.stderr)
    return code


def _create_wizard() -> int:
    name = _ask("VM name: ")
    if not name:
        return 1
    ram = _ask("RAM in MB [2048]: ") or "2048"
    vcpus = _ask("vCPUs [2]: ") or "2"
    disk = _ask("disk size in GB [20]: ") or "20"
    osvar = _ask("os-variant [generic] (list with: osinfo-query os): ") or "generic"
    iso = _ask("install ISO path (empty = blank disk): ") or ""
    argv = [
        "virt-install",
        "--name", name,
        "--ram", ram,
        "--vcpus", vcpus,
        "--disk", f"size={disk}",
        "--os-variant", osvar,
        "--network", "network=default",
        "--graphics", "spice",
        "--noautoconsole",
    ]
    if iso:
        argv += ["--cdrom", iso]
    else:
        argv += ["--import"]
    print(f"{BOLD}creating VM '{name}'...{RESET}\n")
    return run_interactive(argv)


def _ask(prompt_text: str) -> str:
    try:
        return input(prompt_text).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return ""


class VmApp:
    def __init__(self):
        self.missing = not have_virsh()
        self.refresh()

    def refresh(self):
        self.vms = vms()
        self.sel = 0
        if self.vms:
            self.sel = min(self.sel, len(self.vms) - 1)

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

    def _op(self, name, op, label):
        curses.endwin()
        print(f"\n{BOLD}minty: {label} '{name}'...{RESET}\n")
        code = run_interactive(["virsh", op, name])
        if code != 0:
            print(f"{RED}{label} failed (rc {code}).{RESET}\n")
        self._reenter()
        self.refresh()

    def do_start(self, stdscr):
        if self.vms:
            self._op(self.vms[self.sel]["name"], "start", "starting")

    def do_stop(self, stdscr):
        if self.vms:
            self._op(self.vms[self.sel]["name"], "shutdown", "shutting down")

    def do_reboot(self, stdscr):
        if self.vms:
            self._op(self.vms[self.sel]["name"], "reboot", "rebooting")

    def do_destroy(self, stdscr):
        if self.vms and self._confirm(stdscr, f"force stop '{self.vms[self.sel]['name']}'? (y/N)"):
            self._op(self.vms[self.sel]["name"], "destroy", "force stopping")

    def do_delete(self, stdscr):
        if not self.vms:
            return
        name = self.vms[self.sel]["name"]
        if self._confirm(stdscr, f"delete (undefine) '{name}'? (y/N)"):
            if self._confirm(stdscr, "also remove its disk storage? (y/N)"):
                run_interactive(["virsh", "undefine", name, "--remove-all-storage"])
            else:
                run_interactive(["virsh", "undefine", name])
            self.refresh()

    def do_create(self, stdscr):
        curses.endwin()
        print(f"\n{BOLD}creating a new VM...{RESET}\n")
        _create_wizard()
        self._reenter()
        self.refresh()

    def do_install(self, stdscr):
        if self._confirm(stdscr, "qemu/libvirt is not installed. install it now? (y/N)"):
            curses.endwin()
            print()
            _install_stack()
            self._reenter()
            self.missing = not have_virsh()
            self.refresh()

    def draw(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, " minty virtual machines ", w - 1, curses.A_BOLD | curses.A_REVERSE)
        if self.missing:
            stdscr.addnstr(1, 0, " qemu/libvirt is not installed - press i to install the stack ", w - 1, curses.A_DIM)
            return
        stdscr.addnstr(1, 0, f" {len(self.vms)} VM(s)   host: {os.uname().nodename} ", w - 1, curses.A_DIM)
        y = 3
        for idx, v in enumerate(self.vms):
            if y >= h - 2:
                break
            running = v["state"] == "running"
            mark = "●" if running else "○"
            attr = curses.A_REVERSE if idx == self.sel else 0
            line = f" {mark} {v['name']:<24} {v['state']}"
            stdscr.addnstr(y, 0, line.ljust(w - 1), w - 1, attr)
            y += 1
        stdscr.addnstr(h - 1, 0,
                       " Enter start   s shutdown   r reboot   d force-stop   x delete   n create   j/k   q quit ",
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
            if key in (10, 13, curses.KEY_ENTER, ord(" ")):
                self.do_start(stdscr)
            elif key in (ord("s"), ord("S")):
                self.do_stop(stdscr)
            elif key in (ord("r"), ord("R")):
                self.do_reboot(stdscr)
            elif key in (ord("d"), ord("D")):
                self.do_destroy(stdscr)
            elif key in (ord("x"), ord("X")):
                self.do_delete(stdscr)
            elif key in (ord("n"), ord("N")):
                self.do_create(stdscr)
            elif key in (curses.KEY_DOWN, ord("j")):
                if self.vms:
                    self.sel = (self.sel + 1) % len(self.vms)
            elif key in (curses.KEY_UP, ord("k")):
                if self.vms:
                    self.sel = (self.sel - 1) % len(self.vms)


def run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = VmApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
