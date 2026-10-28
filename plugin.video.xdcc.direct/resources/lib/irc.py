"""
Client IRC minimo, preso dal bot del server e adattato a Kodi: una sola connessione, aperta al primo video e chiusa
dopo idle_close secondi senza usarla (il server blocca chi si riconnette troppo in fretta).

Fa solo quello che serve: registrazione, PING, ingresso nel canale (i bot servono solo chi c'è), messaggi e NOTICE
dei bot, offerte DCC SEND, DCC RESUME/ACCEPT per partire da un byte qualsiasi. Il DCC è "attivo": è il bot ad aprire
la porta e Kodi si collega a lui, quindi a casa non serve aprire niente. Il DCC passivo (porta 0) non è gestito.

Connessione morta in silenzio (per esempio l'IP di casa che cambia: le connessioni aperte muoiono senza avviso):
se il server tace da _QUIET secondi gli si manda un PING, e senza risposta entro _PONG_WAIT la connessione è
chiusa. Prima di ogni richiesta ai bot check_alive() verifica che il server risponda. Il canale è seguito
(in_channel): espulsi, si riprova a entrare una volta; rifiutati (ban), lo dice last_error.

Nick: un nome normale (impostazioni). Se è già in uso, o registrato da un altro (NickServ chiede la password), si
passa al ripiego: lo stesso nome con 3 lettere in fondo. on_nick_taken lo fa sapere al servizio, che la volta dopo
parte direttamente dal ripiego.
"""

import asyncio
import contextlib
import ipaddress
import logging
import random
import re
import ssl
import string
import time

from .lists import NICK_RE

logger = logging.getLogger("xdcc.direct")

_RECONNECT_GAP = 60  # "Throttled: Reconnecting too fast" a chi si ricollega dopo pochi secondi
_CONNECT_TIMEOUT = 30
_REGISTER_TIMEOUT = 90  # il server cerca hostname e ident prima di accettare: anche 20-30 secondi
_JOIN_TIMEOUT = 30
_JOIN_ERRORS = {"403", "405", "471", "473", "474", "475", "477"}
_QUIET = 150  # secondi senza niente dal server (che manda un PING ogni minuto o due): gli si manda un PING
_PONG_WAIT = 60  # e se non risponde entro questo tempo la connessione è morta
_ALIVE_AFTER = 60  # check_alive(): server sentito da meno di così, va bene senza chiedere
_REJOIN_DELAY = 5
_PING_TIMEOUT = 10  # check_alive(): attesa del PONG
# NickServ: "nome registrato, identificati (IDENTIFY) o cambia nome". Non i semplici avvisi ("non è registrato")
_NICKSERV_TAKEN_RE = re.compile(r"\bidentify\b", re.I)
_NICKSERV_FREE_RE = re.compile(r"not\s+registered|isn'?t\s+registered|non\s+(?:è|e')\s+registrat", re.I)

# Comando speciale passato agli ascoltatori quando la connessione cade
DISCONNECTED = "*DISCONNECTED*"

_COLORS_RE = re.compile(r"\x03\d{0,2}(?:,\d{1,2})?|[\x02\x0f\x16\x1d\x1f\x11\x1e]")


class IrcError(Exception):
    """Connessione IRC non riuscita o persa."""


class DccError(Exception):
    """Offerta DCC non utilizzabile o trasferimento non riuscito."""


class IrcMessage:
    def __init__(self, prefix, command, params):
        self.prefix, self.command, self.params = prefix, command, params

    @property
    def nick(self):
        return self.prefix.split("!", 1)[0]


def parse_line(line):
    """Una riga IRC (senza a capo) -> prefisso, comando, parametri; l'ultimo parametro può contenere spazi."""
    if line.startswith("@"):  # tag IRCv3: non servono
        line = line.partition(" ")[2]
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    line, sep, trailing = line.partition(" :")
    params = line.split()
    command = params.pop(0).upper() if params else ""
    if sep:
        params.append(trailing)
    return IrcMessage(prefix, command, params)


