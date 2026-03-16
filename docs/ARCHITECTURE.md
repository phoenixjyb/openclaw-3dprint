# OpenClaw 3D Print — System Architecture

## 1. Overview

OpenClaw 3D Print is an end-to-end pipeline that turns natural language chat messages into physical 3D-printed objects. The system accepts text input from multiple channels (Telegram, Feishu, or the OpenClaw Agent HTTP API), optionally enriches the prompt via an LLM, generates a 3D mesh through cloud APIs, slices the model locally or remotely, and sends the result to a Bambu Lab printer over the local network.

**Core flow:** Chat Input → LLM Enrichment (optional) → 3D Mesh Generation → Slicing → Printer Upload → Physical Object

---

## 2. System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         INPUT CHANNELS                              │
│                                                                     │
│   ┌──────────┐   ┌──────────┐   ┌───────────────┐   ┌──────────┐  │
│   │ Telegram  │   │  Feishu  │   │ OpenClaw Agent│   │   CLI    │  │
│   │   Bot     │   │   Bot    │   │  (HTTP API)   │   │  Manual  │  │
│   └────┬─────┘   └────┬─────┘   └──────┬────────┘   └────┬─────┘  │
│        │               │                │                  │        │
└────────┼───────────────┼────────────────┼──────────────────┼────────┘
         │               │                │                  │
         ▼               ▼                ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     PROMPT PROCESSING                               │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │  [Optional] LLM Prompt Enrichment                           │   │
