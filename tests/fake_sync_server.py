"""A tiny stand-in for the AnkiWeb sync server, enough to test the transport and UI."""

from __future__ import annotations

import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import zstandard


class FakeSyncServer:
    def __init__(self, redirect_meta_once: bool = False):
        self.requests: list[tuple[str, dict, object]] = []
        self.redirect_meta_once = redirect_meta_once
        self.col_mod = 0
        self.col_scm = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(self.rfile.read(length))).read()
                header = json.loads(self.headers.get("anki-sync", "{}"))
                body = json.loads(raw) if raw and raw[:1] in b"{[" else raw
                path = self.path.lstrip("/")
                server.requests.append((path, header, body))
                if path.endswith("sync/meta") and server.redirect_meta_once and not path.startswith("shard/"):
                    server.redirect_meta_once = False
                    self.send_response(308)
                    self.send_header("Location", f"http://127.0.0.1:{server.port}/shard/")
                    self.end_headers()
                    return
                status, payload = server.handle(path.removeprefix("shard/"), header, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                if status == 200:
                    self.send_header("anki-original-size", str(len(data)))
                    data = zstandard.ZstdCompressor().compress(data)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def handle(self, path: str, header: dict, body):
        if path == "sync/hostKey":
            if body.get("u") == "user" and body.get("p") == "pass":
                return 200, {"key": "HKEY"}
            return 403, "invalid credentials"
        if header.get("k") != "HKEY":
            return 403, "invalid key"
        if path == "sync/meta":
            return 200, {"mod": self.col_mod, "scm": self.col_scm, "usn": 0, "ts": int(time.time()),
                         "msg": "", "cont": True, "hostNum": 0, "empty": True, "media_usn": 0}
        if path == "msync/begin":
            return 200, {"data": {"usn": 0, "sk": "x"}, "err": ""}
        if path == "msync/mediaSanity":
            return 200, {"data": "OK", "err": ""}
        return 404, "unknown"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
