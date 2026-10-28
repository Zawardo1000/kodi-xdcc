"""Streaming contro la finta rete IRC: avvio come fa Kodi con un MKV (inizio, indice in fondo, di nuovo l'inizio),
salti, salti ravvicinati, freno quando nessuno legge, code dei bot, nick già presi. Si controlla anche che le
richieste ai bot non siano mai insieme né più vicine di GAP."""

import asyncio
import contextlib
import time

import pytest
from fake_irc import FakeNetwork, content, loopback_offer

from resources.lib import stream as st
from resources.lib.irc import IrcClient

MB = st.MB
SIZE = 64 * MB
GAP = 0.3


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(st, "parse_dcc_send", loopback_offer)
    monkeypatch.setattr(st, "DEBOUNCE", 0.05)
    monkeypatch.setattr(st, "BOT_SETTLE", 0.6)  # il finto bot si accorge della chiusura dopo 0,5 s
    monkeypatch.setattr(st.random, "shuffle", lambda items: None)  # mirror nell'ordine della lista


async def _setup(sources=(("AAA|A|01", "#1"),), nicks=("giulia",), on_nick_taken=None, **network):
    net = await FakeNetwork(SIZE, **network).start()
    irc = IrcClient("127.0.0.1", net.port, False, "#canale", list(nicks), idle_close=60, on_nick_taken=on_nick_taken)
    stream = st.Stream("s1", net.filename, list(sources), SIZE, irc.session, st.Requester(irc, gap=GAP))
    return net, irc, stream


async def _cleanup(net, irc, stream):
    await stream.close()
    await irc.close()
    await net.close()


async def _read(stream, start, n):
    reader = stream.attach(start)
    out, pos = bytearray(), start
    try:
        while len(out) < n:
            data = await asyncio.wait_for(stream.read(reader, pos, min(256 * 1024, n - len(out))), 20)
            out += data
            pos += len(data)
    finally:
        stream.detach(reader)
    return bytes(out)


async def _ready(stream, timeout=20):
    deadline = time.monotonic() + timeout
    while not stream.ready:
        assert not stream.error, stream.error
        assert time.monotonic() < deadline, stream.phase
        await asyncio.sleep(0.02)


def _check_gaps(net):
    sends = net.sends()
    assert all(b[0] - a[0] >= GAP - 0.02 for a, b in zip(sends, sends[1:])), sends


def test_avvio_come_un_mkv_poi_salto():
    tail = SIZE - 300 * 1024

    async def run():
        net, irc, stream = await _setup()
        try:
            stream.start()
            await _ready(stream)
            assert stream.size == SIZE
            assert await _read(stream, 0, 64 * 1024) == content(0, 64 * 1024)  # intestazione
            assert await _read(stream, tail, SIZE - tail) == content(tail, SIZE - tail)  # indice in fondo
            assert await _read(stream, 20_000, 12 * MB) == content(20_000, 12 * MB)  # di nuovo l'inizio, oltre la testa
            assert await _read(stream, 45 * MB, MB) == content(45 * MB, MB)  # salto in avanti
            requests = len(net.sends())
            # inizio e fine di nuovo (Kodi li rilegge a ogni apertura): sono in RAM, nessuna richiesta
            assert await _read(stream, tail + 10, 1000) == content(tail + 10, 1000)
            assert await _read(stream, 100, 1000) == content(100, 1000)
            assert len(net.sends()) == requests
            return net
        finally:
            await _cleanup(net, irc, stream)

    net = asyncio.run(run())
    assert len(net.sends()) == 4  # inizio, fine, dopo la testa in RAM, salto
    assert [pos for _, pos in net.resumes] == [tail, st.HEAD_MAX, 45 * MB]
    assert {r[1] for r in net.sends()} == {"AAA|A|01"} and net.duplicates == 0  # mai "già richiesto"
    _check_gaps(net)


def test_salto_piccolo_aspetta_i_dati_senza_richiedere():
    async def run():
        net, irc, stream = await _setup()
        try:
            stream.start()
            await _ready(stream)
            assert await _read(stream, 0, MB) == content(0, MB)
            assert await _read(stream, MB + 5 * MB, MB) == content(6 * MB, MB)  # 5 MB più avanti: arrivano
            return net
        finally:
            await _cleanup(net, irc, stream)

    assert len(asyncio.run(run()).sends()) == 1


