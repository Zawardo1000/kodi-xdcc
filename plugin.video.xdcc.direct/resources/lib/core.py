"""
Il servizio dell'addon, senza Kodi (service.py lo avvia e gli passa le impostazioni): liste, schede, IRC e
streaming, tutto in un loop asyncio su un suo thread, con il server locale di web.py davanti.

Verso i siti delle liste: una richiesta alla volta, almeno _WEB_GAP secondi tra una e l'altra, liste in memoria
(novità 15 minuti, ricerche 10 minuti, pagina di partenza 6 ore). Verso i bot: stream.Requester.
Un solo video alla volta: aprirne un altro chiude il precedente e libera il posto del bot.
"""

import asyncio
import contextlib
import logging
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

from . import cards, lists
from .irc import IrcClient, IrcError
from .stream import Requester, Stream
from .web import ApiError, WebServer

logger = logging.getLogger("xdcc.direct")

PORTS = (48771, 48772, 48773, 48774, 48775, 0)  # 0: una qualsiasi
IRC_IDLE_CLOSE = 1800  # la connessione IRC resta aperta 30 minuti dopo l'ultimo video
_WEB_GAP = 1.0
_WEB_TIMEOUT = 20
_PAGES_TTL = 6 * 3600
_LATEST_TTL = 900
_SEARCH_TTL = 600
_MAX_RELEASES = 200
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_HOST_RE = re.compile(r"[A-Za-z0-9.-]{1,253}")
_CHANNEL_RE = re.compile(r"[#&][^\s,\x07]{1,49}")


def parse_config(settings):
    """Impostazioni -> configurazione controllata. ApiError(400) con cosa manca, da mostrare nell'addon."""
    page = (settings.get("pagina") or "").strip()
    server = (settings.get("server_irc") or "").strip()
    channel = (settings.get("canale") or "").strip()
    nick = (settings.get("nick") or "").strip()
    missing = [label for label, value in (("pagina delle liste", page), ("server IRC", server), ("canale", channel))
               if not value]
    if missing:
        raise ApiError(400, "Da compilare nelle impostazioni: " + ", ".join(missing) + ".")
    if urllib.parse.urlsplit(page).scheme not in ("http", "https"):
        raise ApiError(400, "La pagina delle liste deve iniziare con http:// o https://")
    host, _, port = server.rpartition(":") if ":" in server else (server, "", "")
    use_ssl = settings.get("ssl", "true") != "false"
    try:
        port = int(port) if port else (6697 if use_ssl else 6667)
    except ValueError:
        raise ApiError(400, "Server IRC non valido: scrivilo come indirizzo:porta") from None
    if not _HOST_RE.fullmatch(host) or not 0 < port < 65536:
        raise ApiError(400, "Server IRC non valido: scrivilo come indirizzo:porta")
    if not channel.startswith(("#", "&")):
        channel = "#" + channel
    if not _CHANNEL_RE.fullmatch(channel):
        raise ApiError(400, "Nome del canale non valido")
    if not lists.NICK_RE.fullmatch(nick):
        raise ApiError(400, "Nome utente IRC non valido: solo lettere, senza spazi")
    fallback = (settings.get("nick_ripiego") or "").strip()
    fallback = fallback if fallback != nick and lists.NICK_RE.fullmatch(fallback) else ""
    if settings.get("nick_occupato") == nick and fallback:
        nicks = [fallback]  # il nome è risultato già preso: direttamente il ripiego (e se è preso, uno nuovo)
    else:
        nicks = [nick] + ([fallback] if fallback else [])
    return {"pagina": page, "host": host, "porta": port, "ssl": use_ssl, "canale": channel, "nicks": nicks, "nick": nick,
            "chiave_tmdb": (settings.get("chiave_tmdb") or "").strip()}


