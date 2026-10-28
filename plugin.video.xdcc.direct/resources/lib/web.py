"""
Server HTTP minimo del servizio, solo su 127.0.0.1 (niente dalla rete di casa) e solo con il gettone del servizio,
che cambia a ogni avvio di Kodi: l'addon (main.py) lo legge dalle proprietà della finestra principale.

- GET /api/<nome>?... con l'header X-Token: le liste e lo stato, in JSON (stessa forma dell'API del server).
- GET/HEAD /v/<gettone>/<streaming>/<nome del file>: il video, con Range (206) per i salti di Kodi.
"""

import asyncio
import contextlib
import hmac
import json
import logging
import mimetypes
from urllib.parse import parse_qsl, unquote, urlsplit

from .stream import StreamError, Superseded

logger = logging.getLogger("xdcc.direct")

_MAX_HEADER = 8192
_READ_TIMEOUT = 15
_MIME = {"mkv": "video/x-matroska", "avi": "video/x-msvideo", "mp4": "video/mp4", "m4v": "video/mp4",
         "ts": "video/mp2t", "mpg": "video/mpeg", "mpeg": "video/mpeg", "wmv": "video/x-ms-wmv",
         "mov": "video/quicktime", "divx": "video/x-msvideo"}
_REASONS = {200: "OK", 206: "Partial Content", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
            405: "Method Not Allowed", 416: "Range Not Satisfiable", 500: "Internal Server Error",
            503: "Service Unavailable"}


class ApiError(Exception):
    """Risposta d'errore per l'addon: {"detail": messaggio}."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def parse_range(header, size):
    """Header Range -> (inizio, fine compresa); None senza Range; ValueError se non si può soddisfare."""
    if not header:
        return None
    unit, _, spec = header.partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        raise ValueError(header)
    first, _, last = spec.strip().partition("-")
    if first:
        start, end = int(first), int(last) if last else size - 1
    else:  # "bytes=-500": gli ultimi 500
        start, end = max(0, size - int(last)), size - 1
    end = min(end, size - 1)
    if start >= size or start > end:
        raise ValueError(header)
    return start, end


class WebServer:
    """api(nome, parametri) -> dizionario (o ApiError); stream_of(id) -> Stream o None."""

    def __init__(self, token, api, stream_of):
        self.token, self.api, self.stream_of = token, api, stream_of
        self.server = None
        self.port = 0

    async def start(self, ports):
        for port in ports:
            try:
                self.server = await asyncio.start_server(self._handle, "127.0.0.1", port)
                self.port = self.server.sockets[0].getsockname()[1]
                return self.port
            except OSError:
                continue
        raise OSError("nessuna porta libera per il servizio")

    async def close(self):
        if self.server:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 3)

    async def _handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _READ_TIMEOUT)
            if len(head) > _MAX_HEADER:
                raise ValueError("header troppo lungo")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                name, sep, value = line.partition(":")
                if sep:
                    headers[name.strip().lower()] = value.strip()
            url = urlsplit(target)
            path = unquote(url.path)
            if path.startswith("/api/"):
                await self._api(writer, method, path[5:], dict(parse_qsl(url.query)), headers)
            elif path.startswith("/v/"):
                await self._video(writer, method, path[3:], headers)
            else:
                await self._send_json(writer, 404, {"detail": "Non trovato"})
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ValueError,
                ConnectionError, OSError):
            pass
        except Exception:
            logger.exception("servizio: errore nella richiesta")
        finally:
            writer.close()

    def _token_ok(self, value):
        return hmac.compare_digest((value or "").encode(), self.token.encode())

    async def _send_head(self, writer, status, headers):
        lines = [f"HTTP/1.1 {status} {_REASONS.get(status, '')}"] + [f"{k}: {v}" for k, v in headers.items()]
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()

    async def _send_json(self, writer, status, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        await self._send_head(writer, status, {"Content-Type": "application/json; charset=utf-8",
                                               "Content-Length": len(data), "Connection": "close",
                                               "Cache-Control": "no-store"})
        writer.write(data)
        await writer.drain()

    async def _api(self, writer, method, name, params, headers):
        if not self._token_ok(headers.get("x-token")):
            await self._send_json(writer, 403, {"detail": "Gettone del servizio non valido"})
            return
        if method != "GET":
            await self._send_json(writer, 405, {"detail": "Solo GET"})
            return
        try:
            body, status = await self.api(name, params), 200
        except ApiError as e:
            body, status = {"detail": e.message}, e.status
        await self._send_json(writer, status, body)

    async def _video(self, writer, method, rest, headers):
        token, _, rest = rest.partition("/")
        sid, _, name = rest.partition("/")
        if not self._token_ok(token):
            await self._send_json(writer, 403, {"detail": "Gettone non valido"})
            return
        stream = self.stream_of(sid)
        if stream is None or stream.size is None:
            await self._send_json(writer, 404, {"detail": "Video non più disponibile"})
            return
        if method not in ("GET", "HEAD"):
            await self._send_json(writer, 405, {"detail": "Solo GET"})
            return
        size = stream.size
        try:
            wanted = parse_range(headers.get("range"), size)
        except ValueError:
            await self._send_head(writer, 416, {"Content-Range": f"bytes */{size}", "Content-Length": 0,
                                                "Connection": "close"})
            return
        start, end = wanted or (0, size - 1)
        extension = name.rsplit(".", 1)[-1].lower()
        response = {"Content-Type": _MIME.get(extension) or mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "Accept-Ranges": "bytes", "Content-Length": end - start + 1, "Connection": "close"}
        if wanted:
            response["Content-Range"] = f"bytes {start}-{end}/{size}"
        await self._send_head(writer, 206 if wanted else 200, response)
        if method == "HEAD":
            return
        reader = stream.attach(start)
        try:
            pos = start
            while pos <= end:
                data = await stream.read(reader, pos, min(256 * 1024, end + 1 - pos))
                writer.write(data)
                await writer.drain()
                pos += len(data)
        except (Superseded, StreamError):
            pass
        finally:
            stream.detach(reader)
