"""Finta rete IRC con bot XDCC su 127.0.0.1, per provare IRC e streaming senza scaricare niente: registrazione (con
nick già presi o registrati), canale, XDCC SEND (anche con coda), DCC RESUME/ACCEPT e invio del file, che ha un
contenuto calcolato (byte i = i % 251). Registra richieste, riprese e trasferimenti con l'ora."""

import asyncio
import contextlib
import re
import time

from resources.lib import irc as irc_module

PATTERN = bytes(range(251)) * 4200


def content(start, n):
    """I byte del file finto da start, n byte."""
    out = bytearray()
    while n > 0:
        offset = start % 251
        take = min(n, len(PATTERN) - offset)
        out += PATTERN[offset:offset + take]
        start += take
        n -= take
    return bytes(out)


def loopback_offer(nick, ctcp):
    """parse_dcc_send che accetta 127.0.0.1 (quello vero rifiuta gli indirizzi non pubblici)."""
    m = irc_module._DCC_SEND_RE.fullmatch(ctcp)
    return irc_module.DccOffer(nick, m["quoted"] or m["plain"], "127.0.0.1", int(m["port"]), int(m["size"]))


class FakeNetwork:
    def __init__(self, size, filename="Film.2026.iTA.mkv", taken=(), registered=(), queue=None, no_resume=(),
                 silent=(), notice_delay=0.5):
        self.size, self.filename = size, filename
        self.taken, self.registered = set(taken), set(registered)
        self.queue = dict(queue or {})  # bot -> (posizione, secondi prima dell'invio)
        self.no_resume, self.silent = set(no_resume), set(silent)
        self.requests = []  # (ora, bot, comando)
        self.resumes = []  # (bot, posizione)
        self.transfers = []  # (bot, inizio, byte inviati)
        self.nicks = []  # nick accettati
        self.lines = []
        self._pending = {}  # porta -> posizione chiesta con RESUME
        self._tasks = []
        self.notice_delay = notice_delay  # come i bot veri: si accorgono tardi che chi riceve ha chiuso
        self._busy = set()  # bot con un trasferimento ancora in corso (per loro)
        self.duplicates = 0  # richieste arrivate mentre il bot credeva di stare ancora inviando
        self._conns = []  # connessioni IRC aperte: {"writer", "nick", "muted"}

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        self.server.close()
        for task in self._tasks:
            task.cancel()

    def mute(self):
        """Le connessioni aperte diventano mute (come quando l'IP di casa cambia): niente più risposte, né PONG.
        Le connessioni nuove funzionano."""
        for conn in self._conns:
            conn["muted"] = True

    def kick(self, reason="flood"):
        for conn in self._conns:
            if not conn["muted"]:
                conn["writer"].write(f":op!o@h KICK #canale {conn['nick']} :{reason}\r\n".encode())

    def sends(self):
        return [r for r in self.requests if r[2].startswith("XDCC SEND")]

    async def _client(self, reader, writer):
        nick, user, registered = None, False, False
        conn = {"writer": writer, "nick": None, "muted": False}
        self._conns.append(conn)

        def send(line):
            if not conn["muted"]:
                writer.write((line + "\r\n").encode())

        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode().rstrip("\r\n")
                if conn["muted"]:
                    continue
                self.lines.append(line)
                command, _, rest = line.partition(" ")
                if command == "PING":
                    send(f":srv PONG srv :{rest.lstrip(':')}")
                elif command == "NICK":
                    wanted = rest.strip().lstrip(":")
                    if wanted in self.taken:
                        send(f":srv 433 {nick or '*'} {wanted} :Nickname is already in use")
                        continue
                    if registered:
                        send(f":{nick}!u@h NICK :{wanted}")
                    nick = wanted
                elif command == "USER":
                    user = True
                elif command == "JOIN":
                    if nick in self.registered:  # come il server vero: NickServ si fa vivo mentre si entra
                        for text in ("This nickname is registered and protected.",
                                     "Please choose a different nickname, or identify via /msg NickServ IDENTIFY.",
                                     "If you do not change within 1 minute, I will change your nick."):
                            send(f":NickServ!s@services NOTICE {nick} :{text}")
                    send(f":{nick}!u@h JOIN {rest.strip()}")
                elif command == "PRIVMSG":
                    target, _, text = rest.partition(" :")
                    self._privmsg(send, nick, target, text)
                elif command == "QUIT":
                    break
                conn["nick"] = nick
                if nick and user and not registered:
                    registered = True
                    self.nicks.append(nick)
                    send(f":srv 001 {nick} :Benvenuto")
                if registered and command == "NICK" and nick not in self.nicks:
                    self.nicks.append(nick)
                await writer.drain()
        finally:
            writer.close()

    def _privmsg(self, send, nick, bot, text):
        self.requests.append((time.monotonic(), bot, text.strip("\x01")))
        if bot in self.silent:
            return
        if text.startswith("XDCC SEND"):
            if bot in self._busy:
                self.duplicates += 1
                send(f":{bot}!b@h NOTICE {nick} :** Hai già richiesto questo pack!")
                return
            self._tasks.append(asyncio.ensure_future(self._offer(send, nick, bot)))
        elif text.startswith("\x01DCC RESUME"):
            m = re.fullmatch(r"\x01DCC RESUME (\S+) (\d+) (\d+)\x01", text)
            port, pos = int(m[2]), int(m[3])
            self.resumes.append((bot, pos))
            if bot not in self.no_resume:
                self._pending[port] = pos
                send(f":{bot}!b@h PRIVMSG {nick} :\x01DCC ACCEPT {m[1]} {port} {pos}\x01")

    async def _offer(self, send, nick, bot):
        if bot in self.queue:
            position, delay = self.queue.pop(bot)
            send(f":{bot}!b@h NOTICE {nick} :** All Slots Full, Added you to the main queue in position {position}.")
            await asyncio.sleep(delay)
        server = await asyncio.start_server(lambda r, w: self._transfer(bot, port, r, w, server), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        send(f":{bot}!b@h NOTICE {nick} :** Sending you pack #1 (\"{self.filename}\")")
        send(f":{bot}!b@h PRIVMSG {nick} :\x01DCC SEND {self.filename} 2130706433 {port} {self.size}\x01")

    async def _transfer(self, bot, port, reader, writer, server):
        server.close()
        pos = start = self._pending.pop(port, 0)
        record = [bot, start, 0]
        self.transfers.append(record)
        self._busy.add(bot)
        try:
            while pos < self.size:
                block = content(pos, min(256 * 1024, self.size - pos))
                writer.write(block)
                await writer.drain()  # chi riceve non legge: qui ci si ferma (come un bot vero)
                pos += len(block)
                record[2] = pos - start
            with contextlib.suppress(asyncio.TimeoutError, ConnectionError):
                await asyncio.wait_for(reader.read(), 2)
        except (ConnectionError, OSError):
            await asyncio.sleep(self.notice_delay)  # chiuso da chi riceve: il bot se ne accorge dopo un po'
        finally:
            self._busy.discard(bot)
            writer.close()
