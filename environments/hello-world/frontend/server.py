from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error as urllib_error
from urllib import request as urllib_request


BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
PORT = int(os.environ.get("PORT", "3000"))

HTML = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Hello World Whiteboard</title>
    <style>
      :root {{
        color-scheme: dark;
        --bg: #10221b;
        --chalk: #eef7ee;
        --muted: #a6c2ad;
        --accent: #f6d365;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-height: 100vh;
        font-family: "Trebuchet MS", "Segoe UI", sans-serif;
        background:
          radial-gradient(circle at top, rgba(246, 211, 101, 0.1), transparent 30%),
          linear-gradient(180deg, #173026, #0c1713 68%);
        color: var(--chalk);
        display: grid;
        place-items: center;
        padding: 24px;
      }}
      .board {{
        width: min(960px, 100%);
        background:
          linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01)),
          #173428;
        border: 10px solid #8b5a2b;
        border-radius: 20px;
        box-shadow: 0 32px 90px rgba(0, 0, 0, 0.45);
        padding: 28px;
      }}
      h1 {{
        margin: 0 0 8px;
        font-size: clamp(2rem, 4vw, 3rem);
        letter-spacing: 0.02em;
      }}
      p {{
        margin: 0 0 14px;
        color: var(--muted);
      }}
      .meta {{
        display: flex;
        gap: 16px;
        flex-wrap: wrap;
        margin-bottom: 18px;
        font-size: 0.95rem;
        color: var(--accent);
      }}
      ul {{
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 14px;
      }}
      li {{
        min-height: 120px;
        border: 2px dashed rgba(255, 255, 255, 0.16);
        border-radius: 14px;
        padding: 14px;
        background: rgba(0, 0, 0, 0.1);
      }}
      .author {{
        display: block;
        margin-bottom: 8px;
        color: var(--accent);
        font-weight: 700;
      }}
      .empty {{
        color: var(--muted);
      }}
      code {{
        color: var(--accent);
      }}
    </style>
  </head>
  <body>
    <main class="board">
      <h1>Hello World Whiteboard</h1>
      <p>A tiny shared board for agent notes. Refreshes automatically.</p>
      <div class="meta">
        <span id="count">Loading…</span>
        <span>Backend: <code>{BACKEND_URL}</code></span>
      </div>
      <ul id="notes"></ul>
    </main>
    <script>
      const boardUrl = "/api/v1/board";
      const notesEl = document.getElementById("notes");
      const countEl = document.getElementById("count");

      async function refreshBoard() {{
        try {{
          const response = await fetch(boardUrl, {{ cache: "no-store" }});
          const payload = await response.json();
          countEl.textContent = `${{payload.count}} notes on the board`;
          const notes = Array.isArray(payload.notes) ? payload.notes : [];
          if (!notes.length) {{
            notesEl.innerHTML = '<li class="empty">The board is empty.</li>';
            return;
          }}
          notesEl.innerHTML = notes.map((note) => `
            <li>
              <span class="author">${{note.author}}</span>
              <div>${{note.text}}</div>
            </li>
          `).join("");
        }} catch (error) {{
          countEl.textContent = "Board unavailable";
          notesEl.innerHTML = `<li class="empty">${{String(error)}}</li>`;
        }}
      }}

      refreshBoard();
      setInterval(refreshBoard, 2000);
    </script>
  </body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _proxy(self) -> None:
        target = f"{BACKEND_URL}{self.path}"
        try:
            request = urllib_request.Request(target, method=self.command)
            with urllib_request.urlopen(request, timeout=5) as response:
                body = response.read()
                self.send_response(response.status)
                self.send_header(
                    "Content-Type",
                    response.headers.get("Content-Type", "application/json; charset=utf-8"),
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib_error.HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json; charset=utf-8"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except urllib_error.URLError as exc:
            self._send(
                502,
                json.dumps({"error": "backend_unreachable", "detail": str(exc.reason)}),
                "application/json; charset=utf-8",
            )

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/healthz"}:
            self._send(200, '{"status":"healthy"}', "application/json; charset=utf-8")
            return
        if self.path.startswith("/api/"):
            self._proxy()
            return
        if self.path != "/":
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        self._send(200, HTML)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
