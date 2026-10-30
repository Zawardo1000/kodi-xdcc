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


def test_menu(addon, monkeypatch):
    monkeypatch.setattr(addon, "client", lambda: FakeApi(
        irc_status={"collegato": True, "nel_canale": True, "nick": "giulia", "canale": "#c"}, prefetch={"ok": True}))
    addon.router("")
    labels = [i[1].label for i in plugin("addDirectoryItems")[0][1][1]]
    assert labels == ["Film · novità", "Serie · novità", "Cerca un film", "Cerca una serie", "Z-videoteca",
                      "Connessione IRC: collegato come giulia in #c · [I]verifica[/I]"]
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
    monkeypatch.setattr(addon.sys, "argv", addon.sys.argv[:3] + ["resume:true"])
    addon.router("?azione=guarda&id=AAA%3AHellfire.2026.mkv")
    assert ("watch", "AAA:Hellfire.2026.mkv", True, 0.0) in api.requests  # la ripresa arriva al servizio
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
                      pair_status=[{"stato": "attesa"}, {"stato": "ok", "chiave": "k-segreta", "utente": 18921256}],
                      direct_config=config)
        servers.append((with_key, fake_kodi.settings.get("chiave"), api))
        return api

    monkeypatch.setattr(addon, "server", server)
    addon.router("?azione=collega")
    assert fake_kodi.settings["chiave"] == "k-segreta" and fake_kodi.settings["utente_telegram"] == "18921256"
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
    assert ("notification", "Aggiornamento alla versione 0.2.0: arriva da solo tra poco") in fake_kodi.calls


def test_nome_del_dispositivo_per_telegram(addon):
    fake_kodi.conditions.update({"System.Platform.Android", "System.Platform.Linux"})
    fake_kodi.labels["System.FriendlyName"] = "SHIELD"
    label = addon.device_label()
    code = fake_kodi.settings["codice"]
    assert label == f"Kodi prova · SHIELD · Android · {code}" and len(code) == 4
    assert not set(code) & set("0O1I")  # niente caratteri che si confondono
    assert addon.device_label() == label  # il codice resta quello: è di questa installazione
    fake_kodi.labels["System.FriendlyName"] = "Kodi (192.168.0.50)"  # predefinito: "Kodi" e l'IP di casa, niente
    assert addon.device_label() == f"Kodi prova · Android · {code}"
    fake_kodi.labels["System.FriendlyName"] = "Salotto (192.168.0.50)"  # cambiato: il nome sì, l'IP no
    assert addon.device_label() == f"Kodi prova · Salotto · Android · {code}"
    fake_kodi.labels["System.FriendlyName"] = "Kodi"  # nome predefinito di Kodi: non dice niente
    fake_kodi.settings["dispositivo"] = ""
    assert addon.device_label() == f"Kodi Direct · Android · {code}"


def test_verifica_irc(addon, monkeypatch):
    api = FakeApi(irc_status=[{"collegato": False, "nel_canale": False, "errore": "ingresso nel canale #c rifiutato: "
                                                                                  "Cannot join channel (+b)"},
                              {"collegato": True, "nel_canale": True, "nick": "giulia", "canale": "#c",
                               "server": "irc.test:6697", "sentito_da": 3}])
    monkeypatch.setattr(addon, "client", lambda: api)
    addon.router("?azione=irc")
    assert ("irc_status", True) in api.requests
    assert any(c[0] == "ok" and "Non collegato" in c[1] and "(+b)" in c[1] for c in fake_kodi.calls)
    fake_kodi.calls.clear()
    addon.router("?azione=irc")
    assert any(c[0] == "ok" and "come [B]giulia[/B]" in c[1] and "3 s fa" in c[1] for c in fake_kodi.calls)



