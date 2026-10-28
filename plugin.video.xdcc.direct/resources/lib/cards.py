"""
Schede di film, serie ed episodi (locandina, sfondo, trama, anno, voto; per gli episodi titolo, trama e immagine),
come quelle dell'addon col server:

- ServerCards: chi ha collegato Kodi a Telegram le chiede al server (/xdcc/api/schede), che usa la sua chiave TMDB:
  la chiave resta lì e la chiave di Kodi si può revocare dal bot.
- TmdbCards: con una chiave TMDB personale nelle impostazioni, direttamente a TMDB.
Senza nessuna delle due, niente schede: le liste funzionano uguale.

Le liste non aspettano più di WAIT secondi: le schede che arrivano dopo restano in memoria per la volta dopo.
Le chiavi viaggiano nell'indirizzo o in un header: nei log solo il tipo di errore, mai l'indirizzo.
"""

import asyncio
import json
import logging
import re
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("xdcc.direct")

WAIT = 6
_TTL = 24 * 3600
_MISS_TTL = 600
_PARALLEL = 4
_USER_AGENT = "plugin.video.xdcc.direct"
_TMDB = "https://api.themoviedb.org/3"
_POSTER = "https://image.tmdb.org/t/p/w500"
_BACKDROP = "https://image.tmdb.org/t/p/w1280"
_STILL = "https://image.tmdb.org/t/p/w300"
_PLACEHOLDER_RE = re.compile(r"(episodio|episode)\s+\d+", re.I)  # titolo finto di TMDB quando manca quello vero
_MAX_SERVER_ITEMS = 100


def _get_json(url, headers=None, timeout=8):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json",
                                                   **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def entry_of(item, title, serie):
    """Che scheda serve a un elemento: ("f", titolo), ("s", titolo) o ("e", titolo, stagione, episodio)."""
    if item.get("episodio") is not None and item.get("stagione") is not None:
        return "e", title, item["stagione"], item["episodio"]
    return ("s" if serie else "f"), title


def split_year(title):
    """"Hellfire 2026" -> ("Hellfire", 2026); l'anno solo se prima c'è anche un titolo."""
    m = re.fullmatch(r"(.+?)\s+((?:19|20)\d{2})", title.strip())
    return (m[1], int(m[2])) if m else (title.strip(), None)


class _Cards:
    """Schede in memoria per _TTL secondi; ogni richiesta una volta sola anche se la vogliono in tanti insieme."""

    def __init__(self):
        self._cache, self._running = {}, {}
        self._slots = None

    async def _blocking(self, function, *args):
        if self._slots is None:
            self._slots = asyncio.Semaphore(_PARALLEL)
        async with self._slots:
            return await asyncio.get_event_loop().run_in_executor(None, function, *args)

    async def _cached(self, key, fetch):
        hit = self._cache.get(key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        if key not in self._running:
            self._running[key] = asyncio.ensure_future(self._fetch(key, fetch))
        return await asyncio.shield(self._running[key])

    async def _fetch(self, key, fetch):
        try:
            value, ttl = await fetch(), _TTL
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"schede: {key[0]} {key[1]!r} non riuscito ({type(e).__name__})")
            value, ttl = None, _MISS_TTL
        self._cache[key] = (value, time.monotonic() + ttl)
        self._running.pop(key, None)
        return value

    async def add_info(self, items, entries, wait=WAIT):
        """Aggiunge a ogni elemento i campi della sua scheda (entries: entry_of) arrivati entro wait secondi."""
        if not items:
            return
        tasks = [asyncio.ensure_future(self.info(entry)) for entry in entries]
        done, _ = await asyncio.wait(tasks, timeout=wait)
        for item, task in zip(items, tasks):
            if task in done and not task.cancelled() and task.exception() is None and task.result():
                item.update(task.result())


