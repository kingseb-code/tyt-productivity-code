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

# 2. Install Python dependencies
echo "[1/4] Installing Python dependencies..."
pip3 install -r "$REPO_DIR/requirements.txt" --quiet
echo "Done."

# 3. Set up wsl.conf (enables systemd)
echo "[2/4] Configuring WSL2 (requires sudo)..."
if grep -q "systemd=true" /etc/wsl.conf 2>/dev/null; then
    echo "wsl.conf already configured."
else
    sudo cp "$REPO_DIR/wsl.conf" /etc/wsl.conf
    echo "wsl.conf installed. You will need to restart WSL2 after this script."
fi

# 4. Install systemd service
echo "[3/4] Installing systemd service..."
sudo cp "$REPO_DIR/systemd/$SERVICE_NAME.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
echo "Service enabled."

# 5. Start the service
echo "[4/4] Starting OpenClaw..."
sudo systemctl start "$SERVICE_NAME"
sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "=== Install complete ==="
echo "OpenClaw will now start automatically every time WSL2 starts."
echo ""
echo "Useful commands:"
echo "  sudo systemctl status openclaw   — check if running"
echo "  sudo systemctl restart openclaw  — restart after code changes"
echo "  journalctl -u openclaw -f        — live logs"
