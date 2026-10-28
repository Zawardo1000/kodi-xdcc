"""Imita un file ancora in download come lo serve il bot: dimensione finale dichiarata, dati che arrivano a 2 MB/s.
/seek/...   come oggi: Accept-Ranges bytes, 416 per i pezzi lontani dalla parte arrivata
/noseek/... prova: Accept-Ranges none, solo dall'inizio"""
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PATH, T0, START, RATE, MARGIN = sys.argv[1], time.monotonic(), int(sys.argv[2]) << 20, 2 << 20, 8 << 20
SIZE = os.path.getsize(PATH)


def available():
    return min(SIZE, START + int(RATE * (time.monotonic() - T0)))


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        seekable = self.path.startswith("/seek/")
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d+)-(\d*)", rng or "")
        first = int(m.group(1)) if m else 0
        print(f"{time.monotonic() - T0:6.1f}s GET {self.path[:8]} Range={rng} arrivati={available() >> 20}MB "
              f"UA={self.headers.get('User-Agent', '')[:30]}", file=sys.stderr, flush=True)
        if first > available() + MARGIN or (not seekable and first > 0):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{SIZE}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206 if (m and seekable) else 200)
        self.send_header("Accept-Ranges", "bytes" if seekable else "none")
        self.send_header("Content-Length", str(SIZE - first))
        if m and seekable:
            self.send_header("Content-Range", f"bytes {first}-{SIZE - 1}/{SIZE}")
        self.send_header("Content-Type", "video/x-matroska")
        self.end_headers()
        pos = first
        with open(PATH, "rb") as f:
            while pos < SIZE:
                end = available()
                if pos >= end:
                    time.sleep(0.2)
                    continue
                data = os.pread(f.fileno(), min(1 << 20, end - pos), pos)
                try:
                    self.wfile.write(data)
                except OSError:
                    return
                pos += len(data)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", 8099), H).serve_forever()
