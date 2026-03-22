from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import socket
import threading
from urllib.request import urlopen


SERVER_PATH = Path(__file__).resolve().parent / "server.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("hello_world_frontend_server", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _BackendHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/api/v1/board":
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(
            {
                "title": "Hello World Whiteboard",
                "prompt": "Leave one short, playful note.",
                "notes": [{"author": "Caretaker", "text": "Test note"}],
                "count": 1,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # noqa: A003
        return


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_frontend_proxies_board_api(monkeypatch):
    backend_port = _free_port()
    backend = ThreadingHTTPServer(("127.0.0.1", backend_port), _BackendHandler)
    _serve(backend)

    monkeypatch.setenv("BACKEND_URL", f"http://127.0.0.1:{backend_port}")
    module = _load_module()

    frontend_port = _free_port()
    frontend = module.ThreadingHTTPServer(("127.0.0.1", frontend_port), module.Handler)
    _serve(frontend)
    try:
        response = urlopen(f"http://127.0.0.1:{frontend_port}/api/v1/board", timeout=5)
        payload = json.loads(response.read().decode("utf-8"))
        assert payload["count"] == 1
        assert payload["notes"][0]["text"] == "Test note"
    finally:
        frontend.shutdown()
        backend.shutdown()
