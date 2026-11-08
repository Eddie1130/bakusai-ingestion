"""Loopback-only HTTP transport for the local workspace; no external assets."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from . import services, templates
from .jobs import Jobs

logger = logging.getLogger(__name__)
STATIC_ROOT = Path(__file__).with_name("static")
MAX_BODY_BYTES = 8 * 1024 * 1024


class WorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 8765) -> None:
        self.workspace = services.Workspace()
        self.jobs = Jobs()
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def server_close(self) -> None:
        self.jobs.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: WorkspaceServer

    def log_message(self, format: str, *args) -> None:
        logger.debug(format, *args)

    def _send(
        self, status: int, body: bytes, mime: str = "application/json; charset=utf-8"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, value: dict) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def _trusted(self, api: bool) -> bool:
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            self._json(403, {"error": "僅允許從本機工作台存取"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in allowed}:
            self._json(403, {"error": "請從本機工作台操作"})
            return False
        if api and not secrets.compare_digest(
            self.headers.get("X-Workspace-Token", "").encode("utf-8"),
            self.server.token.encode("ascii"),
        ):
            self._json(403, {"error": "工作台連線已失效，請重新整理頁面"})
            return False
        return True

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if not self._trusted(route.startswith("/api/")):
            return
        if route == "/api/stores":
            self._dispatch(services.store_list)
        elif route == "/api/templates":
            self._dispatch(templates.catalog)
        elif route == "/api/job":
            self._json(200, self.server.jobs.current())
        elif route in {"/", "/app.js", "/style.css", "/icon.svg"}:
            filename = {"/": "index.html"}.get(route, route[1:])
            mime = {
                "html": "text/html",
                "js": "text/javascript",
                "css": "text/css",
                "svg": "image/svg+xml",
            }
            body = (STATIC_ROOT / filename).read_bytes()
            if route == "/":
                body = body.replace(
                    b"__WORKSPACE_TOKEN__", self.server.token.encode("ascii")
                )
            self._send(200, body, mime[filename.rsplit(".", 1)[1]] + "; charset=utf-8")
        else:
            self._json(404, {"error": "找不到這個頁面"})

    def do_POST(self) -> None:
        if not self._trusted(True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                self._json(413, {"error": "內容過長或為空，請縮小貼上的文字"})
                return
            if self.headers.get_content_type() != "application/json":
                raise ValueError("請使用工作台提供的表單")
            self.connection.settimeout(15)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("表單格式不正確")
        except (ValueError, UnicodeError, TimeoutError):
            self._json(400, {"error": "無法讀取表單內容，請重新操作"})
            return
        routes = {
            "/api/filter-batch": self.server.workspace.filter_batch,
            "/api/filter-report": self.server.workspace.full_filter_report,
            "/api/custom-threads": services.custom_threads,
            "/api/custom-preview": self.server.workspace.custom_preview,
            "/api/custom-page": self.server.workspace.custom_page,
            "/api/browse": services.browse,
            "/api/generate": self.server.workspace.generate,
            "/api/refine": self.server.workspace.refine,
            "/api/verify": self.server.workspace.verify,
            "/api/scrape": self.server.jobs.start,
            "/api/job": self.server.jobs.respond,
        }
        action = routes.get(urlsplit(self.path).path)
        if action is None:
            self._json(404, {"error": "找不到這個操作"})
        else:
            self._dispatch(lambda: action(data))

    def _dispatch(self, action) -> None:
        try:
            self._json(200, action())
        except (ValueError, OverflowError) as exc:
            self._json(400, {"error": str(exc)})
        except OSError:
            logger.exception("Workspace file operation failed")
            self._json(500, {"error": "無法讀取本機資料，請檢查檔案與存取權限"})
        except Exception:
            logger.exception("Workspace operation failed")
            self._json(500, {"error": "操作未完成，請查看終端機訊息後重試"})
