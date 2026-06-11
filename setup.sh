#!/bin/bash
# setup.sh — One-command install on a Raspberry Pi
# Usage: curl -sSL https://... | bash
# Or:    sudo bash setup.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; }

# ── Check root ─────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (sudo)"
    exit 1
fi

# ── Detect platform ────────────────────────────────────────────────────
if [[ ! -f /proc/version ]]; then
    warn "Not running on a Linux system — installing for local development"
    pip install -r requirements.txt
    exit 0
fi

info "Detected Linux — installing for Raspberry Pi"

# ── Install system dependencies ────────────────────────────────────────
info "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv libasound2-dev libportaudio2 ffmpeg > /dev/null

# ── Create install directory ──────────────────────────────────────────
INSTALL_DIR="/opt/audioserver"
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/hls"

# ── Copy files ────────────────────────────────────────────────────────
info "Copying files to $INSTALL_DIR..."
cp -r /dev/stdin/* "$INSTALL_DIR/" 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -d "$SCRIPT_DIR" ]]; then
    cp "$SCRIPT_DIR/main.py" "$SCRIPT_DIR/config.py" \
       "$SCRIPT_DIR/audio_capture.py" "$SCRIPT_DIR/stream_server.py" \
       "$SCRIPT_DIR/hls_encoder.py" \
       "$INSTALL_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/audioserver.service" /etc/systemd/system/ 2>/dev/null || true
fi

# ── Virtual environment ──────────────────────────────────────────────
info "Setting up Python virtual environment..."
cd "$INSTALL_DIR"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt > /dev/null 2>&1

# ── Install systemd service ───────────────────────────────────────────
if [[ -f /etc/systemd/system/audioserver.service ]]; then
    sed -i "s|ExecStart=.*|ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/main.py|" \
        /etc/systemd/system/audioserver.service
    info "Systemd service installed"
else
    warn "No audioserver.service file found — skipping systemd installation"
fi

# ── Create config directory ───────────────────────────────────────────
mkdir -p /etc/audioserver

if [[ ! -f /etc/audioserver/env ]]; then
    cat > /etc/audioserver/env <<'EOF'
AUDIO_DEVICE_INDEX=1
STREAM_PORT=8080
STREAM_SAMPLE_RATE=48000
STREAM_CHANNELS=2
STREAM_BIT_DEPTH=16
BUFFER_SIZE=1024
HLS_SEGMENT_DURATION=1
HLS_SEGMENTS_IN_PLAYLIST=3
HLS_BITRATE=128k
EOF
    info "Config file created at /etc/audioserver/env"
fi

# ── CPU governor ─────────────────────────────────────────────────────
if command -v cpufreq-set &>/dev/null; then
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo performance > "$cpu" 2>/dev/null || true
    done
    info "CPU governor set to performance"
else
    warn "Could not set CPU governor — system will still function normally"
fi

info "Setup complete! Run: sudo systemctl enable audioserver && sudo systemctl start audioserver"
