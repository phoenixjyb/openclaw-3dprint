#!/bin/zsh
# Launcher script for openclaw-3dprint (used by launchd or manual start)
# Customize PATH and proxy settings for your environment.

export PATH="/opt/homebrew/bin:$PATH"

# If you need a proxy for Telegram (e.g. in China), uncomment and set:
# export HTTPS_PROXY=http://127.0.0.1:7890
# export HTTP_PROXY=http://127.0.0.1:7890

cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
exec python -m pipeline --mode dual
