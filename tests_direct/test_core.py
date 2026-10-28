"""Il servizio senza Kodi: impostazioni, liste dai siti (finti), e il giro completo guarda -> stato -> video dal
server locale con salti (Range), contro la finta rete IRC."""
import asyncio
import json
import time
import urllib.error
import urllib.request

import pytest
from fake_irc import FakeNetwork, content, loopback_offer
from test_lists import LISTA, novita

from resources.lib import core as core_module
from resources.lib import stream as st
from resources.lib.web import ApiError, parse_range

FILM = "Hellfire.2026.iTA.WEBRip.AC3.x264-WRM.mkv"
START = ('<a href="http://aaa.test/cerco/A.php"><span>A.A.A.</span></a>'
         '<a href="http://bbb.test/copia/B.php"><span>B.B.B.</span></a>')


def settings(**changes):
    values = {"pagina": "https://liste.test/start.html", "server_irc": "127.0.0.1:6667", "ssl": "false",
              "canale": "canale", "nick": "giulia"}
    values.update(changes)
    return values


def test_impostazioni_controllate():
    config = core_module.parse_config(settings())
    assert (config["host"], config["porta"], config["ssl"], config["canale"]) == ("127.0.0.1", 6667, False, "#canale")
    assert core_module.parse_config(settings(server_irc="irc.test", ssl="true"))["porta"] == 6697
    assert core_module.parse_config(settings(nick_ripiego="giuliakrt"))["nicks"] == ["giulia", "giuliakrt"]
    # il nome è risultato preso: solo il ripiego (se è preso anche quello, uno nuovo)
    assert core_module.parse_config(settings(nick_ripiego="giuliakrt", nick_occupato="giulia"))["nicks"] == \
        ["giuliakrt"]
    assert core_module.parse_config(settings(nick="marco", nick_ripiego="giuliakrt", nick_occupato="giulia"))[
        "nicks"] == ["marco", "giuliakrt"]  # nome cambiato dall'utente: si riprova quello
    with pytest.raises(ApiError, match="pagina delle liste, server IRC, canale"):
        core_module.parse_config({"nick": "giulia"})
    for bad in ({"server_irc": "irc.test:x"}, {"server_irc": "irc test"}, {"canale": "#a b"}, {"nick": "1abc"},
                {"pagina": "file:///etc/passwd"}):
        with pytest.raises(ApiError):
            core_module.parse_config(settings(**bad))


def test_nick_preso_salvato_bene():
    saved = {}
    values = settings()
    core = core_module.Core(lambda: values, save=saved.__setitem__)
    core._nick_taken("giulia", "giuliakrt")
    assert saved == {"nick_occupato": "giulia", "nick_ripiego": "giuliakrt"}
    saved.clear()
    core._nick_taken("giuliakrt", "giuliaxyz")  # preso anche il ripiego: il nome resta segnato, il ripiego cambia
    assert saved == {"nick_ripiego": "giuliaxyz"}


def test_intervallo_richiesto():
    assert parse_range(None, 100) is None
    assert parse_range("bytes=10-", 100) == (10, 99)
    assert parse_range("bytes=10-19", 100) == (10, 19)
    assert parse_range("bytes=-30", 100) == (70, 99)
    assert parse_range("bytes=90-500", 100) == (90, 99)
    for bad in ("bytes=100-", "bytes=5-2", "items=0-", "bytes=0-1,5-6"):
        with pytest.raises(ValueError):
            parse_range(bad, 100)


@pytest.fixture
def fake_sites(monkeypatch):
    asked = []

    async def fetch(self, url, params=None, referer=None):
        asked.append((url, params))
        if url.endswith("start.html"):
            return START, url
        site = "AAA" if "aaa." in url else "BBB"
        if params["func"] == "3":
            return novita([("O G G I", [FILM] if site == "BBB" else []),
                           ("I E R I", ["Altro.Film.2025.iTA.mkv"] if site == "AAA" else [])]), url
        return (LISTA if site == "AAA" else "<html></html>"), url

    monkeypatch.setattr(core_module.Core, "_fetch", fetch)
    monkeypatch.setattr(st, "parse_dcc_send", loopback_offer)
    monkeypatch.setattr(st, "DEBOUNCE", 0.05)
    return asked


def test_liste_e_ordine(fake_sites):
    async def run():
        core = core_module.Core(lambda: settings())
        await core.start(ports=(0,))
        try:
            films = (await core.api("novita", {"tipo": "film"}))["film"]
            again = (await core.api("novita", {"tipo": "film"}))["film"]
            found = (await core.api("cerca", {"q": "hellfire", "tipo": "film"}))["film"]
            return films, again, found
        finally:
            await core.close()

    films, again, found = asyncio.run(run())
    assert [f["nome"] for f in films] == [FILM, "Altro.Film.2025.iTA.mkv"]  # oggi (BBB) prima di ieri (AAA)
    assert again == films
    assert [f["id"] for f in found] == [f"AAA:{FILM}"]
    # pagina di partenza una volta, novità una volta per lista (poi in memoria), ricerca una volta per lista
    assert [(u.rsplit("/", 1)[-1], p and p["func"]) for u, p in fake_sites] == [
        ("start.html", None), ("lista.php", "3"), ("lista.php", "3"), ("lista.php", "1"), ("lista.php", "1")]