def test_punto_di_ripresa_dal_database_di_kodi(addon, monkeypatch, tmp_path):
    """Kodi salva il punto di ripresa con l'indirizzo intero dell'elemento (visto sulla TV, MyVideos131.db)."""
    import sqlite3
    url = "plugin://plugin.video.xdcc.direct/?azione=guarda&id=AAA%3AFilm.mkv"
    con = sqlite3.connect(tmp_path / "MyVideos131.db")
    con.executescript("""CREATE TABLE files (idFile INTEGER PRIMARY KEY, idPath INTEGER, strFilename TEXT);
        CREATE TABLE bookmark (idBookmark INTEGER PRIMARY KEY, idFile INTEGER, timeInSeconds DOUBLE,
                               totalTimeInSeconds DOUBLE, type INTEGER);""")
    con.execute("INSERT INTO files VALUES (1, 1, ?)", (url,))
    con.execute("INSERT INTO bookmark VALUES (1, 1, 1286.842123, 2740.992, 1)")
    con.commit()
    con.close()
    (tmp_path / "MyVideos121.db").write_bytes(b"")  # una versione vecchia: si legge la più nuova
    monkeypatch.setattr(addon.xbmcvfs, "translatePath", lambda path: str(tmp_path) + "/")
    monkeypatch.setattr(addon, "BASE", "plugin://plugin.video.xdcc.direct/")
    monkeypatch.setattr(addon.sys, "argv", ["plugin://plugin.video.xdcc.direct/", "5", "?azione=guarda&id=AAA%3AFilm.mkv",
                                            "resume:true"])
    assert addon.resume_point() == 1286.842123
    monkeypatch.setattr(addon.sys, "argv", ["plugin://plugin.video.xdcc.direct/", "5", "?azione=guarda&id=AAA%3AAltro.mkv"])
    assert addon.resume_point() == 0.0


# --- Z-videoteca ---

CARD = {"titolo_scheda": "Hellfire", "trama": "Fuoco.", "anno": 2026, "locandina": "https://img.test/p.jpg"}
SHOW = {"titolo": "The Last of Us", "cerca": "The Last of Us", "episodi": 2, "titolo_scheda": "The Last of Us",
        "locandina": "https://img.test/s.jpg"}
EPISODE = {"id": "BBB:The.Last.of.Us.S01E02.iTA.1080p.mkv", "titolo": "The Last of Us · S01E02 · 1080p · 1,2 GB",
           "nome": "The.Last.of.Us.S01E02.iTA.1080p.mkv", "dimensione": 1_300_000_000, "giorno": "", "stagione": 1,
           "episodio": 2}


def listed(n=-1):
    """Elementi dell'n-esima lista mostrata: (indirizzo, ListItem, cartella)."""
    return plugin("addDirectoryItems")[n][1][1]


def menu(item):
    """Voci del menu tenendo premuto OK: (etichetta, parametri di RunPlugin)."""
    return [(label, "?" + command[len("RunPlugin("):-1].partition("?")[2]) for label, command in item.props["menu"]]


def test_aggiungi_e_togli_dalla_z_videoteca(addon, monkeypatch):
    api = FakeApi(latest_films=[[{**FILM, **CARD}]] * 3, latest_shows=[[SHOW]])
    monkeypatch.setattr(addon, "client", lambda: api)
    addon.router("?azione=film_novita")
    [(label, add)] = menu(listed()[0][1])
    assert label == "Aggiungi alla Z-videoteca"
    addon.router(add)
    assert ("notification", "Nella Z-videoteca: Hellfire") in fake_kodi.calls
    addon.router(add)
    assert ("notification", "C'è già nella Z-videoteca: Hellfire") in fake_kodi.calls
    addon.router("?azione=serie_novita")
    [(label, add_show)] = menu(listed()[0][1])
    addon.router(add_show)
    assert ("notification", "Nella Z-videoteca: The Last of Us") in fake_kodi.calls

    addon.router("?azione=videoteca")
    shown = listed()
    assert [(i[1].label, i[2]) for i in shown] == [("The Last of Us", True), ("Hellfire 2026 · 1,6 GB", False)]
    assert shown[0][0] == "plugin://plugin.video.xdcc.direct/?azione=serie&q=The+Last+of+Us"
    # lo stesso indirizzo delle novità: Kodi ricorda punto di ripresa e "visto" in tutti e due i posti
    assert shown[1][0] == "plugin://plugin.video.xdcc.direct/?azione=guarda&id=AAA%3AHellfire.2026.mkv"
    assert shown[1][1].props["art"]["poster"] == "https://img.test/p.jpg"
    assert shown[1][1].tag.info["plot"].startswith("Fuoco.")
    [(label, remove)] = menu(shown[1][1])
    assert label == "Togli dalla Z-videoteca" and "qui=1" in remove
    addon.router(remove)
    assert ("notification", "Tolto dalla Z-videoteca: Hellfire") in fake_kodi.calls
    assert ("builtin", "Container.Refresh") in fake_kodi.calls
    addon.router("?azione=film_novita")
    assert menu(listed()[0][1])[0][0] == "Aggiungi alla Z-videoteca"


