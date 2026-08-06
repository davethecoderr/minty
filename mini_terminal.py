#!/usr/bin/env python3
"""minty - a tiny shell that runs in your real terminal."""

import atexit
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

from minty_hist import run_history_picker
from minty_theme import (
    CONFIG_DIR,
    CONFIG_FILE,
    THEME_DIR,
    Theme,
    active_theme,
    delete_theme,
    get_config,
    list_themes,
    load_theme,
    run_theme_ui,
    save_config,
    save_theme,
    set_active,
)

VERSION = "3.0"
HISTFILE = os.path.expanduser("~/.minty_history")
MAXHIST = 2000
HIST_SENTINEL = "__mintyhist__"
DIRS_FILE = os.path.join(CONFIG_DIR, "dirs.json")
LASTDIR_FILE = os.path.join(CONFIG_DIR, "last_dir")

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

ACTIVE_THEME = "default"

LAST_EXIT = 0
OLDPWD = None
LAST_DURATION = 0.0
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
    here = os.path.dirname(os.path.abspath(__file__))
    menu_py = os.path.join(here, "minty_menu.py")
    outfile = os.path.join(tempfile.gettempdir(), "minty_menu.out")
    try:
        os.remove(outfile)
    except OSError:
        pass
    try:
        subprocess.run([sys.executable, menu_py, outfile])
    except KeyboardInterrupt:
        print()
        return 130
    try:
        with open(outfile) as f:
            sel = f.read().strip()
    except OSError:
        sel = ""
    if sel == "opencode":
        return cmd_opencode([])
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
        sys.exit(0)
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


UPDATE_FILES = ["mini_terminal.py", "minty_menu.py", "minty_theme.py", "minty_pkg.py", "minty_hist.py", "minty_tmux.py", "minty_vm.py", "minty_svc.py", "minty_proc.py", "minty_net.py", "minty.sh", "install.sh"]


def _install_from_dir(src: str) -> int:
    here = here_dir()
    missing = [f for f in UPDATE_FILES if not os.path.isfile(os.path.join(src, f))]
    if missing:
        err("update", f"source has no {', '.join(missing)}")
        return 1
    old = _read_version(os.path.join(here, "mini_terminal.py"))
    new = _read_version(os.path.join(src, "mini_terminal.py"))
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
    here = here_dir()
    pkg_py = os.path.join(here, "minty_pkg.py")
    if not os.path.isfile(pkg_py):
        err("pkg", "minty_pkg.py is missing")
        return 1
    try:
        return subprocess.run([sys.executable, pkg_py] + args).returncode
    except KeyboardInterrupt:
        print()
        return 130


APP_MODULES = {
    "tmux": "minty_tmux.py",
    "vms": "minty_vm.py",
    "svc": "minty_svc.py",
    "proc": "minty_proc.py",
    "net": "minty_net.py",
}


def cmd_app(name: str, args: list[str]) -> int:
    here = here_dir()
    mod = os.path.join(here, APP_MODULES[name])
    if not os.path.isfile(mod):
        err(name, f"{APP_MODULES[name]} is missing")
        return 1
    try:
        return subprocess.run([sys.executable, mod] + args).returncode
    except KeyboardInterrupt:
        print()
        return 130


def cmd_exit(args):
    print(f"{DIM}bye from minty{RESET}")
    sys.exit(0)


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
    "menu": ("Open the side menu (or press Ctrl+T)", cmd_menu),
    "theme": ("Open the visual theme app (theme list/apply/export/import)", cmd_theme),
    "pkg": ("Package manager: pkg search/install/remove/update", cmd_pkg),
    "tmux": ("Manage tmux sessions (visual manager)", lambda a: cmd_app("tmux", a)),
    "vms": ("Create/start/manage virtual machines", lambda a: cmd_app("vms", a)),
    "svc": ("Manage systemd services", lambda a: cmd_app("svc", a)),
    "proc": ("Process manager (top/htop-style)", lambda a: cmd_app("proc", a)),
    "net": ("Network and wifi manager", lambda a: cmd_app("net", a)),
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
        print(f"{DIM}Ctrl+T menu · Ctrl+R history · !! rerun · z jump · tmux vms svc proc net{RESET}")

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
            return
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
        LAST_DURATION = time.time() - t0
        threshold = float(settings().get("notify_threshold", 5.0))
        if LAST_DURATION >= threshold and sys.stdin.isatty():
            notify("minty", f"'{line.strip()}' finished in {LAST_DURATION:.1f}s")


if __name__ == "__main__":
    main()