class Core:
    """settings() -> dizionario delle impostazioni (letto a ogni uso); save(id, valore) le scrive; notify(testo)
    mostra un avviso in Kodi."""

    def __init__(self, settings, save=None, notify=None, web_gap=_WEB_GAP):
        self.settings, self.save = settings, save or (lambda key, value: None)
        self.notify = notify or (lambda text: None)
        self.token = secrets.token_urlsafe(24)
        self.web = WebServer(self.token, self.api, self.stream_of)
        self.web_gap = web_gap
        self._web_lock = None
        self._web_last = -web_gap
        self._cache = {}
        self._irc = self._irc_key = None
        self._requester = None
        self._cards = self._cards_key = None
        self.stream = None
        self._starting = {}  # streaming in preparazione (si cercano i mirror): sid -> (nome, task)
        self._failed = {}  # sid -> messaggio, per stato() dopo un errore in preparazione
        self._prefetching = None
        self.update_target = None  # versione nuova dell'addon vista all'apertura: service.py insiste finché arriva
        self.update_tries = 0

    # --- avvio e arresto ---

    async def start(self, ports=PORTS):
        self._web_lock = asyncio.Lock()
        return await self.web.start(ports)

    async def close(self):
        if self.stream:
            await self.stream.close()
        for _, task in self._starting.values():
            task.cancel()
        if self._irc:
            await self._irc.close()
        await self.web.close()

    # --- impostazioni ---

    def config(self):
        return parse_config(self.settings())

    def _irc_for(self, config):
        key = (config["host"], config["porta"], config["ssl"], config["canale"], tuple(config["nicks"]))
        if self._irc is None or (key != self._irc_key and not self._irc.busy):
            old = self._irc
            if old is not None:
                asyncio.ensure_future(old.close())
            self._irc = IrcClient(config["host"], config["porta"], config["ssl"], config["canale"], config["nicks"],
                                  idle_close=IRC_IDLE_CLOSE, on_nick_taken=self._nick_taken, base=config["nick"])
            self._irc_key = key
            self._requester = Requester(self._irc)
        return self._irc, self._requester

    def _nick_taken(self, taken, now):
        """Un nick è già preso. Se è il nome scelto, la prossima volta si parte dal ripiego; il ripiego usato ora
        resta salvato."""
        nick = (self.settings().get("nick") or "").strip()
        if taken == nick:
            self.save("nick_occupato", nick)
        if now != nick:
            self.save("nick_ripiego", now)

    def _cards_for(self, config):
        if not config["chiave_tmdb"]:
            return None
        if config["chiave_tmdb"] != self._cards_key:
            self._cards = cards.TmdbCards(config["chiave_tmdb"])
            self._cards_key = config["chiave_tmdb"]
        return self._cards

    # --- siti delle liste ---

    async def _fetch(self, url, params=None, referer=None):
        """Una pagina dei siti delle liste: una richiesta alla volta, almeno web_gap secondi tra una e l'altra."""
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"User-Agent": _USER_AGENT}
        if referer:
            headers["Referer"] = referer

        def get():
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=_WEB_TIMEOUT) as response:
                return response.read().decode("utf-8-sig", "replace"), response.geturl()

        async with self._web_lock:
            wait = self._web_last + self.web_gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await asyncio.get_event_loop().run_in_executor(None, get)
            finally:
                self._web_last = time.monotonic()

    async def _cached(self, key, ttl, fetch):
        hit = self._cache.get(key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        value = await fetch()
        self._cache[key] = (value, time.monotonic() + ttl)
        return value

    async def _pages(self, config):
        async def fetch():
            html, final_url = await self._fetch(config["pagina"])
            pages = lists.parse_start_page(html, final_url)
            if not pages:
                raise ApiError(503, "Nella pagina delle liste non ci sono i pulsanti delle liste: controlla "
                                    "l'indirizzo nelle impostazioni.")
            return pages

        try:
            return await self._cached(("pagine", config["pagina"]), _PAGES_TTL, fetch)
        except OSError as e:
            raise ApiError(503, f"La pagina delle liste non risponde ({type(e).__name__}).") from None

    async def _from_sites(self, config, params, parse):
        """La stessa richiesta a tutte le liste, una dopo l'altra. ApiError(503) se nessuna risponde."""
        found, errors = [], 0
        for site, page in (await self._pages(config)).items():
            try:
                html, _ = await self._fetch(urllib.parse.urljoin(page, "lista.php"), params, referer=page)
                found += parse(html, site)
            except OSError as e:
                errors += 1
                logger.warning(f"lista {site} non letta ({type(e).__name__})")
        if errors and not found:
            raise ApiError(503, "Le liste non rispondono, riprova più tardi.")
        return found

    async def latest(self, config):
        async def fetch():
            return lists.by_day(await self._from_sites(config, {"func": "3"}, lists.parse_latest))
        return await self._cached(("novita", config["pagina"]), _LATEST_TTL, fetch)

    async def search(self, config, query):
        query = query.strip()
        if len(query) < lists.MIN_QUERY_LEN:
            raise ApiError(400, f"Scrivi almeno {lists.MIN_QUERY_LEN} caratteri")

        async def fetch():
            rows = await self._from_sites(config, {"func": "1", "q": query}, lists.parse_search)
            return lists.group_releases(rows)[:_MAX_RELEASES]
        return await self._cached(("cerca", config["pagina"], query.lower()), _SEARCH_TTL, fetch)

    async def _add_info(self, config, items, serie=None, title=None):
        provider = self._cards_for(config)
        if provider and items:
            entries = []
            for item in items:
                name = title or (item["titolo"] if "cerca" in item else lists.short_title(item["nome"]))
                entries.append(cards.entry_of(item, name, item.get("episodio") is not None if serie is None else serie))
            await provider.add_info(items, entries)
        return items

    # --- API per l'addon ---

    async def api(self, name, params):
        if name == "stato":
            return self.status(params.get("lavoro", ""))
        if name == "irc":
            return await self.irc_status(params.get("prova") == "1")
        if name == "annulla":
            return {"annullato": await self.cancel(params.get("lavoro", ""))}
        config = self.config()
        if name == "novita":
            files = await self.latest(config)
            if params.get("tipo") == "serie":
                return {"serie": await self._add_info(config, lists.latest_shows(files), serie=True)}
            return {"film": await self._add_info(config, lists.latest_films(files), serie=False)}
        if name == "cerca":
            kind = "serie" if params.get("tipo") == "serie" else "film"
            results = lists.search_results(await self.search(config, params.get("q", "")), kind)
            return {kind: await self._add_info(config, results, serie=kind == "serie")}
        if name == "serie":
            query = params.get("q", "")
            found = lists.seasons(query, await self.search(config, query))
            head = {"titolo": query, "cerca": query}
            await self._add_info(config, [head] + [ep for s in found for ep in s["episodi"]], serie=True, title=query)
            return {"titolo": query, "stagioni": found, "serie": head}
        if name == "guarda":
            try:
                seconds = float(params.get("da") or 0)
            except ValueError:
                seconds = 0.0
            seconds = seconds if 0 < seconds < 24 * 3600 else 0.0
            return await self.watch(config, params.get("id", ""), params.get("ripresa") == "1", seconds)
        if name == "aggiorna":
            self.update_target = params.get("versione") or None
            self.update_tries = 0
            return {"ok": True}
        if name == "prepara":
            self.prefetch()
            return {"ok": True}
        raise ApiError(404, "Richiesta sconosciuta")

    def prefetch(self):
        """Novità e schede in sottofondo, all'apertura dell'addon (mai ad addon chiuso): quando si entra nella lista
        sono già pronte. Niente se sono ancora in memoria o se lo sta già facendo."""
        if self._prefetching and not self._prefetching.done():
            return
        self._prefetching = asyncio.ensure_future(self._prefetch())

    async def _prefetch(self):
        try:
            config = self.config()
            files = await self.latest(config)
            provider = self._cards_for(config)
            if provider:
                for items, serie in ((lists.latest_films(files), False), (lists.latest_shows(files), True)):
                    entries = [cards.entry_of(item, item["titolo"] if "cerca" in item else lists.short_title(item["nome"]),
                                              serie) for item in items]
                    await provider.add_info(items, entries, wait=60)
        except (ApiError, OSError) as e:
            logger.info(f"liste non preparate: {getattr(e, 'message', type(e).__name__)}")

    async def irc_status(self, test):
        """Stato della connessione IRC per il menu; con test=True (scelto dalla persona) ci si collega davvero:
        server, canale e un PING."""
        if test:
            config = self.config()
            irc, _ = self._irc_for(config)
            try:
                async with irc.session():
                    alive = await irc.check_alive(timeout=10)
                    if not alive:
                        await irc.ensure_connected()
                        alive = await irc.check_alive(timeout=10)
            except IrcError as e:
                irc.last_error = irc.last_error or str(e)
        elif self._irc is None:
            return {"collegato": False, "nel_canale": False, "errore": "", "provato": False}
        status = self._irc.status()
        status["provato"] = test
        return status

    def stream_of(self, sid):
        return self.stream if self.stream and self.stream.sid == sid and not self.stream.closed else None

    def url(self, stream):
        return (f"http://127.0.0.1:{self.web.port}/v/{self.token}/{stream.sid}/"
                + urllib.parse.quote(stream.filename))

    async def watch(self, config, item_id, resume=False, seconds=0.0):
        site, _, filename = item_id.partition(":")
        if not filename or not lists.PLAYABLE_RE.search(filename):
            raise ApiError(400, "Si guardano solo file video singoli.")
        current = self.stream
        if current and current.filename == filename and not current.closed and not current.error:
            current.warm()  # la connessione IRC si riapre prima che Kodi parta, se nel frattempo si è chiusa
            current.resuming = resume
            current.prepare_resume(seconds if resume else None)
            return {"stato": "avvio", "lavoro": current.sid}
        await self._close_current()
        self._irc_for(config)
        sid = secrets.token_hex(4)
        self._starting[sid] = (filename, asyncio.ensure_future(
            self._prepare(config, sid, site, filename, resume, seconds)))
        return {"stato": "avvio", "lavoro": sid}

    async def _close_current(self):
        for sid, (_, task) in list(self._starting.items()):
            task.cancel()
            self._starting.pop(sid, None)
        stream, self.stream = self.stream, None
        if stream:
            await stream.close()

    async def _prepare(self, config, sid, site, filename, resume=False, seconds=0.0):
        """Mirror del file (dalla ricerca), poi lo streaming."""
        try:
            releases = await self.search(config, filename)
            same = [r for r in releases if r.playable and r.parts[0].filename == filename]
            release = next((r for r in same if r.site == site), same[0] if same else None)
            if release is None:
                raise ApiError(404, "Non è più nelle liste.")
            irc, requester = self._irc_for(config)
            stream = Stream(sid, filename, list(release.parts[0].sources), release.size, irc.session, requester)
            stream.on_error = self.notify
            stream.resuming = resume
            stream.resume_at = seconds if resume else None
            self.stream = stream
            stream.start()
        except ApiError as e:
            self._failed[sid] = e.message
        except (IrcError, OSError) as e:
            self._failed[sid] = f"Non riesco a partire ({type(e).__name__})."
        finally:
            self._starting.pop(sid, None)

    def status(self, sid):
        if sid in self._starting:
            return {"stato": "attesa", "messaggio": "Cerco il file nelle liste…", "ricevuti": 0, "totale": 100}
        if sid in self._failed:
            return {"stato": "errore", "messaggio": self._failed.pop(sid)}
        stream = self.stream_of(sid)
        if stream is None:
            return {"stato": "errore", "messaggio": "Streaming interrotto: riprova."}
        if stream.error:
            return {"stato": "errore", "messaggio": stream.error}
        if stream.ready:
            return {"stato": "pronto", "url": self.url(stream)}
        message = stream.phase
        if stream.received:
            message += f"\n{lists.fmt_size(stream.received)}" + (
                f" · {lists.fmt_size(stream.speed)}/s" if stream.speed else "")
        return {"stato": "attesa", "messaggio": message, "ricevuti": int(stream.progress() * 100), "totale": 100}

    async def cancel(self, sid):
        if sid in self._starting or (self.stream and self.stream.sid == sid):
            await self._close_current()
            return True
        return False


class Runner:
    """Il Core su un thread suo, con il suo loop asyncio: start() aspetta che il server locale sia pronto."""

    def __init__(self, core):
        self.core = core
        self.loop = None
        self._thread = None
        self._stop = None
        self._ready = threading.Event()
        self.error = None
        self.port = 0

    def start(self, timeout=10):
        self._thread = threading.Thread(target=self._main, name="xdcc-direct", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.error:
            raise self.error
        return self.port

    def _main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        finally:
            with contextlib.suppress(Exception):
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    async def _serve(self):
        self._stop = asyncio.Event()
        try:
            self.port = await self.core.start()
        except OSError as e:
            self.error = e
            self._ready.set()
            return
        self._ready.set()
        await self._stop.wait()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.core.close(), 4)

    def stop(self, timeout=5):
        if self.loop and self._stop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(timeout)
