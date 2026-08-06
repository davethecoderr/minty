#!/usr/bin/env python3
"""Side menu for minty - a panel that opens on the right edge of the terminal."""

import curses
import sys

OUTFILE = sys.argv[1] if len(sys.argv) > 1 else None

ITEMS = [
    ("OpenCode AI", "opencode", "Launch the OpenCode AI assistant."),
    ("Rerun last command", "rerun", "Rerun the last command you typed (!!)."),
    ("Themes", "themes", "Browse, apply, edit and share minty themes."),
    ("Packages", "packages", "Search, install and remove packages (pacman/yay/paru)."),
    ("Update system", "system_update", "Full system update with your package manager."),
    ("Update minty", "update", "Update minty from a local path or github repo."),
    ("Install/update opencode", "install_opencode", "Download or update the bundled OpenCode AI."),
    ("System info", "fastfetch", "Show system info with fastfetch."),
    ("Command history", "hist", "Browse command history (like Ctrl+R)."),
    ("Recent directories", "cdr", "Jump to a recently-visited directory."),
    ("tmux sessions", "tmux", "Create, attach to and kill tmux sessions."),
    ("Virtual machines", "vms", "Create, start and manage virtual machines."),
    ("Services", "svc", "Start, stop, enable and manage systemd services."),
    ("Processes", "proc", "Kill processes or open btop/htop."),
    ("Network", "net", "Join wifi and manage connections."),
    ("Edit minty config", "config", "Open minty's persistent config in your editor."),
    ("minty version", "version", "Show which version of minty is running."),
    ("Clear screen", "clear", "Clear the terminal."),
    ("Show help", "help", "List every minty command."),
    ("Open fish shell", "fish", "Exit minty into your real shell."),
    ("Close menu", "close", "Close this menu."),
]

FOOTER = "j/k or arrows to move   Enter to select   q/ESC to close"


def choose(token):
    if OUTFILE:
        try:
            with open(OUTFILE, "w") as f:
                f.write(token + "\n")
        except OSError:
            pass
    else:
        print(token, flush=True)


def wrap(text, width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def main(stdscr):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    try:
        stdscr.keypad(True)
    except curses.error:
        pass
    selected = 0
    while True:
        height, width = stdscr.getmaxyx()
        panel_w = min(34, max(22, width // 3))
        left = width - panel_w
        if left < 12:
            left = 0
        stdscr.erase()

        if left > 0:
            desc = wrap(ITEMS[selected][2], left - 2)
            y = max(0, (height - len(desc)) // 2)
            for line in desc:
                if 0 <= y < height:
                    try:
                        stdscr.addnstr(y, (left - len(line)) // 2, line, left - 1)
                    except curses.error:
                        pass
                    y += 1

        pw = width - left
        if pw > 1:
            try:
                stdscr.addnstr(0, left, " minty menu ", pw - 1, curses.A_BOLD)
            except curses.error:
                pass
            y = 2
            for idx, (label, _, _) in enumerate(ITEMS):
                if y >= height - 1:
                    break
                attr = curses.A_REVERSE if idx == selected else curses.A_NORMAL
                try:
                    stdscr.addnstr(y, left + 1, f" {label} ", pw - 3, attr)
                except curses.error:
                    pass
                y += 1
            try:
                stdscr.addnstr(height - 1, left + 1, "q: close", pw - 3, curses.A_DIM)
            except curses.error:
                pass

        stdscr.refresh()

        try:
            key = stdscr.getch()
        except curses.error:
            key = -1
        if key in (ord("q"), ord("Q"), 27, curses.KEY_LEFT):
            choose("close")
            return
        if key in (curses.KEY_UP, ord("k")):
            selected = (selected - 1) % len(ITEMS)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = (selected + 1) % len(ITEMS)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            choose(ITEMS[selected][1])
            return
        elif ord("1") <= key <= ord(str(len(ITEMS))):
            choose(ITEMS[int(chr(key)) - 1][1])
            return


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except Exception:
        choose("close")
