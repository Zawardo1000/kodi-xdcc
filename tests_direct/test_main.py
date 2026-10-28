"""Il menu di Direct col finto Kodi e un finto servizio: configurazione, liste, attesa, riproduzione, compilazione da
Telegram, aggiornamento delle impostazioni dal server."""
import fake_kodi

from resources.lib.api import ApiError


class FakeApi:
    """Servizio o server finto: risposte preparate dal test, richieste registrate."""

    def __init__(self, **responses):
        self.responses, self.requests = responses, []

    def _answer(self, name, *args):
        self.requests.append((name, *args))
        answer = self.responses[name]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def __getattr__(self, name):
        return lambda *args: self._answer(name, *args)


def plugin(name):
    return [c for c in fake_kodi.calls if c[0] == name]


FILM = {"id": "AAA:Hellfire.2026.mkv", "titolo": "Hellfire 2026 · 1,6 GB", "nome": "Hellfire.2026.mkv",
        "dimensione": 1_700_000_000, "giorno": "oggi", "stagione": None, "episodio": None}


def test_menu(addon):
    addon.router("")
    labels = [i[1].label for i in plugin("addDirectoryItems")[0][1][1]]
    assert labels == ["Film · novità", "Serie · novità", "Cerca un film", "Cerca una serie"]
    fake_kodi.settings["canale"] = ""
    addon.router("")
    items = plugin("addDirectoryItems")[1][1][1]
    assert "Compila da Telegram" in items[0][1].label and "azione=collega" in items[0][0]
    assert "azione=impostazioni" in items[1][0]


def test_nome_utente_a_caso_alla_prima_apertura(addon):
    fake_kodi.settings["nick"] = ""
    addon.router("")
    assert fake_kodi.settings["nick"] in addon.names.NAMES


def test_servizio_non_partito(addon):
    fake_kodi.window.clear()
    addon.router("?azione=film_novita")
    assert ("ok", "Il servizio dell'addon non è partito: riavvia Kodi.") in fake_kodi.calls


def test_impostazioni_mancanti_le_apre(addon, monkeypatch):
    api = FakeApi(latest_films=ApiError(400, "Da compilare nelle impostazioni: canale."))
    monkeypatch.setattr(addon, "client", lambda: api)
    fake_kodi.answers["yesno"] = True
    addon.router("?azione=film_novita")
    assert ("impostazioni",) in fake_kodi.calls


def test_novita_e_guarda(addon, monkeypatch):
    api = FakeApi(latest_films=[[FILM]], watch={"stato": "avvio", "lavoro": "j1"},
                  status=[{"stato": "attesa", "messaggio": "Richiesta a AAA|A|01", "ricevuti": 1, "totale": 2},
                          {"stato": "pronto", "url": "http://127.0.0.1:48771/v/g/j1/Hellfire.2026.mkv"}])
    monkeypatch.setattr(addon, "client", lambda: api)
    addon.router("?azione=film_novita")
    url, item, is_folder = plugin("addDirectoryItems")[0][1][1][0]
    assert url == "plugin://plugin.video.xdcc.direct/?azione=guarda&id=AAA%3AHellfire.2026.mkv" and not is_folder
    addon.router("?azione=guarda&id=AAA%3AHellfire.2026.mkv")
    assert ("update", 50, "Richiesta a AAA|A|01") in fake_kodi.calls
    resolved = plugin("setResolvedUrl")[0][1]
    assert resolved[1] is True and resolved[2].path.startswith("http://127.0.0.1:48771/")
    assert resolved[2].mime == "video/x-matroska" and resolved[2].props["contentlookup"] is False


def test_guarda_errore(addon, monkeypatch):
    api = FakeApi(watch={"stato": "avvio", "lavoro": "j1"},
                  status={"stato": "errore", "messaggio": "Il bot non permette di saltare"})
    monkeypatch.setattr(addon, "client", lambda: api)
    addon.router("?azione=guarda&id=AAA%3AHellfire.2026.mkv")
    assert ("ok", "Il bot non permette di saltare") in fake_kodi.calls
    assert plugin("setResolvedUrl")[0][1][1] is False


def test_compila_da_telegram(addon, monkeypatch):
    for key in ("pagina", "server_irc", "canale"):
        fake_kodi.settings[key] = ""
    config = {"pagina": "https://liste.example/p.html", "server": "irc.example:6697", "ssl": True,
              "canale": "#Canale", "tmdb": "chiave-tmdb"}
    servers = []

    def server(with_key=True):
        api = FakeApi(pair_start={"codice": "ABC123", "token": "t", "scade_tra": 600},
                      pair_status=[{"stato": "attesa"}, {"stato": "ok", "chiave": "k-segreta"}],
                      direct_config=config)
        servers.append((with_key, fake_kodi.settings.get("chiave"), api))
        return api

    monkeypatch.setattr(addon, "server", server)
    addon.router("?azione=collega")
    assert fake_kodi.settings["chiave"] == "k-segreta"
    assert (fake_kodi.settings["pagina"], fake_kodi.settings["server_irc"], fake_kodi.settings["canale"],
            fake_kodi.settings["chiave_tmdb"]) == ("https://liste.example/p.html", "irc.example:6697", "#Canale",
                                                   "chiave-tmdb")
    assert servers[-1][:2] == (True, "k-segreta")  # i valori si chiedono con la chiave appena ricevuta
    assert ("notification", "Impostazioni compilate da Telegram") in fake_kodi.calls


def test_impostazioni_aggiornate_una_volta_al_giorno(addon, monkeypatch):
    fake_kodi.settings["chiave"] = "k"
    asked = []

    def server(with_key=True):
        asked.append(with_key)
        return FakeApi(direct_config={"pagina": "https://nuova.example/p.html", "server": "irc.test:6697",
                                      "ssl": True, "canale": "#canale"})

    monkeypatch.setattr(addon, "server", server)
    addon.router("")
    assert fake_kodi.settings["pagina"] == "https://nuova.example/p.html"
    assert ("notification", "Impostazioni aggiornate dal server") in fake_kodi.calls
    addon.router("")  # poco dopo: niente richiesta
    assert asked == [True]


def test_aggiornamento_guarda_il_suo_addon(addon, monkeypatch):
    index = ('<addons><addon id="plugin.video.xdcc" name="Zawardo XDCC" version="9.0.0"></addon>'
             '<addon id="plugin.video.xdcc.direct" name="Zawardo XDCC Direct" version="0.2.0"></addon></addons>')

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return index.encode()

    monkeypatch.setattr(addon.urllib.request, "urlopen", lambda request, timeout: Response())
    addon.router("")
    assert ("notification", "Aggiornamento alla versione 0.2.0…") in fake_kodi.calls


def test_nome_del_dispositivo_per_telegram(addon):
    fake_kodi.conditions.update({"System.Platform.Android", "System.Platform.Linux"})
    fake_kodi.labels["System.FriendlyName"] = "SHIELD"
    label = addon.device_label()
    code = fake_kodi.settings["codice"]
    assert label == f"Kodi prova · SHIELD · Android · {code}" and len(code) == 4
    assert not set(code) & set("0O1I")  # niente caratteri che si confondono
    assert addon.device_label() == label  # il codice resta quello: è di questa installazione
    fake_kodi.labels["System.FriendlyName"] = "Kodi"  # nome predefinito di Kodi: non dice niente
    fake_kodi.settings["dispositivo"] = ""
    assert addon.device_label() == f"Kodi Direct · Android · {code}"