def strip_colors(text):
    """Testo senza i codici di colore e stile di mIRC."""
    return _COLORS_RE.sub("", text)


class DccOffer:
    def __init__(self, nick, filename, host, port, size):
        self.nick, self.filename, self.host, self.port, self.size = nick, filename, host, port, size


_DCC_SEND_RE = re.compile(
    r'DCC SEND (?:"(?P<quoted>[^"]+)"|(?P<plain>\S+)) (?P<ip>\S+) (?P<port>\d+) (?P<size>\d+)(?: \S+)?\s*', re.I)
_DCC_ACCEPT_RE = re.compile(r'DCC ACCEPT (?:"[^"]+"|\S+) (?P<port>\d+) (?P<pos>\d+)\s*', re.I)


def parse_dcc_send(nick, ctcp):
    """Testo CTCP "DCC SEND file ip porta dimensione [token]" -> offerta; DccError se non si può usare."""
    m = _DCC_SEND_RE.fullmatch(ctcp)
    if not m:
        raise DccError("offerta DCC non riconosciuta")
    ip_text, port, size = m["ip"], int(m["port"]), int(m["size"])
    try:
        ip = ipaddress.ip_address(int(ip_text) if ip_text.isdigit() else ip_text)
    except ValueError:
        raise DccError(f"indirizzo DCC non valido: {ip_text}") from None
    if port == 0:
        raise DccError("il bot chiede un DCC passivo, che qui non si può fare")
    if port > 65535 or size <= 0:
        raise DccError("offerta DCC non valida")
    # ci si collega all'indirizzo scritto nel messaggio: mai verso la rete di casa
    if not ip.is_global:
        raise DccError(f"indirizzo DCC non pubblico: {ip}")
    return DccOffer(nick, m["quoted"] or m["plain"], str(ip), port, size)


def parse_dcc_accept(ctcp):
    """Testo CTCP "DCC ACCEPT file porta posizione" -> (porta, posizione); None se è altro."""
    m = _DCC_ACCEPT_RE.fullmatch(ctcp)
    return (int(m["port"]), int(m["pos"])) if m else None


def fallback_nick(nick, letters=""):
    """Ripiego: il nome con 3 lettere minuscole in fondo (niente numeri: sembrano bot). Il nick IRC si ferma a
    lunghezze diverse da server a server: il nome si accorcia per stare entro 15 caratteri."""
    letters = letters or "".join(random.choice(string.ascii_lowercase) for _ in range(3))
    return nick[:12] + letters


