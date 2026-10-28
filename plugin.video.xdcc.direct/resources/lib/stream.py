"""
Streaming di un file dai bot XDCC senza salvarlo: in RAM solo un po' di file intorno al punto in cui si guarda.

Kodi legge il video da un indirizzo locale (web.py) come da un sito qualsiasi, chiedendo "dal byte X". Quando salta,
il byte lo calcola Kodi dall'indice del file (MKV, MP4 e AVI ce l'hanno); qui si chiede al bot di ripartire da lì:
si chiude il trasferimento e si richiede il pack con DCC RESUME alla posizione nuova.

Per non martellare i bot (Requester): una richiesta alla volta, almeno GAP secondi tra una e l'altra, mai due
trasferimenti insieme. Salti ravvicinati (avanti avanti avanti) diventano una richiesta sola, l'ultima. Un salto
in avanti piccolo (fino a NEAR) aspetta i dati che stanno arrivando invece di richiedere.

In RAM:
- l'inizio del file (HEAD_MAX) e la fine (TAIL_MAX, dove MKV e AVI tengono l'indice): si prendono prima di far
  partire Kodi (_prepare), mentre l'addon mostra la sua finestra di attesa. Kodi li legge appena apre il video (anche
  riprendendo da dove era rimasto) e lì non aspetta più di ~20 secondi una risposta: così, una volta aperto, gli
  manca al massimo una richiesta (il punto di ripresa, o il seguito dell'inizio). Li rilegge a ogni apertura;
- la finestra del trasferimento in corso: BEHIND dietro chi legge (piccoli salti indietro) e al massimo AHEAD_MAX
  davanti. Oltre, si smette di leggere dal bot, che rallenta da solo (TCP): Kodi ha già la sua cache.
In pausa da più di STALL_CLOSE secondi il trasferimento si chiude e libera il posto del bot; alla ripresa si
richiede da dove serve. Video chiuso (nessuno legge da IDLE_STOP secondi): idem.

Kodi legge con due connessioni: dopo un salto tiene aperta, ferma, quella di prima (per tornarci subito se salta
indietro). Ogni lettura ha la sua posizione e il freno segue la più avanti tra quelle che leggono dal trasferimento.
Il trasferimento è della lettura che l'ha chiesto finché riceve dati: un salto (lettura più nuova) lo prende
subito, una lettura più vecchia (quella ferma, che intanto ha riempito il suo buffer) solo se l'altra è ferma da
OWNER_IDLE secondi. Una lettura finisce quando Kodi chiude la connessione (detach).
"""

import asyncio
import contextlib
import logging
import random
import re
import struct
import time

from .irc import DISCONNECTED, DccError, IrcError, parse_dcc_accept, parse_dcc_send, strip_colors

logger = logging.getLogger("xdcc.direct")

MB = 1024 * 1024
GAP = 3.0  # secondi minimi tra due richieste ai bot
BOT_SETTLE = 2.5  # un bot si accorge in 1-2 secondi che il trasferimento è chiuso: prima risponde "già richiesto"
HEAD_MAX = 8 * MB
TAIL_MAX = 8 * MB
AHEAD_MAX = 12 * MB
BEHIND = 4 * MB
NEAR = 8 * MB
SKIP_MAX = 16 * MB  # bot che non riparte dal byte chiesto: si scarta dall'inizio fino a qui, oltre no
READY = 2 * MB
PREPARE_HEAD_WAIT = 15  # secondi al massimo per avere tutto l'inizio (poi basta READY) prima di chiedere la fine
DEBOUNCE = 0.6
STALL_CLOSE = 90
OWNER_IDLE = 2.0
IDLE_STOP = 30
_IDLE_POLL = 5  # ogni quanto si controlla se nessuno guarda più
_TRIM_STEP = 2 * MB
_CHUNK = 256 * 1024
_CONNECT_TIMEOUT = 30
_DATA_TIMEOUT = 20  # il bot non manda niente (e non siamo noi a frenare): prima che Kodi rinunci
_PROBE_TIMEOUT = 20  # prima risposta di un bot (di solito 1-3 secondi)
_SENDING_TIMEOUT = 60  # da "Sending you pack" all'offerta DCC
_QUEUE_TIMEOUT = 3600
_QUEUE_STAY = 2  # in coda fino a questa posizione si aspetta lì, oltre si prova un altro mirror
_RESUME_TIMEOUT = 10  # i bot rispondono al RESUME subito: chi tace non lo fa

