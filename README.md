# minty

A tiny shell that runs in your real terminal. Everything lives in one file:
the shell, visual themes, a package manager, a tmux session manager, a VM
manager, a systemd service manager, a process manager, a network/wifi manager
and an fzf-style history picker.

## Requirements

- Python 3
- A terminal emulator (kitty recommended, but any terminal works)
- OpenCode AI is bundled automatically on first run if not present
- For the built-in minty terminal: `vte3` (Arch: `sudo pacman -S vte3`)

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
command in `~/.local/bin`, add a desktop launcher, and (if kitty is installed)
make minty the shell that kitty opens.

## Use

- Run `minty` in a terminal, or open a new kitty window
- `terminal` — open minty in its own terminal window (its own GTK3/VTE
  terminal emulator, colours matched to your theme)
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
