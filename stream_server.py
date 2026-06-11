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
HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>audioserver</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<style>
    @font-face {{
      font-family: 'Departure Mono';
      src: url('DepartureMono-Regular.otf') format('opentype');
      font-weight: normal;
      font-style: normal;
    }}

    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }}

    body {{
      background: #000;
      color: #fff;
      font-family: 'Departure Mono', monospace;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }}

    h1 {{
      font-size: 3rem;
      margin-bottom: 1rem;
    }}

    h2 {{
      font-size: 2rem;
      margin-top: 2rem;
      margin-bottom: 1rem;
    }}

    h3 {{
      font-size: 1.5rem;
      margin-top: 1.5rem;
      margin-bottom: 0.75rem;
    }}

    p {{
      line-height: 1.8;
      margin-bottom: 1rem;
      max-width: 60ch;
    }}

    a {{
      color: #fff;
      text-decoration: underline;
    }}

    a:hover {{
      text-decoration: none;
    }}

    button {{
      background: #fff;
      color: #000;
      border: none;
      font-family: 'Departure Mono', monospace;
      font-size: 1rem;
      padding: 0.75rem 1.5rem;
      cursor: pointer;
      border-radius: 0;
      margin-right: 0.5rem;
      margin-top: 1rem;
    }}

    button:hover {{
      background: #ccc;
    }}

    .container {{
      width: 100%;
      max-width: 800px;
      margin: 0 auto;
    }}

    .section {{
      margin-bottom: 3rem;
    }}

    .stat {{
      display: flex;
      justify-content: space-between;
      padding: 0.5rem 0;
      border-bottom: 1px solid #333;
    }}

    .stat span:first-child {{
      color: #888;
    }}
</style></head>
<body>
<div class="container">
  <div class="section">
    <h1>audioserver</h1>
  </div>
  <div class="section">
    <h2>stream info</h2>
    <div class="stat"><span>stream url</span><span>{stream_url}</span></div>
    <div class="stat"><span>format</span><span>hls (aac)</span></div>
    <div class="stat"><span>sample rate</span><span>{sample_rate} hz</span></div>
    <div class="stat"><span>channels</span><span>{channels}</span></div>
    <div class="stat"><span>bitrate</span><span>{bitrate}</span></div>
    <div class="stat"><span>clients</span><span id="clients">0</span></div>
  </div>
  <div class="section">
    <h2>controls</h2>
    <div class="stat"><span>uptime</span><span id="uptime">{uptime}</span></div>
    <div class="stat"><span>status</span><span id="status">stopped</span></div>
    <button id="play-btn" onclick="toggleStream()">play</button>
  </div>
</div>
<audio id="player" style="display:none"></audio>

<script>
  const streamUrl = "{stream_url}";
  const audio = document.getElementById("player");
  let hls = null;
  let playing = false;

  function toggleStream() {{
    const btn = document.getElementById("play-btn");
    if (!playing) {{
      if (Hls.isSupported()) {{
        hls = new Hls();
        hls.loadSource(streamUrl);
        hls.attachMedia(audio);
        hls.on(Hls.Events.MANIFEST_PARSED, function() {{
          audio.play();
        }});
      }} else if (audio.canPlayType('application/vnd.apple.mpegurl')) {{
        audio.src = streamUrl;
        audio.play();
      }}
      btn.textContent = "stop";
      document.getElementById("status").textContent = "streaming";
      playing = true;
    }} else {{
      audio.pause();
      audio.src = "";
      if (hls) {{
        hls.destroy();
        hls = null;
      }}
      btn.textContent = "play";
      document.getElementById("status").textContent = "stopped";
      playing = false;
    }}
  }}

  // Poll status endpoint
  setInterval(async () => {{
    try {{
      const r = await fetch("/status");
      const d = await r.json();
      document.getElementById("clients").textContent = d.clients;
    }} catch {{}}
  }}, 3000);
</script>
</body>
</html>
"""


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
        # Serve static font file
        self._font_path = Path(__file__).parent / "DepartureMono-Regular.otf"
        app.router.add_get("/DepartureMono-Regular.otf", self._font_handler)
        return app

    async def _index_handler(self, request: web.Request) -> web.Response:
        uptime = self._elapsed()
        host = request.host if request.host else f"{self._host}:{self._port}"
        stream_url = f"http://{host}/stream.m3u8"
        html = HTML_PAGE.format(
            host=host,
            port=self._port,
            stream_url=stream_url,
            sample_rate=self._sample_rate,
            channels=self._channels,
            bitrate=self._bitrate,
            uptime=f"{uptime:.0f}s",
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
