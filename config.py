"""
Configuration for the audio stream server.

Environment variables override defaults:
    AUDIO_DEVICE_INDEX   - Index of the audio interface (from `arecord -L`)
    STREAM_PORT          - HTTP port to serve on (default: 8080)
    STREAM_SAMPLE_RATE   - Sample rate in Hz (default: 48000)
    STREAM_CHANNELS      - Number of channels (default: 2)
    STREAM_BIT_DEPTH     - Capture bit depth: 16 or 24 (default: 16)
    BUFFER_SIZE          - Audio buffer frames (default: 1024)
    HLS_SEGMENT_DURATION - HLS segment duration in seconds (default: 1)
    HLS_SEGMENTS_IN_PLAYLIST - Number of segments in playlist (default: 3)
    HLS_BITRATE          - AAC bitrate (default: 128k)
    STREAM_PASSWORD      - SHOUTcast/Icecast auth password (optional)
"""

import os


# ── Defaults ──────────────────────────────────────────────────────────

DEFAULTS = {
    "audio_device_index": None,   # None = use system default
    "stream_port": 8080,
    "stream_sample_rate": 48000,
    "stream_channels": 2,
    "stream_bit_depth": 16,
    "buffer_size": 1024,
    "hls_segment_duration": 1,
    "hls_segments_in_playlist": 3,
    "hls_bitrate": "128k",
    "stream_password": None,       # Set for SHOUTcast/Icecast relay
}


# ── Loading ───────────────────────────────────────────────────────────

def load():
    """Return a dict of configuration values."""
    cfg = dict(DEFAULTS)

    if os.environ.get("AUDIO_DEVICE_INDEX"):
        cfg["audio_device_index"] = int(os.environ["AUDIO_DEVICE_INDEX"])

    if os.environ.get("STREAM_PORT"):
        cfg["stream_port"] = int(os.environ["STREAM_PORT"])

    if os.environ.get("STREAM_SAMPLE_RATE"):
        cfg["stream_sample_rate"] = int(os.environ["STREAM_SAMPLE_RATE"])

    if os.environ.get("STREAM_CHANNELS"):
        cfg["stream_channels"] = int(os.environ["STREAM_CHANNELS"])

    bd = os.environ.get("STREAM_BIT_DEPTH", "16")
    if bd not in ("16", "24"):
        bd = "16"
    cfg["stream_bit_depth"] = int(bd)

    if os.environ.get("BUFFER_SIZE"):
        cfg["buffer_size"] = int(os.environ["BUFFER_SIZE"])

    if os.environ.get("HLS_SEGMENT_DURATION"):
        cfg["hls_segment_duration"] = int(os.environ["HLS_SEGMENT_DURATION"])

    if os.environ.get("HLS_SEGMENTS_IN_PLAYLIST"):
        cfg["hls_segments_in_playlist"] = int(os.environ["HLS_SEGMENTS_IN_PLAYLIST"])

    if os.environ.get("HLS_BITRATE"):
        cfg["hls_bitrate"] = os.environ["HLS_BITRATE"]

    pw = os.environ.get("STREAM_PASSWORD")
    if pw:
        cfg["stream_password"] = pw

    return cfg
