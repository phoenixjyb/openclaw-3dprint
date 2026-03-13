---
name: 3dprint
description: Turn natural language into 3D prints on a Bambu Lab printer. Use when the user asks to 3D print something, check printer status, or manage print jobs. Orchestrates LLM prompt enrichment, 3D model generation (Tripo3D), slicing (PrusaSlicer), and direct printer communication (FTPS/MQTT).
homepage: https://github.com/phoenixjyb/openclaw-3dprint
metadata:
  {
    "openclaw":
      {
        "emoji": "🖨",
        "os": ["macos", "linux"],
        "requires": { "bins": ["3dprint"] },
        "install":
          [
            {
              "id": "pip",
              "kind": "pip",
              "package": "openclaw-3dprint",
              "bins": ["3dprint"],
              "label": "Install openclaw-3dprint (pip)",
            },
          ],
      },
  }
---

# 3dprint — Text-to-3D-Print Pipeline

Turn a chat message like "print me a small vase" into a physical 3D print, fully automated.

## Quick Start

1. Install: `pip install openclaw-3dprint`
2. Copy config: `cp .env.example ~/.openclaw-3dprint/pipeline.env` and fill in your keys
3. Start the pipeline server: `openclaw-3dprint --mode feishu`
4. Use the CLI from OpenClaw: `3dprint request a small cinderella figurine`

## CLI Commands

```bash
3dprint request <description>   # Submit a new 3D print request
3dprint approve <job_id>        # Approve the current pipeline stage
3dprint reject <job_id>         # Reject/cancel the current stage
3dprint status [job_id]         # Show job status (all or specific)
3dprint queue                   # Show printer queue status
3dprint help                    # Show help
```

## Pipeline Stages

Each print job goes through these stages (approval required between each):

1. **LLM Interpretation** — enriches the user prompt into a detailed 3D modeling prompt
2. **3D Model Generation** — generates a mesh via Tripo3D (or Meshy.ai)
3. **Slicing** — slices the model with PrusaSlicer (local) or Bambu Studio (remote Windows)
4. **Printing** — uploads .3mf via FTPS and starts print via MQTT

## Configuration

All config is read from `~/.openclaw-3dprint/pipeline.env`. Required settings:

| Setting | Description |
|---------|-------------|
| `OPENAI_API_KEY` | LLM API key (OpenAI, xAI/Grok, etc.) |
| `TRIPO_API_KEY` | Tripo3D API key for mesh generation |
| `BAMBU_PRINTER_IP` | Printer IP address on local network |
| `BAMBU_PRINTER_SERIAL` | Printer serial number |
| `BAMBU_PRINTER_ACCESS_CODE` | Printer access code (from LCD screen) |

Optional settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `BOT_MODE` | `feishu` | Bot mode: `feishu`, `telegram`, or `dual` |
| `SLICER_MODE` | `local` | `local` (PrusaSlicer) or `remote` (Windows SSH) |
| `SLICER_PATH` | auto-detect | Path to PrusaSlicer binary |
| `BAMBU_SEND_METHOD` | `ftp` | `ftp` (direct) or `studio` (Bambu Studio CLI) |
| `MESH_PROVIDER` | `tripo` | `tripo` or `meshy` |

See `.env.example` for the full list.

## Printer Queue

Multiple agents/users share one printer safely. The pipeline uses cross-process locking
(`fcntl.flock`) so concurrent print requests are serialised. Users see their queue position
while waiting.

## Typical Agent Interaction

When a user says "3D print me a dragon":

1. Agent runs `3dprint request a dragon`
2. Pipeline returns job ID and starts LLM enrichment
3. Agent shows the enriched prompt and asks user to approve: `3dprint approve <id>`
4. After each approval, the next stage runs automatically
5. When printing starts, the agent reports progress via MQTT updates
6. User receives a "print complete" notification

## Requirements

- Python ≥ 3.11
- PrusaSlicer (for local slicing): `brew install --cask prusa-slicer`
- Network access to Bambu printer (same LAN, ports 990 + 8883)
- API keys: LLM provider + Tripo3D
