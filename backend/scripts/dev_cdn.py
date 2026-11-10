"""Development stand-in for the template CDN.

    python -m scripts.dev_cdn [--port 8001]

Serves ``<TEMPLATE_STORAGE_DIR>/blank_pdfs/<sha256>.pdf`` read-only on loopback, with
the CORS header a browser needs to fetch it from the dev frontend origin. It is a
separate process on purpose: the API never serves or proxies PDF bytes, exactly as in
production where a CDN/object store does this job (DEC-3).

Development only. It refuses to start in a shared environment, binds to 127.0.0.1,
serves only content-addressed blank PDFs, never lists directories and never redirects.
"""

from __future__ import annotations

import argparse
import pathlib
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.config import get_settings

_PATH = re.compile(r"^/blank_pdfs/([a-f0-9]{64})\.pdf$")


def make_handler(root: pathlib.Path, origins: set[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "govforms-dev-cdn"

        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def do_GET(self) -> None:
            match = _PATH.match(self.path)
            target = root / "blank_pdfs" / f"{match.group(1)}.pdf" if match else None
            if target is None or not target.is_file():
                self.send_response(404)
                self._cors()
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = target.read_bytes()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: object) -> None:
            status = args[1] if len(args) > 1 else ""
            print(f"dev-cdn {self.command} {self.path.split('?')[0]} -> {status}")

    return Handler


def main() -> None:
    settings = get_settings()
    if settings.is_shared_environment:
        raise SystemExit("dev_cdn is for local development only")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    root = pathlib.Path(settings.template_storage_dir)
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), make_handler(root, set(settings.cors_allowed_origins_list))
    )
    print(f"dev CDN serving {root}/blank_pdfs on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