_DENIED_WORDS = ("denied", "invalid", "negat", "non valid", "rifiut")
_QUEUED_WORDS = ("queue", "coda", "already", "già")
_SENDING_WORDS = ("send", "inviando", "invio")


class StreamError(Exception):
    """Lo streaming non può continuare (messaggio da mostrare)."""


class ReaderGone(Exception):
    """Kodi ha chiuso la connessione di questa lettura."""


async def wait_event(event, timeout):
    """Come wait_for(event.wait(), timeout), ma l'annullamento arriva sempre: il wait_for di Python 3.11 (Kodi 21)
    lo perde se l'evento scatta nello stesso momento, e chi doveva fermarsi continua."""
    waiter = asyncio.ensure_future(event.wait())
    try:
        await asyncio.wait({waiter}, timeout=timeout)
    finally:
        waiter.cancel()
    return event.is_set()


def _queue_position(text):
    m = re.search(r"(?:position|posizione)\D{0,3}(\d+)", text, re.I)
    return int(m[1]) if m else 999


class _Request:
    def __init__(self, bot, pack, offer):
        self.bot, self.pack, self.offer = bot, pack, offer
        self.status = "attesa"  # attesa | invio | coda | rifiutato | lasciato
        self.position = 0
        self.note = ""


class Requester:
    """Richieste ai bot su una connessione IRC: una alla volta, almeno gap secondi tra una e l'altra."""

    def __init__(self, irc, gap=GAP):
        self.irc, self.gap = irc, gap
        self._last = -gap
        self._lock = asyncio.Lock()

    async def wait_gap(self, early=0.0):
        """Aspetta che siano passati gap secondi dall'ultima richiesta (meno early)."""
        wait = self._last + self.gap - early - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    def _private_from(self, msg, nick):
        if msg.command not in ("PRIVMSG", "NOTICE") or len(msg.params) < 2:
            return None
        if msg.nick.lower() != nick.lower() or msg.params[0].lower() != self.irc.nick.lower():
            return None
        return msg.params[-1]

    async def acquire(self, sources, prefer=None, report=None, avoid=()):
        """Offerta DCC del file da uno dei mirror, chiesti uno alla volta (prima prefer, poi in ordine casuale).
        In coda fino alla posizione _QUEUE_STAY si aspetta lì; più indietro si prova il mirror dopo, e se tutti
        mettono in coda si torna da quello con la coda più corta. -> (bot, offerta); DccError se nessuno lo manda."""
        report = report or (lambda text: None)
        order = [s for s in sources if s[0] not in avoid] or list(sources)
        random.shuffle(order)
        order.sort(key=lambda s: s[0] != prefer)
        async with self._lock:
            queued, errors = [], []
            for bot, pack in order:
                result = await self._ask(bot, pack, report, stay=False)
                if isinstance(result, tuple):
                    return result
                if isinstance(result, int):
                    queued.append((result, bot, pack))
                elif result:
                    errors.append(f"{bot}: {result}")
            if queued:
                _, bot, pack = min(queued)
                result = await self._ask(bot, pack, report, stay=True)
                if isinstance(result, tuple):
                    return result
                errors.append(f"{bot}: {result or 'in coda da troppo tempo'}")
        raise DccError("nessun bot ha inviato il file" + (f" ({errors[-1]})" if errors else ""))

    async def _ask(self, bot, pack, report, stay):
        """Una richiesta a un bot. -> (bot, offerta), posizione in coda (int, se non si resta), o il motivo."""
        await self.wait_gap()
        loop = asyncio.get_event_loop()
        r = _Request(bot, pack, loop.create_future())
        changed = asyncio.Event()

        def listener(msg):
            if msg.command == DISCONNECTED:
                if not r.offer.done():
                    r.offer.set_exception(IrcError("connessione IRC persa"))
                changed.set()
                return
            if msg.command == "401" and len(msg.params) > 1 and msg.params[1].lower() == bot.lower():
                r.status, r.note = "rifiutato", "bot non collegato"
                changed.set()
                return
            text = self._private_from(msg, bot)
            if text is None:
                return
            if text.startswith("\x01"):
                ctcp = text.strip("\x01")
                if msg.command == "PRIVMSG" and ctcp.upper().startswith("DCC SEND") and not r.offer.done():
                    try:
                        r.offer.set_result(parse_dcc_send(msg.nick, ctcp))
                        r.status = "invio"
                    except DccError as e:
                        r.status, r.note = "rifiutato", str(e)
                    changed.set()
                return
            note = strip_colors(text).strip().lstrip("*").strip()[:200]
            low = note.lower()
            r.note = note
            logger.info(f"XDCC: {bot}: {note}")
            if any(word in low for word in _DENIED_WORDS):
                r.status = "rifiutato"
            elif any(word in low for word in _QUEUED_WORDS):
                r.status, r.position = "coda", _queue_position(note)
            elif any(word in low for word in _SENDING_WORDS):
                r.status = "invio"
            changed.set()

        async def wait(condition, timeout):
            deadline = time.monotonic() + timeout
            while not r.offer.done() and not condition():
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                changed.clear()
                await wait_event(changed, min(left, 5))
            if r.offer.done() and isinstance(r.offer.exception(), IrcError):
                raise r.offer.exception()
            return r.offer.done() and r.offer.exception() is None

        self.irc.add_listener(listener)
        got = False
        try:
            self.irc.privmsg(bot, f"XDCC SEND {pack}")
            self._last = time.monotonic()
            logger.info(f"XDCC: richiesta a {bot} ({pack})")
            report(f"Richiesta a {bot}")
            got = await wait(lambda: r.status != "attesa", _PROBE_TIMEOUT)
            if not got and r.status == "coda":
                if not stay and r.position > _QUEUE_STAY:
                    return r.position
                deadline = time.monotonic() + _QUEUE_TIMEOUT
                while not got and r.status == "coda" and time.monotonic() < deadline:
                    where = f"posizione {r.position}" if r.position < 999 else "posizione non detta"
                    report(f"In coda da {bot} ({where})")
                    got = await wait(lambda: r.status != "coda", 30)
            # "Ti sto inviando..." (anche dopo la coda): l'offerta DCC arriva qualche secondo dopo
            if not got and r.status == "invio":
                got = await wait(lambda: r.status != "invio", _SENDING_TIMEOUT)
            if got:
                return bot, r.offer.result()
            return r.note or "non risponde"
        finally:
            self.irc.remove_listener(listener)
            if not got and r.status in ("attesa", "invio", "coda"):
                with contextlib.suppress(IrcError):  # via dalla coda, o libera il posto dell'invio offerto
                    self.irc.privmsg(bot, "XDCC CANCEL" if r.status == "invio" else "XDCC REMOVE")

    async def resume(self, offer, start):
        """Chiede al bot di partire dal byte start (DCC RESUME): -> da dove parte davvero (0 = da capo)."""
        accepted = asyncio.get_event_loop().create_future()

        def listener(msg):
            text = self._private_from(msg, offer.nick)
            if text and text.startswith("\x01") and not accepted.done():
                reply = parse_dcc_accept(text.strip("\x01"))
                if reply and reply[0] == offer.port:
                    accepted.set_result(reply[1])

        self.irc.add_listener(listener)
        try:
            filename = f'"{offer.filename}"' if " " in offer.filename else offer.filename
            self.irc.ctcp(offer.nick, f"DCC RESUME {filename} {offer.port} {start}")
            logger.info(f"XDCC: {offer.nick}: riparti da {start}")
            position = await asyncio.wait_for(accepted, _RESUME_TIMEOUT)
            return position if 0 < position <= start else 0
        except asyncio.TimeoutError:
            logger.info(f"XDCC: {offer.nick} non riparte dal byte chiesto")
            return 0
        finally:
            self.irc.remove_listener(listener)

    def cancel(self, bot):
        with contextlib.suppress(IrcError):
            self.irc.privmsg(bot, "XDCC CANCEL")