def test_z_videoteca_vuota(addon):
    addon.router("?azione=videoteca")
    [(url, item, is_folder)] = listed()
    assert "Vuota" in item.label and url.endswith("azione=zv_aiuto") and not is_folder
    addon.router("?azione=zv_aiuto")
    assert any(c[0] == "ok" and "Aggiungi alla Z-videoteca" in c[1] for c in fake_kodi.calls)


def test_da_un_episodio_si_aggiunge_la_serie(addon, monkeypatch):
    api = FakeApi(show_page={"stagioni": [{"numero": 1, "episodi": [EPISODE]}],
                             "serie": {"titolo": "The Last of Us", "cerca": "The Last of Us", **CARD}})
    monkeypatch.setattr(addon, "client", lambda: api)
    addon.router("?azione=serie&q=The+Last+of+Us")
    [(label, add)] = menu(listed()[0][1])
    assert label == "Aggiungi la serie alla Z-videoteca"
    addon.router(add)
    saved = addon.videoteca().show("the last of us")
    assert saved["cerca"] == "The Last of Us" and saved["locandina"] == "https://img.test/p.jpg"
    addon.router("?azione=serie&q=The+Last+of+Us")
    assert menu(listed()[0][1])[0][0] == "Togli la serie dalla Z-videoteca"


def kodi_db(folder, bookmarks):
    """Database dei video di Kodi con i punti di ripresa: [(indirizzo, secondi, durata)], dal più vecchio."""
    import sqlite3
    folder.mkdir()
    con = sqlite3.connect(folder / "MyVideos131.db")
    con.executescript("""CREATE TABLE files (idFile INTEGER PRIMARY KEY, idPath INTEGER, strFilename TEXT);
        CREATE TABLE bookmark (idBookmark INTEGER PRIMARY KEY, idFile INTEGER, timeInSeconds DOUBLE,
                               totalTimeInSeconds DOUBLE, type INTEGER);""")
    for n, (url, seconds, total) in enumerate(bookmarks, 1):
        con.execute("INSERT INTO files VALUES (?, 1, ?)", (n, url))
        con.execute("INSERT INTO bookmark VALUES (?, ?, ?, ?, 1)", (n, n, seconds, total))
    con.commit()
    con.close()


def test_continua_a_guardare(addon, monkeypatch, tmp_path):
    base = "plugin://plugin.video.xdcc.direct/?azione=guarda&id="
    kodi_db(tmp_path / "db", [
        (base + "AAA%3AHellfire.2026.mkv", 1286.8, 5400.0),  # film della videoteca
        (base + "AAA%3AAltro.Film.2025.mkv", 600.0, 5000.0),  # visto dalle novità, non nella videoteca
        ("plugin://plugin.video.altro/?id=AAA%3AHellfire.2026.mkv", 60.0, 100.0),  # un altro addon
        (base + "BBB%3AThe.Last.of.Us.S01E02.iTA.1080p.mkv", 75.0, 3300.0),  # episodio di una serie della videoteca
    ])
    real = addon.xbmcvfs.translatePath
    monkeypatch.setattr(addon.xbmcvfs, "translatePath",
                        lambda path: str(tmp_path / "db") + "/" if path == "special://database/" else real(path))
    addon.videoteca().add_film({**FILM, **CARD})
    addon.videoteca().add_show(SHOW)

    addon.router("?azione=videoteca")
    first = listed()[0]
    assert first[1].label == "[B]Continua a guardare[/B] (2)" and first[0].endswith("azione=continua") and first[2]
    addon.router("?azione=continua")
    shown = listed()
    # dal più recente; l'indirizzo è quello salvato da Kodi, così chiede da dove riprendere
    assert [i[1].label for i in shown] == ["The Last of Us · S01E02 · 1080p · [I]da 1:15[/I]",
                                           "Hellfire 2026 · 1,6 GB · [I]da 21:26[/I]"]
    assert shown[0][0] == base + "BBB%3AThe.Last.of.Us.S01E02.iTA.1080p.mkv"
    assert shown[0][1].tag.info["resumepoint"] == (75.0, 3300.0)
    assert shown[0][1].tag.info["tvshowtitle"] == "The Last of Us"
    assert shown[0][1].props["art"]["poster"] == "https://img.test/s.jpg"
    # tolta la serie dalla videoteca, i suoi episodi spariscono anche da qui
    [(label, remove)] = menu(shown[0][1])
    assert label == "Togli la serie dalla Z-videoteca"
    addon.router(remove)
    addon.router("?azione=continua")
    assert [i[1].label for i in listed()] == ["Hellfire 2026 · 1,6 GB · [I]da 21:26[/I]"]
    addon.router(menu(listed()[0][1])[0][1])  # e così il film
    addon.router("?azione=videoteca")
    assert "Continua" not in listed()[0][1].label


