"""L'addon col finto Kodi e un finto server: menu, liste, attesa, riproduzione, collegamento."""
import fake_kodi
from resources.lib.api import ApiError


class FakeApi:
    """Server finto: risposte preparate dal test, richieste registrate."""

    def __init__(self, **responses):
        self.responses, self.requests = responses, []

    def _answer(self, name, *args):
        self.requests.append((name, *args))
        answer = self.responses[name]
        if isinstance(answer, list):  # una risposta per chiamata
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def __getattr__(self, name):
        return lambda *args: self._answer(name, *args)


def plugin(name):
    return [c for c in fake_kodi.calls if c[0] == name]


FILM = {"id": "RLD:Hellfire.2026.mkv", "titolo": "Hellfire 2026 · 1,6 GB", "nome": "Hellfire.2026.mkv",
        "dimensione": 1_700_000_000, "giorno": "oggi", "stagione": None, "episodio": None}


def test_menu_senza_chiave_propone_il_collegamento(addon):
    addon.router("")
    items = plugin("addDirectoryItems")[0][1][1]
    assert "Collega a Telegram" in items[0][1].label and "azione=collega" in items[0][0]
    fake_kodi.settings["chiave"] = "k"
    addon.router("")
    labels = [i[1].label for i in plugin("addDirectoryItems")[1][1][1]]
    assert labels == ["Film · novità", "Serie · novità", "Cerca un film", "Cerca una serie", "Già sul server"]


def test_novita_film(addon, monkeypatch):
    monkeypatch.setattr(addon, "client", lambda with_key=True: FakeApi(latest_films=[[FILM]]))  # una chiamata
    addon.router("?azione=film_novita")
    url, item, is_folder = plugin("addDirectoryItems")[0][1][1][0]
    assert not is_folder and item.props["IsPlayable"] == "true"
    assert url == "plugin://plugin.video.xdcc/?azione=guarda&id=RLD%3AHellfire.2026.mkv"
    assert item.tag.info["mediatype"] == "movie"


def test_guarda_aspetta_e_poi_parte(addon, monkeypatch):
    api = FakeApi(watch={"stato": "avvio", "lavoro": "j1"},
                  status=[{"stato": "attesa", "messaggio": "4 MB di 1,6 GB\nparte tra 1 min", "ricevuti": 4, "totale": 100},
                          {"stato": "pronto", "url": "https://server.test/xdcc/link/Hellfire.2026.mkv"}])
    monkeypatch.setattr(addon, "client", lambda with_key=True: api)
    addon.router("?azione=guarda&id=RLD%3AHellfire.2026.mkv")
    assert ("update", 4, "4 MB di 1,6 GB[CR]parte tra 1 min") in fake_kodi.calls
    resolved = plugin("setResolvedUrl")[0][1]
    assert resolved[1] is True and resolved[2].path.endswith("/Hellfire.2026.mkv")
    assert resolved[2].mime == "video/x-matroska" and resolved[2].props["contentlookup"] is False


def test_guarda_occupato(addon, monkeypatch):
    api = FakeApi(watch={"stato": "occupato", "messaggio": "C'è già uno streaming in corso da Kodi: riprova più tardi."})
    monkeypatch.setattr(addon, "client", lambda with_key=True: api)
    addon.router("?azione=guarda&id=RLD%3AAltro.mkv")
    assert ("ok", "C'è già uno streaming in corso da Kodi: riprova più tardi.") in fake_kodi.calls
    assert plugin("setResolvedUrl")[0][1][1] is False


def test_guarda_annullato_ferma_il_download(addon, monkeypatch):
    api = FakeApi(watch={"stato": "avvio", "lavoro": "j1"}, cancel={"annullato": True})
    monkeypatch.setattr(addon, "client", lambda with_key=True: api)
    fake_kodi.answers["canceled"] = [True]
    addon.router("?azione=guarda&id=RLD%3AHellfire.2026.mkv")
    assert ("cancel", "j1") in api.requests and plugin("setResolvedUrl")[0][1][1] is False


def test_chiave_non_valida_propone_di_ricollegare(addon, monkeypatch):
    monkeypatch.setattr(addon, "client", lambda with_key=True: FakeApi(latest_films=ApiError(401, "Kodi non collegato")))
    addon.router("?azione=film_novita")
    assert plugin("yesno") and plugin("endOfDirectory")[-1][2] == {"succeeded": False}


