#!/bin/zsh
# Launcher script for openclaw-3dprint (used by launchd or manual start)
export PATH="/opt/homebrew/bin:$PATH"

cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
exec python -m pipeline --mode dual
