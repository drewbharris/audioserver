"""
HLS encoder: converts PCM → AAC using FFmpeg subprocess, outputting
HLS segments (.ts) and a master playlist (stream.m3u8).
"""

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class HlsEncoder:
    """Encodes raw PCM audio to AAC in HLS format via ffmpeg subprocess."""

    def __init__(
        self,
        output_dir: Path,
        sample_rate: int = 48000,
        channels: int = 2,
        segment_duration: int = 1,
        segments_in_playlist: int = 3,
        segment_wrap: int = 50,
        bitrate: str = "128k",
    ):
        self._sample_rate = sample_rate
        self._channels = channels
        self._output_dir = Path(output_dir)
        self._segment_duration = segment_duration
        self._segments_in_playlist = segments_in_playlist
        self._segment_wrap = segment_wrap
        self._bitrate = bitrate
        self._ffmpeg_proc = None
        self._running = True
        self._restart_count = 0
        self._max_restarts = 5
        self._tasks: list[asyncio.Task] = []

    # ── helpers ────────────────────────────────────────────────────────

    def _build_cmd(self) -> list[str]:
        """Build the ffmpeg command for HLS segment output."""
        segment_pattern = str(self._output_dir / "stream%03d.ts")
        return [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "warning",
            "-f", "s16le",
            "-ar", str(self._sample_rate),
            "-ac", str(self._channels),
            "-i", "-",
            "-c:a", "aac",
            "-b:a", self._bitrate,
            "-f", "segment",
            "-segment_format", "mpegts",
            "-segment_time", str(self._segment_duration),
            "-segment_list", str(self._output_dir / "stream.m3u8"),
            "-segment_list_type", "m3u8",
            "-segment_list_size", str(self._segments_in_playlist),
            "-segment_wrap", str(self._segment_wrap),
            "-segment_start_number", "0",
            segment_pattern,
        ]

    def _ensure_output_dir(self):
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _prune_old_segments(self):
        try:
            for f in self._output_dir.glob("stream*.ts"):
                f.unlink(missing_ok=True)
            for f in self._output_dir.glob("*.m3u8"):
                if f.name != "stream.m3u8":
                    f.unlink(missing_ok=True)
        except Exception as exc:
            log.debug("Prune error: %s", exc)

    # ── start / stop ───────────────────────────────────────────────────

    async def start(self, pcm_queue: asyncio.Queue):
        """Start the HLS encoder subprocess. Returns immediately after spawning."""
        self._ensure_output_dir()
        self._pcm_queue = pcm_queue
        # Run the encoder loop in a background task
        asyncio.create_task(self._encoder_loop())
        log.info("HLS encoder started.")

    async def _encoder_loop(self):
        """Main encoder loop: manages FFmpeg lifecycle."""
        while self._running:
            # Start FFmpeg process
            cmd = self._build_cmd()
            try:
                self._ffmpeg_proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except Exception as exc:
                log.error("Failed to start FFmpeg: %s", exc)
                self._restart_count += 1
                if self._restart_count >= self._max_restarts:
                    log.error("Max restarts reached. Stopping.")
                    break
                await asyncio.sleep(2 ** min(self._restart_count, 4))
                continue

            log.info("FFmpeg process started (PID=%s)", self._ffmpeg_proc.pid)
            self._restart_count = 0

            # Start background tasks concurrently
            self._tasks = [
                asyncio.create_task(self._feed_pcm()),
                asyncio.create_task(self._monitor_stderr()),
                asyncio.create_task(self._ffmpeg_proc.wait()),
            ]

            # Wait for FFmpeg to exit
            retcode = await self._tasks[2]
            log.info("FFmpeg exited with code %s", retcode)

            # Cancel background tasks
            for t in self._tasks[:2]:
                t.cancel()
            await asyncio.gather(*self._tasks[:2], return_exceptions=True)

            if not self._running:
                break

            # Restart
            self._restart_count += 1
            if self._restart_count > self._max_restarts:
                log.error("Max restarts reached. Giving up.")
                break
            self._prune_old_segments()
            await asyncio.sleep(1)

        log.info("HLS encoder stopped.")

    async def stop(self):
        """Stop the encoder and close FFmpeg."""
        self._running = False
        if self._ffmpeg_proc and self._ffmpeg_proc.stdin:
            try:
                self._ffmpeg_proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._ffmpeg_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._ffmpeg_proc.kill()
                await self._ffmpeg_proc.wait()

    # ── Background tasks ───────────────────────────────────────────────

    async def _feed_pcm(self):
        """Continuously read PCM from queue and feed to FFmpeg stdin."""
        count = 0
        while self._running and self._ffmpeg_proc and self._ffmpeg_proc.stdin:
            try:
                chunk = await self._pcm_queue.get()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.error("Queue get error: %s", exc)
                return

            count += 1
            if self._ffmpeg_proc.stdin and not self._ffmpeg_proc.stdin.is_closing():
                self._ffmpeg_proc.stdin.write(chunk)

                # Drain periodically to prevent pipe buffer fill-up
                if count % 50 == 0:
                    try:
                        await asyncio.wait_for(
                            self._ffmpeg_proc.stdin.drain(), timeout=10.0
                        )
                    except Exception:
                        pass

    async def _monitor_stderr(self):
        """Monitor FFmpeg stderr for errors."""
        try:
            while self._running:
                line = await self._ffmpeg_proc.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text:
                    log.debug("FFmpeg: %s", text)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def output_dir(self) -> Path:
        return self._output_dir