class IrcClient:
    """Una connessione IRC condivisa: si apre con session() e si chiude da sola idle_close secondi dopo l'ultimo
    uso."""

    def __init__(self, host, port, use_ssl, channel, nicks, idle_close=1800, on_nick_taken=None, base=None):
        self.host, self.port, self.use_ssl, self.channel = host, port, use_ssl, channel
        self.nicks = [n for n in nicks if n and NICK_RE.fullmatch(n)]  # in ordine: il nome, poi il ripiego
        if not self.nicks:
            raise IrcError("nome utente IRC non valido")
        self.base = base or self.nicks[0]  # il nome scelto: i ripieghi nuovi sono questo + 3 lettere
        self.nick = self.nicks[0]
        self.idle_close = idle_close
        self.on_nick_taken = on_nick_taken
        self._writer = None
        self._read_task = None
        self._registered = self._joined = None
        self._listeners = []
        self._lock = asyncio.Lock()
        self._users = 0
        self._idle_task = None
        self._last_attempt = -_RECONNECT_GAP
        self.last_rx = 0.0  # ultima riga dal server (time.monotonic)
        self.in_channel = False
        self.last_error = ""  # ultimo problema (per lo stato nell'addon)
        self._pongs = {}  # gettone del PING -> future
        self._rejoined = False

    @property
    def connected(self):
        return self._read_task is not None and not self._read_task.done()

    @property
    def busy(self):
        return self._users > 0

    @contextlib.asynccontextmanager
    async def session(self):
        """Connessione pronta (registrati e nel canale) per tutta la durata del blocco."""
        self._users += 1
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None
        try:
            await self.ensure_connected()
            yield self
        finally:
            self._users -= 1
            if self._users == 0 and self.connected:
                self._idle_task = asyncio.ensure_future(self._close_when_idle())

    async def _close_when_idle(self):
        await asyncio.sleep(self.idle_close)
        if self._users == 0:
            logger.info("IRC: nessun video da un po', chiudo la connessione")
            await self.close()

    async def ensure_connected(self, wait_cb=None):
        async with self._lock:
            if self.connected and self.in_channel:
                return
            if self.connected:  # collegati ma fuori dal canale (espulsi): si riprova a entrare
                await self._join()
                return
            wait = self._last_attempt + _RECONNECT_GAP - time.monotonic()
            if wait > 0:
                logger.info(f"IRC: attendo {wait:.0f} s prima di ricollegarmi (il server blocca le riconnessioni rapide)")
                await asyncio.sleep(wait)
            self._last_attempt = time.monotonic()
            await self._connect()

    async def _connect(self):
        context = None
        if self.use_ssl:
            # certificati spesso autofirmati: cifratura sì, verifica dell'identità no
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        logger.info(f"IRC: connessione a {self.host}:{self.port}{' (SSL)' if context else ''}")
        try:
            reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=context), _CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            raise IrcError(f"server IRC non raggiungibile ({type(e).__name__})") from e

        loop = asyncio.get_event_loop()
        self._registered, self._joined = loop.create_future(), loop.create_future()
        for future in (self._registered, self._joined):
            # un errore arrivato dopo un timeout non resta "mai letto" (avviso di asyncio nel log)
            future.add_done_callback(lambda f: f.cancelled() or f.exception())
        self.nick = self.nicks[0]
        self._used = {self.nick.lower()}  # nick avuti in questa connessione: le risposte possono arrivare col vecchio
        self._changing = None  # nick chiesto dopo la registrazione, in attesa della conferma del server
        self._read_task = asyncio.ensure_future(self._read_loop(reader))
        self.send(f"NICK {self.nick}")
        self.send(f"USER {self.nick} 0 * :{self.nick}")
        try:
            await asyncio.wait_for(asyncio.shield(self._registered), _REGISTER_TIMEOUT)
        except asyncio.TimeoutError as e:
            await self.close()
            self.last_error = "il server IRC non ha completato l'accesso"
            raise IrcError(self.last_error) from e
        except IrcError as e:
            await self.close()
            self.last_error = str(e)
            raise
        try:
            await self._join()
        except IrcError:
            await self.close()
            raise
        self.last_error = ""
        logger.info(f"IRC: collegato come {self.nick} e nel canale {self.channel}")

    async def _join(self):
        if self._joined is None or self._joined.done():
            self._joined = asyncio.get_event_loop().create_future()
            self._joined.add_done_callback(lambda f: f.cancelled() or f.exception())
        self.send(f"JOIN {self.channel}")
        try:
            await asyncio.wait_for(asyncio.shield(self._joined), _JOIN_TIMEOUT)
        except asyncio.TimeoutError as e:
            self.last_error = f"il server non ha fatto entrare nel canale {self.channel}"
            raise IrcError(self.last_error) from e
        except IrcError as e:
            self.last_error = str(e)
            raise

    async def check_alive(self, timeout=None):
        """Il server risponde ancora? Sentito da poco: sì; altrimenti un PING e si aspetta il PONG. Se non risponde
        la connessione si chiude (la prossima ensure_connected si ricollega)."""
        if not self.connected:
            return False
        if time.monotonic() - self.last_rx < _ALIVE_AFTER:
            return True
        token = f"vivo{random.randint(100000, 999999)}"
        pong = self._pongs[token] = asyncio.get_event_loop().create_future()
        try:
            self.send(f"PING :{token}")
            await asyncio.wait_for(asyncio.shield(pong), timeout or _PING_TIMEOUT)
            return True
        except (asyncio.TimeoutError, IrcError):
            logger.warning(f"IRC: il server non risponde da {time.monotonic() - self.last_rx:.0f} s: "
                           "connessione morta, mi ricollego")
            self.last_error = "connessione IRC morta (il server non rispondeva più)"
            await self.close()
            return False
        finally:
            self._pongs.pop(token, None)

    def status(self):
        """Stato per l'addon."""
        return {"collegato": self.connected, "nel_canale": self.connected and self.in_channel, "nick": self.nick,
                "canale": self.channel, "server": f"{self.host}:{self.port}",
                "sentito_da": int(time.monotonic() - self.last_rx) if self.connected else None,
                "errore": self.last_error}

    async def _read_loop(self, reader):
        reason = "connessione chiusa dal server"
        pending, pinged = None, False
        self.last_rx = time.monotonic()
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(reader.readline())
                # (asyncio.wait e non wait_for: un annullamento non va mai perso, vedi stream.wait_event)
                done, _ = await asyncio.wait({pending}, timeout=_PONG_WAIT if pinged else _QUIET)
                if not done:
                    if pinged:
                        reason = f"connessione morta (nessuna risposta dal server da {_QUIET + _PONG_WAIT} s)"
                        self.last_error = "connessione IRC morta (il server non rispondeva più)"
                        break
                    self.send("PING :sveglia")
                    pinged = True
                    continue
                raw, pending, pinged = pending.result(), None, False
                self.last_rx = time.monotonic()
                if not raw:
                    break
                try:
                    line = raw.decode("utf-8").rstrip("\r\n")
                except UnicodeDecodeError:
                    line = raw.decode("latin-1").rstrip("\r\n")
                if line:
                    self._dispatch(parse_line(line))
        except (OSError, ConnectionError, ValueError) as e:
            reason = "connessione chiusa" if self._writer is None else f"connessione persa ({type(e).__name__})"
        except asyncio.CancelledError:
            reason = "connessione chiusa"
            raise
        finally:
            if pending is not None:
                pending.cancel()
            self.in_channel = False
            if self._writer is not None:  # morta o chiusa dal server: chi scrive deve saperlo subito
                self._writer.close()
                self._writer = None
            logger.info(f"IRC: {reason}")
            for future in (self._registered, self._joined):
                if future and not future.done():
                    future.set_exception(IrcError(reason))
            self._notify(IrcMessage("", DISCONNECTED, [reason]))

    def _next_nick(self, taken):
        """taken non va (in uso, o registrato da un altro): il ripiego, o un ripiego nuovo se anche quello è preso.
        Prima della registrazione il nick è subito quello nuovo; dopo, solo quando il server conferma (NICK)."""
        index = self.nicks.index(taken) if taken in self.nicks else 0
        if index + 1 < len(self.nicks):
            new = self.nicks[index + 1]
        else:
            new = fallback_nick(self.base)
            self.nicks.append(new)
        logger.info(f"IRC: il nick {taken} è già preso, uso {new}")
        if self.on_nick_taken:
            self.on_nick_taken(taken, new)
        if not self._registered.done():
            self.nick = new
        else:
            self._changing = new
        self._used.add(new.lower())
        self.send(f"NICK {new}")

    def _dispatch(self, msg):
        if msg.command == "PING":
            self.send(f"PONG :{msg.params[-1] if msg.params else ''}")
        elif msg.command == "PONG" and msg.params and msg.params[-1] in self._pongs:
            pong = self._pongs[msg.params[-1]]
            if not pong.done():
                pong.set_result(None)
        elif msg.command == "001" and not self._registered.done():
            self.nick = msg.params[0] if msg.params else self.nick
            self._registered.set_result(None)
        elif msg.command in ("432", "433", "436", "437") and len(msg.params) > 1 and msg.params[1] in self.nicks:
            self._next_nick(msg.params[1])
        elif msg.command == "NICK" and msg.nick.lower() == self.nick.lower() and msg.params:
            self.nick, self._changing = msg.params[-1], None
            self._used.add(self.nick.lower())
            if self.nick not in self.nicks:  # cambiato dal server (es. "Guest123"): il nome era di un altro
                self._next_nick(msg.nick)
        elif msg.command == "NOTICE" and msg.nick.lower() == "nickserv" and msg.params:
            text = strip_colors(msg.params[-1])
            logger.info(f"IRC: NickServ: {text[:200]}")
            taken = _NICKSERV_TAKEN_RE.search(text) and not _NICKSERV_FREE_RE.search(text)
            if taken and not self._changing and self.nick in self.nicks:
                self._next_nick(self.nick)  # il nome è registrato da qualcun altro: tra poco il server lo toglierebbe
        elif msg.command == "JOIN" and msg.nick.lower() in self._used:
            self.in_channel = True
            if not self._joined.done():
                self._joined.set_result(None)
        elif msg.command in _JOIN_ERRORS and not self._joined.done():
            logger.warning(f"IRC: ingresso nel canale {self.channel} rifiutato: {msg.params[-1]}")
            self._joined.set_exception(IrcError(f"ingresso nel canale {self.channel} rifiutato: {msg.params[-1]}"))
        elif msg.command == "KICK" and len(msg.params) > 1 and msg.params[1].lower() in self._used:
            reason = msg.params[2] if len(msg.params) > 2 else ""
            logger.warning(f"IRC: espulso dal canale {msg.params[0]} da {msg.nick}: {reason}")
            self.in_channel = False
            self.last_error = f"espulso dal canale da {msg.nick}" + (f": {reason}" if reason else "")
            if not self._rejoined:  # una volta sola: se ci ributtano fuori, si lascia stare
                self._rejoined = True
                asyncio.get_event_loop().call_later(_REJOIN_DELAY, self._rejoin)
        elif msg.command == "PART" and msg.nick.lower() in self._used:
            self.in_channel = False
        elif msg.command == "ERROR":
            text = msg.params[-1] if msg.params else ""
            logger.warning(f"IRC: il server chiude la connessione: {text}")
            self.last_error = f"il server ha chiuso la connessione: {text[:150]}"
        elif msg.command == "NOTICE" and "!" not in msg.prefix and msg.params and msg.params[0] != "*":
            logger.info(f"IRC: avviso del server: {strip_colors(msg.params[-1])[:200]}")
        self._notify(msg)

    def _rejoin(self):
        if self.connected and not self.in_channel:
            with contextlib.suppress(IrcError):
                self.send(f"JOIN {self.channel}")

    def _notify(self, msg):
        for listener in list(self._listeners):
            try:
                listener(msg)
            except Exception:
                logger.exception("IRC: errore in un ascoltatore")

    def add_listener(self, listener):
        self._listeners.append(listener)

    def remove_listener(self, listener):
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def send(self, line):
        if "\r" in line or "\n" in line:
            raise ValueError("a capo in un comando IRC")
        if self._writer is None or self._writer.is_closing():
            raise IrcError("connessione IRC chiusa")
        self._writer.write(line.encode("utf-8") + b"\r\n")

    def privmsg(self, target, text):
        self.send(f"PRIVMSG {target} :{text}")

    def ctcp(self, target, text):
        self.privmsg(target, f"\x01{text}\x01")

    async def close(self):
        """Esce dal server (QUIT) e chiude la connessione."""
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None
        writer, self._writer = self._writer, None
        if writer and not writer.is_closing():
            with contextlib.suppress(Exception):
                writer.write(b"QUIT :bye\r\n")
                await asyncio.wait_for(writer.drain(), 3)
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 3)
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._read_task
        self._read_task = None
