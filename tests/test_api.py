"""Client dell'API contro un piccolo server HTTP locale: chiave nell'header, parametri, errori del server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from resources.lib.api import ApiError, XdccApi

seen = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        seen.append((self.path, self.headers.get("X-Api-Key")))
        if self.path.startswith("/xdcc/api/novita"):
            body, code = {"film": [{"id": "RLD:a.mkv"}]}, 200
        else:
            body, code = {"detail": "Kodi non collegato"}, 401
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    seen.clear()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_chiave_e_parametri(server):
    films = XdccApi(server + "/", key="segreta").latest_films()
    assert films == [{"id": "RLD:a.mkv"}]
    assert seen == [("/xdcc/api/novita?tipo=film", "segreta")]


def test_errore_del_server(server):
    with pytest.raises(ApiError) as error:
        XdccApi(server, key="").on_server()
    assert error.value.status == 401 and error.value.message == "Kodi non collegato"
    assert seen[0][1] is None  # senza chiave: niente header


def test_server_irraggiungibile():
    with pytest.raises(ApiError) as error:
        XdccApi("http://127.0.0.1:9", timeout=2).latest_films()
    assert error.value.status == 0
