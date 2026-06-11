"""
Audio capture from a sound card / audio interface using sounddevice.

Provides a thread-safe queue and a context manager that reads audio in
real time from the specified device and pushes buffers into an asyncio
queue for the streaming server to consume.
"""

import asyncio
import logging
import struct
from contextlib import contextmanager

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


def _bytes_per_sample(bit_depth: int) -> int:
    return bit_depth // 8


def _to_bytes(samples: np.ndarray, bit_depth: int) -> bytes:
    """Convert numpy audio array to raw PCM bytes."""
    if bit_depth == 16:
        return (samples * 32767).astype(np.int16).tobytes(order="C")
    elif bit_depth == 24:
        # 24-bit: pack as 3 bytes per sample (little-endian)
        ints = (samples * 8388607).astype(np.int32)
        b = np.empty(ints.nbytes + len(ints), dtype=np.uint8)
        for i in range(3):
            b[i::4] = (ints >> (i * 8)) & 0xFF
        return b.tobytes()
    else:
        raise ValueError(f"Unsupported bit depth: {bit_depth}")


# ── Capture callback ──────────────────────────────────────────────────

class AudioCapture:
    """Captures audio from a sound device and pushes to an asyncio queue."""

    def __init__(self, queue: asyncio.Queue, sample_rate: int, channels: int,
                 bit_depth: int, buffer_size: int, device_index: int | None):
        self._queue = queue
        self._sample_rate = sample_rate
        self._channels = channels
        self._bit_depth = bit_depth
        self._buffer_size = buffer_size
        self._device_index = device_index
        self._stream = None
        self._running = False

    # ── helpers ────────────────────────────────────────────────────────

    def _callback(self, indata, frames, time_info, status):
        """Called by sounddevice on each audio buffer."""
        if status:
            log.warning("Stream callback status: %s", status)
            return
        # indata shape: (frames, channels) float32 in [-1, 1]
        # downmix to mono or keep channels
        if self._channels == 1 and indata.shape[1] > 1:
            indata = indata.mean(axis=1, keepdims=True)
        try:
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait,
                _to_bytes(indata[:, 0] if self._channels == 1 else indata.flatten(), self._bit_depth)
            )
        except asyncio.QueueFull:
            # Silently drop frame — real-time audio, better late than never
            pass

    # ── start / stop ───────────────────────────────────────────────────

    async def start(self):
        """Open the device and start capturing in a background thread."""
        dev_name = self._device_index if self._device_index is not None else "default"
        log.info(
            "Opening audio device index=%s  rate=%d  ch=%d  bd=%d  buf=%d",
            dev_name, self._sample_rate, self._channels, self._bit_depth, self._buffer_size,
        )

        self._loop = asyncio.get_running_loop()
        self._stream = sd.InputStream(
            device=self._device_index,
            samplerate=self._sample_rate,
            channels=self._channels,
            blocksize=self._buffer_size,
            callback=self._callback,
            finished_callback=lambda: log.info("Audio stream finished."),
        )
        self._stream.start()
        self._running = True
        log.info("Audio capture started.")

    async def stop(self):
        """Stop and close the audio stream."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._running = False
            log.info("Audio capture stopped.")

    @property
    def is_running(self) -> bool:
        return self._running