def test_salti_ravvicinati_una_sola_richiesta():
    async def run():
        net, irc, stream = await _setup()
        try:
            stream.start()
            await _ready(stream)
            abandoned = []
            for pos in (30 * MB, 40 * MB):  # avanti, avanti... (Kodi chiude la lettura vecchia a ogni salto)
                if abandoned:
                    stream.detach(abandoned[-1][0])
                reader = stream.attach(pos)
                abandoned.append((reader, asyncio.ensure_future(stream.read(reader, pos, 1000))))
                await asyncio.sleep(0.01)
            stream.detach(abandoned[-1][0])
            assert await _read(stream, 50 * MB, MB) == content(50 * MB, MB)  # ...si ferma qui
            for _, task in abandoned:
                with pytest.raises(st.ReaderGone):
                    await task
            return net
        finally:
            await _cleanup(net, irc, stream)

    net = asyncio.run(run())
    assert [pos for _, pos in net.resumes] == [50 * MB]
    _check_gaps(net)


def test_due_letture_insieme_come_nella_ripresa():
    """Riprendendo un video Kodi legge l'inizio del file con una connessione e il punto di ripresa con un'altra:
    la seconda non deve restare senza dati (il freno segue chi legge dal trasferimento, non l'ultima arrivata)."""
    async def run():
        net, irc, stream = await _setup()
        try:
            stream.start()
            await _ready(stream)
            head = stream.attach(0)
            far = stream.attach(30 * MB)
            pos_head, pos_far = 0, 30 * MB
            for _ in range(24):  # 24 MB dal punto di ripresa, il doppio della finestra, con l'inizio letto in mezzo
                data = await asyncio.wait_for(stream.read(head, pos_head % (2 * MB), 64 * 1024), 20)
                assert data == content(pos_head % (2 * MB), len(data))
                pos_head += len(data)
                block = b""
                while len(block) < MB:
                    block += await asyncio.wait_for(stream.read(far, pos_far + len(block), MB - len(block)), 20)
                assert block == content(pos_far, MB)
                pos_far += MB
            stream.detach(head)
            stream.detach(far)
            return net
        finally:
            await _cleanup(net, irc, stream)

    net = asyncio.run(run())
    assert [pos for _, pos in net.resumes] == [30 * MB]


def test_lettura_vecchia_ferma_non_ruba_il_trasferimento(monkeypatch):
    """Kodi tiene aperta, ferma, la connessione di prima (qui all'inizio del file); quella ha riempito il suo
    buffer fino in fondo alla parte in RAM e chiede il seguito. Finché la lettura nuova riceve dati il trasferimento
    resta suo; quando si ferma (Kodi è tornato alla connessione vecchia) passa all'altra."""
    monkeypatch.setattr(st, "OWNER_IDLE", 0.5)

    async def run():
        net, irc, stream = await _setup()
        try:
            stream.start()
            await _ready(stream)
            old = stream.attach(0)
            new = stream.attach(30 * MB)
            pos = 30 * MB
            first = await asyncio.wait_for(stream.read(new, pos, 256 * 1024), 20)  # il trasferimento ora è suo
            pos += len(first)
            parked = asyncio.ensure_future(stream.read(old, st.HEAD_MAX, 1000))  # oltre l'inizio in RAM
            for _ in range(12):  # la lettura nuova riceve dati: il trasferimento resta suo
                data = await asyncio.wait_for(stream.read(new, pos, 256 * 1024), 20)
                assert data == content(pos, len(data))
                pos += len(data)
                await asyncio.sleep(0.1)
            assert not parked.done() and [p for _, p in net.resumes] == [30 * MB]
            data = await asyncio.wait_for(parked, 20)  # la nuova è ferma: ora tocca alla vecchia
            assert data == content(st.HEAD_MAX, len(data))
            stream.detach(old)
            stream.detach(new)
            return net
        finally:
            await _cleanup(net, irc, stream)

    net = asyncio.run(run())
    assert [p for _, p in net.resumes] == [30 * MB, st.HEAD_MAX]


def test_punto_gia_scartato_dalla_ram_si_richiede():
    """Kodi fermato e riaperto in un punto del trasferimento in corso che la RAM ha già scartato: quei dati non
    arriveranno più da quel trasferimento, vanno richiesti (prima la lettura aspettava per sempre)."""
    async def run():
        net, irc, stream = await _setup()
        try:
            stream.start()
            await _ready(stream)
            assert await _read(stream, 0, 20 * MB) == content(0, 20 * MB)  # la finestra va oltre 9 MB e la scarta
            assert stream.buf_start > 9 * MB
            assert await _read(stream, 9 * MB, MB) == content(9 * MB, MB)
            return net
        finally:
            await _cleanup(net, irc, stream)

    net = asyncio.run(run())
    assert [pos for _, pos in net.resumes] == [9 * MB]


def test_ripresa_dallo_stesso_punto_senza_richiedere():
    """Video fermato e ripreso dove era: la finestra è rimasta in RAM (nessuno legge: niente si scarta)."""
    async def run():
        net, irc, stream = await _setup()
        try:
            stream.start()
            await _ready(stream)
            assert await _read(stream, 30 * MB, MB) == content(30 * MB, MB)
            await asyncio.sleep(0.5)  # fermato: il trasferimento continua un po' e poi frena
            requests = len(net.sends())
            assert await _read(stream, 30 * MB, 2 * MB) == content(30 * MB, 2 * MB)
            return net, requests
        finally:
            await _cleanup(net, irc, stream)

    net, requests = asyncio.run(run())
    assert len(net.sends()) == requests == 2


