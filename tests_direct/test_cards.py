"""Schede da TMDB con la chiave delle impostazioni (risposte finte di TMDB, niente rete)."""
import asyncio
import urllib.parse

from resources.lib import cards, core


def test_schede_da_tmdb(monkeypatch):
    asked = []

    def get_json(url, headers=None, timeout=8):
        path, _, query = url.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        asked.append((path.rsplit("/3", 1)[-1], params))
        if "/search/movie" in path:
            return {"results": [{"id": 7, "title": "Hellfire", "overview": "Un incendio.", "poster_path": "/p.jpg",
                                 "release_date": "2026-03-01", "vote_average": 6.84}]}
        if "/search/tv" in path:
            return {"results": [{"id": 9, "name": "Brothers", "overview": "Fratelli.", "first_air_date": "2026-01-01"}]}
        if "/season/1" in path:
            return {"episodes": [{"episode_number": 2, "name": "Il pozzo", "overview": "Secondo.",
                                  "still_path": "/e2.jpg"}]}
        raise OSError("non previsto")

    monkeypatch.setattr(cards, "_get_json", get_json)
    provider = cards.TmdbCards("chiave-tmdb")
    film, episode = {"episodio": None}, {"stagione": 1, "episodio": 2}

    async def run():
        await provider.add_info([film, episode], [cards.entry_of(film, "Hellfire 2026", False),
                                                  cards.entry_of(episode, "Brothers", True)])
        await provider.add_info([dict(film)], [("f", "Hellfire 2026")])  # di nuovo: dalla memoria

    asyncio.run(run())
    assert film["titolo_scheda"] == "Hellfire" and film["anno"] == 2026 and film["voto"] == 6.8
    assert film["locandina"] == "https://image.tmdb.org/t/p/w500/p.jpg"
    assert episode["titolo_episodio"] == "Il pozzo" and episode["trama"] == "Secondo."
    assert all(params["api_key"] == "chiave-tmdb" for _, params in asked)
    assert asked[0] == ("/search/movie", {"api_key": "chiave-tmdb", "query": "Hellfire", "language": "it-IT",
                                          "year": "2026"})
    assert len([a for a in asked if a[0] == "/search/movie"]) == 1


def test_senza_chiave_niente_schede():
    base = {"pagina": "https://liste.test/p.html", "server_irc": "irc.test", "canale": "#c", "nick": "giulia"}
    service = core.Core(lambda: base)
    assert service._cards_for(core.parse_config(base)) is None
    provider = service._cards_for(core.parse_config(dict(base, chiave_tmdb="k")))
    assert isinstance(provider, cards.TmdbCards) and provider.api_key == "k"