def test_guarda_e_salti_dal_server_locale(fake_sites):
    size = 8 * st.MB

    async def run():
        net = await FakeNetwork(size, filename=FILM).start()
        core = core_module.Core(lambda: settings(server_irc=f"127.0.0.1:{net.port}"))
        await core.start(ports=(0,))
        loop = asyncio.get_event_loop()
        try:
            started = await core.api("guarda", {"id": f"AAA:{FILM}"})
            assert started["stato"] == "avvio"
            deadline = time.monotonic() + 20
            status = core.status(started["lavoro"])
            while status["stato"] == "attesa" and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                status = core.status(started["lavoro"])
            assert status["stato"] == "pronto", status

            def get(url, headers):
                request = urllib.request.Request(url, headers=headers)
                try:
                    with urllib.request.urlopen(request, timeout=20) as response:
                        return response.status, dict(response.headers), response.read()
                except urllib.error.HTTPError as e:
                    return e.code, dict(e.headers), e.read()

            url = status["url"]
            first = await loop.run_in_executor(None, get, url, {"Range": "bytes=1000-2999"})
            end = await loop.run_in_executor(None, get, url, {"Range": f"bytes={size - 5000}-"})
            whole_head = await loop.run_in_executor(None, get, url, {})
            outside = await loop.run_in_executor(None, get, url, {"Range": f"bytes={size}-"})
            stolen = await loop.run_in_executor(None, get, url.replace(core.token, "x" * len(core.token)), {})
            api = f"http://127.0.0.1:{core.web.port}/api/stato?lavoro=x"
            no_token = await loop.run_in_executor(None, get, api, {})
            with_token = await loop.run_in_executor(None, get, api, {"X-Token": core.token})
            return first, end, whole_head, outside, stolen, no_token, with_token, net
        finally:
            await core.close()
            await net.close()

    first, end, whole, outside, stolen, no_token, with_token, net = asyncio.run(run())
    assert first[0] == 206 and first[1]["Content-Range"] == f"bytes 1000-2999/{size}" and first[2] == content(1000, 2000)
    assert end[0] == 206 and end[2] == content(size - 5000, 5000)
    assert whole[0] == 200 and whole[1]["Accept-Ranges"] == "bytes" and whole[2] == content(0, size)
    assert outside[0] == 416 and stolen[0] == 403 and no_token[0] == 403
    assert with_token[0] == 200 and json.loads(with_token[2])["stato"] == "errore"
    assert net.nicks == ["giulia"] and any(line == "JOIN #canale" for line in net.lines)


def test_connessione_chiusa_mentre_aspetta():
    """Kodi chiude la connessione (salto, stop) mentre la lettura aspetta i dati: la lettura finisce subito."""
    import socket

    from resources.lib.web import WebServer

    events = []

    class WaitingStream:
        size = 1000

        def attach(self, pos):
            events.append("attach")
            return object()

        def detach(self, reader):
            events.append("detach")

        async def read(self, reader, pos, n):
            await asyncio.sleep(30)  # i dati non arrivano

    async def run():
        web = WebServer("g", None, lambda sid: WaitingStream())
        port = await web.start((0,))
        loop = asyncio.get_event_loop()

        def request():
            with socket.create_connection(("127.0.0.1", port)) as sock:
                sock.sendall(b"GET /v/g/s1/film.mkv HTTP/1.1\r\nRange: bytes=500-\r\n\r\n")
                return sock.recv(1000)

        head = await loop.run_in_executor(None, request)
        for _ in range(50):
            if "detach" in events:
                break
            await asyncio.sleep(0.02)
        await web.close()
        return head

    head = asyncio.run(run())
    assert head.startswith(b"HTTP/1.1 206") and b"Content-Range: bytes 500-999/1000" in head
    assert events[:2] == ["attach", "detach"]


def test_stato_e_verifica_di_irc(fake_sites):
    async def run():
        net = await FakeNetwork(8 * st.MB, filename=FILM).start()
        core = core_module.Core(lambda: settings(server_irc=f"127.0.0.1:{net.port}"))
        await core.start(ports=(0,))
        try:
            before = await core.api("irc", {"prova": "0"})  # non si collega per questo
            tested = await core.api("irc", {"prova": "1"})  # scelto dalla persona: si collega e verifica
            return before, tested, net
        finally:
            await core.close()
            await net.close()

    before, tested, net = asyncio.run(run())
    assert not before["collegato"] and not before["provato"]
    assert tested["nel_canale"] and tested["nick"] == "giulia" and tested["canale"] == "#canale" and tested["provato"]
    assert net.nicks == ["giulia"]


def test_senza_impostazioni_lo_dice():
    async def run():
        core = core_module.Core(lambda: {"nick": "giulia"})
        await core.start(ports=(0,))
        try:
            with pytest.raises(ApiError) as error:
                await core.api("novita", {"tipo": "film"})
            return error.value
        finally:
            await core.close()

    error = asyncio.run(run())
    assert error.status == 400 and "impostazioni" in error.message


def test_novita_preparate_in_sottofondo(fake_sites):
    async def run():
        core = core_module.Core(lambda: settings())
        await core.start(ports=(0,))
        try:
            assert await core.api("prepara", {}) == {"ok": True}  # risponde subito
            await core._prefetching
            asked = len(fake_sites)
            films = (await core.api("novita", {"tipo": "film"}))["film"]
            return asked, films
        finally:
            await core.close()

    asked, films = asyncio.run(run())
    assert asked == 3 and len(fake_sites) == 3  # pagina di partenza e le due liste, una volta sola
    assert [f["nome"] for f in films] == [FILM, "Altro.Film.2025.iTA.mkv"]
