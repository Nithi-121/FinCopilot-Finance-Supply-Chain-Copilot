"""Serve only the standalone dashboard file on loopback; never expose .env or data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SHOWCASE = Path(__file__).resolve().parents[1] / "showcase.html"


class ShowcaseHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] not in {"/", "/showcase.html"}:
            self.send_error(404)
            return
        content = SHOWCASE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        print(f"{self.client_address[0]} - {format % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8502), ShowcaseHandler)
    print("FinCopilot showcase: http://127.0.0.1:8502/showcase.html")
    server.serve_forever()
