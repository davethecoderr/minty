#!/usr/bin/env python3
"""minty - a tiny shell that runs in your real terminal.

Single file, everything built in: the shell, visual themes, a package
manager, a tmux session manager, a VM manager, a systemd service manager,
a process manager, a network/wifi manager, an fzf-style history picker and
its own GTK3/VTE terminal emulator ('minty terminal').
Install with install.sh or run: python3 minty.py
"""

import atexit
import curses
import datetime
import difflib
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

try:
    import readline
except ImportError:
    readline = None

def _c(code: str) -> str:
    return f"\033[{code}m" if sys.stdout.isatty() else ""


RESET = _c("0")
BOLD = _c("1")
DIM = _c("2")
RED = _c("31")
GREEN = _c("32")
YELLOW = _c("33")
BLUE = _c("34")
MAGENTA = _c("35")
CYAN = _c("36")
B_RED = _c("1;31")
B_GREEN = _c("1;32")
B_MAGENTA = _c("1;35")
B_CYAN = _c("1;36")

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

# ---- themes/config (minty_theme.py) ----

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "minty")
THEME_DIR = os.path.join(CONFIG_DIR, "themes")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
THEME_VERSION = 1
# Default palette (matches the classic minty look).
DEFAULT_COLORS = {
    "reset": "0",
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "bright_red": "1;31",
    "bright_green": "1;32",
    "bright_magenta": "1;35",
    "bright_cyan": "1;36",
}

DEFAULT_PROMPT = {
    "user": "green",
    "host": "blue",
    "dir": "cyan",
    "branch": "magenta",
    "mark": "yellow",
    "error": "bright_red",
    "separator": "@",
    "venv": "bright_magenta",
    "dirty": "bright_yellow",
    "ahead": "bright_green",
    "behind": "bright_red",
    "duration": "bright_cyan",
    "time": "bright_cyan",
    "show_git": True,
    "show_status": True,
    "show_time": False,
}

# Colors the visual editor can tweak (reset/bold/dim stay as they are).
EDITABLE_COLORS = [
    "red", "green", "yellow", "blue", "magenta", "cyan",
    "bright_red", "bright_green", "bright_magenta", "bright_cyan",
]

PROMPT_ROLE_ORDER = [
    ("user", "color"), ("host", "color"), ("dir", "color"),
    ("branch", "color"), ("mark", "color"), ("error", "color"),
    ("venv", "color"), ("duration", "color"),
    ("separator", "text"),
    ("show_git", "bool"), ("show_status", "bool"), ("show_time", "bool"),
]

DEFAULT_SETTINGS = {
    "show_banner": True,
    "show_hint": True,
}

DEFAULT_THEME = {
    "name": "default",
    "version": THEME_VERSION,
    "description": "The classic minty look.",
    "author": "",
    "colors": dict(DEFAULT_COLORS),
    "prompt": dict(DEFAULT_PROMPT),
    "aliases": {},
    "settings": dict(DEFAULT_SETTINGS),
}

# Standard ANSI color -> RGB, so legacy codes can be edited as colors.
ANSI_TO_RGB = {
    30: (0, 0, 0), 31: (205, 49, 49), 32: (13, 188, 121), 33: (229, 229, 16),
    34: (36, 114, 200), 35: (188, 63, 188), 36: (17, 168, 205), 37: (229, 229, 229),
    90: (128, 128, 128), 91: (255, 85, 85), 92: (80, 250, 123), 93: (255, 241, 224),
    94: (120, 172, 255), 95: (255, 133, 254), 96: (0, 229, 255), 97: (255, 255, 255),
}


def _esc(code: str) -> str:
    return f"\033[{code}m"


