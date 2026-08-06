#!/usr/bin/env python3
"""minty history picker - a visual, fzf-style command history search.

Used by minty's Ctrl+R. You type to filter, pick with j/k and run with Enter.
"""

import curses
import sys


def run_history_picker(items: list[str], initial: str = "") -> str | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    app = _Picker(items, initial)
    try:
        curses.wrapper(app.run)
        return app.result
    except Exception:
        return None


class _Picker:
    def __init__(self, items, initial=""):
        self.items = [i for i in items if i]
        self.query = initial
        self.sel = 0
        self.result = None

    def matches(self):
        q = self.query.strip().lower()
        if not q:
            return self.items
        return [i for i in self.items if q in i.lower()]

    def run(self, stdscr):
        try:
            curses.curs_set(0)
            stdscr.keypad(True)
        except curses.error:
            pass
        while True:
            h, w = stdscr.getmaxyx()
            matches = self.matches()
            if matches:
                self.sel = max(0, min(self.sel, len(matches) - 1))
            stdscr.erase()
            stdscr.addnstr(0, 0, " minty history ", w - 1, curses.A_BOLD | curses.A_REVERSE)
            stdscr.addnstr(1, 0, ("filter: " + self.query)[: w - 1], w - 1, curses.A_DIM)
            y = 3
            for idx, item in enumerate(matches):
                if y >= h - 1:
                    break
                attr = curses.A_REVERSE if idx == self.sel else 0
                stdscr.addnstr(y, 0, item[: w - 1].ljust(w - 1), w - 1, attr)
                y += 1
            footer = f"{len(matches)} match(es)   type to filter   j/k move   Enter run   ESC close"
            stdscr.addnstr(h - 1, 0, footer[: w - 1], w - 1, curses.A_DIM)
            stdscr.refresh()
            try:
                key = stdscr.getch()
            except curses.error:
                key = -1
            if key == 27:
                return
            if key in (10, 13, curses.KEY_ENTER):
                if matches:
                    self.result = matches[self.sel]
                return
            if key in (curses.KEY_DOWN, ord("j")):
                if matches:
                    self.sel = (self.sel + 1) % len(matches)
            elif key in (curses.KEY_UP, ord("k")):
                if matches:
                    self.sel = (self.sel - 1) % len(matches)
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
            elif key == curses.KEY_PPAGE and matches:
                self.sel = 0
            elif key == curses.KEY_NPAGE and matches:
                self.sel = len(matches) - 1
            elif 32 <= key < 127 and len(self.query) < 200:
                self.query += chr(key)


if __name__ == "__main__":
    items = sys.argv[1:] or ["echo hello", "pkg search minty", "theme list", "exit"]
    picked = run_history_picker(items)
    if picked is not None:
        print(f"picked: {picked}")
