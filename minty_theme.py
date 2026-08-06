#!/usr/bin/env python3
"""minty themes - themes, config, and the visual theme app for minty.

Theme files are JSON documents kept in ~/.config/minty/themes/<name>.json.
A theme can control every color, the prompt, aliases, and settings.

The visual app (run_theme_ui) is a curses picker where you can browse,
preview, apply, edit (RGB), create, import and export themes.
"""

import curses
import json
import os
import shutil
import subprocess
import sys
import tempfile

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "minty")
THEME_DIR = os.path.join(CONFIG_DIR, "themes")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
THEME_VERSION = 1

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"

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


# --------------------------------------------------------------------------
# Non-visual CLI so themes work outside the curses app too:
#   minty_theme.py list | apply <name> | export <name> | import <file> | show <name>
# --------------------------------------------------------------------------

def _cli(argv):
    if len(argv) < 2 or argv[1] in ("edit", "ui", "app"):
        run_theme_ui()
        return 0
    cmd = argv[1]
    if cmd == "list":
        active = get_config().get("active_theme", "default")
        for name in list_themes():
            mark = " *" if name == active else ""
            print(f"{name}{mark}")
        return 0
    if cmd in ("apply", "use") and len(argv) > 2:
        return 0 if set_active(argv[2]) else 1
    if cmd == "export" and len(argv) > 2:
        theme = load_theme(argv[2])
        print(json.dumps(theme.to_dict(), indent=2))
        return 0
    if cmd == "show" and len(argv) > 2:
        theme = load_theme(argv[2])
        print(json.dumps(theme.to_dict(), indent=2))
        return 0
    if cmd == "import" and len(argv) > 2:
        path = os.path.expanduser(argv[2])
        try:
            with open(path) as f:
                data = json.load(f)
            theme = Theme(data)
            if not theme.name or theme.name == "default":
                theme.data["name"] = os.path.splitext(os.path.basename(path))[0]
            if theme.name in list_themes():
                print(f"theme '{theme.name}' already exists", file=sys.stderr)
                return 1
            theme.save()
            print(f"imported '{theme.name}'")
            return 0
        except (OSError, ValueError) as e:
            print(f"import failed: {e}", file=sys.stderr)
            return 1
    print("usage: minty_theme.py [list|apply <name>|export <name>|import <file>|show <name>]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
