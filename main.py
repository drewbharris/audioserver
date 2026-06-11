#!/usr/bin/env python3
"""
Audio Stream Server — captures audio from a sound card and streams it
over HTTP as HLS (HTTP Live Streaming).

HLS works in all browsers (including Safari) and native players.

Usage:
    pip install -r requirements.txt
    python main.py

Or with environment variables:
    AUDIO_DEVICE_INDEX=1 STREAM_PORT=8080 python main.py

On Raspberry Pi you can also create a systemd service.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from config import load
from audio_capture import AudioCapture
from hls_encoder import HlsEncoder
from stream_server import StreamServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audioserver")

# ── Globals ───────────────────────────────────────────────────────────

cfg = load()
audio_queue: asyncio.Queue[bytes] | None = None
capture: AudioCapture | None = None
encoder: HlsEncoder | None = None
server: StreamServer | None = None
_shutdown = asyncio.Event()

# ── HLS output directory ──────────────────────────────────────────────

HLS_OUTPUT_DIR = Path(cfg.get("hls_output_dir", "/opt/audioserver/hls"))


# ── Main loop ─────────────────────────────────────────────────────────

async def main():
    global audio_queue, capture, encoder, server

    audio_queue = asyncio.Queue(maxsize=2048)
    capture = AudioCapture(
        queue=audio_queue,
        sample_rate=cfg["stream_sample_rate"],
        channels=cfg["stream_channels"],
        bit_depth=cfg["stream_bit_depth"],
        buffer_size=cfg["buffer_size"],
        device_index=cfg["audio_device_index"],
    )

    # Start HLS encoder
    encoder = HlsEncoder(
        output_dir=HLS_OUTPUT_DIR,
        sample_rate=cfg["stream_sample_rate"],
        channels=cfg["stream_channels"],
        segment_duration=cfg.get("hls_segment_duration", 1),
        segments_in_playlist=cfg.get("hls_segments_in_playlist", 3),
        bitrate=cfg.get("hls_bitrate", "128k"),
    )
    await encoder.start(audio_queue)

    # Start HTTP server
    server = StreamServer(cfg)
    await server.start(audio_queue, encoder=encoder)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: _shutdown_signal(s))

    # Start audio capture
    capture_task = asyncio.create_task(capture.start())

    log.info("Server running — press Ctrl+C to stop.")
    await _shutdown.wait()

    await capture.stop()
    await server.stop()
    await encoder.stop()
    log.info("Goodbye.")


def _shutdown_signal(sig):
    log.info("Received signal %s — shutting down...", sig)
    _shutdown.set()


# ── Entry ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick device listing if index not provided
    if cfg["audio_device_index"] is None:
        try:
            import sounddevice as sd
            print("\nAvailable audio devices:")
            for i, dev in enumerate(sd.query_devices()):
                print(f"  [{i}] {dev['name']}")
            print(f"\nSet AUDIO_DEVICE_INDEX=<index> or pass --device INDEX")
        except Exception:
            pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _shutdown.set()
        asyncio.run(main())