def test_film_sparito_si_sceglie_un_altra_versione(addon, monkeypatch):
    other = {"id": "BBB:Hellfire.2026.iTA.720p.mkv", "titolo": "Hellfire 2026 · 720p · 858 MB",
             "nome": "Hellfire.2026.iTA.720p.mkv", "dimensione": 900_000_000}
    remake = {"id": "AAA:Hellfire.2025.mkv", "titolo": "Hellfire 2025 · 1,1 GB", "nome": "Hellfire.2025.mkv",
              "dimensione": 1_200_000_000}
    api = FakeApi(watch=[{"stato": "avvio", "lavoro": "j1"}, {"stato": "avvio", "lavoro": "j2"},
                         {"stato": "pronto", "url": "http://127.0.0.1:1/v/g/j3/Hellfire.2026.iTA.720p.mkv"}],
                  status=[{"stato": "errore", "messaggio": "Non è più nelle liste.", "mancante": True},
                          {"stato": "pronto", "url": "http://127.0.0.1:1/v/g/j2/Hellfire.2026.iTA.720p.mkv"}],
                  search=[[{**FILM, "id": "BBB:Hellfire.2026.mkv"}, other, remake]])
    monkeypatch.setattr(addon, "client", lambda: api)
    addon.videoteca().add_film({**FILM, **CARD})
    fake_kodi.answers["select"] = 0
    addon.router("?azione=guarda&id=AAA%3AHellfire.2026.mkv")
    assert ("search", "Hellfire 2026", "film") in api.requests
    # solo le altre versioni dello stesso film (non lo stesso file da un'altra lista, non un altro anno)
    assert ("select", "«Hellfire» non c'è più: scegli un'altra versione",
            ["Hellfire 2026 · 720p · 858 MB"]) in fake_kodi.calls
    assert ("watch", "BBB:Hellfire.2026.iTA.720p.mkv", False, 0.0) in api.requests
    assert plugin("setResolvedUrl")[0][1][1] is True
    # d'ora in poi, dallo stesso indirizzo, si guarda direttamente la nuova versione
    assert addon.videoteca().film_by_id(FILM["id"])["sostituto"]["nome"] == "Hellfire.2026.iTA.720p.mkv"
    api.requests.clear()
    addon.router("?azione=guarda&id=AAA%3AHellfire.2026.mkv")
    assert api.requests[0] == ("watch", "BBB:Hellfire.2026.iTA.720p.mkv", False, 0.0)
    addon.router("?azione=videoteca")
    assert listed()[0][1].label == "Hellfire 2026 · 720p · 858 MB"


def test_film_sparito_scelta_annullata(addon, monkeypatch):
    other = {"id": "BBB:Hellfire.2026.iTA.720p.mkv", "titolo": "Hellfire 2026 · 720p", "nome": "Hellfire.2026.iTA.720p.mkv",
             "dimensione": 900_000_000}
    api = FakeApi(watch={"stato": "avvio", "lavoro": "j1"},
                  status={"stato": "errore", "messaggio": "Non è più nelle liste.", "mancante": True},
                  search=[[other]])
    monkeypatch.setattr(addon, "client", lambda: api)
    addon.videoteca().add_film(FILM)
    addon.router("?azione=guarda&id=AAA%3AHellfire.2026.mkv")  # select: -1, annullato
    assert plugin("setResolvedUrl")[0][1][1] is False
    assert "sostituto" not in addon.videoteca().film_by_id(FILM["id"])


