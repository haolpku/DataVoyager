"""Loopback-only HTTP application; no web framework or frontend build required."""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from .workspace import Workspace

STATIC = Path(__file__).with_name("static")


class ChatServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, workspace):
        super().__init__(address, Handler)
        self.workspace = workspace
        self.token = secrets.token_urlsafe(32)
        port = self.server_address[1]
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {"http://" + host for host in self.hosts}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _check(self, mutate=False):
        if self.headers.get("Host") not in self.server.hosts:
            raise PermissionError("Local host required")
        origin = self.headers.get("Origin")
        if origin and origin not in self.server.origins:
            raise PermissionError("Cross-origin request rejected")
        if mutate and not hmac.compare_digest(self.headers.get("X-DataVoyager-Token", ""), self.server.token):
            raise PermissionError("Session token required; reload the page")

    def _send(self, status, content, mime="application/json; charset=utf-8", filename=None):
        if not isinstance(content, bytes):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self._dispatch(False)

    def do_POST(self):
        self._dispatch(True)

    def _dispatch(self, mutate):
        try:
            self._check(mutate)
            path = urlsplit(self.path).path
            parts = path.strip("/").split("/")
            app = self.server.workspace
            if not mutate:
                if path in {"/", "/app.js", "/style.css"}:
                    name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
                    mime = {"/": "text/html", "/app.js": "text/javascript", "/style.css": "text/css"}[path]
                    return self._send(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8")
                if path == "/api/bootstrap":
                    return self._send(200, {"token": self.server.token, "config": app.public_config(), "sessions": app.list_sessions()})
                if path == "/api/sessions":
                    return self._send(200, app.list_sessions())
                if len(parts) == 3 and parts[:2] == ["api", "sessions"]:
                    return self._send(200, app.snapshot(parts[2]))
                if len(parts) == 7 and parts[:2] == ["api", "sessions"] and parts[3] == "runs" and parts[5] == "download":
                    artifact = app.artifact(parts[2], parts[4], parts[6])
                    return self._send(200, artifact.read_bytes(), "application/octet-stream", artifact.name)
                return self._send(404, {"error": "Not found"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("JSON body required")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise ValueError("Request must be 1–65536 bytes")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            if path == "/api/config":
                return self._send(200, app.configure(data))
            if path == "/api/sessions":
                return self._send(201, app.create())
            if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "messages":
                return self._send(202, app.send(parts[2], data.get("message")))
            if len(parts) == 6 and parts[:2] == ["api", "sessions"] and parts[3] == "runs" and parts[5] == "cancel":
                return self._send(200, app.cancel(parts[2], parts[4]))
            self._send(404, {"error": "Not found"})
        except PermissionError as exc:
            self._send(403, {"error": str(exc)})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self._send(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._send(500, {"error": "后台处理失败，请检查运行目录和文件权限"})


def serve(root: Path, port=8765):
    import fcntl
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Prevent two supervisors from sharing and rewriting the same session state.
    with (root / ".server.lock").open("a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("This chat workspace is already open in another server") from None
        app = Workspace(root)
        server = ChatServer(("127.0.0.1", port), app)
        print(f"DataVoyager → http://127.0.0.1:{server.server_address[1]}\nWorkspace: {root}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.close()
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("runs/chat"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.root, args.port)