def test_nessuno_legge_il_bot_si_ferma():
    async def run():
        net, irc, stream = await _setup()
        try:
            stream.start()
            await _ready(stream)
            assert await _read(stream, 0, MB) == content(0, MB)
            await asyncio.sleep(1)  # Kodi ha la sua cache piena: non legge
            return net, len(stream.buf), stream.received
        finally:
            await _cleanup(net, irc, stream)

    net, window, received = asyncio.run(run())
    assert window <= st.AHEAD_MAX + st.BEHIND + 2 * MB
    assert received <= MB + st.AHEAD_MAX + 512 * 1024
    assert net.transfers[0][2] < SIZE  # il bot è rimasto indietro (TCP pieno), non ha mandato tutto


def test_coda_lunga_prova_un_altro_mirror():
    async def run():
        net, irc, stream = await _setup(sources=(("AAA|A|01", "#1"), ("AAA|A|02", "#7")),
                                        queue={"AAA|A|01": (5, 30)})
        try:
            stream.start()
            await _ready(stream)
            return net, stream.bot
        finally:
            await _cleanup(net, irc, stream)

    net, bot = asyncio.run(run())
    assert bot == "AAA|A|02"
    assert [(r[1], r[2]) for r in net.requests][:3] == [("AAA|A|01", "XDCC SEND #1"), ("AAA|A|01", "XDCC REMOVE"),
                                                         ("AAA|A|02", "XDCC SEND #7")]
    _check_gaps(net)


def test_coda_corta_si_aspetta():
    async def run():
        net, irc, stream = await _setup(sources=(("AAA|A|01", "#1"), ("AAA|A|02", "#7")),
                                        queue={"AAA|A|01": (1, 0.5)})
        try:
            stream.start()
            phases = set()
            while not stream.ready:
                phases.add(stream.phase)
                await asyncio.sleep(0.02)
            return net, stream.bot, phases
        finally:
            await _cleanup(net, irc, stream)

    net, bot, phases = asyncio.run(run())
    assert bot == "AAA|A|01" and "In coda da AAA|A|01 (posizione 1)" in phases
    assert not [r for r in net.requests if r[1] == "AAA|A|02"]


def test_bot_che_non_riparte_dal_punto_chiesto(monkeypatch):
    monkeypatch.setattr(st, "_RESUME_TIMEOUT", 0.5)

    async def run():
        net, irc, stream = await _setup(no_resume={"AAA|A|01"})
        try:
            stream.start()
            await _ready(stream)
            with pytest.raises(st.StreamError, match="non permette di saltare"):
                await _read(stream, 40 * MB, 1000)
        finally:
            await _cleanup(net, irc, stream)

    asyncio.run(run())


def test_nick_gia_preso_o_registrato():
    taken = []

    async def run(**network):
        net, irc, stream = await _setup(on_nick_taken=lambda old, new: taken.append((old, new)), **network)
        try:
            async with irc.session():
                await asyncio.sleep(0.2)
                return irc.nick, net.nicks
        finally:
            await _cleanup(net, irc, stream)

    nick, accepted = asyncio.run(run(taken={"giulia"}))
    assert nick.startswith("giulia") and len(nick) == 9 and nick[6:].isalpha() and accepted == [nick]
    assert taken == [("giulia", nick)]
    taken.clear()
    nick, accepted = asyncio.run(run(registered={"giulia"}))  # NickServ: è di un altro
    assert nick.startswith("giulia") and len(nick) == 9 and accepted == ["giulia", nick]
    assert taken == [("giulia", nick)]


def test_ripiego_salvato_si_usa_subito():
    async def run():
        net, irc, stream = await _setup(nicks=("giuliakrt", "giulia"), taken={"giulia"})
        try:
            async with irc.session():
                return irc.nick, net.lines
        finally:
            await _cleanup(net, irc, stream)

    nick, lines = asyncio.run(run())
    assert nick == "giuliakrt" and "NICK giulia" not in lines


def test_chiudere_libera_il_bot_in_coda():
    async def run():
        net, irc, stream = await _setup(queue={"AAA|A|01": (1, 30)})
        try:
            stream.start()
            while not net.requests:
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.2)
            await stream.close()
            await asyncio.sleep(0.1)
            return net
        finally:
            with contextlib.suppress(Exception):
                await _cleanup(net, irc, stream)

    net = asyncio.run(run())
    assert [r[2] for r in net.requests] == ["XDCC SEND #1", "XDCC REMOVE"]
