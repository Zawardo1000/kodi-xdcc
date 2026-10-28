"""
Client per main.py, solo libreria standard:
- ServiceApi: il servizio dell'addon (127.0.0.1, gettone nell'header X-Token), stessi metodi del client dell'altro
  addon verso il server, così il menu è lo stesso;
- ServerApi: il server del bot, solo per chi è registrato: abbinamento con Telegram e valori da mettere nelle
  impostazioni (pagina delle liste, server IRC, canale).
"""

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "plugin.video.xdcc.direct"


class ApiError(Exception):
    """Errore con il messaggio da mostrare (status HTTP; 0 = non raggiungibile)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _get(url, headers, timeout, unreachable):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, _detail(e)) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ApiError(0, f"{unreachable} ({type(e).__name__})") from None


def _detail(error):
    """Messaggio d'errore ({"detail": "..."}), altrimenti lo status HTTP."""
    try:
        detail = json.loads(error.read().decode("utf-8")).get("detail")
        if isinstance(detail, str) and detail:
            return detail
    except (ValueError, AttributeError, OSError):
        pass
    return f"Errore ({error.code})"


class ServiceApi:
    def __init__(self, port, token, timeout=60):
        self.base = f"http://127.0.0.1:{port}/api"
        self.token = token
        self.timeout = timeout

    def get(self, path, **params):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        return _get(url, {"X-Token": self.token}, self.timeout, "Il servizio dell'addon non risponde: riavvia Kodi")

    def latest_films(self):
        return self.get("/novita", tipo="film")["film"]

    def latest_shows(self):
        return self.get("/novita", tipo="serie")["serie"]

    def show_page(self, query):
        page = self.get("/serie", q=query)
        return {"stagioni": page["stagioni"], "serie": page.get("serie") or {}}

    def search(self, query, kind):
        return self.get("/cerca", q=query, tipo=kind)["serie" if kind == "serie" else "film"]

    def watch(self, item_id):
        return self.get("/guarda", id=item_id)

    def status(self, job, item_id):
        return self.get("/stato", lavoro=job, id=item_id)

    def cancel(self, job):
        return self.get("/annulla", lavoro=job)


class ServerApi:
    def __init__(self, server, key="", timeout=30):
        self.base = server.rstrip("/") + "/xdcc/api"
        self.key = key
        self.timeout = timeout

    def get(self, path, **params):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        return _get(url, {"X-Api-Key": self.key} if self.key else {}, self.timeout, "Server non raggiungibile")

    def pair_start(self, device):
        return self.get("/abbina/inizio", dispositivo=device)

    def pair_status(self, code, token):
        return self.get("/abbina/stato", codice=code, token=token)

    def direct_config(self):
        """{"pagina", "server", "ssl", "canale"}: i valori per le impostazioni."""
        return self.get("/direct")
