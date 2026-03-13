# 🖨 openclaw-3dprint

**Turn chat messages into physical 3D prints — fully automated.**

An [OpenClaw](https://openclaw.ai) skill that orchestrates the entire text-to-3D-print pipeline:
natural language → LLM interpretation → 3D model generation → slicing → printing on a Bambu Lab printer.

```
User: "3D print me a small cinderella figurine"
  ↓
Pipeline: LLM enrichment → Tripo3D mesh → PrusaSlicer → Bambu P2S printer
  ↓
Result: Physical object on your print bed 🎉
```

## Architecture

```
┌──────────────────┐
│  Chat Message    │  (Telegram / Feishu / OpenClaw agent)
└────────┬─────────┘
         ▼
┌──────────────────┐
│  LLM Interpret   │  OpenAI-compatible API (GPT, Grok, Claude, etc.)
│  Enrich prompt   │  → detailed 3D modeling description
└────────┬─────────┘
         ▼  (approval)
┌──────────────────┐
│  Mesh Generate   │  Tripo3D or Meshy.ai API
│  Text → 3D model │  → .glb / .obj file
└────────┬─────────┘
         ▼  (approval)
┌──────────────────┐
│  Slice           │  PrusaSlicer (local) or Bambu Studio (remote)
│  3D model → gcode│  → .3mf file with print instructions
└────────┬─────────┘
         ▼  (approval)
┌──────────────────┐
│  Print           │  FTPS upload + MQTT command → Bambu printer
│  Send to printer │  Progress monitoring via MQTT subscription
└──────────────────┘
```

## Prerequisites

| Requirement | Version | How to get it |
|-------------|---------|---------------|
| **Python** | ≥ 3.11 | `brew install python@3.12` or [python.org](https://www.python.org/downloads/) |
| **PrusaSlicer** | any | `brew install --cask prusa-slicer` (macOS) or [download](https://www.prusa3d.com/page/prusaslicer_424/) |
| **Bambu Lab printer** | any | Must be on the same LAN with LAN mode enabled |
| **LLM API key** | — | [xAI/Grok](https://console.x.ai) (recommended) or [OpenAI](https://platform.openai.com/api-keys) |
| **Tripo3D API key** | — | Sign up at [tripo3d.ai](https://www.tripo3d.ai) → Dashboard → API Keys |
| **A chat channel** | — | Pick one: [Telegram BotFather](https://t.me/BotFather), [Feishu](https://open.feishu.cn/app), or just the HTTP API |

> 💡 **Finding your printer info:** On the printer's LCD screen, go to **Settings → Network** for the IP and Access Code, and **Settings → Device** for the Serial Number.

## Quick Start

### 1. Install

```bash
pip install openclaw-3dprint
```

Or from source:

```bash
git clone https://github.com/phoenixjyb/openclaw-3dprint.git
cd openclaw-3dprint
pip install -e .
```

Optional extras:

```bash
pip install "openclaw-3dprint[telegram]"     # + Telegram bot support
pip install "openclaw-3dprint[windows]"      # + remote Windows slicing via SSH
pip install "openclaw-3dprint[dev]"          # + pytest, ruff for development
```

> If you prefer `requirements.txt`: `pip install -r requirements.txt`

### 2. Install PrusaSlicer (for local slicing)

```bash
# macOS
brew install --cask prusa-slicer

# Linux — download from https://www.prusa3d.com/page/prusaslicer_424/
```

### 3. Configure

```bash
mkdir -p ~/.openclaw-3dprint
cp .env.example ~/.openclaw-3dprint/pipeline.env
```

Edit `~/.openclaw-3dprint/pipeline.env` with your settings:

```env
# LLM (any OpenAI-compatible API)
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.x.ai/v1
OPENAI_MODEL=grok-3

# 3D model generation
TRIPO_API_KEY=your-tripo-key

# Bambu printer
BAMBU_PRINTER_IP=192.168.1.100
BAMBU_PRINTER_SERIAL=your-serial
BAMBU_PRINTER_ACCESS_CODE=your-code
```

### 4. Run

```bash
# As an OpenClaw skill (HTTP API for agent integration)
openclaw-3dprint --mode feishu

# As a Telegram bot
openclaw-3dprint --mode telegram

# Both at once
openclaw-3dprint --mode dual
```

### 5. Use

Via the CLI (from OpenClaw agent or terminal):

```bash
3dprint request a small dragon figurine
3dprint status
3dprint approve <job_id>
```

Via Telegram: Send a message to your bot.

Via Feishu/OpenClaw: Your agent calls the HTTP API automatically.

## OpenClaw Skill Installation

If you use [OpenClaw](https://openclaw.ai), this package works as a skill:

1. Install the package: `pip install openclaw-3dprint`
2. Copy `SKILL.md` to your skills directory, or install via ClawHub:
   ```bash
   clawhub install 3dprint
   ```
3. The agent will automatically use the `3dprint` CLI when users ask to 3D print something.

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_MODE` | No | `feishu` | `feishu`, `telegram`, or `dual` |
| `OPENAI_API_KEY` | **Yes** | — | LLM API key |
| `OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | LLM API endpoint |
| `OPENAI_MODEL` | No | `gpt-4o` | LLM model name |
| `MESH_PROVIDER` | No | `tripo` | `tripo` or `meshy` |
| `TRIPO_API_KEY` | Yes* | — | Tripo3D key (*if using tripo) |
| `MESHY_API_KEY` | Yes* | — | Meshy.ai key (*if using meshy) |
| `SLICER_MODE` | No | `local` | `local` (PrusaSlicer) or `remote` (SSH to Windows) |
| `SLICER_PATH` | No | auto-detect | Path to slicer binary |
| `BAMBU_PRINTER_IP` | **Yes** | — | Printer IP on LAN |
| `BAMBU_PRINTER_SERIAL` | **Yes** | — | Printer serial number |
| `BAMBU_PRINTER_ACCESS_CODE` | **Yes** | — | Printer access code |
| `BAMBU_SEND_METHOD` | No | `ftp` | `ftp` (direct FTPS) or `studio` (Bambu Studio CLI) |
| `TELEGRAM_BOT_TOKEN` | Yes* | — | *If using telegram mode |
| `TELEGRAM_ALLOWED_USER_IDS` | No | — | Comma-separated Telegram user IDs |
| `FEISHU_APP_ID` | Yes* | — | *If using feishu mode |
| `FEISHU_APP_SECRET` | Yes* | — | *If using feishu mode |
| `FEISHU_CHAT_ID` | Yes* | — | *If using feishu mode |
| `FEISHU_API_PORT` | No | `8765` | HTTP API port |
| `STAGING_DIR` | No | `~/.openclaw-3dprint/staging` | Temp file directory |

## Remote Windows Slicing (Optional)

If you prefer Bambu Studio's slicer (better Bambu-specific profiles), you can run it on a Windows PC:

```env
SLICER_MODE=remote
WINDOWS_HOST=192.168.1.200
WINDOWS_USER=your-user
WINDOWS_SSH_KEY=~/.ssh/id_ed25519
```

The pipeline will SSH to the Windows machine, slice there, and copy back the result.

## Multi-User Printer Queue

Multiple users/agents can share one printer safely. The pipeline uses cross-process file locking
(`fcntl.flock`) to serialise print jobs. Users see their queue position while waiting.

## Project Structure

```
openclaw-3dprint/
├── SKILL.md                    # OpenClaw skill definition
├── README.md                   # This file
├── pyproject.toml              # Python package config
├── .env.example                # Config template
├── scripts/
│   └── 3dprint                 # CLI wrapper for agents
├── pipeline/
│   ├── __main__.py             # Entry point
│   ├── orchestrator.py         # Pipeline coordinator
│   ├── printer_queue.py        # Cross-process printer lock
│   ├── bot.py                  # Telegram bot
│   ├── feishu_bot.py           # HTTP API + Feishu messaging
│   ├── feishu_client.py        # Feishu API client
│   ├── models/
│   │   └── job.py              # PrintJob state machine
│   ├── services/
│   │   ├── openai_client.py    # LLM client
│   │   ├── tripo_client.py     # Tripo3D mesh generation
│   │   ├── meshy_client.py     # Meshy.ai mesh generation
│   │   ├── bambu_printer.py    # Direct FTPS + MQTT to printer
│   │   └── bambu_mqtt.py       # MQTT protocol helpers
│   ├── stages/
│   │   ├── llm_interpret.py    # Stage 1: prompt enrichment
│   │   ├── mesh_generate.py    # Stage 2: 3D model generation
│   │   ├── slice.py            # Stage 3: slicing
│   │   └── print_job.py        # Stage 4: send to printer
│   └── utils/
│       └── config.py           # Settings loader
└── tests/                      # Unit tests
```

## Development

```bash
git clone https://github.com/phoenixjyb/openclaw-3dprint.git
cd openclaw-3dprint
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check pipeline/
pytest tests/ -q
```

## Supported Printers

Currently tested with:
- **Bambu Lab P1S / P1P** — via FTPS (port 990) + MQTT (port 8883)
- **Bambu Lab X1 / X1C** — same protocol

Any Bambu Lab printer with LAN mode enabled should work. The printer must be on the same
network as the machine running the pipeline.

## License

MIT — see [pyproject.toml](pyproject.toml).
