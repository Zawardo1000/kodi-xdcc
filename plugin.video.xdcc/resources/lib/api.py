"""
Client dell'API del server (/xdcc/api/... del bot LVI): solo libreria standard, così gira in ogni Kodi e si prova
senza Kodi. Ogni richiesta porta la chiave nell'header X-Api-Key (tranne i due passi dell'abbinamento).
"""

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "plugin.video.xdcc"


class ApiError(Exception):
    """Errore dal server (status HTTP e messaggio da mostrare) o server non raggiungibile (status 0)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class XdccApi:
    def __init__(self, server, key="", timeout=30):
        self.base = server.rstrip("/") + "/xdcc/api"
        self.key = key
        self.timeout = timeout

    def get(self, path, **params):
        """GET path?params -> dizionario JSON. ApiError con il messaggio del server se risponde con un errore."""
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        if self.key:
            request.add_header("X-Api-Key", self.key)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, _detail(e)) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiError(0, f"Server non raggiungibile ({e})") from None

    # --- abbinamento (senza chiave) ---

    def pair_start(self, device):
        return self.get("/abbina/inizio", dispositivo=device)

    def pair_status(self, code, token):
        return self.get("/abbina/stato", codice=code, token=token)

    # --- liste ---

    def latest_films(self):
        return self.get("/novita", tipo="film")["film"]

    def latest_shows(self):
        return self.get("/novita", tipo="serie")["serie"]

    def seasons(self, query):
        return self.get("/serie", q=query)["stagioni"]

    def search(self, query, kind):
        return self.get("/cerca", q=query, tipo=kind)["serie" if kind == "serie" else "film"]

    def on_server(self):
        return self.get("/server")["film"]

    # --- guardare ---

    def watch(self, item_id):
        return self.get("/guarda", id=item_id)

    def status(self, job, item_id):
        return self.get("/stato", lavoro=job, id=item_id)

    def cancel(self, job):
        return self.get("/annulla", lavoro=job)


def _detail(error):
    """Messaggio d'errore di FastAPI ({"detail": "..."}), altrimenti lo status HTTP."""
    try:
        detail = json.loads(error.read().decode("utf-8")).get("detail")
        if isinstance(detail, str) and detail:
            return detail
    except (ValueError, AttributeError, OSError):
        pass
    return f"Errore del server ({error.code})"
