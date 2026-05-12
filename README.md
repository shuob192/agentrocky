# agentrocky

> **Linux / Windows port** of [itmesneha/agentrocky](https://github.com/itmesneha/agentrocky).
> The original is a macOS app built with Swift/SwiftUI/AppKit.
> This fork rewrites it in Python + PyQt6 to run on Linux and Windows, keeping all the same features and behaviour.

A Linux / Windows desktop companion app that puts an animated pixel-art character on your screen — powered by [Claude Code](https://claude.ai/code).

Rocky walks along the edges of your screen. Click him to open a retro terminal-style chat window and talk to Claude or Codex directly from your desktop. When a task finishes, he celebrates with a little jazz dance.

---

## Features

- **Animated sprite** — Rocky walks around all four edges of your screen with smooth 60fps motion and 8fps sprite animation
- **Jazz celebrations** — Rocky dances when an agent finishes a task, and spontaneously jazzes out every 15–45 seconds while idle
- **Speech bubbles** — Rocky shows status messages while working ("working", "building", "thinking") and celebrates when done ("rocky done!")
- **Retro terminal chat** — click Rocky to open a 420×520 dark-themed chat window with color-coded output:
  - Green for assistant responses
  - Cyan for tool calls
  - Red for errors
- **Persistent session** — the agent session survives the chat window being opened and closed
- **Live tool call visibility** — see exactly what Claude is doing as it runs commands and uses tools
- **Multi-agent support** — switch between Claude and Codex from the settings panel
- **Always-on-top** — floats above all windows; drag to reposition anywhere on screen

## Requirements

- **Linux** — X11 or Wayland (compositing window manager needed for transparency)
- **Windows** — Windows 10 or later (DWM compositing provides transparency)
- Python 3.9+
- PyQt6 (`pip install PyQt6`)
- [Claude Code CLI](https://claude.ai/code) installed and accessible on PATH
- Optional (Linux): `libnotify-bin` (`notify-send`) for desktop notifications

## Quick Start

```bash
git clone https://github.com/snehas/agentrocky.git
cd agentrocky
pip install PyQt6
python main.py
```

Rocky appears on the bottom edge of your screen — click him to start chatting.

The session runs with your home directory (`~`) as the working context, so Claude can run commands and tools relative to `~`.

### Wayland note (Linux)

If Rocky does not appear on top of other windows, try forcing the XCB backend:

```bash
QT_QPA_PLATFORM=xcb python main.py
```

## Usage

| Action | Result |
|--------|--------|
| Left-click | Open / close the chat window |
| Drag | Reposition Rocky anywhere on screen |
| Right-click | Sleep / wake Rocky |

## Sprite States

| State | Frames | Trigger |
|-------|--------|---------|
| Standing | `stand.png` | Chat window is open |
| Walking | `walkleft1.png`, `walkleft2.png` | Default movement (walks all four screen edges) |
| Jazz | `jazz1.png`, `jazz2.png`, `jazz3.png` | Task complete or random idle celebration |

## Architecture

| File | Purpose |
|------|---------|
| `main.py` | Entry point; creates the Qt application and launches Rocky |
| `rocky_state.py` | Shared state — position, direction, wall, chat visibility, speech bubbles |
| `agent_session.py` | Spawns and manages the `claude` or `codex` subprocess; parses stream-JSON over stdin/stdout |
| `rocky_window.py` | Floating transparent window; 60fps walk loop, physics, sprite rendering, speech bubbles |
| `chat_window.py` | Terminal-style chat UI with scrollable, color-coded message history and settings panel |

## How It Works

agentrocky launches Claude Code as a subprocess with stream-JSON I/O:

```
claude -p --output-format stream-json --input-format stream-json --verbose --dangerously-skip-permissions
```

Messages are serialized as newline-delimited JSON and streamed over stdin/stdout. The app parses Claude's output in real time, updating the chat log and triggering animations based on task lifecycle events (tool calls in progress, task complete).

The floating window is a transparent, borderless, always-on-top Qt widget using `WA_TranslucentBackground` — no taskbar entry, composited above other windows.

Settings (default agent, model, thinking level) are saved to `~/.config/agentrocky/settings.json`.

### Rocky's personality

The agent is wrapped with a persona system prompt defined in `rocky_persona.py`. This makes Claude (and Codex) respond as Rocky — an Eridian engineer from *Project Hail Mary* — with warm, slightly broken English and occasional musical chord asides. For Claude the prompt is appended via `--append-system-prompt`, preserving Claude Code's built-in coding instructions. For Codex it is prepended to each prompt call.

If you prefer a plain Claude or Codex experience, open `rocky_persona.py` and empty the `ROCKY_PERSONA` string.