class Theme:
    def __init__(self, data):
        merged = json.loads(json.dumps(DEFAULT_THEME))
        merged.update({k: v for k, v in data.items() if k != "name"})
        merged["name"] = str(data.get("name", "default")).strip() or "default"
        self.data = merged

    @property
    def name(self):
        return self.data["name"]

    @property
    def colors(self):
        return self.data["colors"]

    @property
    def prompt(self):
        return self.data["prompt"]

    @property
    def aliases(self):
        return self.data["aliases"]

    @property
    def settings(self):
        return self.data["settings"]

    def c(self, key: str) -> str:
        """Return the wrapped SGR escape for a named color."""
        val = self.colors.get(key, DEFAULT_COLORS.get(key, "0"))
        return _esc(self.resolve(val))

    def resolve(self, val: str) -> str:
        """Turn a theme color value into an SGR code. Supports '#rrggbb' hex."""
        val = str(val).strip()
        if val.startswith("#"):
            try:
                r = int(val[1:3], 16)
                g = int(val[3:5], 16)
                b = int(val[5:7], 16)
                return f"38;2;{r};{g};{b}"
            except ValueError:
                return "0"
        return val or "0"

    def prompt_role(self, role: str) -> str:
        """Color key a prompt role uses."""
        key = self.prompt.get(role, DEFAULT_PROMPT.get(role, "green"))
        if key not in self.colors:
            return "green"
        return key

    def to_dict(self):
        return self.data

    def save(self, path=None):
        path = path or os.path.join(THEME_DIR, f"{self.name}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.data, f, indent=2)
            f.write("\n")
        return path


def load_theme(name: str) -> Theme:
    data = _read_json(os.path.join(THEME_DIR, f"{name}.json"))
    if data is None:
        data = dict(DEFAULT_THEME)
        data["name"] = name
    return Theme(data)


def list_themes() -> list[str]:
    os.makedirs(THEME_DIR, exist_ok=True)
    names = []
    for fn in sorted(os.listdir(THEME_DIR)):
        if fn.endswith(".json"):
            names.append(fn[:-5])
    if not names:
        save_theme(load_theme("default"))
        names = ["default"]
    return names


def save_theme(theme: Theme) -> str:
    return theme.save()


def delete_theme(name: str) -> bool:
    if name == "default":
        return False
    try:
        os.remove(os.path.join(THEME_DIR, f"{name}.json"))
        return True
    except OSError:
        return False


def _read_json(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def get_config() -> dict:
    cfg = _read_json(CONFIG_FILE)
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("active_theme", "default")
    cfg.setdefault("update", {"type": "", "source": ""})
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def active_theme() -> Theme:
    return load_theme(get_config().get("active_theme", "default"))


def set_active(name: str) -> bool:
    if name not in list_themes():
        return False
    cfg = get_config()
    cfg["active_theme"] = name
    save_config(cfg)
    return True


# --------------------------------------------------------------------------
# Color math for the visual preview
# --------------------------------------------------------------------------

def color_rgb(value: str):
    """Return (r, g, b) for a theme color value."""
    value = str(value).strip()
    if value.startswith("#"):
        try:
            return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
        except ValueError:
            return (255, 255, 255)
    try:
        base = int(value.split(";")[-1])
    except (ValueError, AttributeError):
        return (255, 255, 255)
    if 30 <= base <= 37 or 90 <= base <= 97:
        return ANSI_TO_RGB.get(base, (255, 255, 255))
    return (255, 255, 255)


def _cube_step(v: int) -> int:
    if v < 48:
        return 0
    if v < 115:
        return 1
    return min(5, (v - 35) // 40)


def rgb_to_256(r: int, g: int, b: int) -> int:
    r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
    if r == g == b:
        if r <= 8:
            return 16
        if r >= 248:
            return 231
        return 232 + int((r - 8) / 10)
    return 16 + 36 * _cube_step(r) + 6 * _cube_step(g) + _cube_step(b)


def _luminance(rgb) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0


def hex_from_rgb(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, v)) for v in rgb)


# --------------------------------------------------------------------------
# Preview rendering (used by the curses app)
# --------------------------------------------------------------------------

class Preview:
    """Renders a theme as curses color-pair segments."""

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.pairs = {}
        self.next_pair = 1
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()

    def _pair(self, fg_rgb=None, bg_rgb=None):
        if not curses.has_colors():
            return 0
        fg = rgb_to_256(*fg_rgb) if fg_rgb else -1
        bg = rgb_to_256(*bg_rgb) if bg_rgb else -1
        key = (fg, bg)
        if key not in self.pairs:
            if self.next_pair >= curses.COLOR_PAIRS:
                self.pairs[key] = 0
            else:
                p = self.next_pair
                self.next_pair += 1
                try:
                    curses.init_pair(p, fg, bg)
                    self.pairs[key] = p
                except curses.error:
                    self.pairs[key] = 0
        return self.pairs[key]

    def fg(self, color_value):
        return curses.color_pair(self._pair(color_rgb(color_value)))

    def swatch(self, color_value):
        rgb = color_rgb(color_value)
        text_color = rgb_to_256(255, 255, 255) if _luminance(rgb) < 0.5 else rgb_to_256(0, 0, 0)
        pair = self._pair((text_color, text_color, text_color), bg_rgb=rgb)
        return curses.color_pair(pair) | curses.A_REVERSE

    def seg(self, text, color_value, attr=0):
        return (text, self.fg(color_value) | attr)

    def plain(self, text):
        return (text, 0)


# --------------------------------------------------------------------------
# The curses visual app
# --------------------------------------------------------------------------

def _input_line(stdscr, prompt_text, initial="", y=None, x=None):
    """Blocking one-line text input; returns string or None on ESC."""
    h, w = stdscr.getmaxyx()
    if y is None:
        y = h - 1
    if x is None:
        x = 0
    buf = list(initial)
    while True:
        stdscr.move(y, x)
        line = (prompt_text + "".join(buf)).ljust(w - x - 1)[:w - x - 1]
        stdscr.addnstr(y, x, line, w - x - 1, curses.A_REVERSE)
        stdscr.move(y, x + len(prompt_text) + len(buf))
        stdscr.refresh()
        key = stdscr.getch()
        if key in (10, 13, curses.KEY_ENTER):
            return "".join(buf).strip()
        if key in (27, ord("q")):
            return None
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if buf:
                buf.pop()
        elif key == curses.KEY_DC and buf:
            buf.pop(0)
        elif 32 <= key < 127 and len("".join(buf)) < w - x - len(prompt_text) - 1:
            buf.append(chr(key))


def _confirm(stdscr, message):
    """Yes/no prompt; returns bool."""
    y = max(0, stdscr.getmaxyx()[0] - 1)
    stdscr.addnstr(y, 0, message + " (y/N) ", stdscr.getmaxyx()[1] - 1, curses.A_REVERSE)
    stdscr.refresh()
    key = stdscr.getch()
    return key in (ord("y"), ord("Y"))


class ThemeApp:
    def __init__(self):
        self.names = list_themes()
        self.sel = 0
        active = get_config().get("active_theme", "default")
        if active in self.names:
            self.sel = self.names.index(active)
        self.mode = "browse"      # browse | edit
        self.tab = 0              # 0 colors, 1 prompt, 2 aliases
        self.row = 0
        self.component = 0        # 0=R 1=G 2=B
        self.theme = None         # Theme being edited
        self.dirty = False
        self.applied = None       # theme applied this session
        self.import_line = False
        self.new_name_line = None
        self.msg = ""
        self.pv = None

    # ---- small helpers ----
    def set_msg(self, m):
        self.msg = m

    def apply_sel(self):
        if set_active(self.names[self.sel]):
            self.applied = self.names[self.sel]
            self.set_msg(f"applied '{self.names[self.sel]}' - restart minty for full effect")

    def start_edit(self):
        self.theme = load_theme(self.names[self.sel])
        self.mode = "edit"
        self.tab = 0
        self.row = 0
        self.component = 0
        self.dirty = False
        self.set_msg(f"editing '{self.theme.name}'  (s: save   o: raw file   q: back)")

    def save_edit(self):
        self.theme.save()
        self.dirty = False
        self.set_msg(f"saved '{self.theme.name}'")

    def ensure_default(self):
        if "default" not in self.names:
            save_theme(load_theme("default"))
            self.names = list_themes()

    def new_theme(self, stdscr):
        cur = self.theme or load_theme(self.names[self.sel])
        data = json.loads(json.dumps(cur.to_dict()))
        data["name"] = ""
        data["description"] = "Created in the minty theme app."
        name = _input_line(stdscr, "new theme name: ")
        if not name:
            return
        if name in self.names and not _confirm(stdscr, f"overwrite '{name}'? (y/N)"):
            return
        theme = Theme(data)
        theme.data["name"] = name
        theme.save()
        self.names = list_themes()
        self.sel = self.names.index(name)
        self.set_msg(f"created '{name}' - press e to edit its colors")

    def do_import(self, stdscr):
        path = _input_line(stdscr, "theme file path: ")
        if not path:
            return
        path = os.path.expanduser(path)
        try:
            with open(path) as f:
                data = json.load(f)
            theme = Theme(data)
            if not theme.name or theme.name == "default":
                theme.data["name"] = os.path.splitext(os.path.basename(path))[0]
            if theme.name in self.names:
                self.set_msg(f"'{theme.name}' already exists - edit it, don't import")
                return
            theme.save()
            self.names = list_themes()
            self.sel = self.names.index(theme.name)
            self.set_msg(f"imported '{theme.name}' from {path}")
        except (OSError, ValueError) as e:
            self.set_msg(f"import failed: {e}")

    def do_export(self):
        theme = self.theme or load_theme(self.names[self.sel])
        out = os.path.expanduser(f"~/{theme.name}.mintytheme.json")
        try:
            with open(out, "w") as f:
                json.dump(theme.to_dict(), f, indent=2)
                f.write("\n")
            self.set_msg(f"exported to {out} (share this file)")
        except OSError as e:
            self.set_msg(f"export failed: {e}")

    def delete_sel(self, stdscr):
        name = self.names[self.sel]
        if name == "default":
            self.set_msg("can't delete the default theme")
            return
        if _confirm(stdscr, f"delete '{name}'? (y/N)"):
            delete_theme(name)
            self.names = list_themes()
            self.sel = min(self.sel, len(self.names) - 1)
            self.set_msg(f"deleted '{name}'")

    # ---- edit mode actions ----
    def cur_color_key(self):
        return EDITABLE_COLORS[min(self.row, len(EDITABLE_COLORS) - 1)]

    def color_value(self, key):
        return self.theme.colors.get(key, DEFAULT_COLORS.get(key, "0"))

    def nudge_color(self, delta):
        key = self.cur_color_key()
        rgb = list(color_rgb(self.color_value(key)))
        rgb[self.component] = max(0, min(255, rgb[self.component] + delta))
        self.theme.colors[key] = hex_from_rgb(rgb)
        self.dirty = True

    def cycle_prompt_color(self):
        role = [r for r, _ in PROMPT_ROLE_ORDER if r not in ("show_git", "show_status")]
        role = role[min(self.row, len(role) - 1)]
        keys = EDITABLE_COLORS
        cur = self.theme.prompt_role(role)
        nxt = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else keys[0]
        self.theme.prompt[role] = nxt
        self.dirty = True

    def toggle_prompt_bool(self):
        role = [r for r, _ in PROMPT_ROLE_ORDER][min(self.row, len(PROMPT_ROLE_ORDER) - 1)]
        if role in ("show_git", "show_status"):
            self.theme.prompt[role] = not self.theme.prompt.get(role, True)
            self.dirty = True

    def alias_list(self):
        return sorted(self.theme.aliases.items())

    def add_alias(self, stdscr):
        raw = _input_line(stdscr, "alias (name=value): ")
        if not raw:
            return
        if "=" in raw:
            k, v = raw.split("=", 1)
            self.theme.aliases[k.strip()] = v.strip()
            self.dirty = True
            self.set_msg(f"added alias {k.strip()}")
        else:
            self.set_msg("usage: name=value")

    def delete_alias(self):
        items = self.alias_list()
        if not items:
            return
        key = items[min(self.row, len(items) - 1)][0]
        del self.theme.aliases[key]
        self.dirty = True

    # ---- drawing ----
    def _draw_browse(self, stdscr):
        h, w = stdscr.getmaxyx()
        list_w = min(34, max(24, w // 4))
        stdscr.addnstr(0, 0, " minty themes ", w - 1, curses.A_BOLD | curses.A_REVERSE)
        stdscr.addnstr(0, list_w, " PREVIEW ", w - list_w - 1, curses.A_BOLD | curses.A_REVERSE)
        y = 1
        for idx, name in enumerate(self.names):
            if y >= h - 1:
                break
            mark = "> " if idx == self.sel else "  "
            cur = "●" if name == get_config().get("active_theme", "default") else " "
            attr = curses.A_REVERSE if idx == self.sel else 0
            label = f"{mark}{cur} {name}"
            stdscr.addnstr(y, 0, label.ljust(list_w), list_w, attr)
            y += 1

        if self.msg:
            stdscr.addnstr(h - 1, 0, " " + self.msg, w - 1, curses.A_DIM)
        else:
            stdscr.addnstr(
                h - 1, 0,
                " j/k move  Enter apply  e edit  n new  d delete  i import  x export  q quit ",
                w - 1, curses.A_DIM)

        if not self.names:
            return
        theme = load_theme(self.names[self.sel])
        self._draw_preview(stdscr, theme, list_w)

    def _draw_preview(self, stdscr, theme, left):
        h, w = stdscr.getmaxyx()
        if left + 2 >= w:
            return
        pv = Preview(stdscr)
        x = left + 1
        y = 1
        y += self._emit_at(stdscr, pv, y, x, [(" ", 0)])
        desc = theme.data.get("description", "")
        author = theme.data.get("author", "")
        if desc:
            y += self._emit_at(stdscr, pv, y, x, [(desc, curses.A_DIM)])
        if author:
            y += self._emit_at(stdscr, pv, y, x, [(f"by {author}", curses.A_DIM)])
        y += self._emit_at(stdscr, pv, y, x, [("", 0)])

        for key in EDITABLE_COLORS:
            if y >= h - 2:
                break
            val = theme.colors.get(key, DEFAULT_COLORS.get(key, "0"))
            sw = pv.swatch(val)
            segs = [("  ", sw), (f" {key:<14}", pv.fg(val)), (f"{str(val):<14}", curses.A_DIM)]
            y += self._emit_at(stdscr, pv, y, x, segs)

        if y < h - 2:
            y += self._emit_at(stdscr, pv, y, x, [("prompt:", curses.A_BOLD)])
        for segs in self._prompt_segments(theme, pv):
            if y >= h - 2:
                break
            y += self._emit_at(stdscr, pv, y, x, segs)

    def _prompt_segments(self, theme, pv):
        prompt = theme.prompt
        sep = prompt.get("separator", "@")
        segs = []
        if prompt.get("show_status", True):
            segs.append(pv.seg("✗ ", theme.prompt_role("error")))
        segs.append(pv.seg("(venv) ", theme.prompt_role("venv")))
        segs.append(pv.seg("user", theme.prompt_role("user")))
        segs.append(pv.plain(sep))
        segs.append(pv.seg("host", theme.prompt_role("host")))
        segs.append(pv.plain(":"))
        segs.append(pv.seg("~/proj", theme.prompt_role("dir")))
        if prompt.get("show_git", True):
            segs.append(pv.plain(" "))
            segs.append(pv.seg("❮main", theme.prompt_role("branch")))
            segs.append(pv.seg("•", theme.prompt_role("dirty")))
            segs.append(pv.seg("↑1", theme.prompt_role("ahead")))
            segs.append(pv.seg("↓2", theme.prompt_role("behind")))
            segs.append(pv.seg("❯", theme.prompt_role("branch")))
        if prompt.get("show_time", False):
            segs.append(pv.plain(" "))
            segs.append(pv.seg("14:30", theme.prompt_role("time")))
        segs.append(pv.plain(" "))
        segs.append(pv.seg("2.4s", theme.prompt_role("duration")))
        segs.append(pv.plain(" "))
        segs.append(pv.seg("$", theme.prompt_role("mark")))
        segs.append(pv.plain(" "))
        return [segs]

    def _draw_edit(self, stdscr):
        h, w = stdscr.getmaxyx()
        header = f" editing '{self.theme.name}' "
        stdscr.addnstr(0, 0, header.ljust(w - 1), w - 1, curses.A_BOLD | curses.A_REVERSE)
        tabs = ["Colors", "Prompt", "Aliases"]
        tx = 0
        for i, t in enumerate(tabs):
            attr = curses.A_REVERSE if i == self.tab else 0
            label = f" {t} "
            stdscr.addnstr(0, tx, label, len(label) + 2, attr)
            tx += len(label)
        stdscr.addnstr(0, tx, "".ljust(w - 1), w - 1, curses.A_DIM)

        if self.tab == 0:
            self._draw_edit_colors(stdscr)
        elif self.tab == 1:
            self._draw_edit_prompt(stdscr)
        else:
            self._draw_edit_aliases(stdscr)

        if self.msg:
            stdscr.addnstr(h - 1, 0, " " + self.msg, w - 1, curses.A_DIM)
        else:
            stdscr.addnstr(h - 1, 0,
                " j/k move  h/l -1/+1  H/L -16/+16  r/g/b channel  Tab switch  s save  o raw  q back ",
                w - 1, curses.A_DIM)

    def _draw_edit_colors(self, stdscr):
        h, w = stdscr.getmaxyx()
        pv = Preview(stdscr)
        y = 2
        for idx, key in enumerate(EDITABLE_COLORS):
            if y >= h - 1:
                break
            val = self.theme.colors.get(key, DEFAULT_COLORS.get(key, "0"))
            rgb = color_rgb(val)
            label = key
            if idx == self.row:
                label += f"  [{'RGB'[self.component]}]"
            sw = pv.swatch(val)
            y += self._emit_at(stdscr, pv, y, 2, [
                ("> " if idx == self.row else "  ", 0),
                ("  ", sw),
                (f" {label:<18}", pv.fg(val) | (curses.A_REVERSE if idx == self.row else 0)),
                (f"{str(val):<9}", curses.A_DIM if idx != self.row else 0),
                (f"rgb{tuple(rgb)}", curses.A_DIM),
            ])

    def _draw_edit_prompt(self, stdscr):
        h, w = stdscr.getmaxyx()
        pv = Preview(stdscr)
        y = 2
        roles = PROMPT_ROLE_ORDER
        for idx, (role, kind) in enumerate(roles):
            if y >= h - 1:
                break
            val = self.theme.prompt.get(role, DEFAULT_PROMPT.get(role, ""))
            if kind == "color":
                colkey = val if val in EDITABLE_COLORS else "green"
                sw = pv.swatch(self.theme.colors.get(colkey, "0"))
                segs = [
                    ("> " if idx == self.row else "  ", 0),
                    ("  ", sw),
                    (f" {role:<12}", pv.fg(self.theme.colors.get(colkey, "0"))),
                    (f"color: {colkey}", curses.A_DIM),
                ]
            elif kind == "text":
                segs = [
                    ("> " if idx == self.row else "  ", 0),
                    (f" {role:<12}", 0),
                    (f"text: '{val}'  (edit in the raw file)", curses.A_DIM),
                ]
            else:
                segs = [
                    ("> " if idx == self.row else "  ", 0),
                    (f" {role:<12}", 0),
                    (f"{str(val)}  (Enter toggles)", curses.A_BOLD if idx == self.row else 0),
                ]
            y += self._emit_at(stdscr, pv, y, 2, segs)

    def _draw_edit_aliases(self, stdscr):
        h, w = stdscr.getmaxyx()
        y = 2
        items = self.alias_list()
        if not items:
            stdscr.addnstr(y, 2, " no aliases  (a adds, o opens the raw file)", curses.A_DIM)
            return
        for idx, (k, v) in enumerate(items):
            if y >= h - 1:
                break
            mark = "> " if idx == self.row else "  "
            stdscr.addnstr(y, 2, mark, len(mark))
            stdscr.addnstr(y, 4, f"{k} = {v}", w - 6, curses.A_REVERSE if idx == self.row else 0)
            y += 1

    def _emit_at(self, stdscr, pv, y, x, segments):
        h, w = stdscr.getmaxyx()
        if y >= h:
            return 1
        cx = x
        for text, attr in segments:
            if cx >= w:
                break
            n = min(len(text), w - cx)
            if n > 0:
                try:
                    stdscr.addnstr(y, cx, text[:n], n, attr)
                except curses.error:
                    pass
                cx += n
        return 1

    # ---- main loop ----
    def run(self, stdscr):
        try:
            curses.curs_set(0)
            stdscr.keypad(True)
        except curses.error:
            pass
        self.ensure_default()
        while True:
            stdscr.erase()
            if self.mode == "browse":
                self._draw_browse(stdscr)
            else:
                self._draw_edit(stdscr)
            stdscr.refresh()
            try:
                key = stdscr.getch()
            except curses.error:
                key = -1

            if self.mode == "browse":
                if not self._browse_key(stdscr, key):
                    return
            else:
                if not self._edit_key(stdscr, key):
                    if self.dirty and _confirm(stdscr, "unsaved changes - save? (y/N)"):
                        self.save_edit()
                    self.mode = "browse"
                    self.theme = None
                    self.msg = ""
                    self.names = list_themes()

    def _browse_key(self, stdscr, key):
        if key in (ord("q"), ord("Q"), 27):
            return False
        if key in (curses.KEY_DOWN, ord("j")):
            self.sel = (self.sel + 1) % len(self.names)
        elif key in (curses.KEY_UP, ord("k")):
            self.sel = (self.sel - 1) % len(self.names)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            self.apply_sel()
        elif key in (ord("e"), ord("E")):
            self.start_edit()
        elif key in (ord("n"), ord("N"), ord("c"), ord("C")):
            self.new_theme(stdscr)
        elif key in (ord("d"), ord("D")):
            self.delete_sel(stdscr)
        elif key in (ord("i"), ord("I")):
            self.do_import(stdscr)
        elif key in (ord("x"), ord("X")):
            self.do_export()
        elif key == ord("o"):
            self.open_raw()
        return True

    def open_raw(self):
        path = self.theme.save() if self.theme else None
        if path is None:
            path = os.path.join(THEME_DIR, f"{self.names[self.sel]}.json")
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
        try:
            subprocess.call([editor, path])
            self.set_msg("raw file edited")
        except FileNotFoundError:
            self.set_msg(f"editor not found: {editor}")

    def _edit_key(self, stdscr, key):
        if key in (ord("q"), ord("Q"), 27):
            return False
        if key == ord("\t") or key == curses.KEY_BTAB:
            self.tab = (self.tab + 1) % 3
            self.row = 0
            return True
        if key in (ord("s"), ord("S")):
            self.save_edit()
            return True
        if key in (ord("o"), ord("O")):
            self.open_raw()
            return True
        if self.tab == 0:
            self._edit_colors_key(stdscr, key)
        elif self.tab == 1:
            self._edit_prompt_key(stdscr, key)
        else:
            self._edit_aliases_key(stdscr, key)
        return True

    def _edit_colors_key(self, stdscr, key):
        if key in (curses.KEY_DOWN, ord("j")):
            self.row = (self.row + 1) % len(EDITABLE_COLORS)
        elif key in (curses.KEY_UP, ord("k")):
            self.row = (self.row - 1) % len(EDITABLE_COLORS)
        elif key in (ord("h"), curses.KEY_LEFT):
            self.nudge_color(-1)
        elif key in (ord("l"), curses.KEY_RIGHT):
            self.nudge_color(1)
        elif key in (ord("H"),):
            self.nudge_color(-16)
        elif key in (ord("L"),):
            self.nudge_color(16)
        elif key in (ord("r"),):
            self.component = 0
        elif key in (ord("g"),):
            self.component = 1
        elif key in (ord("b"),):
            self.component = 2

    def _edit_prompt_key(self, stdscr, key):
        if key in (curses.KEY_DOWN, ord("j")):
            self.row = (self.row + 1) % len(PROMPT_ROLE_ORDER)
        elif key in (curses.KEY_UP, ord("k")):
            self.row = (self.row - 1) % len(PROMPT_ROLE_ORDER)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" "), ord("c"), ord("C")):
            self.cycle_prompt_color()
            self.toggle_prompt_bool()

    def _edit_aliases_key(self, stdscr, key):
        items = self.alias_list()
        if key in (curses.KEY_DOWN, ord("j")):
            self.row = (self.row + 1) % max(1, len(items))
        elif key in (curses.KEY_UP, ord("k")):
            self.row = (self.row - 1) % max(1, len(items))
        elif key in (ord("a"), ord("A")):
            self.add_alias(stdscr)
            self.row = 0
        elif key in (ord("d"), ord("D")):
            self.delete_alias()


def run_theme_ui() -> str | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    app = ThemeApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return None
    return app.applied
# ---- history picker (minty_hist.py) ----

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

# ---- package manager (minty_pkg.py) ----

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

def _pkg_cli(args):
    pm = detect_pm()
    if pm is None:
        print("no supported package manager found (pacman, yay, paru, apt)", file=sys.stderr)
        return 1
    if not args or args[0] in ("app", "ui", "menu"):
        return 0 if _pkg_run_app() else 1
    cmd = args[0]
    rest = args[1:]

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


def _pkg_run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = PkgApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True
# ---- side menu (minty_menu.py) ----

MENU_ITEMS = [
    ("OpenCode AI", "opencode", "Launch the OpenCode AI assistant."),
    ("Rerun last command", "rerun", "Rerun the last command you typed (!!)."),
    ("Learn code guide", "learn", "How-to snippets: files, editing, git, python, pipes and more."),
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


def _menu_wrap(text, width):
    words = text.split()
    wlines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            wlines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        wlines.append(cur)
    return wlines


def _menu_picker() -> str:
    """Side menu panel on the right edge of the terminal; returns the token."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return "close"
    result = {"token": "close"}

    def _run(stdscr):
        try:
            curses.curs_set(0)
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
                desc = _menu_wrap(MENU_ITEMS[selected][2], left - 2)
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
                for idx, (label, _, _) in enumerate(MENU_ITEMS):
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
                result["token"] = "close"
                return
            if key in (curses.KEY_UP, ord("k")):
                selected = (selected - 1) % len(MENU_ITEMS)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = (selected + 1) % len(MENU_ITEMS)
            elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
                result["token"] = MENU_ITEMS[selected][1]
                return
            elif ord("1") <= key <= ord(str(len(MENU_ITEMS))):
                result["token"] = MENU_ITEMS[int(chr(key)) - 1][1]
                return

    try:
        curses.wrapper(_run)
    except Exception:
        pass
    return result["token"]

# ---- tmux session manager (minty_tmux.py) ----

def have_tmux() -> bool:
    return shutil.which("tmux") is not None



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


def _tmux_cli(args):
    if not args or args[0] in ("app", "ui"):
        return 0 if _tmux_run_app() else 1
    cmd, rest = args[0], args[1:]
    if cmd == "install":
        print(f"{YELLOW}installing tmux...{RESET}")
        return _pkg_cli(["install", "tmux"])
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
            _pkg_cli(["install", "tmux"])
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


def _tmux_run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = TmuxApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True
# ---- virtual machines (minty_vm.py) ----

STACK = ["qemu-full", "libvirt", "virt-install"]


def have_virsh() -> bool:
    return shutil.which("virsh") is not None
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


def _vm_cli(args):
    if not args or args[0] in ("app", "ui"):
        return 0 if _vm_run_app() else 1
    cmd, rest = args[0], args[1:]
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
    code = _pkg_cli(["install"] + STACK)
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


def _vm_run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = VmApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True

# ---- systemd services (minty_svc.py) ----

UNIT_RE = re.compile(r"^\s*(\S+\.service)\s+(\S+)\s+(\S+)\s+(\S+)(.*)$")
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


def _svc_cli(args):
    if not args or args[0] in ("app", "ui"):
        return 0 if _svc_run_app() else 1
    cmd, rest = args[0], args[1:]
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


def _svc_run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = SvcApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True
# ---- process manager (minty_proc.py) ----

PS_BASE = ["ps", "-eo", "pid,user,pcpu,pmem,rss,args", "--no-headers"]
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


def _proc_cli(args):
    if not args or args[0] in ("app", "ui"):
        return 0 if _proc_run_app() else 1
    cmd, rest = args[0], args[1:]
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


def _proc_run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = ProcApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True

# ---- network/wifi (minty_net.py) ----

def have_nmcli() -> bool:
    return shutil.which("nmcli") is not None
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


def _net_cli(args):
    if not args or args[0] in ("app", "ui"):
        return 0 if _net_run_app() else 1
    cmd, rest = args[0], args[1:]
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


def _net_run_app() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = NetApp()
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True

# ---- learn: built-in code guide (minty_learn.py) ----

LEARN_TOPICS = [
    {
        "title": "Create a file",
        "desc": "Make new files fast, from empty to full of text.",
        "lines": [
            ("h", "Create an empty file"),
            ("c", "touch notes.txt"),
            ("t", "Write a line into a file (creates it if missing):"),
            ("c", 'echo "hello world" > notes.txt'),
            ("t", "Add more lines without overwriting (>> appends):"),
            ("c", 'echo "another line" >> notes.txt'),
            ("t", "Type content directly, then press Ctrl+D to save:"),
            ("c", "cat > notes.txt"),
            ("t", "Multi-line content without opening an editor (here-doc):"),
            ("c", "cat > notes.txt <<EOF"),
            ("c", "line one"),
            ("c", "line two"),
            ("c", "EOF"),
            ("t", "Copy an existing file to make a new one:"),
            ("c", "cp config.txt config.bak"),
            ("b", ">  overwrites    >>  appends    <<EOF  here-doc"),
        ],
    },
    {
        "title": "Edit a file",
        "desc": "Open files in editors and tweak them from the terminal.",
        "lines": [
            ("h", "Easy editors"),
            ("c", "nano notes.txt"),
            ("t", "nano is beginner friendly. Ctrl+O saves, Ctrl+X quits."),
            ("c", "micro notes.txt"),
            ("t", "micro feels like a modern editor with mouse + arrow keys."),
            ("h", "vim"),
            ("c", "vim notes.txt"),
            ("t", "vim: press i to insert, Esc to stop typing, :wq to save & quit, :q! to quit without saving."),
            ("t", "In-place edits without opening an editor:"),
            ("c", "sed -i 's/old/new/g' notes.txt"),
            ("c", "sed -i '/delete this line/d' notes.txt"),
            ("t", "Open in a GUI editor (if installed):"),
            ("c", "code notes.txt"),
            ("b", "Use 'sudoedit file' (not 'sudo nano') to edit root-owned files safely."),
        ],
    },
    {
        "title": "View a file",
        "desc": "Read files without an editor.",
        "lines": [
            ("c", "cat notes.txt"),
            ("t", "Scroll through a big file with less (arrows scroll, q quits):"),
            ("c", "less notes.txt"),
            ("t", "First / last lines: "),
            ("c", "head notes.txt"),
            ("c", "tail notes.txt"),
            ("c", "tail -f server.log"),
            ("t", "tail -f follows a log while it grows. Ctrl+C stops it."),
            ("t", "Line numbers and matching lines:"),
            ("c", "cat -n notes.txt"),
            ("c", "grep -n 'todo' notes.txt"),
            ("t", "Count lines, words and bytes:"),
            ("c", "wc notes.txt"),
        ],
    },
    {
        "title": "Files & folders",
        "desc": "The everyday file commands.",
        "lines": [
            ("c", "ls -l -h"),
            ("t", "-l long list, -a show hidden, -h human sizes, -t by time, -r reverse"),
            ("t", "Make folders, including parents at once:"),
            ("c", "mkdir -p projects/website/src"),
            ("t", "Copy / move / rename:"),
            ("c", "cp -r folder newfolder"),
            ("c", "mv file.txt newname.txt"),
            ("c", "mv file.txt docs/"),
            ("t", "Remove (be careful — there is no undo):"),
            ("c", "rm file.txt"),
            ("c", "rm -r folder"),
            ("b", "In minty, 'trash' moves to the trash instead of deleting."),
            ("t", "See a folder tree:"),
            ("c", "tree -L 2"),
        ],
    },
    {
        "title": "Search & find",
        "desc": "Find files and text anywhere.",
        "lines": [
            ("t", "Search inside files for text:"),
            ("c", "grep -rin 'error' ."),
            ("t", "-r recursive, -i ignore case, -n line numbers, -l filenames only"),
            ("t", "Find files by name / size / age:"),
            ("c", "find . -name '*.py'"),
            ("c", "find /home -size +100M"),
            ("c", "find . -mtime -7"),
            ("t", "Delete everything matching (e.g. node_modules):"),
            ("c", "find . -type d -name 'node_modules' -exec rm -rf {} +"),
            ("t", "ripgrep ('rg') is much faster for big trees:"),
            ("c", "rg 'TODO' ."),
        ],
    },
    {
        "title": "Permissions",
        "desc": "Who can read, write and run what.",
        "lines": [
            ("c", "ls -l"),
            ("t", "Read it like: drwxr-xr-x. d=dir, then r=read, w=write, x=execute in three groups: owner / group / others."),
            ("t", "Make a script runnable:"),
            ("c", "chmod +x script.sh"),
            ("c", "chmod 755 script.sh"),
            ("t", "Numbers: 4=read, 2=write, 1=execute. 755 = rwxr-xr-x."),
            ("c", "chmod 644 file.txt"),
            ("t", "Change ownership (needs sudo):"),
            ("c", "sudo chown david:david file.txt"),
            ("b", "Never 'chmod 777' — that lets everyone write to the file."),
        ],
    },
    {
        "title": "Pipes & redirects",
        "desc": "Chain commands together and save their output.",
        "lines": [
            ("t", "| sends the left command's output into the right command:"),
            ("c", "ls -l | less"),
            ("c", "history | grep python"),
            ("t", "Redirect output to a file:"),
            ("c", "command > file.txt"),
            ("c", "command >> file.txt"),
            ("c", "command 2> errors.log"),
            ("c", "command > out.log 2>&1"),
            ("t", "Run things in order / conditionally:"),
            ("c", "mkdir x && cd x && touch a"),
            ("c", "cd x || exit 1"),
            ("t", "Show output AND save it:"),
            ("c", "long_task | tee output.log"),
        ],
    },
    {
        "title": "Git basics",
        "desc": "Version control for your code.",
        "lines": [
            ("c", "git init"),
            ("c", "git clone https://github.com/user/repo"),
            ("t", "See what changed:"),
            ("c", "git status"),
            ("c", "git diff"),
            ("t", "Stage, commit, push:"),
            ("c", "git add ."),
            ("c", 'git commit -m "my message"'),
            ("c", "git push"),
            ("t", "Fetch updates and branches:"),
            ("c", "git pull"),
            ("c", "git branch -a"),
            ("c", "git checkout -b new-feature"),
            ("c", "git log --oneline"),
            ("b", "Never commit secrets. Use a .gitignore file."),
        ],
    },
    {
        "title": "Python basics",
        "desc": "Run and write Python from the terminal.",
        "lines": [
            ("c", "python3 script.py"),
            ("c", "print('hello world')"),
            ("t", "Variables:"),
            ("c", "name = 'david'"),
            ("c", "age = 30"),
            ("t", "Conditional & loop:"),
            ("c", "if age >= 18:"),
            ("c", "    print('adult')"),
            ("c", "for i in range(5):"),
            ("c", "    print(i)"),
            ("t", "Function:"),
            ("c", "def greet(name):"),
            ("c", "    return f'hi {name}'"),
            ("t", "Read a file:"),
            ("c", "with open('data.txt') as f:"),
            ("c", "    print(f.read())"),
        ],
    },
    {
        "title": "Variables & env",
        "desc": "Variables, quoting and environment.",
        "lines": [
            ("c", "MYVAR=hello"),
            ("c", "echo $MYVAR"),
            ("t", "Export so child programs see it:"),
            ("c", "export MYVAR=hello"),
            ("t", "Special variables:"),
            ("c", "echo $HOME"),
            ("c", "echo $PATH"),
            ("c", "echo $?"),
            ("c", "echo $$"),
            ("t", "$? is the exit code of the last command. $$ is this shell's PID."),
            ("t", "Quoting matters:"),
            ("c", 'echo "value $MYVAR"'),
            ("c", "echo 'value $MYVAR'"),
            ("t", "Double quotes expand variables, single quotes are literal."),
        ],
    },
    {
        "title": "Processes & jobs",
        "desc": "See, stop and background running programs.",
        "lines": [
            ("c", "ps aux | grep python"),
            ("t", "In minty, 'proc' opens a visual manager and 'proc kill PID' force-kills."),
            ("t", "Freeze a running job with Ctrl+Z, then:"),
            ("c", "jobs"),
            ("c", "bg"),
            ("c", "fg"),
            ("t", "bg keeps it running in the background, fg brings it back."),
            ("t", "Kill politely, then escalate:"),
            ("c", "kill 1234"),
            ("c", "kill -9 1234"),
            ("c", "pkill -f server.py"),
        ],
    },
    {
        "title": "Network",
        "desc": "Ping, download and connect.",
        "lines": [
            ("c", "ping 1.1.1.1"),
            ("t", "Download files:"),
            ("c", "curl -O https://example.com/file.zip"),
            ("c", "wget https://example.com/file.zip"),
            ("t", "IPs and open ports:"),
            ("c", "ip a"),
            ("c", "ss -tulpn"),
            ("t", "Remote shell and copy (SSH):"),
            ("c", "ssh user@host"),
            ("c", "scp file.txt user@host:/remote/path"),
            ("t", "Look up DNS:"),
            ("c", "getent hosts example.com"),
        ],
    },
    {
        "title": "Install software",
        "desc": "Package manager cheat-sheet.",
        "lines": [
            ("t", "Arch (pacman) — minty's 'pkg' command wraps these:"),
            ("c", "sudo pacman -S package"),
            ("c", "pacman -Ss search-term"),
            ("c", "pacman -Rns package"),
            ("c", "sudo pacman -Syu"),
            ("t", "AUR helpers (yay / paru):"),
            ("c", "yay -S package"),
            ("c", "yay -Syu --noconfirm"),
            ("t", "Debian / Ubuntu (apt):"),
            ("c", "sudo apt install package"),
            ("c", "apt search term"),
            ("c", "sudo apt update && sudo apt upgrade -y"),
        ],
    },
    {
        "title": "System info",
        "desc": "How to look at your machine.",
        "lines": [
            ("c", "uname -a"),
            ("c", "uptime"),
            ("t", "Disk and memory:"),
            ("c", "df -h"),
            ("c", "free -h"),
            ("t", "CPU / hardware detail:"),
            ("c", "lscpu"),
            ("c", "lspci"),
            ("t", "Pretty overview (usually installed):"),
            ("c", "fastfetch"),
            ("c", "neofetch"),
        ],
    },
    {
        "title": "minty shortcuts",
        "desc": "The fast keys and built-in managers.",
        "lines": [
            ("b", "Ctrl+T   side menu (OpenCode, themes, packages, managers)"),
            ("b", "Ctrl+R   browse command history"),
            ("b", "!!       rerun the last command"),
            ("b", "z        jump to a frequent directory"),
            ("b", "cdr      pick a recent directory"),
            ("b", "tab      autocomplete commands and files"),
            ("t", "Built-in managers:"),
            ("c", "theme   visual theme editor"),
            ("c", "pkg     package manager"),
            ("c", "tmux    tmux session manager"),
            ("c", "vms     virtual machines"),
            ("c", "svc     systemd services"),
            ("c", "proc    processes"),
            ("c", "net     wifi & connections"),
            ("t", "Learn more about any topic:"),
            ("c", "learn git"),
            ("t", "Exit minty into your real shell:"),
            ("c", "exit"),
        ],
    },
]


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines = []
    cur = words[0]
    for word in words[1:]:
        if len(cur) + 1 + len(word) <= width:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


class LearnApp:
    def __init__(self, initial: str = ""):
        self.topics = LEARN_TOPICS
        self.sel = 0
        self.query = initial
        self.offset = 0

    def filtered(self):
        q = self.query.strip().lower()
        if not q:
            return self.topics
        return [t for t in self.topics
                if q in t["title"].lower() or q in t["desc"].lower()]

    def run(self, stdscr):
        try:
            curses.curs_set(0)
            stdscr.keypad(True)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)   # code
                curses.init_pair(2, curses.COLOR_CYAN, -1)    # bullets
        except curses.error:
            pass
        while True:
            h, w = stdscr.getmaxyx()
            items = self.filtered()
            if items:
                self.sel = max(0, min(self.sel, len(items) - 1))
            list_w = max(22, min(36, w // 3))
            stdscr.erase()
            stdscr.addnstr(0, 0, " minty learn — code guide ", w - 1,
                           curses.A_BOLD | curses.A_REVERSE)
            stdscr.addnstr(1, 0, "topics  (type to filter)".ljust(list_w),
                           list_w, curses.A_DIM)
            y = 2
            visible = h - 3
            if len(items) > visible:
                top = max(0, self.sel - visible + 1)
            else:
                top = 0
            for idx in range(top, min(len(items), top + visible)):
                attr = curses.A_REVERSE if idx == self.sel else 0
                label = " > " + items[idx]["title"]
                try:
                    stdscr.addnstr(y, 0, label.ljust(list_w)[:list_w], list_w, attr)
                except curses.error:
                    pass
                y += 1

            if items:
                topic = items[self.sel]
                rx = list_w + 1
                rw = w - rx - 1
                if rw > 4:
                    stdscr.addnstr(1, rx, topic["title"][:rw], rw, curses.A_BOLD)
                    stdscr.addnstr(2, rx, topic["desc"][:rw], rw, curses.A_DIM)
                    content = []
                    for kind, text in topic["lines"]:
                        for line in _wrap(text, rw - 3):
                            content.append((kind, line))
                    ry = 3
                    body = h - ry - 1
                    maxoff = max(0, len(content) - body)
                    self.offset = max(0, min(self.offset, maxoff))
                    for i in range(self.offset, min(len(content), self.offset + body)):
                        kind, text = content[i]
                        attr = 0
                        pair = 0
                        if kind == "h":
                            attr = curses.A_BOLD
                        elif kind == "c":
                            pair = 1
                        elif kind == "b":
                            pair = 2
                        seg = ("  " + text)[:rw]
                        try:
                            stdscr.addnstr(ry, rx, seg, rw,
                                           curses.color_pair(pair) | attr if pair else attr)
                        except curses.error:
                            pass
                        ry += 1

            foot = "j/k move   type filter   PgUp/PgDn scroll   Home/End top/bottom   q quit"
            stdscr.addnstr(h - 1, 0, foot[: w - 1], w - 1, curses.A_DIM)
            stdscr.refresh()
            try:
                key = stdscr.getch()
            except curses.error:
                key = -1
            if key in (ord("q"), ord("Q"), 27):
                return
            if key in (curses.KEY_DOWN, ord("j")):
                if items:
                    self.sel = (self.sel + 1) % len(items)
                    self.offset = 0
            elif key in (curses.KEY_UP, ord("k")):
                if items:
                    self.sel = (self.sel - 1) % len(items)
                    self.offset = 0
            elif key in (curses.KEY_NPAGE, ord(" ")):
                self.offset += h - 6
            elif key in (curses.KEY_PPAGE, ord("b")):
                self.offset -= h - 6
            elif key == curses.KEY_HOME:
                self.offset = 0
            elif key == curses.KEY_END:
                self.offset = 10 ** 9
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
                self.sel = 0
                self.offset = 0
            elif 32 <= key < 127 and len(self.query) < 100:
                self.query += chr(key)
                self.sel = 0
                self.offset = 0


def run_learn_app(initial: str = "") -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    app = LearnApp(initial)
    try:
        curses.wrapper(app.run)
    except Exception:
        return False
    return True


def cmd_learn(args):
    initial = " ".join(args)
    if initial:
        print(f"{DIM}code guide — filtered to '{initial}'{RESET}")
    if not run_learn_app(initial):
        err("learn", "needs an interactive terminal")
        return 1
    return 0

# ---- minty shell core ----

VERSION = "4.2"
HISTFILE = os.path.expanduser("~/.minty_history")
MAXHIST = 2000
HIST_SENTINEL = "__mintyhist__"
DIRS_FILE = os.path.join(CONFIG_DIR, "dirs.json")
LASTDIR_FILE = os.path.join(CONFIG_DIR, "last_dir")

ACTIVE_THEME = "default"

LAST_EXIT = 0
OLDPWD = None
LAST_DURATION = 0.0
_QUIT = False
USER_ALIASES = {}

DEFAULT_ALIASES = {
    "l": "ls",
    "ll": "ls -l",
    "la": "ls -a",
    "h": "history",
    "q": "exit",
    "cls": "clear",
    "..": "cd ..",
    "...": "cd ../..",
}

ALIASES = dict(DEFAULT_ALIASES)


def cfg_get() -> dict:
    return get_config()


def persist_aliases() -> None:
    """Save user-set aliases into the persistent config."""
    cfg = get_config()
    cfg["aliases"] = dict(USER_ALIASES)
    save_config(cfg)


def load_user_aliases() -> None:
    """Merge persistent aliases from config into the running ALIASES."""
    global USER_ALIASES
    cfg = get_config()
    USER_ALIASES = dict(cfg.get("aliases") or {})
    ALIASES.update(USER_ALIASES)


def settings() -> dict:
    cfg = get_config()
    return cfg.setdefault("settings", {})


def apply_theme(name: str) -> Theme:
    """Load a theme and rewire the global colors/aliases/prompt around it."""
    global ACTIVE_THEME, RESET, BOLD, DIM, RED, GREEN, YELLOW, BLUE
    global MAGENTA, CYAN, B_RED, B_GREEN, B_MAGENTA, B_CYAN, ALIASES
    theme = load_theme(name)
    ACTIVE_THEME = theme.name
    RESET = theme.c("reset")
    BOLD = theme.c("bold")
    DIM = theme.c("dim")
    RED = theme.c("red")
    GREEN = theme.c("green")
    YELLOW = theme.c("yellow")
    BLUE = theme.c("blue")
    MAGENTA = theme.c("magenta")
    CYAN = theme.c("cyan")
    B_RED = theme.c("bright_red")
    B_GREEN = theme.c("bright_green")
    B_MAGENTA = theme.c("bright_magenta")
    B_CYAN = theme.c("bright_cyan")
    ALIASES = dict(DEFAULT_ALIASES)
    ALIASES.update(theme.aliases)
    ALIASES.update(USER_ALIASES)
    return theme


def ptc(theme: Theme, role: str) -> str:
    """Colored escape for a prompt role in the given theme."""
    return theme.c(theme.prompt_role(role))


def err(name: str, message: str) -> None:
    print(f"{B_RED}{name}{RESET}: {message}")


def git_info(path: str) -> dict | None:
    """Return {branch, dirty, ahead, behind} for the git repo at/above path."""
    d = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            try:
                proc = subprocess.run(
                    ["git", "status", "--porcelain", "--branch"],
                    cwd=d,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            if proc.returncode != 0:
                return None
            head = proc.stdout.splitlines()[0] if proc.stdout else "##"
            m = re.match(r"## (\S+)", head)
            branch = m.group(1) if m else "?"
            branch = branch.split("...")[0]
            ahead = behind = 0
            mod = re.search(r"\[ahead (\d+)\]", head)
            if mod:
                ahead = int(mod.group(1))
            mod = re.search(r"behind (\d+)", head)
            if mod:
                behind = int(mod.group(1))
            dirty = len([l for l in proc.stdout.splitlines() if l and not l.startswith("##")]) > 0
            return {"branch": branch, "dirty": dirty, "ahead": ahead, "behind": behind}
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _venv_name() -> str | None:
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        return os.path.basename(venv)
    p = os.environ.get("CONDA_DEFAULT_ENV")
    if p:
        return p
    return None


def prompt() -> str:
    theme = load_theme(ACTIVE_THEME)
    pconf = theme.prompt
    user = os.environ.get("USER") or "user"
    host = os.uname().nodename.split(".")[0]
    home = os.path.expanduser("~")
    shown = os.getcwd().replace(home, "~", 1)
    mark = "#" if os.geteuid() == 0 else "$"
    sep = str(pconf.get("separator", "@"))
    p = ""
    if pconf.get("show_status", True) and LAST_EXIT:
        p += f"{ptc(theme, 'error')}✗{RESET} "
    venv = _venv_name()
    if venv:
        p += f"{ptc(theme, 'venv')}({venv}){RESET} "
    p += f"{ptc(theme, 'user')}{user}{RESET}{sep}{ptc(theme, 'host')}{host}{RESET}:{ptc(theme, 'dir')}{shown}{RESET}"
    if pconf.get("show_git", True):
        info = git_info(os.getcwd())
        if info:
            p += f" {ptc(theme, 'branch')}❮{info['branch']}{RESET}"
            if info["dirty"]:
                p += f"{ptc(theme, 'dirty')}•{RESET}"
            if info["ahead"]:
                p += f"{ptc(theme, 'ahead')}↑{info['ahead']}{RESET}"
            if info["behind"]:
                p += f"{ptc(theme, 'behind')}↓{info['behind']}{RESET}"
            p += f"{ptc(theme, 'branch')}❯{RESET}"
    if LAST_DURATION >= float(settings().get("duration_threshold", 3.0)):
        p += f" {DIM}[{LAST_DURATION:.1f}s]{RESET}"
    if pconf.get("show_time", False):
        p += f" {ptc(theme, 'time')}{datetime.datetime.now().strftime('%H:%M')}{RESET}"
    return p + f" {ptc(theme, 'mark')}{mark}{RESET} "


def expand_arg(arg: str) -> str:
    arg = os.path.expanduser(arg)
    arg = os.path.expandvars(arg)
    if "$?" in arg:
        arg = arg.replace("$?", str(LAST_EXIT))
    return arg


def expand_glob(arg: str) -> list[str]:
    if any(ch in arg for ch in "*?["):
        matches = sorted(glob.glob(arg))
        if matches:
            return matches
    return [arg]


def has_shell_ops(line: str) -> bool:
    quote = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "|&;<>`":
            return True
        elif ch == "$" and i + 1 < n and line[i + 1] == "(":
            return True
        i += 1
    return False


def run_any(cmd: str) -> int:
    global LAST_EXIT
    try:
        proc = subprocess.Popen(cmd, shell=True)
        proc.wait()
        LAST_EXIT = proc.returncode
    except KeyboardInterrupt:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass
        print()
        LAST_EXIT = 130
    except OSError as e:
        err("shell", str(e))
        LAST_EXIT = 1
    return LAST_EXIT


def run_proc(argv: list[str]) -> int:
    global LAST_EXIT
    try:
        proc = subprocess.Popen(argv)
        proc.wait()
        LAST_EXIT = proc.returncode
    except KeyboardInterrupt:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass
        print()
        LAST_EXIT = 130
    except OSError as e:
        err("opencode", str(e))
        LAST_EXIT = 1
    return LAST_EXIT


def here_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def find_opencode() -> str | None:
    bundled = os.path.join(here_dir(), "opencode")
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    return shutil.which("opencode")


def ensure_opencode() -> bool:
    if find_opencode():
        return True
    print(f"{YELLOW}opencode is missing — installing it for you...{RESET}")
    if not shutil.which("curl"):
        err("opencode", "curl is not installed, cannot download opencode")
        return False
    try:
        subprocess.run("curl -fsSL https://opencode.ai/install | bash", shell=True)
    except KeyboardInterrupt:
        print()
        return False
    if find_opencode():
        print(f"{GREEN}opencode installed.{RESET}")
        return True
    err("opencode", "installer finished but opencode wasn't found")
    return False


def human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return str(n)


def fmt_entry(path: str, name: str) -> str:
    if os.path.isdir(path) and not os.path.islink(path):
        return f"{B_CYAN}{name}/{RESET}"
    if os.path.islink(path):
        return f"{MAGENTA}{name}{RESET}"
    if os.access(path, os.X_OK):
        return f"{B_GREEN}{name}{RESET}"
    return name


def cmd_help(args):
    if args:
        for name in args:
            if name in COMMANDS:
                print(f"{B_GREEN}{name}{RESET} — {COMMANDS[name][0]}")
            elif name in ALIASES:
                print(f"{B_GREEN}{name}{RESET} → {ALIASES[name]}")
            else:
                err("help", f"unknown command: {name}")
                return 1
        return 0
    print(f"{B_GREEN}minty{RESET} v{VERSION} — built-in commands:")
    for name in sorted(COMMANDS):
        print(f"  {YELLOW}{name:<12}{RESET} {COMMANDS[name][0]}")
    print(f"  {YELLOW}<anything else>{RESET}  run it in the real shell (git, python, pipes, ...)")
    if ALIASES:
        print(f"\n{DIM}aliases:{RESET} " + ", ".join(sorted(ALIASES)))
    return 0


def cmd_echo(args):
    newline = True
    escapes = False
    text = []
    for a in args:
        if not text and a.startswith("-") and len(a) > 1 and set(a[1:]) <= {"n", "e"}:
            newline = newline and "n" not in a
            escapes = escapes or "e" in a
        else:
            text.append(a)
    out = " ".join(text)
    if escapes:
        out = re.sub(r"\\\\", "\x00", out)
        out = re.sub(r"\\n", "\n", out)
        out = re.sub(r"\\t", "\t", out)
        out = out.replace("\x00", "\\")
    print(out, end="\n" if newline else "")
    return 0


def cmd_pwd(args):
    print(os.getcwd())
    return 0


def _mode_str(st) -> str:
    m = st.st_mode
    kind = ("d" if (m & 0o170000) == 0o040000 else
            "l" if (m & 0o170000) == 0o120000 else "-")
    perms = "rwxrwxrwx"
    bits = (0o400, 0o200, 0o100, 0o040, 0o020, 0o010, 0o004, 0o002, 0o001)
    return kind + "".join(p if m & b else "-" for p, b in zip(perms, bits))


def _sort_entries(target: str, entries: list[str]) -> list[str]:
    def key(e: str) -> tuple:
        p = os.path.join(target, e)
        isdir = os.path.isdir(p) and not os.path.islink(p)
        return (0 if isdir else 1, e.lower())
    return sorted(entries, key=key)


def _ls_long(target: str, flags: str) -> int:
    try:
        entries = _sort_entries(target, os.listdir(target))
    except OSError as e:
        err("ls", f"cannot access '{target}': {e.strerror}")
        return 1
    if "a" not in flags:
        entries = [e for e in entries if not e.startswith(".")]
    total = 0
    lines = []
    for e in entries:
        p = os.path.join(target, e)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        total += st.st_blocks * 512
        size = human_size(st.st_size) if "h" in flags else str(st.st_size)
        date = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M")
        name = fmt_entry(p, e)
        if os.path.islink(p):
            try:
                link = os.readlink(p)
                name = f"{name} -> {link}"
            except OSError:
                pass
        lines.append(
            f"{_mode_str(st)} {st.st_nlink:>2} {st.st_uid:>4} {st.st_gid:>4} "
            f"{size:>8} {date} {name}"
        )
    print(f"total {total}")
    for ln in lines:
        print(ln)
    return 0


def cmd_ls(args):
    flags = ""
    rest = []
    for a in args:
        if a.startswith("-") and a != "-" and not rest:
            flags += a[1:]
        else:
            rest.append(a)
    targets = rest or ["."]
    if len(targets) > 1:
        for t in targets:
            print(f"{B_MAGENTA}{t}:{RESET}")
            code = _ls_long(t, flags) if "l" in flags else _ls_short(t, flags)
            if code:
                return code
            if t != targets[-1]:
                print()
        return 0
    t = targets[0]
    if "l" in flags:
        return _ls_long(t, flags)
    return _ls_short(t, flags)


def _ls_short(target: str, flags: str) -> int:
    try:
        entries = _sort_entries(target, os.listdir(target))
    except OSError as e:
        err("ls", f"cannot access '{target}': {e.strerror}")
        return 1
    if "a" not in flags:
        entries = [e for e in entries if not e.startswith(".")]
    print("  ".join(fmt_entry(os.path.join(target, e), e) for e in entries))
    return 0


def _load_dirs() -> dict:
    try:
        with open(DIRS_FILE) as f:
            db = json.load(f)
            return db if isinstance(db, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_dirs(db: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(DIRS_FILE, "w") as f:
            json.dump(db, f, indent=2)
    except OSError:
        pass


def _bump_dir(path: str) -> None:
    path = os.path.abspath(path)
    db = _load_dirs()
    for p in list(db):
        db[p] = db[p] * 0.97
    db[path] = db.get(path, 0.0) + 1.0
    db = {p: s for p, s in db.items() if s >= 0.05}
    _save_dirs(db)


def cmd_cd(args):
    global OLDPWD
    if not args:
        target = os.path.expanduser("~")
    else:
        target = expand_arg(args[0])
    if target == "-":
        if OLDPWD is None:
            err("cd", "no previous directory (OLDPWD not set)")
            return 1
        target = OLDPWD
        print(target)
    elif target == "~" or target == "~" + os.sep:
        target = os.path.expanduser("~")
    prev = os.getcwd()
    target = os.path.abspath(os.path.expanduser(target))
    try:
        os.chdir(target)
    except OSError as e:
        err("cd", f"{e.strerror}: {target}")
        return 1
    OLDPWD = prev
    _bump_dir(target)
    return 0


def cmd_z(args):
    """Jump to a frequently-visited directory (frecency)."""
    db = _load_dirs()
    if not args:
        top = sorted(db.items(), key=lambda kv: kv[1], reverse=True)[:10]
        for p, s in top:
            print(f"{s:6.1f}  {p}")
        return 0
    needle = args[0]
    matches = [(p, s) for p, s in db.items() if needle in p]
    if not matches:
        err("z", f"no visited directory matches '{needle}'")
        return 1
    matches.sort(key=lambda kv: kv[1], reverse=True)
    target = matches[0][0]
    try:
        os.chdir(target)
    except OSError as e:
        err("z", f"{e.strerror}: {target}")
        return 1
    print(f"{DIM}→ {target}{RESET}")
    _bump_dir(target)
    return 0


def cmd_cdr(args):
    """Pick a recently-visited directory."""
    db = _load_dirs()
    top = sorted(db.items(), key=lambda kv: kv[1], reverse=True)[:10]
    if not top:
        err("cdr", "no visited directories yet")
        return 1
    for i, (p, s) in enumerate(top, 1):
        print(f"{YELLOW}{i:>2}{RESET}  {p}")
    if sys.stdin.isatty():
        try:
            choice = input("pick: ")
        except (KeyboardInterrupt, EOFError):
            print()
            return 130
        choice = choice.strip()
        if not choice:
            return 0
        try:
            idx = int(choice) - 1
        except ValueError:
            idx = -1
        if 0 <= idx < len(top):
            try:
                os.chdir(top[idx][0])
                _bump_dir(top[idx][0])
            except OSError as e:
                err("cdr", f"{e.strerror}: {top[idx][0]}")
                return 1
    return 0


def cmd_mkdir(args):
    if not args:
        err("mkdir", "missing operand")
        return 1
    for t in args:
        try:
            os.makedirs(t, exist_ok=True)
        except OSError as e:
            err("mkdir", f"cannot create directory '{t}': {e.strerror}")
            return 1
    return 0


def _trash_path() -> str:
    return os.path.expanduser("~/.local/share/Trash")


def cmd_trash(args):
    if not args:
        err("trash", "missing operand")
        return 1
    trashdir = _trash_path()
    files = os.path.join(trashdir, "files")
    os.makedirs(files, exist_ok=True)
    for t in args:
        src = os.path.abspath(os.path.expanduser(t))
        if not os.path.lexists(src):
            err("trash", f"cannot trash '{t}': No such file or directory")
            return 1
        base = os.path.basename(src.rstrip(os.sep)) or os.path.basename(src)
        dst = os.path.join(files, base)
        if os.path.lexists(dst):
            dst = os.path.join(files, f"{base}.{int(time.time())}")
        try:
            shutil.move(src, dst)
            print(f"{DIM}trashed {src} -> {dst}{RESET}")
        except OSError as e:
            err("trash", f"cannot trash '{t}': {e.strerror}")
            return 1
    return 0


def cmd_rm(args):
    if not args:
        err("rm", "missing operand")
        return 1
    recursive = False
    force = False
    interactive = False
    trash = False
    targets = []
    for a in args:
        if a.startswith("-") and len(a) > 1 and a != "-":
            recursive = recursive or ("r" in a or "R" in a)
            force = force or "f" in a
            interactive = interactive or "i" in a
            trash = trash or "T" in a or "--trash" in a
        elif a == "--trash":
            trash = True
        else:
            targets.append(a)
    if not targets:
        err("rm", "missing operand")
        return 1
    if trash:
        return cmd_trash(targets)
    for t in targets:
        ab = os.path.abspath(t)
        home = os.path.expanduser("~")
        if ab in ("/", home) or ab.startswith(home + "/"):
            err("rm", f"refusing to remove '{t}'")
            return 1
        if interactive and sys.stdin.isatty():
            try:
                resp = input(f"remove '{t}'? [y/N] ")
            except (KeyboardInterrupt, EOFError):
                print()
                return 130
            if resp.strip().lower() not in ("y", "yes"):
                continue
        try:
            if os.path.isdir(t) and not os.path.islink(t):
                if not recursive:
                    err("rm", f"cannot remove '{t}': is a directory (use -r)")
                    return 1
                shutil.rmtree(t)
            else:
                os.remove(t)
        except FileNotFoundError:
            if not force:
                err("rm", f"cannot remove '{t}': No such file or directory")
                return 1
        except OSError as e:
            err("rm", f"cannot remove '{t}': {e.strerror}")
            return 1
    return 0


def cmd_cat(args):
    numbered = False
    files = []
    for a in args:
        if a == "-n" and not files:
            numbered = True
        else:
            files.append(a)
    if not files:
        files = ["-"]
    n = 1
    for f in files:
        try:
            if f == "-":
                for line in sys.stdin:
                    if numbered:
                        print(f"{n:>6}\t{line}", end="")
                        n += 1
                    else:
                        sys.stdout.write(line)
                continue
            with open(f, errors="replace") as fh:
                for line in fh:
                    if numbered:
                        print(f"{n:>6}\t{line}", end="")
                        n += 1
                    else:
                        sys.stdout.write(line)
        except IsADirectoryError:
            err("cat", f"{f}: Is a directory")
            return 1
        except OSError as e:
            err("cat", f"{f}: {e.strerror}")
            return 1
    return 0


def cmd_touch(args):
    if not args:
        err("touch", "missing operand")
        return 1
    for t in args:
        try:
            with open(t, "a"):
                os.utime(t, None)
        except OSError as e:
            err("touch", f"cannot touch '{t}': {e.strerror}")
            return 1
    return 0


def _copy_move(verb: str, args):
    if len(args) < 2:
        err(verb, "missing file operand")
        return 1
    srcs, dst = args[:-1], args[-1]
    if len(srcs) > 1 and not os.path.isdir(dst):
        try:
            os.makedirs(dst, exist_ok=True)
        except OSError as e:
            err(verb, f"cannot create directory '{dst}': {e.strerror}")
            return 1
    for s in srcs:
        try:
            if verb == "cp":
                shutil.copy2(s, dst)
            else:
                shutil.move(s, dst)
        except OSError as e:
            err(verb, f"{e.strerror}: {s}")
            return 1
    return 0


def cmd_cp(args):
    return _copy_move("cp", args)


def cmd_mv(args):
    return _copy_move("mv", args)


def cmd_clear(args):
    print("\033[2J\033[H", end="")
    return 0


def cmd_date(args):
    print(datetime.datetime.now().strftime("%a %b %d %H:%M:%S %Y"))
    return 0


def cmd_whoami(args):
    print(os.environ.get("USER") or os.getlogin())
    return 0


def cmd_uname(args):
    u = os.uname()
    print(f"{u.sysname} {u.nodename} {u.release} {u.version} {u.machine}")
    return 0


def cmd_history(args):
    if readline is None:
        err("history", "history is unavailable (no readline)")
        return 1
    if args and args[0] == "-c":
        readline.clear_history()
        return 0
    n = MAXHIST
    if args and args[0].startswith("-") and args[0] != "-c":
        try:
            n = int(args[0][1:])
        except ValueError:
            pass
    total = readline.get_current_history_length()
    start = max(1, total - n + 1)
    for i in range(start, total + 1):
        item = readline.get_history_item(i)
        if item is not None:
            print(f"{i:>4}  {item}")
    return 0


def cmd_grep(args):
    ci = False
    numbered = False
    pattern = None
    files = []
    for a in args:
        if pattern is None and a.startswith("-") and a != "-" and len(a) > 1:
            ci = ci or "i" in a
            numbered = numbered or "n" in a
        elif pattern is None:
            pattern = a
        else:
            files.append(a)
    if pattern is None:
        err("grep", "missing pattern")
        return 1
    try:
        rx = re.compile(pattern, re.IGNORECASE if ci else 0)
    except re.error as e:
        err("grep", f"invalid pattern: {e}")
        return 2
    if not files:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if rx.search(line):
                print(line)
        return 0
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                for num, line in enumerate(fh, 1):
                    line = line.rstrip("\n")
                    if rx.search(line):
                        if numbered:
                            print(f"{num}:{line}")
                        elif len(files) > 1:
                            print(f"{B_MAGENTA}{f}{RESET}:{line}")
                        else:
                            print(line)
        except OSError as e:
            err("grep", f"{f}: {e.strerror}")
            return 1
    return 0


def cmd_head(args):
    n = 10
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-n" and i + 1 < len(args):
            try:
                n = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif a.startswith("-") and a != "-":
            try:
                n = int(a[1:])
            except ValueError:
                err("head", f"invalid option: {a}")
                return 1
            i += 1
        else:
            files.append(a)
            i += 1
    if not files:
        files = ["-"]
    for idx, f in enumerate(files):
        if len(files) > 1:
            print(f"==> {f} <==")
        try:
            if f == "-":
                lines = sys.stdin.readlines()
            else:
                with open(f) as fh:
                    lines = fh.readlines()
        except OSError as e:
            err("head", f"{f}: {e.strerror}")
            return 1
        sys.stdout.write("".join(lines[:n]))
    return 0


def cmd_tail(args):
    n = 10
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-n" and i + 1 < len(args):
            try:
                n = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif a.startswith("-") and a != "-":
            try:
                n = int(a[1:])
            except ValueError:
                err("tail", f"invalid option: {a}")
                return 1
            i += 1
        else:
            files.append(a)
            i += 1
    if not files:
        files = ["-"]
    for idx, f in enumerate(files):
        if len(files) > 1:
            print(f"==> {f} <==")
        try:
            if f == "-":
                lines = sys.stdin.readlines()
            else:
                with open(f) as fh:
                    lines = fh.readlines()
        except OSError as e:
            err("tail", f"{f}: {e.strerror}")
            return 1
        sys.stdout.write("".join(lines[-n:]))
    return 0


def cmd_tree(args):
    depth = None
    root = "."
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-L" and i + 1 < len(args):
            try:
                depth = int(args[i + 1])
            except ValueError:
                err("tree", f"invalid depth: {args[i + 1]}")
                return 1
            i += 2
        elif a.startswith("-") and a != "-":
            err("tree", f"invalid option: {a}")
            return 1
        else:
            root = a
            i += 1
    print(f"{B_CYAN}{root}/{RESET}")

    def walk(d, pref, level):
        if depth is not None and level > depth:
            return
        try:
            items = sorted(os.listdir(d))
        except OSError:
            return
        for idx, name in enumerate(items):
            path = os.path.join(d, name)
            last = idx == len(items) - 1
            conn = "└── " if last else "├── "
            if os.path.isdir(path) and not os.path.islink(path):
                print(pref + conn + f"{B_CYAN}{name}{RESET}")
                walk(path, pref + ("    " if last else "│   "), level + 1)
            elif os.path.islink(path):
                try:
                    link = os.readlink(path)
                    print(pref + conn + f"{MAGENTA}{name}{RESET} -> {link}")
                except OSError:
                    print(pref + conn + f"{MAGENTA}{name}{RESET}")
            else:
                print(pref + conn + name)

    walk(root, "", 0)
    return 0


def cmd_which(args):
    if not args:
        return 0
    for name in args:
        found = shutil.which(name)
        if found:
            print(found)
        else:
            err("which", f"no {name} in ({os.environ.get('PATH', '')})")
    return 0


def cmd_env(args):
    for k in sorted(os.environ):
        print(f"{k}={os.environ[k]}")
    return 0


def cmd_export(args):
    if not args:
        return cmd_env(args)
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            os.environ[k] = expand_arg(v)
        else:
            v = os.environ.get(a, "")
            print(f"{a}={v}")
    return 0


def cmd_alias(args):
    if not args:
        for name, val in sorted(ALIASES.items()):
            print(f"{name}='{val}'")
        return 0
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            ALIASES[k] = v
            USER_ALIASES[k] = v
            persist_aliases()
        else:
            print(f"{a}='{ALIASES.get(a, '')}'")
    return 0


def cmd_unalias(args):
    if not args:
        err("unalias", "missing operand")
        return 1
    for a in args:
        if a == "-a":
            for k in list(ALIASES):
                if k not in DEFAULT_ALIASES:
                    del ALIASES[k]
            USER_ALIASES.clear()
            persist_aliases()
        elif a in USER_ALIASES:
            del ALIASES[a]
            del USER_ALIASES[a]
            persist_aliases()
        elif a in ALIASES:
            err("unalias", f"'{a}' is built in or from a theme - edit config.json to remove it")
            return 1
        else:
            err("unalias", f"no such alias: {a}")
            return 1
    return 0


def cmd_version(args):
    print(f"minty v{VERSION}")
    print(f"python {sys.version.split()[0]}")
    return 0


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-a", "minty", title, body])
        except OSError:
            pass


def prefill_input(text: str) -> None:
    """Make the next input() start with `text` already typed."""

    def _hook():
        if readline is not None:
            try:
                readline.insert_text(text)
            except Exception:
                pass
        readline.set_startup_hook(None)

    if readline is not None:
        readline.set_startup_hook(_hook)


def _history_items() -> list[str]:
    if readline is None:
        return []
    total = readline.get_current_history_length()
    items = []
    for i in range(1, total + 1):
        it = readline.get_history_item(i)
        if it:
            items.append(it)
    return items


def cmd_hist(args):
    """Open the visual history picker."""
    if readline is None:
        err("hist", "history is unavailable (no readline)")
        return 1
    picked = run_history_picker(_history_items())
    if picked is not None:
        prefill_input(picked)
        print(f"{DIM}→ {picked}{RESET}")
    return 0


def cmd_config(args):
    """Open the persistent minty config for editing."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.isfile(CONFIG_FILE):
        save_config(get_config())
    try:
        code = subprocess.run([editor, CONFIG_FILE]).returncode
    except FileNotFoundError:
        err("config", f"editor not found: {editor}")
        return 1
    load_user_aliases()
    apply_theme(get_config().get("active_theme", "default"))
    print(f"{DIM}config reloaded{RESET}")
    return code


def cmd_opencode(args):
    exe = find_opencode()
    if not exe:
        if not ensure_opencode():
            return 1
        exe = find_opencode()
    return run_proc([exe] + args)


def cmd_menu(args):
    sel = _menu_picker()
    if sel in ("close", ""):
        return 0
    if sel == "opencode":
        return cmd_opencode([])
    if sel == "learn":
        return cmd_learn([])
    if sel == "fastfetch":
        if shutil.which("fastfetch"):
            return run_any("fastfetch")
        u = os.uname()
        print(f"{u.sysname} {u.release} {u.machine}")
        print(f"Host: {u.nodename}")
        print(f"User: {os.environ.get('USER')}")
        return 0
    if sel == "config":
        return cmd_config([])
    if sel == "hist":
        return cmd_hist([])
    if sel == "cdr":
        return cmd_cdr([])
    if sel == "rerun":
        if readline is None or readline.get_current_history_length() == 0:
            err("menu", "no previous command")
            return 1
        last = readline.get_history_item(readline.get_current_history_length())
        if last:
            print(f"{DIM}→ {last}{RESET}")
            return execute(last)
        return 1
    if sel == "install_opencode":
        return 0 if ensure_opencode() else 1
    if sel == "version":
        return cmd_version([])
    if sel == "clear":
        return cmd_clear([])
    if sel == "help":
        return cmd_help([])
    if sel == "themes":
        applied = run_theme_ui()
        if applied:
            apply_theme(applied)
            print(f"{GREEN}theme '{applied}' applied.{RESET}")
        return 0
    if sel == "packages":
        return cmd_pkg([])
    if sel == "system_update":
        return cmd_pkg(["update"])
    if sel in APP_MODULES:
        return cmd_app(sel, [])
    if sel == "update":
        return cmd_update([])
    if sel == "fish":
        print("falling through to your real shell")
        _QUIT = True
        return 0


def cmd_theme(args):
    if not args:
        applied = run_theme_ui()
        if applied:
            apply_theme(applied)
            print(f"{GREEN}theme '{applied}' applied.{RESET}")
        return 0
    sub = args[0]
    rest = args[1:]
    if sub == "list":
        active = get_config().get("active_theme", "default")
        for name in list_themes():
            print(f"{GREEN}●{RESET} {name}" if name == active else f"{DIM}○{RESET} {name}")
        return 0
    if sub == "apply" and rest:
        if set_active(rest[0]):
            apply_theme(rest[0])
            print(f"{GREEN}theme '{rest[0]}' applied.{RESET}")
            return 0
        err("theme", f"no such theme: {rest[0]}")
        return 1
    if sub in ("export", "show") and rest:
        theme = load_theme(rest[0])
        print(json.dumps(theme.to_dict(), indent=2))
        return 0
    if sub == "import" and rest:
        path = os.path.expanduser(rest[0])
        try:
            with open(path) as f:
                data = json.load(f)
            theme = Theme(data)
            if not theme.name or theme.name == "default":
                theme.data["name"] = os.path.splitext(os.path.basename(path))[0]
            if theme.name in list_themes():
                err("theme", f"'{theme.name}' already exists")
                return 1
            theme.save()
            print(f"{GREEN}imported theme '{theme.name}'.{RESET}")
            return 0
        except (OSError, ValueError) as e:
            err("theme", f"import failed: {e}")
            return 1
    if sub == "edit" and rest:
        if rest[0] not in list_themes():
            err("theme", f"no such theme: {rest[0]}")
            return 1
        applied = run_theme_ui()
        if applied:
            apply_theme(applied)
        return 0
    err("theme", "usage: theme | theme list | theme apply <name> | theme export <name> | theme import <file>")
    return 2


def _read_version(path: str) -> str:
    try:
        with open(path, errors="replace") as f:
            m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
            return m.group(1) if m else "?"
    except OSError:
        return "?"


UPDATE_FILES = ["minty.py", "install.sh"]


def _install_from_dir(src: str) -> int:
    here = here_dir()
    missing = [f for f in UPDATE_FILES if not os.path.isfile(os.path.join(src, f))]
    if missing:
        err("update", f"source has no {', '.join(missing)}")
        return 1
    old = _read_version(os.path.join(here, "minty.py"))
    new = _read_version(os.path.join(src, "minty.py"))
    for f in UPDATE_FILES:
        try:
            shutil.copy2(os.path.join(src, f), os.path.join(here, f))
        except OSError as e:
            err("update", f"failed to copy {f}: {e}")
            return 1
    print(f"{GREEN}minty updated: {old} -> {new}{RESET}")
    print(f"{DIM}restart minty to use the new version (or type 'exit' and run minty again){RESET}")
    return 0


def _install_from_github(src: str) -> int:
    if not shutil.which("git"):
        err("update", "git is not installed, cannot update from github")
        return 1
    s = src.strip()
    if s.startswith("git@") or s.startswith("https://") or s.startswith("http://"):
        url = s
    else:
        s = s.rstrip("/")
        if s.startswith("github.com/"):
            s = s[len("github.com/"):]
        if "/" not in s:
            err("update", f"expected a github repo like 'owner/repo', got '{src}'")
            return 1
        url = f"https://github.com/{s}.git"
    tmp = tempfile.mkdtemp(prefix="minty-update-")
    try:
        print(f"{DIM}cloning {url} ...{RESET}")
        try:
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", url, tmp],
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            err("update", "git is not installed")
            return 1
        if proc.returncode != 0:
            err("update", "clone failed: " + (proc.stderr or b"").decode(errors="replace").strip()[:200])
            return 1
        return _install_from_dir(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_update(args):
    cfg = get_config()
    up = cfg.get("update", {}) or {}
    src_type = up.get("type", "")
    src = up.get("source", "")
    show_check = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--local", "--github", "--source"):
            if i + 1 >= len(args):
                err("update", f"{a} needs a value")
                return 1
            src = args[i + 1]
            src_type = "local" if a == "--local" else "github" if a == "--github" else "auto"
            i += 2
        elif a == "--check":
            show_check = True
            i += 1
        else:
            err("update", f"unknown option: {a}")
            return 1
    if src_type == "auto":
        src_type = "github" if re.match(r"^(https?://|git@|github\.com/)", src) else "local"
    if not src:
        err("update", "no update source configured. Run: minty update --source <path or github repo>")
        return 1
    up = {"type": src_type, "source": src}
    cfg["update"] = up
    save_config(cfg)
    if show_check:
        print(f"{DIM}update source: {src_type} -> {src}{RESET}")
        return 0
    if src_type == "github":
        code = _install_from_github(src)
    else:
        code = _install_from_dir(os.path.expanduser(src))
    if code == 0:
        save_config(cfg)
    return code


def cmd_pkg(args):
    try:
        return _pkg_cli(args)
    except KeyboardInterrupt:
        print()
        return 130


# --------------------------------------------------------------------------
# minty terminal: a real GTK3 + VTE terminal emulator, so minty can be its
# own terminal window.  Run it with:  minty terminal
# --------------------------------------------------------------------------

_GUI_CACHE: dict = {}


def _terminal_available() -> bool:
    """True when GTK3 + VTE can be imported (pygobject + vte3 installed)."""
    if "ok" not in _GUI_CACHE:
        try:
            _gi = __import__("gi")
            _gi.require_version("Gtk", "3.0")
            _gi.require_version("Vte", "2.91")
            from gi.repository import Vte  # noqa: F401

            _GUI_CACHE["ok"] = True
        except Exception:
            _GUI_CACHE["ok"] = False
    return _GUI_CACHE["ok"]


def _terminal_deps_hint() -> str:
    return "the minty terminal needs GTK3 + VTE.\n" \
           "install with: sudo pacman -S vte3  (python-gobject is usually already present)"


def _rgba(r: int, g: int, b: int):
    from gi.repository import Gdk

    return Gdk.RGBA(max(0, min(255, r)) / 255.0,
                    max(0, min(255, g)) / 255.0,
                    max(0, min(255, b)) / 255.0,
                    1.0)


def _parse_hex(value, default):
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
        except ValueError:
            pass
    return default


def _terminal_palette(theme: Theme) -> list:
    """Build the 16-colour VTE palette from the active minty theme."""
    c = theme.colors

    def col(key: str, fallback: str):
        return _rgba(*color_rgb(c.get(key, fallback)))

    return [
        _rgba(*ANSI_TO_RGB[30]),                       # 0  black
        col("red", "31"),                              # 1  red
        col("green", "32"),                            # 2  green
        col("yellow", "33"),                           # 3  yellow
        col("blue", "34"),                             # 4  blue
        col("magenta", "35"),                          # 5  magenta
        col("cyan", "36"),                             # 6  cyan
        _rgba(*ANSI_TO_RGB[37]),                       # 7  white
        _rgba(*ANSI_TO_RGB[90]),                       # 8  bright black
        col("bright_red", "91"),                       # 9  bright red
        col("bright_green", "92"),                     # 10 bright green
        _rgba(*ANSI_TO_RGB[93]),                       # 11 bright yellow
        _rgba(*ANSI_TO_RGB[94]),                       # 12 bright blue
        col("bright_magenta", "95"),                   # 13 bright magenta
        col("bright_cyan", "96"),                      # 14 bright cyan
        _rgba(*ANSI_TO_RGB[97]),                       # 15 bright white
    ]


def _terminal_font(size=None) -> str:
    s = settings()
    size = size or int(s.get("terminal_font_size", 13))
    return "%s %s" % (s.get("terminal_font", "monospace"), int(size))


def _spawn_detached() -> None:
    """Open a minty terminal window as an independent process."""
    devnull = os.open(os.devnull, os.O_RDWR)
    try:
        subprocess.Popen(
            [sys.executable, os.path.realpath(__file__), "terminal"],
            stdin=devnull,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        pass
    finally:
        os.close(devnull)


def _bind_terminal_keys(term, window) -> None:
    from gi.repository import Gdk, Gtk, Pango

    def on_key(widget, event, window):
        key = Gdk.keyval_name(event.keyval) or ""
        mods = event.state
        ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
        if ctrl and shift:
            if key in ("C", "c"):
                term.copy_clipboard()
                return True
            if key in ("V", "v"):
                term.paste_clipboard()
                return True
            if key in ("N", "n"):
                _spawn_detached()
                return True
            if key in ("Q", "q"):
                Gtk.main_quit()
                return True
        elif ctrl:
            s = settings()
            if key in ("plus", "equal", "KP_Add"):
                size = min(40, int(s.get("terminal_font_size", 13)) + 1)
                s["terminal_font_size"] = size
                term.set_font(Pango.FontDescription.from_string(_terminal_font(size)))
                return True
            if key == "minus":
                size = max(6, int(s.get("terminal_font_size", 13)) - 1)
                s["terminal_font_size"] = size
                term.set_font(Pango.FontDescription.from_string(_terminal_font(size)))
                return True
            if key == "0":
                size = int(s.get("terminal_font_size", 13))
                term.set_font(Pango.FontDescription.from_string(_terminal_font(size)))
                return True
        return False

    term.connect("key-press-event", on_key, window)


def run_terminal_gui(cwd: str | None = None) -> int:
    """Open a minty terminal window. Blocks until the window closes."""
    try:
        _gi = __import__("gi")
        _gi.require_version("Gtk", "3.0")
        _gi.require_version("Vte", "2.91")
        from gi.repository import Gtk, GLib, Gdk, Pango, Vte
    except Exception as e:
        err("terminal", f"{_terminal_deps_hint()}  ({e})")
        return 1

    theme = load_theme(ACTIVE_THEME)
    s = settings()

    window = Gtk.Window()
    window.set_title("minty")
    window.set_default_size(int(s.get("terminal_width", 980)),
                            int(s.get("terminal_height", 620)))
    window.set_icon_name("utilities-terminal")

    term = Vte.Terminal()
    term.set_scrollback_lines(int(s.get("terminal_scrollback", 10000)))
    term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
    term.set_mouse_autohide(True)
    term.set_font(Pango.FontDescription.from_string(_terminal_font()))
    bg = _parse_hex(s.get("terminal_bg"), (10, 12, 16))
    fg = _parse_hex(s.get("terminal_fg"), (229, 229, 229))
    term.set_colors(_rgba(*fg), _rgba(*bg), _terminal_palette(theme))

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    scrolled.add(term)
    window.add(scrolled)

    term.connect("window-title-changed",
                 lambda t, *a: window.set_title(t or "minty"))
    term.connect("child-exited", lambda *a: Gtk.main_quit())
    window.connect("destroy", lambda *a: Gtk.main_quit())
    _bind_terminal_keys(term, window)

    envv = dict(os.environ)
    envv.setdefault("TERM", "xterm-256color")
    argv = [sys.executable, os.path.realpath(__file__)]
    try:
        import warnings as _w
        with _w.catch_warnings():
            _w.simplefilter("ignore", DeprecationWarning)
            result = term.spawn_sync(
                Vte.PtyFlags.DEFAULT,
                cwd or os.getcwd(),
                argv,
                ["%s=%s" % (k, v) for k, v in envv.items()],
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                None,
            )
    except TypeError:
        err("terminal", "this VTE version changed its spawn API; "
                        "upgrade with: sudo pacman -S vte3")
        return 1
    ok = result[0] if isinstance(result, tuple) else result
    if not ok:
        err("terminal", "failed to start minty inside the terminal")
        return 1

    window.show_all()
    Gtk.main()
    return 0


def cmd_terminal(args):
    """Open a minty terminal window (a real GTK3/VTE terminal emulator)."""
    if not _terminal_available():
        err("terminal", _terminal_deps_hint())
        return 1
    _spawn_detached()
    print(f"{DIM}opened a minty terminal window — exit it with 'exit'.{RESET}")
    return 0


APP_MODULES = {
    "tmux": _tmux_cli,
    "vms": _vm_cli,
    "svc": _svc_cli,
    "proc": _proc_cli,
    "net": _net_cli,
}


def cmd_app(name: str, args: list[str]) -> int:
    try:
        return APP_MODULES[name](args)
    except KeyboardInterrupt:
        print()
        return 130


def cmd_exit(args):
    global _QUIT
    print(f"{DIM}bye from minty{RESET}")
    _QUIT = True
    return 0


def _fallthrough():
    """Hand over the terminal to the user's real shell (like the old minty.sh)."""
    sys.stdout.flush()
    sys.stderr.flush()
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        os.execvp(shell, [shell, "-l"])
    except OSError:
        pass


COMMANDS = {
    "help": ("Show this help message", cmd_help),
    "echo": ("Print text to the screen", cmd_echo),
    "pwd": ("Print current working directory", cmd_pwd),
    "ls": ("List files (try ls -l -h)", cmd_ls),
    "cd": ("Change directory (cd - goes back)", cmd_cd),
    "mkdir": ("Create a directory", cmd_mkdir),
    "rm": ("Remove a file or directory (-r for dirs)", cmd_rm),
    "cat": ("Print contents of a file (-n numbers)", cmd_cat),
    "touch": ("Create an empty file", cmd_touch),
    "cp": ("Copy a file or directory", cmd_cp),
    "mv": ("Move or rename a file", cmd_mv),
    "grep": ("Search text for a pattern (-i, -n)", cmd_grep),
    "head": ("Print the first lines of a file", cmd_head),
    "tail": ("Print the last lines of a file", cmd_tail),
    "tree": ("Print a directory tree (-L depth)", cmd_tree),
    "clear": ("Clear the screen", cmd_clear),
    "date": ("Show the current date and time", cmd_date),
    "whoami": ("Show current user", cmd_whoami),
    "uname": ("Show system information", cmd_uname),
    "history": ("Show command history (-c clears)", cmd_history),
    "which": ("Show where a program is installed", cmd_which),
    "env": ("Show all environment variables", cmd_env),
    "export": ("Set an environment variable", cmd_export),
    "alias": ("List or create aliases", cmd_alias),
    "unalias": ("Remove an alias (-a clears all)", cmd_unalias),
    "version": ("Show minty version", cmd_version),
    "z": ("Jump to a frequently-visited directory", cmd_z),
    "cdr": ("Pick a recently-visited directory", cmd_cdr),
    "trash": ("Move files to the trash instead of deleting", cmd_trash),
    "hist": ("Browse command history (like Ctrl+R)", cmd_hist),
    "config": ("Open the persistent minty config in your editor", cmd_config),
    "opencode": ("Start the OpenCode AI assistant (bundled)", cmd_opencode),
    "learn": ("Open the code guide with how-to snippets", cmd_learn),
    "menu": ("Open the side menu (or press Ctrl+T)", cmd_menu),
    "theme": ("Open the visual theme app (theme list/apply/export/import)", cmd_theme),
    "pkg": ("Package manager: pkg search/install/remove/update", cmd_pkg),
    "tmux": ("Manage tmux sessions (visual manager)", lambda a: cmd_app("tmux", a)),
    "vms": ("Create/start/manage virtual machines", lambda a: cmd_app("vms", a)),
    "svc": ("Manage systemd services", lambda a: cmd_app("svc", a)),
    "proc": ("Process manager (top/htop-style)", lambda a: cmd_app("proc", a)),
    "net": ("Network and wifi manager", lambda a: cmd_app("net", a)),
    "terminal": ("Open the minty terminal window (its own GTK3/VTE terminal)", cmd_terminal),
    "update": ("Update minty from a local path or github repo", cmd_update),
    "exit": ("Leave the terminal", cmd_exit),
    "quit": ("Leave the terminal", cmd_exit),
}

PATH_PROGRAMS = set()
for _d in os.environ.get("PATH", "").split(os.pathsep):
    if os.path.isdir(_d):
        try:
            PATH_PROGRAMS.update(os.listdir(_d))
        except OSError:
            pass

DIR_COMMANDS = {"cd", "z", "ls", "cat", "mkdir", "rm", "cp", "mv", "touch", "head", "tail", "tree", "grep"}


def qname(name: str) -> str:
    if re.search(r"[\s|&;<>$`'\"()*?\[\]~]", name):
        return shlex.quote(name)
    return name


def complete(text, state):
    line = readline.get_line_buffer()
    beg = readline.get_begidx()
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    first = tokens[0] if tokens else ""
    candidates = []
    if beg == 0:
        names = set(COMMANDS) | set(ALIASES) | PATH_PROGRAMS
        candidates = [c for c in sorted(names) if c.startswith(text)]
    elif first in DIR_COMMANDS:
        d, _, base = text.rpartition("/")
        d = d or "."
        try:
            entries = os.listdir(d)
        except OSError:
            entries = []
        for e in entries:
            if not e.startswith(base):
                continue
            cand = e if d == "." else os.path.join(d, e)
            if os.path.isdir(os.path.join(d, e)):
                cand += "/"
            candidates.append(qname(cand))
        candidates = [c for c in candidates if c.startswith(text) or text.startswith(c.rstrip("/"))]
    else:
        candidates = [c for c in sorted(PATH_PROGRAMS) if c.startswith(text)]
    return candidates[state] if state < len(candidates) else None


def resolve_bang(line):
    if readline is None:
        err("minty", "history is unavailable")
        return None
    total = readline.get_current_history_length()
    if re.fullmatch(r"!!", line):
        if total > 0:
            return readline.get_history_item(total)
        err("minty", "no previous command")
        return None
    m = re.fullmatch(r"!(\d+)", line)
    if m:
        idx = int(m.group(1))
        if 0 < idx <= total:
            return readline.get_history_item(idx)
        err("minty", f"history index {idx} out of range")
        return None
    return None


def execute(line: str) -> int:
    global LAST_EXIT
    line = line.strip()
    if not line or line.startswith("#"):
        return 0
    if line.startswith("!"):
        resolved = resolve_bang(line)
        if resolved is None:
            return 1
        line = resolved.strip()
        print(f"{DIM}→ {line}{RESET}")
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line, re.S)
    if m and not has_shell_ops(line):
        os.environ[m.group(1)] = m.group(2)
        LAST_EXIT = 0
        return 0
    if has_shell_ops(line):
        return run_any(line)
    try:
        parts = shlex.split(line)
    except ValueError as e:
        err("minty", f"unbalanced quotes or escapes: {e}")
        LAST_EXIT = 2
        return 2
    if not parts:
        return 0
    name = parts[0]
    if name in ALIASES:
        suffix = " ".join(shlex.quote(p) for p in parts[1:]) if len(parts) > 1 else ""
        newline = ALIASES[name] + (f" {suffix}" if suffix else "")
        try:
            parts = shlex.split(newline)
        except ValueError:
            pass
        name = parts[0]
    if name in COMMANDS:
        args = []
        for p in parts[1:]:
            args.extend(expand_glob(p))
        args = [expand_arg(a) for a in args]
        try:
            code = COMMANDS[name][1](args)
        except KeyboardInterrupt:
            print()
            code = 130
        except BrokenPipeError:
            code = 141
        LAST_EXIT = code
        return code
    if shutil.which(name) is not None:
        return run_any(line)
    return _not_found(name, parts)


def _not_found(name: str, parts: list[str]) -> int:
    pool = sorted(set(COMMANDS) | set(ALIASES) | PATH_PROGRAMS)
    sugg = difflib.get_close_matches(name, pool, n=3, cutoff=0.55)
    print(f"{B_RED}minty{RESET}: command not found: {name}")
    if sugg:
        print(f"{DIM}did you mean:{RESET}  " + "  ".join(f"{BOLD}{s}{RESET}" for s in sugg))
    elif (sys.stdin.isatty()
          and any(shutil.which(p) for p in ("paru", "yay", "pacman", "apt"))
          and settings().get("suggest_install", True)):
        try:
            resp = input(f"install '{name}' with your package manager? [y/N] ")
        except (KeyboardInterrupt, EOFError):
            print()
            return 130
        if resp.strip().lower() in ("y", "yes"):
            return cmd_pkg(["install", name])
    return 127


def setup_readline():
    if readline is None:
        return
    readline.set_completer(complete)
    readline.set_completer_delims(" \t\n")
    try:
        readline.parse_and_bind("set show-all-if-ambiguous on")
        readline.parse_and_bind("set completion-ignore-case on")
        readline.parse_and_bind("set colored-stats on")
        readline.parse_and_bind("set menu-complete-display-prefix on")
    except Exception:
        pass
    for bind in (
        'tab: complete',
        '"\\C-t": "menu\\n"',
        '"\\C-r": "__mintyhist__\\n"',
        '"\\C-l": clear-screen',
        '"\\C-a": beginning-of-line',
        '"\\C-e": end-of-line',
        '"\\C-u": unix-line-discard',
        '"\\C-k": kill-line',
        '"\\C-w": unix-word-rubout',
        '"\\C-p": previous-history',
        '"\\C-n": next-history',
    ):
        try:
            readline.parse_and_bind(bind)
        except Exception:
            pass


def load_history():
    if readline is None:
        return
    try:
        readline.read_history_file(HISTFILE)
    except OSError:
        pass
    readline.set_history_length(MAXHIST)


@atexit.register
def save_history():
    if readline is None:
        return
    try:
        total = readline.get_current_history_length()
        items = [readline.get_history_item(i) for i in range(1, total + 1)]
        seen = set()
        uniq = []
        for it in items:
            if it and it not in seen:
                seen.add(it)
                uniq.append(it)
        with open(HISTFILE, "w") as f:
            f.write("\n".join(uniq[-MAXHIST:]) + "\n")
    except OSError:
        pass


def banner() -> str:
    return f"""{RESET}
{BOLD}{GREEN} __  __ _ _   _ _______   __{RESET}
{BOLD}{GREEN}|  \\/  (_) | | |_   _\\ \\ / /{RESET}
{BOLD}{GREEN}| |\\/| | | |_| | | |  \\ V /{RESET}
{BOLD}{GREEN}| |  | | |  _  | | |   | |{RESET}
{BOLD}{GREEN}|_|  |_|_|_| |_| |_|   |_|{RESET}
"""


@atexit.register
def save_last_dir():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LASTDIR_FILE, "w") as f:
            f.write(os.getcwd())
    except OSError:
        pass


def _restore_cwd() -> None:
    try:
        with open(LASTDIR_FILE) as f:
            last = f.read().strip()
        if last and os.path.isdir(last):
            os.chdir(last)
    except OSError:
        pass


def _handle_hist_line(line: str) -> None:
    _, _, after = line.partition(HIST_SENTINEL)
    try:
        n = readline.get_current_history_length()
        if n > 0 and HIST_SENTINEL in (readline.get_history_item(n) or ""):
            readline.remove_history_item(n - 1)
    except Exception:
        pass
    picked = run_history_picker(_history_items(), after.strip())
    if picked is not None:
        prefill_input(picked)
        print(f"{DIM}→ {picked}{RESET}")


def main():
    global _QUIT
    if len(sys.argv) > 1 and sys.argv[1] in ("terminal", "--terminal"):
        sys.argv = [sys.argv[0]]
        raise SystemExit(run_terminal_gui())
    if len(sys.argv) > 1 and sys.argv[1] in ("learn", "--learn"):
        raise SystemExit(cmd_learn(sys.argv[2:]))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    cfg = get_config()
    load_user_aliases()
    apply_theme(cfg.get("active_theme", "default"))
    if settings().get("restore_cwd", True):
        _restore_cwd()
    load_history()
    setup_readline()

    if find_opencode() is None:
        ensure_opencode()

    theme = load_theme(ACTIVE_THEME)
    if theme.settings.get("show_banner", True):
        print(banner())
    print(f"{BOLD}minty v{VERSION}{RESET} — type 'help' for commands, 'exit' to quit.  {DIM}(theme: {ACTIVE_THEME}){RESET}")
    if theme.settings.get("show_hint", True):
        print(f"{DIM}Ctrl+T menu · Ctrl+R history · !! rerun · z jump · learn · tmux vms svc proc net{RESET}")

    global LAST_DURATION
    while True:
        try:
            line = input(prompt())
        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            print()
            print(f"{DIM}bye from minty{RESET}")
            _QUIT = True
            break
        if HIST_SENTINEL in line:
            _handle_hist_line(line)
            continue
        if not line.strip():
            continue
        t0 = time.time()
        try:
            execute(line)
        except BrokenPipeError:
            return
        if _QUIT:
            break
        LAST_DURATION = time.time() - t0
        threshold = float(settings().get("notify_threshold", 5.0))
        if LAST_DURATION >= threshold and sys.stdin.isatty():
            notify("minty", f"'{line.strip()}' finished in {LAST_DURATION:.1f}s")

    _fallthrough()



if __name__ == "__main__":
    main()
