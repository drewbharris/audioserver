# Audio Stream Server

Streams audio from a USB/audio interface on a Raspberry Pi as an **HLS (HTTP Live Streaming)** stream — compatible with all browsers (Chrome, Safari, Firefox) and native players.

```
  ┌─────────────┐    raw PCM    ┌─────────────┐   AAC   ┌───────────────────┐
  │ Audio       │ ─────────────▶│ HLS Encoder │ ──────▶ │  Audio Stream     │
  │ Interface   │   (USB)       │ (ffmpeg)    │         │  Server (Python)  │
  │ (e.g. Focus- │               └─────────────┘         │                   │
  │ rite, etc.) │                                        │  HTTP :8080        │
  └─────────────┘                                        │                   │
                                                         │  /stream.m3u8 ─▶ HLS
                                                         │  /stream{NNN}.ts ─▶ segments
                                                         │  /status.json ─▶ health
                                                         └────────┬──────────┘
                                                                  │
                                                           Internet / LAN
```

### How it works

1. **Audio capture** — `sounddevice` reads PCM audio from the USB audio interface
2. **HLS encoding** — PCM data is fed to `ffmpeg` which encodes it to AAC and outputs HLS segments (.ts files) and a playlist (stream.m3u8)
3. **HTTP streaming** — The HLS segments and playlist are served over HTTP
4. **Browser playback** — Uses hls.js for Chrome/Firefox, native HLS for Safari

## Features

- **Real-time capture** from any ALSA-compatible audio interface
- **HLS streaming** — works in all browsers including Safari
- **Web dashboard** — status page with play button and client count
- **Configurable segment duration** — trade latency for stability
- **Automatic FFmpeg restarts** — if encoding fails, it restarts automatically
- **Systemd service** — runs as a background daemon on boot

## Quick Start

```bash
# Install system dependencies
sudo bash setup.sh

# Find your audio device index
python main.py

# Start the server
AUDIO_DEVICE_INDEX=1 python main.py
```

## Configuration

All settings via environment variables (defaults in parentheses):

| Variable                  | Default | Description                                  |
|---------------------------|---------|----------------------------------------------|
| `AUDIO_DEVICE_INDEX`      | *auto*  | Index from `python main.py` device list      |
| `STREAM_PORT`             | `8080`  | HTTP port to listen on                       |
| `STREAM_SAMPLE_RATE`      | `48000` | Sample rate in Hz                            |
| `STREAM_CHANNELS`         | `2`     | 1 = mono, 2 = stereo                        |
| `STREAM_BIT_DEPTH`        | `16`    | 16 or 24                                     |
| `BUFFER_SIZE`             | `1024`  | Frames per buffer (lower = lower latency)    |
| `HLS_SEGMENT_DURATION`    | `1`     | HLS segment duration in seconds              |
| `HLS_SEGMENTS_IN_PLAYLIST`| `3`     | Number of segments to keep in playlist       |
| `HLS_BITRATE`             | `128k`  | AAC bitrate (e.g. 128k, 192k, 256k)          |

### Example

```bash
AUDIO_DEVICE_INDEX=1 \
STREAM_PORT=8080 \
STREAM_SAMPLE_RATE=48000 \
STREAM_CHANNELS=2 \
HLS_BITRATE=192k \
python main.py
```

## Endpoints

| URL                        | Description                        |
|----------------------------|------------------------------------|
| `http://<pi-ip>:8080/`     | Web dashboard with HLS player      |
| `http://<pi-ip>:8080/stream.m3u8` | HLS master playlist        |
| `http://<pi-ip>:8080/stream001.ts` | HLS audio segment (dynamic) |
| `http://<pi-ip>:8080/status` | JSON status endpoint            |

### Use with players

```bash
# VLC
vlc http://<pi-ip>:8080/stream.m3u8

# mpv
mpv http://<pi-ip>:8080/stream.m3u8

# Browser
open http://<pi-ip>:8080/
```

## Systemd Service

After testing, install as a service:

```bash
# Edit the service file
sudo cp audioserver.service /etc/systemd/system/
sudo systemctl daemon-reload

# Create a config file
sudo mkdir -p /etc/audioserver
cat <<EOF | sudo tee /etc/audioserver/env
AUDIO_DEVICE_INDEX=1
STREAM_PORT=8080
STREAM_SAMPLE_RATE=48000
STREAM_CHANNELS=2
EOF

# Enable and start
sudo systemctl enable audioserver
sudo systemctl start audioserver

# Check status
sudo systemctl status audioserver
journalctl -u audioserver -f
```

## Raspberry Pi Setup

### Audio interface detection

```bash
# List all audio devices
arecord -L

# Check your interface is connected
cat /proc/asound/cards
```

### Install dependencies

```bash
# System packages
sudo apt update
sudo apt install -y python3-pip python3-venv libasound2-dev ffmpeg

# Create virtualenv (recommended)
python3 -m venv /opt/audioserver/venv
source /opt/audioserver/venv/bin/activate
pip install -r requirements.txt

# Or install system-wide (for systemd with /usr/bin/python3)
sudo pip3 install sounddevice numpy aiohttp
```

### Tune for low latency

On the Pi, set real-time priorities:

```bash
# /etc/security/limits.conf
pi           soft   rtprio         99
pi           hard   rtprio         99
pi           soft   memlock        unlimited
pi           hard   memlock        unlimited
```

```bash
# Lower the buffer for lower latency (may cause dropouts on heavy load)
BUFFER_SIZE=512
# Or even 256 for ~6ms latency (use with 48kHz for best results)
BUFFER_SIZE=256
```

### CPU governor

```bash
# Performance mode for consistent real-time performance
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

## Troubleshooting

### No sound / empty stream

1. Test capture: `arecord -D hw:X,Y -f S16_LE -r 48000 -t wav test.wav`
2. Verify device index: `python main.py` (lists devices)
3. Check permissions: `sudo usermod -aG audio $USER` (then reboot)
4. Check ffmpeg is installed: `which ffmpeg`

### Dropouts / crackling

1. Increase `BUFFER_SIZE` to 2048 or 4096
2. Switch to `performance` CPU governor (see above)
3. Use a powered USB hub for the audio interface
4. Reduce `STREAM_CHANNELS` to 1 (mono) if CPU-bound

### Stream not accessible

1. Check firewall: `sudo ufw allow 8080/tcp`
2. Verify IP: `hostname -I`
3. Test locally: `curl http://localhost:8080/status`

### systemd won't start

```bash
# Check the service file paths
cat /etc/systemd/system/audioserver.service

# Check logs
journalctl -u audioserver -n 50

# The service uses /usr/bin/python3 - make sure the venv is activated
# in the service file's EnvironmentFile, or use the venv python
```

## License

MIT
# audioserver
