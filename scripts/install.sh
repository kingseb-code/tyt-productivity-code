#!/bin/bash
# OpenClaw install script — run this once on your WSL2 Ubuntu machine
# Usage: bash scripts/install.sh

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"
SERVICE_NAME="openclaw"

echo "=== OpenClaw Installer ==="
echo "Repo: $REPO_DIR"
echo ""

# 1. Check .env exists
if [ ! -f "$REPO_DIR/.env" ]; then
    echo "ERROR: .env file not found."
    echo "Copy .env.example to .env and fill in your BOT_TOKEN and CHAT_ID."
    exit 1
fi

# 2. Create virtual environment and install dependencies
echo "[1/4] Installing Python dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    if ! python3 -m venv --help &>/dev/null; then
        echo "python3-venv not found — installing..."
        sudo apt-get install -y python3-venv python3-full
    fi
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt" --quiet
echo "Done."

# 3. Set up wsl.conf (enables systemd)
echo "[2/4] Configuring WSL2 systemd..."
if grep -q "systemd=true" /etc/wsl.conf 2>/dev/null; then
    echo "wsl.conf already configured."
    NEEDS_RESTART=false
else
    sudo cp "$REPO_DIR/wsl.conf" /etc/wsl.conf
    echo "wsl.conf installed."
    NEEDS_RESTART=true
fi

# 4. Install systemd service
echo "[3/4] Installing systemd service..."
sudo cp "$REPO_DIR/systemd/$SERVICE_NAME.service" /etc/systemd/system/

# Check if systemd is actually running
if [ "$(cat /proc/1/comm)" = "systemd" ]; then
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"

    echo "[4/4] Starting OpenClaw..."
    sudo systemctl start "$SERVICE_NAME"
    sleep 2
    sudo systemctl status "$SERVICE_NAME" --no-pager

    echo ""
    echo "=== Install complete ==="
    echo "OpenClaw will now start automatically every time WSL2 starts."
else
    sudo systemctl daemon-reload 2>/dev/null || true

    echo ""
    echo "=== Almost done — WSL2 restart required ==="
    echo ""
    echo "systemd is not active yet. You need to restart WSL2."
    echo ""
    echo "  1. Open Windows PowerShell (the blue one, NOT this terminal)"
    echo "  2. Run:  wsl --shutdown"
    echo "  3. Close this terminal and open WSL2/Ubuntu again"
    echo "  4. Run:  cd ~/tyt-productivity-code && bash scripts/install.sh"
    echo ""
fi

echo "Useful commands (once systemd is active):"
echo "  sudo systemctl status openclaw   — check if running"
echo "  sudo systemctl restart openclaw  — restart after code changes"
echo "  journalctl -u openclaw -f        — live logs"