class TmdbCards(_Cards):
    def __init__(self, api_key):
        super().__init__()
        self.api_key = api_key

    def _get(self, path, **params):
        query = urllib.parse.urlencode({"api_key": self.api_key, **params})
        return self._blocking(_get_json, f"{_TMDB}{path}?{query}")

    async def _search(self, kind, title):
        query, year = split_year(title)
        results = []
        if year:
            year_param = "year" if kind == "movie" else "first_air_date_year"
            results = (await self._get(f"/search/{kind}", query=query, language="it-IT", **{year_param: year}))["results"]
        if not results:
            results = (await self._get(f"/search/{kind}", query=query, language="it-IT"))["results"]
        if not results:
            return None
        best = results[0]
        overview = best.get("overview") or (await self._get(f"/{kind}/{best['id']}", language="en-US")).get("overview") or ""
        date = best.get("release_date") or best.get("first_air_date") or ""
        return {"id": best["id"], "titolo_scheda": best.get("title") or best.get("name"), "trama": overview,
                "locandina": _POSTER + best["poster_path"] if best.get("poster_path") else None,
                "sfondo": _BACKDROP + best["backdrop_path"] if best.get("backdrop_path") else None,
                "anno": int(date[:4]) if date[:4].isdigit() else None,
                "voto": round(best["vote_average"], 1) if best.get("vote_average") else None}

    async def _season(self, tv_id, number):
        episodes = (await self._get(f"/tv/{tv_id}/season/{number}", language="it-IT")).get("episodes", [])
        english = {}
        if any(not ep.get("overview") for ep in episodes):
            english = {ep["episode_number"]: ep
                       for ep in (await self._get(f"/tv/{tv_id}/season/{number}", language="en-US")).get("episodes", [])}
        found = {}
        for ep in episodes:
            other = english.get(ep["episode_number"], {})
            name = ep.get("name") or ""
            if not name or _PLACEHOLDER_RE.fullmatch(name):
                name = other.get("name") or ""
            found[ep["episode_number"]] = {
                "titolo_episodio": None if _PLACEHOLDER_RE.fullmatch(name) else name or None,
                "trama": ep.get("overview") or other.get("overview") or "",
                "miniatura": _STILL + ep["still_path"] if ep.get("still_path") else None,
            }
        return found

    async def info(self, entry):
        kind = "movie" if entry[0] == "f" else "tv"
        title = entry[1]
        card = await self._cached((kind, title.lower()), lambda: self._search(kind, title))
        if not card:
            return {}
        fields = {k: v for k, v in card.items() if k != "id" and v}
        if entry[0] == "e":
            season, episode = entry[2], entry[3]
            episodes = await self._cached(("season", card["id"], season), lambda: self._season(card["id"], season)) or {}
            fields.update({k: v for k, v in episodes.get(episode, {}).items() if v})
        return fields


class ServerCards(_Cards):
    """Le schede dal server: tutte quelle di una lista in una richiesta sola."""

    def __init__(self, server, key):
        super().__init__()
        self.base = server.rstrip("/") + "/xdcc/api/schede"
        self.key = key

    async def info(self, entry):  # (non usata: add_info chiede tutto insieme)
        found = await self._many([entry])
        return found[0] if found else {}

    async def _many(self, entries):
        params = [("v", ":".join(str(x) for x in (e[0], *e[2:], e[1]))) for e in entries]
        url = self.base + "?" + urllib.parse.urlencode(params)
        data = await self._blocking(_get_json, url, {"X-Api-Key": self.key}, WAIT + 4)
        cards = data.get("schede") or []
        return [c if isinstance(c, dict) else {} for c in cards] + [{}] * (len(entries) - len(cards))

    async def add_info(self, items, entries, wait=WAIT):
        if not items:
            return
        missing = []
        for item, entry in zip(items, entries):
            hit = self._cache.get(entry)
            if hit and hit[1] > time.monotonic():
                item.update(hit[0])
            else:
                missing.append((item, entry))
        for start in range(0, len(missing), _MAX_SERVER_ITEMS):
            batch = missing[start:start + _MAX_SERVER_ITEMS]
            try:
                cards = await asyncio.wait_for(self._many([entry for _, entry in batch]), wait + 5)
            except (OSError, ValueError, asyncio.TimeoutError) as e:
                logger.warning(f"schede dal server non riuscite ({type(e).__name__})")
                return
            for (item, entry), card in zip(batch, cards):
                self._cache[entry] = (card, time.monotonic() + (_TTL if card else _MISS_TTL))
                item.update(card)