│   │  (OpenAI / Claude / OpenClaw Agent's own LLM)               │   │
│   │  "a cat" → "a cute sitting cat, smooth surface, solid       │   │
│   │   base, printable geometry, no thin features"               │   │
│   └──────────────────────┬──────────────────────────────────────┘   │
│                          │                                          │
└──────────────────────────┼──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   3D MESH GENERATION                                │
│                                                                     │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────┐   │
│   │ Tripo3D  │   │ Meshy.ai │   │ Magic3D  │   │ Rodin/Neural │   │
│   │(default) │   │(built-in)│   │(planned) │   │  (planned)   │   │
│   └────┬─────┘   └──────────┘   └──────────┘   └──────────────┘   │
│        │                                                            │
│        ▼                                                            │
│   model.glb / model.stl  (~5-50 MB)                                │
│                                                                     │
└────────┼────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       SLICING                                       │
│                                                                     │
│   Route 1: Local                    Route 2: Remote                 │
│   ┌──────────────────┐              ┌──────────────────────┐        │
│   │  PrusaSlicer CLI │              │  Windows PC (SSH)    │        │
│   │  (Mac / Linux)   │              │  Bambu Studio CLI    │        │
│   └────────┬─────────┘              └──────────┬───────────┘        │
│            │                                   │                    │
│            └──────────────┬────────────────────┘                    │
│                           │                                         │
│                           ▼                                         │
│                  model_sliced.3mf  (~2-20 MB)                       │
│                                                                     │
└───────────────────────────┼─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PRINTER DELIVERY                                  │
│                                                                     │
│   ┌───────────────────┐       ┌────────────────────┐                │
│   │  FTPS Upload      │       │  MQTT Print Command │               │
│   │  (port 990, TLS)  │──────▶│  (port 8883, TLS)  │               │
│   │  → /cache/*.3mf   │       │  → start print job  │               │
│   └───────────────────┘       └─────────┬──────────┘                │
│                                         │                           │
│                                         ▼                           │
│                              ┌──────────────────┐                   │
│                              │  Bambu Lab Printer│                  │
│                              │  (LAN Mode)       │                  │
│                              └────────┬─────────┘                   │
│                                       │                             │
│                                       ▼                             │
│                              ┌──────────────────┐                   │
│                              │  Physical Object  │                  │
│                              └──────────────────┘                   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Dependency Matrix

| Component | Required? | Purpose | Alternatives |
|-----------|-----------|---------|--------------|
| Python ≥ 3.11 | ✅ Must | Runtime environment | — |
| Tripo3D API key | ✅ Must | 3D mesh generation | Meshy.ai, Magic3D, Neural4D, Rodin AI |
| Bambu printer + LAN access | ✅ Must | Physical printing | — |
| PrusaSlicer | ✅ Must (local mode) | Slice mesh into printable .3mf | OrcaSlicer, or remote Bambu Studio |
| LLM API key | ❌ Optional | Prompt enrichment for better mesh results | OpenClaw agent's own LLM, or manual prompt |
| Telegram bot | ❌ Optional | Chat input channel | Feishu, OpenClaw agent HTTP API |
| Feishu bot | ❌ Optional | Chat input channel | Telegram, OpenClaw agent HTTP API |
| Windows PC | ❌ Optional | Remote slicing with Bambu Studio | Local PrusaSlicer (Mac/Linux) |
| paramiko (SSH) | ❌ Optional | Remote Windows communication | Not needed if local slicer |

---

## 4. Pipeline Routes

The system supports multiple pipeline routes depending on your setup and preferences.

### Route A: OpenClaw Agent (Recommended)

```
OpenClaw Agent → enriched_prompt → HTTP API (port 8765)
  → Tripo3D mesh generation
  → PrusaSlicer (local)
  → FTPS upload + MQTT print
```

**Requirements:** Tripo3D API key + Bambu printer + PrusaSlicer  
**LLM key NOT needed** — the OpenClaw Agent handles enrichment itself.

This is the recommended route because the agent already has its own LLM capabilities and produces high-quality enriched prompts tuned for 3D printing.

### Route B: Telegram Standalone

```
Telegram message → built-in LLM enrichment
  → Tripo3D mesh generation
  → PrusaSlicer (local)
  → FTPS upload + MQTT print
```

**Requirements:** Telegram bot token + LLM API key + Tripo3D API key + Bambu printer + PrusaSlicer

In this mode, the pipeline itself calls an LLM to enrich the user's raw text into a 3D-print-friendly prompt.

### Route C: Remote Windows Slicing

```
Same as Route A or B, but slicing is done on a remote Windows PC:
  → SSH into Windows machine
  → Bambu Studio CLI slices the model
  → .3mf transferred back
  → FTPS upload + MQTT print
```

**Requirements:** Everything from Route A or B + Windows PC + SSH access + paramiko

Use this route if you need Bambu Studio-specific slicing features or profiles not available in PrusaSlicer.

### Route D: Fully Manual CLI

```
Terminal → `3dprint request --enriched "a cute cat with solid base" description`
  → Tripo3D mesh generation
  → PrusaSlicer (local)
  → FTPS upload + MQTT print
```

**Requirements:** Tripo3D API key + Bambu printer + PrusaSlicer  
**No bot or agent needed.** The user provides the enriched prompt directly.

---

## 5. Supported Printers

| Printer | FTPS (990) | MQTT (8883) | LAN Mode | Notes |
|---------|-----------|------------|----------|-------|
| Bambu Lab X1 / X1 Carbon / X1E | ✅ | ✅ | ✅ | Needs SD card for LAN prints |
| Bambu Lab P1S / P1P | ✅ | ✅ | ✅ | Enable Developer Mode |
| Bambu Lab A1 / A1 Mini | ✅ | ✅ | ✅ | Entry-level, same protocol |

> **Note:** Any Bambu Lab printer with LAN mode and Developer Mode enabled should work. The printer must be on the same local network as the machine running the pipeline.
>
> - **Enable LAN mode:** Printer → Settings → Network → LAN Only Mode
> - **Enable Developer Mode:** Printer → Settings → Network → Developer Mode

---

## 6. Supported Mesh Providers

| Provider | Status | API | Price | Python SDK | Notes |
|----------|--------|-----|-------|-----------|-------|
| Tripo3D | ✅ Built-in | REST | ~$12/mo | Yes (`tripo3d`) | Default provider, fast, good quality |
| Meshy.ai | ✅ Built-in | REST | ~$16/mo | — | Team features, clean topology |
| Magic3D | 🔜 Planned | REST | ~$10/mo | — | Fastest generation |
| Rodin AI | 🔜 Planned | REST | ~$20/mo | — | Best for customization |
| Neural4D | 🔜 Planned | REST | ~$7/mo | — | Watertight meshes, print-ready |

---

## 7. Fallback & Error Handling

| Failure Scenario | Handling Strategy |
|-----------------|-------------------|
| **Mesh generation fails** | Retry up to 3 times with exponential backoff → notify user → cancel job |
| **Slicer not found** | Check alternative paths (Linux vs Mac default locations) → clear error message with install instructions |
| **Printer unreachable** | Retry FTPS connection 3 times → check MQTT connectivity → notify user with troubleshooting steps |
| **LLM unavailable (standalone mode)** | Return error: "Configure `OPENAI_API_KEY` or use `enriched_prompt` directly" |
| **Approval timeout** | 1 hour timeout → auto-cancel job → notify user |
| **Printer queue full** | Show queue position → wait for slot → notify user when available |

---

## 8. Network Ports

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 990 | FTPS (implicit TLS) | Pipeline → Printer | Upload `.3mf` files to printer |
| 8883 | MQTTS (TLS) | Pipeline ↔ Printer | Print commands + status monitoring |
| 8765 | HTTP | Agent → Pipeline | API requests (configurable) |
| 22 | SSH | Pipeline → Windows | Remote slicing (optional) |

---

## 9. Security

- **Secrets management:** All API keys, tokens, and credentials are stored in `~/.openclaw-3dprint/pipeline.env` — never committed to code or git.
- **Printer communication:** All traffic to the printer uses TLS encryption (FTPS for file upload, MQTTS for commands).
- **HTTP API binding:** The API server listens on `127.0.0.1` only (localhost). It is not exposed to the network.
- **Telegram bot authentication:** User IDs are validated against a configurable allowlist to prevent unauthorized access.
- **Git hygiene:** `.gitignore` excludes all `.env` files and the staging directory.

---

## 10. Printer Monitor & Notifications

A background **PrinterMonitor** subscribes to the printer's MQTT feed and sends real-time notifications for *all* print events — whether triggered by the pipeline or started manually from Bambu Studio.

### Monitored Events

| Event | Trigger | Notification |
|-------|---------|-------------|
| **Print started** | State change → `RUNNING` | Job name, estimated time |
| **Progress** | Every N% (configurable) | Percentage, time remaining |
| **Print finished** | `RUNNING` → `FINISH` | Total time, success |
| **Print failed** | State → `FAILED` | Error details |
| **Print paused** | `RUNNING` → `PAUSE` | Pause reason |
| **HMS alert** | Hardware/firmware alert | Alert code + description |

### Dual Notification Channels

- **Telegram** (ybcc) — via `python-telegram-bot` + ClashX proxy
- **Feishu** (chuan) — via Feishu Open API + ClashX proxy

Both channels fire independently; a failure in one does not block the other.

### macOS Deployment: MQTT TLS Proxy

On macOS, Homebrew Python is blocked from reaching LAN devices by **Local Network Privacy** (TCC). The pipeline works around this with a TLS-terminating TCP proxy:

```
Pipeline (brew Python)  ──plain TCP──▶  MQTT Proxy (system Python)  ──TLS──▶  Printer:8883
    localhost:18883                        /usr/bin/python3
```

**Critical:** The proxy must run as an **independent launchd agent**, not as a subprocess of the pipeline. macOS inherits LAN restrictions from parent to child processes, so even `/usr/bin/python3` is blocked when spawned by brew Python.

| Component | launchd label | Managed by |
|-----------|--------------|------------|
| Pipeline | `com.openclaw-3dprint.pipeline` | `run-pipeline.zsh` → brew Python venv |
| MQTT Proxy | `com.openclaw-3dprint.mqtt-proxy` | `/usr/bin/python3 scripts/mqtt-proxy.py` |

---

## 11. File Flow

```
User prompt (text)
  → enriched_prompt (text)
  → model.glb / model.stl       (3D mesh, ~5-50 MB)
  → model_sliced.3mf             (sliced print file, ~2-20 MB)
  → uploaded to printer /cache/  (via FTPS)
  → MQTT print command           (start job)
  → physical object
```

All intermediate files are stored in `~/.openclaw-3dprint/staging/` and automatically cleaned up after the print job completes successfully.
