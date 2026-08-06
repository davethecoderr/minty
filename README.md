# minty

minty is its own terminal — a single file that brings the shell **and** a
real GTK3/VTE terminal emulator, so it doesn't need kitty or any other
terminal. It also packs visual themes, a package manager, a tmux session
manager, a VM manager, a systemd service manager, a process manager, a
network/wifi manager and an fzf-style history picker.

## Requirements

- Python 3
- For the built-in minty terminal: `vte3` + python-gobject
  (Arch: `sudo pacman -S vte3`)
- OpenCode AI is bundled automatically on first run if not present

## Install

### One-line install (from this repo)

```bash
curl -fsSL https://raw.githubusercontent.com/davethecoderr/minty/main/install.sh | bash
```

### Local install

Clone (or copy) the repo, then:

```bash
bash install.sh
```

Both methods copy `minty.py` into `~/.local/share/minty`, create a `minty`
command in `~/.local/bin` and add a desktop launcher. `minty` opens its own
terminal window — no kitty required.

## Use

- Launch the `minty terminal` desktop app, or run `minty terminal` in any
  terminal — it opens its own GTK3/VTE window with colours matched to your
  theme
- Run `minty` in an existing terminal to start the minty shell there
- `help` — list every built-in command
- `theme` — browse, apply, edit and share themes
- `pkg` — search / install / remove packages and update your system
- `tmux`, `vms`, `svc`, `proc`, `net` — visual managers for each
- `hist` or `Ctrl+R` — browse command history
- `Ctrl+T` — side menu
- `exit` — leave minty and fall through to your real shell

## Update

```bash
minty update --source davethecoderr/minty
```

## Uninstall

```bash
bash ~/.local/share/minty/install.sh --uninstall
```

## Credits

AI helped a lot building this. 🧠💚

