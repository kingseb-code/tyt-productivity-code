#!/bin/bash
# OpenClaw install script — run this once on your WSL2 Ubuntu machine
# Usage: bash scripts/install.sh

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

# 2. Ensure pip is available
echo "[1/4] Installing Python dependencies..."
if ! python3 -m pip --version &>/dev/null; then
    echo "pip not found — installing..."
    sudo apt-get install -y python3-pip
fi
python3 -m pip install -r "$REPO_DIR/requirements.txt" --quiet
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

# Check if systemd is actually running (it won't be until after WSL2 restart)
if pidof systemd &>/dev/null || [ "$(cat /proc/1/comm)" = "systemd" ]; then
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
    echo "=== Almost done — one more step required ==="
    echo ""
    echo "systemd is not active yet. You need to restart WSL2 once."
    echo ""
    echo "Do this in Windows PowerShell (NOT here):"
    echo "  wsl --shutdown"
    echo "  wsl"
    echo ""
    echo "After WSL2 restarts, run this script again to finish the install."
fi

echo ""
echo "Useful commands (once systemd is active):"
echo "  sudo systemctl status openclaw   — check if running"
echo "  sudo systemctl restart openclaw  — restart after code changes"
echo "  journalctl -u openclaw -f        — live logs"
