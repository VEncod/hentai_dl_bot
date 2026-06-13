#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Hentai DL Bot — Local/VPS Setup Script
# Run this once on a fresh Ubuntu/Debian server to install all dependencies
# ─────────────────────────────────────────────────────────────────────────────

set -e

echo "╔══════════════════════════════════════════════════╗"
echo "║     Hentai DL Bot — VPS/Local Setup Script       ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo -e "${YELLOW}[1/5]${NC} Updating system packages..."
$SUDO apt-get update -qq

echo -e "${YELLOW}[2/5]${NC} Installing system dependencies (FFmpeg, Python3, pip)..."
$SUDO apt-get install -y -qq python3 python3-pip python3-venv ffmpeg git wget curl

echo -e "${YELLOW}[3/5]${NC} Setting up Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

echo -e "${YELLOW}[4/5]${NC} Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo -e "${YELLOW}[5/5]${NC} Setting up N_m3u8DL-RE binary..."
chmod +x binary/N_m3u8DL-RE

# Check if .env exists
if [ ! -f ".env" ]; then
    echo ""
    echo -e "${YELLOW}Creating .env file from template...${NC}"
    cp .env.example .env
    echo -e "${RED}⚠️  Please edit .env file with your credentials before starting the bot!${NC}"
    echo -e "   Run: ${GREEN}nano .env${NC}"
fi

echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "To start the bot:"
echo "  1. Edit .env with your credentials:  nano .env"
echo "  2. Activate venv:                    source venv/bin/activate"
echo "  3. Start the bot:                    python3 app.py"
echo ""
echo "Or use the systemd service for auto-restart:"
echo "  sudo cp hentai-dl-bot.service /etc/systemd/system/"
echo "  sudo systemctl enable hentai-dl-bot"
echo "  sudo systemctl start hentai-dl-bot"
echo ""