def test_film_sparito_fuori_dalla_videoteca_solo_il_messaggio(addon, monkeypatch):
    api = FakeApi(watch={"stato": "avvio", "lavoro": "j1"},
                  status={"stato": "errore", "messaggio": "Non è più nelle liste.", "mancante": True})
    monkeypatch.setattr(addon, "client", lambda: api)
    addon.router("?azione=guarda&id=AAA%3AHellfire.2026.mkv")
    assert ("ok", "Non è più nelle liste.") in fake_kodi.calls
    assert not [r for r in api.requests if r[0] == "search"]
    assert plugin("setResolvedUrl")[0][1][1] is False


def test_aggiungi_anche_dalla_ricerca(addon, monkeypatch):
    api = FakeApi(search=[[{**FILM, **CARD}], [SHOW]])
    monkeypatch.setattr(addon, "client", lambda: api)
    fake_kodi.answers["input"] = "hellfire"
    addon.router("?azione=cerca&tipo=film")
    [(label, add)] = menu(listed()[0][1])
    assert label == "Aggiungi alla Z-videoteca"
    addon.router(add)
    fake_kodi.answers["input"] = "last of us"
    addon.router("?azione=cerca&tipo=serie")
    [(label, add_show)] = menu(listed()[0][1])
    assert label == "Aggiungi alla Z-videoteca"
    addon.router(add_show)
    assert [i["tipo"] for i in addon.videoteca().items] == ["film", "serie"]


# --- finestra Informazioni: trailer e cast ---

DETAILS = {"trailer": "ItTrailer01", "cast": [["Al", "Max", "https://img.test/al.jpg"], ["Bea", "Sara", ""]],
           "generi": ["Azione"], "regia": ["Rita"], "durata": 102}


def test_informazioni_con_trailer_e_cast(addon, monkeypatch):
    api = FakeApi(latest_films=[[{**FILM, **CARD, **DETAILS}]] * 2)
    monkeypatch.setattr(addon, "client", lambda: api)
    fake_kodi.conditions.add("System.Platform.Android")
    addon.router("?azione=film_novita")
    info = listed()[0][1].tag.info
    assert info["trailer"] == "plugin://plugin.video.xdcc.direct/?azione=trailer&v=ItTrailer01"
    assert info["cast"] == [("Al", "Max", 0, "https://img.test/al.jpg"), ("Bea", "Sara", 1, "")]
    assert info["genres"] == ["Azione"] and info["directors"] == ["Rita"] and info["duration"] == 102 * 60
    # il trailer nell'app (YouTube; nelle impostazioni anche SmartTube): a Kodi niente da riprodurre
    addon.router("?azione=trailer&v=ItTrailer01")
    assert ("builtin", "StartAndroidActivity(com.google.android.youtube.tv,android.intent.action.VIEW,,"
                       "https://www.youtube.com/watch?v=ItTrailer01)") in fake_kodi.calls
    assert plugin("setResolvedUrl")[-1][1][1] is False
    fake_kodi.settings["trailer"] = "1"
    addon.router("?azione=trailer&v=ItTrailer01")
    assert [c for c in fake_kodi.calls if c[0] == "builtin"][-1][1].startswith("StartAndroidActivity(org.smarttube.stable,")
    addon.router("?azione=trailer&v=x);Quit(")  # non è una chiave di YouTube: nessun comando
    assert not any(c[0] == "builtin" and "Quit" in c[1] for c in fake_kodi.calls)
    # fuori da Android (o se scelto nelle impostazioni): l'addon YouTube di Kodi
    fake_kodi.conditions.clear()
    monkeypatch.setattr(addon, "_trailer_app", None)
    addon.router("?azione=film_novita")
    assert listed()[0][1].tag.info["trailer"] == "plugin://plugin.video.youtube/play/?video_id=ItTrailer01"


def test_trailer_e_cast_restano_nella_videoteca(addon, monkeypatch):
    monkeypatch.setattr(addon, "client", lambda: FakeApi())
    addon.videoteca().add_film({**FILM, **CARD, **DETAILS})
    addon.router("?azione=videoteca")
    info = listed()[0][1].tag.info
    assert info["trailer"].endswith("video_id=ItTrailer01") and info["cast"][0][0] == "Al"

