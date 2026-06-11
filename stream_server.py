"""
HTTP streaming server.

Serves the live audio as HLS (HTTP Live Streaming):
    /              – HTML page with HLS player
    /stream.m3u8   – HLS master playlist
    /stream{NNN}.ts – HLS media segments (served statically)
    /status        – JSON status (sample rate, channels, connected clients)
"""

import asyncio
import logging
import time
from pathlib import Path

from aiohttp import web

log = logging.getLogger(__name__)

# ── HTML page ─────────────────────────────────────────────────────────
_HTML_DIR = Path(__file__).parent / "web"
with open(_HTML_DIR / "index.html", "r") as _f:
    HTML_PAGE = _f.read()


# ── Stream handler class ──────────────────────────────────────────────

class StreamServer:
    """HTTP server that serves HLS audio segments."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._host = cfg.get("listen_host", "0.0.0.0")
        self._port = cfg["stream_port"]
        self._hls_dir = Path(cfg.get("hls_output_dir", "/opt/audioserver/hls"))
        self._sample_rate = cfg["stream_sample_rate"]
        self._channels = cfg["stream_channels"]
        self._bitrate = cfg.get("hls_bitrate", "128k")

        self._audio_queue: asyncio.Queue[bytes] | None = None
        self._clients: dict[str, object] = {}  # keyed by client address
        self._start_time = 0.0
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    # ── Routes ─────────────────────────────────────────────────────────

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/stream.m3u8", self._playlist_handler)
        app.router.add_get("/status", self._status_handler)
        app.router.add_get("/", self._index_handler)
        app.router.add_get("/status.json", self._status_handler)
        # Serve HLS segment files
        app.router.add_get("/stream{segment:03d}.ts", self._segment_handler)
        app.router.add_get("/stream{segment}.ts", self._segment_handler)
        # Serve static font file from web/
        self._font_path = _HTML_DIR / "DepartureMono-Regular.otf"
        app.router.add_get("/DepartureMono-Regular.otf", self._font_handler)
        return app

    async def _index_handler(self, request: web.Request) -> web.Response:
        uptime = self._elapsed()
        host = request.host if request.host else f"{self._host}:{self._port}"
        stream_url = f"http://{host}/stream.m3u8"
        html = (
            HTML_PAGE.replace("{stream_url}", stream_url)
            .replace("{sample_rate}", str(self._sample_rate))
            .replace("{channels}", str(self._channels))
            .replace("{bitrate}", self._bitrate)
            .replace("{uptime}", f"{uptime:.0f}s")
        )
        return web.Response(text=html, content_type="text/html")

    async def _font_handler(self, request: web.Request) -> web.Response:
        """Serve the Departure Mono font file."""
        if not self._font_path.exists():
            return web.Response(status=404, text="Font not found")
        return web.FileResponse(self._font_path, headers={
            "Cache-Control": "public, max-age=31536000",
            "Content-Type": "font/otf",
        })

    async def _playlist_handler(self, request: web.Request) -> web.Response:
        """Serve the HLS master playlist."""
        host = request.host if request.host else f"{self._host}:{self._port}"

        # Read the current segment list from the m3u8 file
        m3u8_path = self._hls_dir / "stream.m3u8"
        if m3u8_path.exists():
            try:
                with open(m3u8_path, "r") as f:
                    content = f.read()
                # Replace relative .ts paths with absolute URLs
                import re
                content = re.sub(
                    r"^(stream\d+\.ts)$",
                    f"http://{host}/\\1",
                    content,
                    flags=re.MULTILINE,
                )
                return web.Response(text=content, content_type="application/vnd.apple.mpegurl")
            except Exception as exc:
                log.debug("Error reading playlist: %s", exc)

        # Fallback: return a minimal playlist
        playlist = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:2\n"
            f"#EXT-X-MEDIA-SEQUENCE:0\n"
            f"#EXT-X-PLAYLIST-TYPE:EVENT\n"
            f"http://{host}/stream000.ts\n"
            "#EXT-X-ENDLIST\n"
        )
        return web.Response(text=playlist, content_type="application/vnd.apple.mpegurl")

    async def _segment_handler(self, request: web.Request) -> web.Response:
        """Serve an HLS segment file."""
        segment_num = request.match_info.get("segment", "000")
        # Handle both "000" and "000" formats
        try:
            segment_num = int(segment_num)
        except ValueError:
            return web.Response(status=404, text="Not found")
        segment_path = self._hls_dir / f"stream{segment_num:03d}.ts"
        if not segment_path.exists():
            return web.Response(status=404, text="Not found")
        return web.FileResponse(segment_path, headers={
            "Cache-Control": "no-cache",
            "Content-Type": "audio/mpeg",
        })

    async def _status_handler(self, request: web.Request) -> web.Response:
        data = {
            "format": "hls",
            "sample_rate": self._sample_rate,
            "channels": self._channels,
            "bitrate": self._bitrate,
            "clients": len(self._clients),
            "uptime": round(self._elapsed(), 1),
        }
        return web.json_response(data)

    # ── start / stop ───────────────────────────────────────────────────

    async def start(self, audio_queue: asyncio.Queue, encoder=None):
        """Start the HTTP server."""
        self._audio_queue = audio_queue
        self._app = self._build_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._start_time = time.time()
        log.info("HLS server listening on http://%s:%d", self._host, self._port)

    async def stop(self):
        """Shut down the HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            log.info("HTTP server stopped.")

    def _elapsed(self) -> float:
        if self._start_time:
            return time.time() - self._start_time
        return 0.0