class _Reader:
    def __init__(self, gen, pos):
        self.gen, self.pos, self.gone = gen, pos, False
        self.served = time.monotonic()  # ultimi dati dati a questa lettura (o apertura)


class Stream:
    """Un file che si sta guardando. irc_session() apre la connessione (o la riusa) per tutto il tempo in cui il
    file serve; requester fa le richieste ai bot."""

    def __init__(self, sid, filename, sources, size_hint, irc_session, requester):
        self.sid, self.filename, self.sources, self.size_hint = sid, filename, sources, size_hint
        self.irc_session, self.requester = irc_session, requester
        self.size = None  # esatta, dalla prima offerta DCC
        self.phase, self.error, self.bot = "In partenza…", None, None
        self.head = bytearray()
        self.tail, self.tail_start = None, None
        self._tail_new, self._tail_new_start = None, None
        self.buf, self.buf_start = bytearray(), 0
        self.transfer_start = None  # inizio del trasferimento in corso (None: nessuno)
        self.received, self.speed = 0, 0.0
        self._speed_mark = (time.monotonic(), 0)
        self._readers = set()  # letture aperte da Kodi
        self._owner = None  # lettura per cui si è chiesto il trasferimento in corso
        self._reader_gen = 0
        self.last_read = time.monotonic()
        self.target, self._pending, self._skip_until = 0, None, None
        self._failures = {}  # bot -> trasferimenti finiti senza dati di fila
        self._released = (None, 0.0)  # (bot, quando) dell'ultimo trasferimento chiuso da noi a metà
        self._event = asyncio.Event()
        self._wake = asyncio.Event()
        self._transfer_task = None
        self._transfer_id = 0  # cambia a ogni trasferimento: quello vecchio, se non si è accorto di doversi fermare, si ferma
        self._run_task = None
        self.closed = False
        self.session_up = False  # connessione IRC pronta per questo file (_run è dentro la sessione)
        self.preparing = False
        self._prepared = False  # inizio e fine del file in RAM: Kodi può partire
        self._prepare_task = None
        self.on_error = None  # avviso a chi guarda, se lo streaming si ferma mentre il video è aperto

    # --- stato ---

    @property
    def ready(self):
        """Kodi può aprire il video: inizio e fine in RAM e connessione IRC pronta (per la richiesta che servirà)."""
        return self.size is not None and self._prepared and self.session_up and self.requester.irc.connected

    def progress(self):
        """Preparazione da 0 a 1: metà l'inizio del file, metà la fine."""
        if self.size is None:
            return 0.0
        head = min(1.0, len(self.head) / min(HEAD_MAX, self.size))
        if self.size <= HEAD_MAX:
            return head
        tail = 1.0 if self.tail is not None else (len(self._tail_new or b"") / TAIL_MAX if self._tail_new else 0.0)
        return head / 2 + min(1.0, tail) / 2

    def status(self):
        return {"pronto": self.ready, "errore": self.error, "fase": self.phase, "ricevuti": self.received,
                "totale": self.size or self.size_hint, "velocita": int(self.speed)}

    @property
    def readers(self):
        return len(self._readers)

    def _notify(self):
        self._event.set()
        self._event = asyncio.Event()

    async def _wait_change(self, timeout):
        await wait_event(self._event, timeout)

    def _transfer_running(self):
        return self._transfer_task is not None and not self._transfer_task.done()

    def _buf_end(self):
        return self.buf_start + len(self.buf)

    def _lookup(self, pos, n):
        """Byte da pos (al massimo n) se sono in RAM; b"" se no."""
        if pos < len(self.head):
            return bytes(self.head[pos:pos + n])
        if self.tail is not None and pos >= self.tail_start:
            return bytes(self.tail[pos - self.tail_start:pos - self.tail_start + n])
        if self.buf_start <= pos < self._buf_end():
            return bytes(self.buf[pos - self.buf_start:pos - self.buf_start + n])
        return b""

    def _covered(self, pos):
        """pos è in RAM o sta per arrivare (dal trasferimento in corso o da quello richiesto)."""
        if self._lookup(pos, 1):
            return True
        # dal trasferimento in corso arriva solo quello che viene dopo la finestra: la parte già scartata no
        if self._transfer_running() and self.buf_start <= pos <= max(self._buf_end(), self._skip_until or 0) + NEAR:
            return True
        return self._pending is not None and self._pending <= pos <= self._pending + NEAR

    # --- lettura (da web.py) ---

    def attach(self, pos):
        """Kodi apre una lettura da pos (le altre aperte restano valide)."""
        self._reader_gen += 1
        reader = _Reader(self._reader_gen, pos)
        self._readers.add(reader)
        self.last_read = time.monotonic()
        logger.info(f"XDCC: Kodi legge da {pos} ({len(self._readers)} letture aperte)")
        self._notify()
        return reader

    def detach(self, reader):
        """Kodi ha chiuso la connessione della lettura (anche mentre aspettava i dati)."""
        reader.gone = True
        self._readers.discard(reader)
        self.last_read = time.monotonic()
        self._notify()

    def _positions(self, low, high):
        return [r.pos for r in self._readers if low <= r.pos <= high]

    async def read(self, reader, pos, n):
        """Fino a n byte da pos, aspettando che arrivino (e chiedendoli al bot se non stanno arrivando e il
        trasferimento non serve a un'altra lettura: vedi all'inizio)."""
        while True:
            if self.closed:
                raise StreamError("Streaming chiuso")
            if reader.gone:
                raise ReaderGone()
            if self.error:
                raise StreamError(self.error)
            reader.pos, self.last_read = pos, time.monotonic()
            data = self._lookup(pos, n)
            if data:
                reader.served = time.monotonic()
                self._notify()  # chi aspetta spazio (il trasferimento frenato) riparte
                return data
            owner = self._owner
            if not self._covered(pos) and (owner is None or owner.gone or reader.gen >= owner.gen
                                           or time.monotonic() - owner.served > OWNER_IDLE):
                self._owner = reader
                self.want(pos)
            await self._wait_change(1.0)

    def want(self, pos):
        """Serve il file da pos: lo chiederà il ciclo di _run (dopo DEBOUNCE e GAP)."""
        self.target = pos
        self._wake.set()
        if self._run_task is None or self._run_task.done():
            self._run_task = asyncio.ensure_future(self._run())

    # --- ciclo principale ---

    def start(self):
        self.preparing = True
        self._prepare_task = asyncio.ensure_future(self._prepare())

    def warm(self):
        """Lo stesso file riaperto: la connessione IRC si riapre (se nel frattempo si è chiusa) prima che Kodi parta."""
        self.last_read = time.monotonic()
        if self._run_task is None or self._run_task.done():
            self._run_task = asyncio.ensure_future(self._run())
        elif not self.requester.irc.connected:  # caduta mentre il file era aperto
            asyncio.ensure_future(self._reconnect())

    async def _reconnect(self):
        try:
            await self.requester.irc.ensure_connected()
        except IrcError as e:
            self.error = f"Connessione IRC non riuscita ({e})"
        self._notify()

    async def _prepare(self):
        """Inizio del file (HEAD_MAX, o READY se il bot è lento) e poi la fine (TAIL_MAX), prima di far partire Kodi."""
        try:
            self.want(0)
            deadline = time.monotonic() + PREPARE_HEAD_WAIT
            while not self.closed and not self.error:
                if self.size is not None and (len(self.head) >= min(HEAD_MAX, self.size) or (
                        time.monotonic() > deadline and len(self.head) >= min(READY, self.size))):
                    break
                await self._wait_change(1.0)
            if self.size is not None and self.size > HEAD_MAX + TAIL_MAX:
                tail_from = self.size - TAIL_MAX
                self.want(tail_from)
                while not self.closed and not self.error and not (self.tail is not None and self.tail_start <= tail_from):
                    if not self._transfer_running() and self._pending is None and self._run_task.done():
                        self.want(tail_from)  # il ciclo è uscito (errore del bot gestito): si riprova
                    self.phase = "Preparo la fine del file (l'indice)…" if self._transfer_running() else self.phase
                    await self._wait_change(1.0)
            if not self.closed and not self.error:
                self._prepared = True
                self.last_read = time.monotonic()  # Kodi sta per aprire: il conto di IDLE_STOP parte da qui
        finally:
            self.preparing = False
            self._notify()

    async def _run(self):
        """Finché serve: tiene aperta la connessione IRC e fa partire i trasferimenti dove serve. Esce quando nessuno
        legge più (la connessione IRC poi si chiude da sola dopo un po')."""
        try:
            self.phase = "Collegamento a IRC…"
            self._notify()
            async with self.irc_session():
                self.session_up = True
                self._notify()
                try:
                    while not self.closed:
                        if not await wait_event(self._wake, _IDLE_POLL):
                            idle = self.readers == 0 and time.monotonic() - self.last_read > IDLE_STOP
                            if idle and self._pending is None and not self.preparing:
                                logger.info("XDCC: nessuno guarda più, chiudo il trasferimento")
                                await self._stop_transfer()
                                return
                            continue
                        target = await self._settle()
                        if not self._covered(target):
                            await self._switch(target)
                finally:
                    self.session_up = False
        except (IrcError, DccError) as e:
            self.error = str(e)[:1].upper() + str(e)[1:]
            logger.warning(f"XDCC: {self.filename}: {e}")
        except StreamError as e:
            self.error = str(e)
        finally:
            self._pending = None
            await self._stop_transfer()
            self._notify()
        if self.error and self.readers and self.on_error:
            self.on_error(self.error)

    async def _settle(self):
        """Aspetta che i salti si fermino: conta solo l'ultimo (la stessa posizione chiesta di nuovo non allunga
        l'attesa)."""
        while True:
            target = self.target
            await asyncio.sleep(DEBOUNCE)
            if self.target == target:
                self._wake.clear()
                return target

    async def _switch(self, target):
        # il trasferimento dall'inizio continua durante l'attesa se sta ancora riempiendo l'inizio del file
        filling_head = self._transfer_running() and self.transfer_start == 0 and len(self.head) < HEAD_MAX
        self._pending = target
        try:
            if filling_head:  # continua finché il bot ha tempo di accorgersi della chiusura prima della richiesta
                await self.requester.wait_gap(early=BOT_SETTLE)
            await self._stop_transfer()
            await self.requester.wait_gap()
            if not target <= self.target <= target + NEAR:  # un altro salto durante l'attesa: conta quello
                target = self._pending = self.target
            released, when = self._released
            if released and released == self.bot:
                await asyncio.sleep(max(0.0, when + BOT_SETTLE - time.monotonic()))
            avoid = {bot for bot, count in self._failures.items() if count >= 2}
            irc = self.requester.irc
            # connessione caduta, morta in silenzio o fuori dal canale: ci si ricollega prima di chiedere
            if not await irc.check_alive() or not irc.in_channel:
                self.phase = "Collegamento a IRC…"
                self._notify()
                await irc.ensure_connected()
            self.phase = "Richiesta al bot…"
            self._notify()
            bot, offer = await self.requester.acquire(self.sources, prefer=self.bot, report=self._report,
                                                      avoid=avoid)
            if self.size is None:
                self.size = offer.size
            if offer.size != self.size or target >= offer.size:
                self.requester.cancel(bot)
                raise StreamError("Il bot ha mandato un file diverso: riprova.")
            position, self._skip_until = 0, None
            if target > 0:
                self.phase = f"Riparto da {target // MB} MB…"
                self._notify()
                position = await self.requester.resume(offer, target)
                if position != target:
                    if position or target > SKIP_MAX:
                        self.requester.cancel(bot)
                        raise StreamError(f"Il bot {bot} non permette di saltare: il video si guarda solo "
                                          "dall'inizio, senza salti.")
                    self._skip_until = target
            self.bot = bot
            if self.closed:  # chiuso mentre si aspettava il bot: il suo posto si libera subito
                self.requester.cancel(bot)
                return
            self._begin_transfer(offer, position)
        finally:
            self._pending = None
            self._notify()

    def _report(self, text):
        self.phase = text
        self._notify()

    # --- trasferimento ---

    def _begin_transfer(self, offer, position):
        self.transfer_start = position
        self.buf, self.buf_start = bytearray(), position  # la finestra vecchia è di un altro punto del file
        if self.size - position <= TAIL_MAX and (self.tail is None or position < self.tail_start):
            self._tail_new, self._tail_new_start = bytearray(), position
        else:
            self._tail_new = None
        self._transfer_id += 1
        self._transfer_task = asyncio.ensure_future(self._receive(offer, position, self._transfer_id))

    async def _stop_transfer(self):
        self._transfer_id += 1
        task, self._transfer_task = self._transfer_task, None
        if task and not task.done():
            self._released = (self.bot, time.monotonic())
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.transfer_start = None

    async def _receive(self, offer, position, transfer_id):
        """Riceve dal bot (DCC) da position in poi, nella finestra in RAM, frenando quando è piena. Conferma al bot
        i byte ricevuti come i client IRC: 4 byte, il totale modulo 2^32."""
        self.phase = f"Collegamento a {offer.nick}…"
        self._notify()
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(offer.host, offer.port), _CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            self._fail(offer.nick, f"connessione al bot non riuscita ({type(e).__name__})")
            return
        pos = position
        self.phase = f"Ricevo da {offer.nick}"
        self._speed_mark = (time.monotonic(), self.received)
        self._notify()
        try:
            while pos < offer.size and transfer_id == self._transfer_id:
                if not await self._room(pos, transfer_id):
                    if transfer_id == self._transfer_id:
                        logger.info("XDCC: in pausa da un po', libero il posto del bot")
                    return
                try:
                    chunk = await asyncio.wait_for(reader.read(_CHUNK), _DATA_TIMEOUT)
                except asyncio.TimeoutError:
                    self._fail(offer.nick, f"il bot non manda più dati da {_DATA_TIMEOUT} secondi")
                    return
                if not chunk or transfer_id != self._transfer_id:
                    break
                self._append(pos, chunk)
                pos += len(chunk)
                writer.write(struct.pack("!I", pos & 0xFFFFFFFF))
            if pos >= offer.size:
                self._failures.pop(offer.nick, None)
                if self._tail_new is not None:
                    self.tail, self.tail_start, self._tail_new = self._tail_new, self._tail_new_start, None
            elif pos - position < MB:
                self._fail(offer.nick, "il bot ha chiuso il trasferimento")
        except (OSError, ConnectionError) as e:
            self._fail(offer.nick, f"trasferimento interrotto ({type(e).__name__})")
        finally:
            writer.close()
            self.phase = "In attesa"
            self._notify()

    def _fail(self, bot, reason):
        """Trasferimento finito male: alla prossima richiesta si riprova; dopo 2 di fila, con un altro mirror."""
        self._failures[bot] = self._failures.get(bot, 0) + 1
        logger.warning(f"XDCC: {bot}: {reason}")
        if all(self._failures.get(b, 0) >= 3 for b, _ in self.sources):
            self.error = f"Nessun bot riesce a inviare il file ({reason})."
            if self.readers and self.on_error:
                self.on_error(self.error)

    async def _room(self, pos, transfer_id):
        """Aspetta che ci sia posto nella finestra (chi legge è andato avanti). False: pausa troppo lunga, o
        trasferimento da fermare."""
        while transfer_id == self._transfer_id and not self.closed:
            readers = self._positions(self.buf_start, pos + NEAR)  # chi legge da questo trasferimento
            anchor = max(readers) if readers else self.transfer_start
            if self._skip_until is not None:
                anchor = max(anchor, self._skip_until)
            if pos - anchor < AHEAD_MAX:
                return True
            if time.monotonic() - self.last_read > STALL_CLOSE:
                return False
            await self._wait_change(1.0)
        return False

    def _append(self, pos, chunk):
        if pos != self._buf_end():  # (non succede: la finestra parte con il trasferimento)
            self.buf, self.buf_start = bytearray(), pos
        if self._skip_until is not None and pos + len(chunk) <= self._skip_until - BEHIND:
            self.buf_start = pos + len(chunk)  # bot che riparte da capo: l'inizio si scarta (tranne la testa)
        else:
            self.buf += chunk
        if pos <= len(self.head) < HEAD_MAX:
            self.head += chunk[len(self.head) - pos:HEAD_MAX - pos]
        if self._tail_new is not None:
            self._tail_new += chunk
        self.received += len(chunk)
        # finestra: BEHIND dietro chi legge, il resto si toglie (a passi, per non spostare memoria a ogni blocco)
        end = self._buf_end()
        readers = self._positions(self.buf_start, end)  # dietro la più indietro di chi legge nella finestra
        anchor = min(readers) if readers else self.buf_start  # nessuno legge (video fermato): si tiene tutto, per
        # riprendere dallo stesso punto senza richiedere (il trasferimento intanto è frenato)
        keep_from = max(self.buf_start, anchor - BEHIND, end - AHEAD_MAX - BEHIND)
        if keep_from - self.buf_start >= _TRIM_STEP:
            del self.buf[:keep_from - self.buf_start]
            self.buf_start = keep_from
        now = time.monotonic()
        mark_time, mark_bytes = self._speed_mark
        if now - mark_time >= 2:
            self.speed = (self.received - mark_bytes) / (now - mark_time)
            self._speed_mark = (now, self.received)
        self._notify()

    async def close(self):
        """Fine dello streaming (altro video o annullato): si ferma tutto, posto del bot liberato."""
        self.closed = True
        self._notify()
        if self._prepare_task and not self._prepare_task.done():
            self._prepare_task.cancel()
        task = self._run_task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._stop_transfer()
