[English](README.md) | **中文**

# 🖨 openclaw-3dprint

**聊天消息一键变实物 —— 全自动 3D 打印。**

这是一个 [OpenClaw](https://openclaw.ai) 技能，可以完整串联从文字到实物的 3D 打印管道：
自然语言 → LLM 理解 → 3D 模型生成 → 切片 → 发送到 Bambu Lab 打印机。

```
用户："帮我 3D 打印一个小灰姑娘手办"
  ↓
管道：LLM 提示词增强 → Tripo3D 网格生成 → PrusaSlicer 切片 → Bambu P2S 打印机
  ↓
结果：打印平台上出现了实物 🎉
```

## 架构概览

```
┌──────────────────┐
│  Chat Message    │  (Telegram / 飞书 / OpenClaw 智能体)
└────────┬─────────┘
         ▼
┌──────────────────┐
│  LLM Interpret   │  兼容 OpenAI 的 API（GPT、Grok、Claude 等）
│  Enrich prompt   │  → 生成详细的 3D 建模描述
└────────┬─────────┘
         ▼  (审批)
┌──────────────────┐
│  Mesh Generate   │  Tripo3D 或 Meshy.ai API
│  Text → 3D model │  → 输出 .glb / .obj 文件
└────────┬─────────┘
         ▼  (审批)
┌──────────────────┐
│  Slice           │  PrusaSlicer（本地）或 Bambu Studio（远程）
│  3D model → gcode│  → 生成包含打印指令的 .3mf 文件
└────────┬─────────┘
         ▼  (审批)
┌──────────────────┐
│  Print           │  通过 FTPS 上传 + MQTT 指令 → Bambu 打印机
│  Send to printer │  通过 MQTT 订阅监控打印进度
└──────────────────┘
```

## 环境要求

| 依赖项 | 版本 | 获取方式 |
|--------|------|----------|
| **Python** | ≥ 3.11 | `brew install python@3.12` 或前往 [python.org](https://www.python.org/downloads/) 下载 |
| **PrusaSlicer** | 任意 | `brew install --cask prusa-slicer`（macOS）或 [前往下载](https://www.prusa3d.com/page/prusaslicer_424/) |
| **Bambu Lab 打印机** | 任意 | 需与电脑处于同一局域网，且开启局域网模式 |
| **LLM API 密钥** | — | 任意兼容 OpenAI 的服务商：[OpenAI](https://platform.openai.com/api-keys)、[xAI/Grok](https://console.x.ai)、[Anthropic](https://console.anthropic.com/) 等 |
| **Tripo3D API 密钥** | — | 在 [tripo3d.ai](https://www.tripo3d.ai) 注册 → 控制台 → API Keys |
| **聊天渠道** | — | 任选其一：[Telegram BotFather](https://t.me/BotFather)、[飞书](https://open.feishu.cn/app)，或直接使用 HTTP API |

> 💡 **查找打印机信息：** 在打印机液晶屏上进入 **设置 → 网络** 获取 IP 和访问码，进入 **设置 → 设备** 获取序列号。

## 快速开始

### 1. 安装

```bash
pip install openclaw-3dprint
```

从源码安装：

```bash
git clone https://github.com/phoenixjyb/openclaw-3dprint.git
cd openclaw-3dprint
pip install -e .
```

可选扩展：

```bash
pip install "openclaw-3dprint[telegram]"     # + Telegram 机器人支持
pip install "openclaw-3dprint[windows]"      # + 通过 SSH 远程 Windows 切片
pip install "openclaw-3dprint[dev]"          # + pytest、ruff 开发工具
```

> 也可以使用 `requirements.txt`：`pip install -r requirements.txt`

### 2. 安装 PrusaSlicer（用于本地切片）

```bash
# macOS
brew install --cask prusa-slicer

# Linux — 从 https://www.prusa3d.com/page/prusaslicer_424/ 下载
```

### 3. 配置

```bash
mkdir -p ~/.openclaw-3dprint
cp .env.example ~/.openclaw-3dprint/pipeline.env
```

编辑 `~/.openclaw-3dprint/pipeline.env`，填入你的配置：

```env
# LLM（兼容 OpenAI 的 API）
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# 3D 模型生成
TRIPO_API_KEY=your-tripo-key

# Bambu 打印机
BAMBU_PRINTER_IP=192.168.1.100
BAMBU_PRINTER_SERIAL=your-serial
BAMBU_PRINTER_ACCESS_CODE=your-code
```

### 4. 启动

```bash
# 作为 OpenClaw 技能运行（HTTP API，供智能体集成）
openclaw-3dprint --mode feishu

# 作为 Telegram 机器人运行
openclaw-3dprint --mode telegram

# 同时运行两种模式
openclaw-3dprint --mode dual
```

### 5. 使用

通过命令行（从 OpenClaw 智能体或终端调用）：

```bash
3dprint request a small dragon figurine
3dprint status
3dprint approve <job_id>
```

通过 Telegram — 使用 `/print` 命令：

```
/print 一个小龙摆件
/status
/cancel <job_id>
/help
```

> ⚠️ 仅 `/print <描述>` 会触发打印流程，普通消息会被忽略，避免误触发。

通过飞书 / OpenClaw：智能体会自动调用 HTTP API。

## OpenClaw 技能安装

如果你使用 [OpenClaw](https://openclaw.ai)，本项目可作为一个技能接入：

1. 安装包：`pip install openclaw-3dprint`
2. 将 `SKILL.md` 复制到你的技能目录，或通过 ClawHub 安装：
   ```bash
   clawhub install 3dprint
   ```
3. 当用户要求 3D 打印时，智能体会自动调用 `3dprint` 命令行工具。

## 配置参考

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `BOT_MODE` | 否 | `feishu` | `feishu`、`telegram` 或 `dual` |
| `OPENAI_API_KEY` | **是** | — | LLM API 密钥 |
| `OPENAI_BASE_URL` | 否 | `https://api.openai.com/v1` | LLM API 端点 |
| `OPENAI_MODEL` | 否 | `gpt-4o` | LLM 模型名称 |
| `MESH_PROVIDER` | 否 | `tripo` | `tripo` 或 `meshy` |
| `TRIPO_API_KEY` | 条件* | — | Tripo3D 密钥（*使用 tripo 时必填） |
| `MESHY_API_KEY` | 条件* | — | Meshy.ai 密钥（*使用 meshy 时必填） |
| `SLICER_MODE` | 否 | `local` | `local`（PrusaSlicer）或 `remote`（SSH 到 Windows） |
| `SLICER_PATH` | 否 | 自动检测 | 切片软件路径 |
| `BAMBU_PRINTER_IP` | **是** | — | 打印机局域网 IP |
| `BAMBU_PRINTER_SERIAL` | **是** | — | 打印机序列号 |
| `BAMBU_PRINTER_ACCESS_CODE` | **是** | — | 打印机访问码 |
| `BAMBU_SEND_METHOD` | 否 | `ftp` | `ftp`（FTPS 直连）或 `studio`（Bambu Studio CLI） |
| `TELEGRAM_BOT_TOKEN` | 条件* | — | *使用 telegram 模式时必填 |
| `TELEGRAM_ALLOWED_USER_IDS` | 否 | — | 允许使用的 Telegram 用户 ID，逗号分隔 |
| `FEISHU_APP_ID` | 条件* | — | *使用飞书模式时必填 |
| `FEISHU_APP_SECRET` | 条件* | — | *使用飞书模式时必填 |
| `FEISHU_CHAT_ID` | 条件* | — | *使用飞书模式时必填 |
| `FEISHU_API_PORT` | 否 | `8765` | HTTP API 端口 |
| `STAGING_DIR` | 否 | `~/.openclaw-3dprint/staging` | 临时文件目录 |

## 远程 Windows 切片（可选）

如果你更倾向于使用 Bambu Studio 的切片引擎（对 Bambu 系列打印机有更好的预设配置），可以在 Windows 电脑上运行切片：

```env
SLICER_MODE=remote
WINDOWS_HOST=192.168.1.200
WINDOWS_USER=your-user
WINDOWS_SSH_KEY=~/.ssh/id_ed25519
```

管道会通过 SSH 连接到 Windows 主机，在远端切片后将结果传回。

## 多用户打印队列

多个用户或智能体可以安全共享同一台打印机。管道通过跨进程文件锁（`fcntl.flock`）对打印任务进行串行化管理，等待中的用户可以看到自己的排队位置。

## 项目结构

```
openclaw-3dprint/
├── SKILL.md                    # OpenClaw 技能定义
├── README.md                   # 英文说明文档
├── README_ZH.md                # 中文说明文档（本文件）
├── pyproject.toml              # Python 包配置
├── .env.example                # 配置模板
├── scripts/
│   └── 3dprint                 # 供智能体调用的 CLI 封装脚本
├── pipeline/
│   ├── __main__.py             # 入口文件
│   ├── orchestrator.py         # 管道调度器
│   ├── printer_queue.py        # 跨进程打印锁
│   ├── bot.py                  # Telegram 机器人
│   ├── feishu_bot.py           # HTTP API + 飞书消息
│   ├── feishu_client.py        # 飞书 API 客户端
│   ├── models/
│   │   └── job.py              # PrintJob 状态机
│   ├── services/
│   │   ├── openai_client.py    # LLM 客户端
│   │   ├── tripo_client.py     # Tripo3D 网格生成
│   │   ├── meshy_client.py     # Meshy.ai 网格生成
│   │   ├── bambu_printer.py    # FTPS + MQTT 直连打印机
│   │   └── bambu_mqtt.py       # MQTT 协议辅助模块
│   ├── stages/
│   │   ├── llm_interpret.py    # 阶段 1：提示词增强
│   │   ├── mesh_generate.py    # 阶段 2：3D 模型生成
│   │   ├── slice.py            # 阶段 3：切片
│   │   └── print_job.py        # 阶段 4：发送到打印机
│   └── utils/
│       └── config.py           # 配置加载器
└── tests/                      # 单元测试
```

## 开发指南

```bash
git clone https://github.com/phoenixjyb/openclaw-3dprint.git
cd openclaw-3dprint
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check pipeline/
pytest tests/ -q
```

## 支持的打印机

目前已测试通过：
- **Bambu Lab P1S / P1P** — 通过 FTPS（端口 990）+ MQTT（端口 8883）
- **Bambu Lab X1 / X1C** — 相同协议

理论上任何开启了局域网模式的 Bambu Lab 打印机都可以使用。打印机必须与运行管道的电脑处于同一网络。

## 许可证

MIT — 详见 [pyproject.toml](pyproject.toml)。

## 系统架构

详见 [docs/ARCHITECTURE_ZH.md](docs/ARCHITECTURE_ZH.md) 了解完整系统架构、支持的打印机型号、管道路线和容错策略。

[English Architecture Doc](docs/ARCHITECTURE.md)