def test_serie_con_piu_stagioni_e_con_una(addon, monkeypatch):
    episode = dict(FILM, stagione=1, episodio=5)
    api = FakeApi(show_page=[{"stagioni": [{"numero": 1, "episodi": [episode]}, {"numero": 2, "episodi": [episode]}],
                              "serie": {"locandina": "https://img/serie.jpg"}},
                             {"stagioni": [{"numero": 1, "episodi": [episode]}], "serie": {}}])
    monkeypatch.setattr(addon, "client", lambda with_key=True: api)
    addon.router("?azione=serie&q=Nord")
    folders = plugin("addDirectoryItems")[0][1][1]
    assert [i[1].label for i in folders] == ["Stagione 1 (1 episodi)", "Stagione 2 (1 episodi)"]
    assert folders[0][1].props["art"]["poster"] == "https://img/serie.jpg"  # la locandina della serie
    addon.router("?azione=serie&q=Nord")  # una stagione sola: subito gli episodi
    url, item, is_folder = plugin("addDirectoryItems")[1][1][1][0]
    assert not is_folder and item.tag.info["season"] == 1 and item.tag.info["episode"] == 5


def test_collega(addon, monkeypatch):
    api = FakeApi(pair_start={"codice": "K7P2QX", "token": "t", "scade_tra": 600},
                  pair_status=[{"stato": "attesa", "chiave": None}, {"stato": "ok", "chiave": "segreta"}])
    monkeypatch.setattr(addon, "client", lambda with_key=True: api)
    assert addon.pair() is True
    assert fake_kodi.settings["chiave"] == "segreta"
    code = fake_kodi.settings["codice"]
    assert len(code) == 4 and ("pair_start", f"Kodi prova · {code}") in api.requests  # codice fisso, salvato
    assert any("/xdcc kodi K7P2QX" in c[1] for c in fake_kodi.calls if c[0] == "progress")


def test_schede_tmdb(addon, monkeypatch):
    film = dict(FILM, titolo_scheda="Hellfire", trama="Un incendio.", anno=2026, voto=6.8,
                locandina="https://img/p.jpg", sfondo="https://img/b.jpg")
    episode = dict(FILM, stagione=1, episodio=1, titolo_scheda="Sheriff Country", titolo_episodio="Pilota",
                   trama="Il primo.", locandina="https://img/s.jpg", miniatura="https://img/e1.jpg")
    show = {"titolo": "Sheriff Country", "cerca": "Sheriff Country", "episodi": 2, "trama": "La serie.",
            "locandina": "https://img/s.jpg"}
    api = FakeApi(latest_films=[[film]], latest_shows=[[show]],
                  show_page=[{"stagioni": [{"numero": 1, "episodi": [episode]}], "serie": {}}])
    monkeypatch.setattr(addon, "client", lambda with_key=True: api)
    addon.router("?azione=film_novita")
    item = plugin("addDirectoryItems")[0][1][1][0][1]
    assert item.label == "Hellfire 2026 · 1,6 GB"  # l'etichetta resta quella con dimensione e qualità
    assert item.tag.info["title"] == "Hellfire" and item.tag.info["year"] == 2026 and item.tag.info["rating"] == 6.8
    assert item.tag.info["plot"].startswith("Un incendio.\n\nHellfire.2026.mkv")
    assert item.props["art"] == {"poster": "https://img/p.jpg", "fanart": "https://img/b.jpg",
                                 "thumb": "https://img/p.jpg"}
    addon.router("?azione=serie_novita")
    folder = plugin("addDirectoryItems")[1][1][1][0][1]
    assert folder.tag.info["plot"] == "La serie." and folder.props["art"]["poster"] == "https://img/s.jpg"
    addon.router("?azione=serie&q=Sheriff+Country")
    item = plugin("addDirectoryItems")[2][1][1][0][1]
    assert item.tag.info["title"] == "Pilota" and item.tag.info["tvshowtitle"] == "Sheriff Country"
    assert item.props["art"]["thumb"] == "https://img/e1.jpg" and item.props["art"]["poster"] == "https://img/s.jpg"


class _Index:
    """Risposta finta di urlopen: l'indice del repository."""

    def __init__(self, version):
        self.body = (f'<addons><addon id="plugin.video.xdcc" name="Zawardo XDCC" version="{version}" '
                     'provider-name="zawardo"></addon></addons>').encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def test_aggiornamento_all_apertura(addon, monkeypatch, tmp_path):
    repo = tmp_path / "addons" / "repository.zawardo"
    repo.mkdir(parents=True)
    (repo / "addon.xml").write_text('<addon><extension><dir><info compressed="false">https://repo.test/addons.xml'
                                    '</info></dir></extension></addon>', encoding="utf-8")
    fake_kodi.home["path"] = str(tmp_path) + "/"
    asked = []

    def urlopen(request, timeout):
        asked.append(request.full_url)
        return _Index("0.10.0")  # 0.10 > 0.3: confronto per numeri, non per testo

    monkeypatch.setattr(addon.urllib.request, "urlopen", urlopen)
    fake_kodi.settings["chiave"] = "k"
    addon.router("")
    assert asked == ["https://repo.test/addons.xml"]  # l'indirizzo del repository installato
    assert ("builtin", "UpdateAddonRepos") in fake_kodi.calls
    assert ("notification", "Aggiornamento alla versione 0.10.0: arriva da solo tra poco") in fake_kodi.calls
    addon.router("")  # non ancora installata: alla prossima apertura si insiste
    assert len(asked) == 2


def test_niente_aggiornamento_se_e_gia_l_ultima(addon, monkeypatch):
    fake_kodi.home["path"] = "/non/esiste/"
    asked = []
    monkeypatch.setattr(addon.urllib.request, "urlopen", lambda request, timeout: asked.append(request.full_url)
                        or _Index("0.3.0"))
    addon.router("")
    # repository non leggibile: l'indirizzo di riserva (GitHub non ha detto l'ultimo commit: quello di sempre)
    assert asked == ["https://api.github.com/repos/Zawardo1000/kodi-xdcc/commits/main", addon.UPDATE_INDEX]
    assert not [c for c in fake_kodi.calls if c[0] == "builtin"]


def test_aggiornamento_senza_rete(addon, monkeypatch):
    def offline(request, timeout):
        raise OSError("rete giù")

    monkeypatch.setattr(addon.urllib.request, "urlopen", offline)
    addon.router("")
    assert plugin("addDirectoryItems")  # il menu c'è lo stesso
    assert not [c for c in fake_kodi.calls if c[0] == "builtin"]



def test_aggiornamento_senza_la_cache_di_github(addon, monkeypatch):
    """L'indice si legge all'ultimo commit (GitHub tiene in cache l'indirizzo di sempre fino a 5 minuti); niente
    secondo controllo per 10 minuti se è già l'ultima versione."""
    fake_kodi.home["path"] = "/non/esiste/"
    sha = "0123456789abcdef0123456789abcdef01234567"
    asked = []

    class Sha(_Index):
        def read(self):
            return sha.encode()

    def urlopen(request, timeout):
        asked.append(request.full_url)
        return Sha("") if "api.github.com" in request.full_url else _Index("0.3.0")

    monkeypatch.setattr(addon.urllib.request, "urlopen", urlopen)
    addon.router("")
    addon.router("")
    assert asked == ["https://api.github.com/repos/Zawardo1000/kodi-xdcc/commits/main",
                     f"https://raw.githubusercontent.com/Zawardo1000/kodi-xdcc/{sha}/repo/addons.xml"]


def test_zip_vecchi_tolti_all_apertura(addon, monkeypatch, tmp_path):
    import os
    packages = tmp_path / "addons" / "packages"
    packages.mkdir(parents=True)
    for name in ("plugin.video.xdcc-0.3.2.zip", "plugin.video.altro-2.0.zip"):
        (packages / name).write_bytes(b"zip")
        os.utime(packages / name, (1, 1))
    fake_kodi.home["path"] = str(tmp_path) + "/"
    monkeypatch.setattr(addon.urllib.request, "urlopen", lambda request, timeout: _Index("0.0.1"))
    addon.router("")
    assert os.listdir(packages) == ["plugin.video.altro-2.0.zip"]
