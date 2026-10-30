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
        if path.endswith("/movie/7"):  # dettagli: trailer, cast, generi, regia, durata
            assert params["append_to_response"] == "videos,credits" and params["include_video_language"] == "it,en"
            return {"original_language": "en", "runtime": 102, "genres": [{"name": "Azione"}, {"name": "Thriller"}],
                    "videos": {"results": [
                        {"site": "YouTube", "key": "EnTrailer01", "type": "Trailer", "iso_639_1": "en", "official": True},
                        {"site": "YouTube", "key": "ItTeaser001", "type": "Teaser", "iso_639_1": "it", "official": True},
                        {"site": "YouTube", "key": "ItTrailer01", "type": "Trailer", "iso_639_1": "it"}]},
                    "credits": {"cast": [{"name": "Bea", "character": "Sara", "order": 1},
                                         {"name": "Al", "character": "Max", "order": 0, "profile_path": "/al.jpg"}],
                                "crew": [{"name": "Rita", "job": "Director"}, {"name": "Ugo", "job": "Writer"}]}}
        if path.endswith("/tv/9"):
            return {"original_language": "ko", "episode_run_time": [50], "created_by": [{"name": "Kim"}],
                    "videos": {"results": [{"site": "YouTube", "key": "KoTrailer01", "type": "Trailer",
                                            "iso_639_1": "ko"}]}}
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
    # per la finestra Informazioni: il trailer italiano (anche non ufficiale) prima del teaser e di quello inglese
    assert film["trailer"] == "ItTrailer01" and film["durata"] == 102 and film["generi"] == ["Azione", "Thriller"]
    assert film["cast"] == [["Al", "Max", "https://image.tmdb.org/t/p/w185/al.jpg"], ["Bea", "Sara", ""]]
    assert film["regia"] == ["Rita"]
    # l'episodio: trailer (nella lingua originale: in italiano non c'è) e ideatori della serie
    assert episode["trailer"] == "KoTrailer01" and episode["regia"] == ["Kim"] and episode["durata"] == 50
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


def test_scelta_del_trailer():
    video = lambda key, lang, kind="Trailer", official=False: {  # noqa: E731
        "site": "YouTube", "key": key, "type": kind, "iso_639_1": lang, "official": official}
    assert cards.pick_trailer([video("fr1", "fr"), video("en1", "en", official=True)], "fr") == "fr1"  # originale
    assert cards.pick_trailer([video("de1", "de"), video("en1", "en")], "fr") == "en1"  # poi l'inglese
    assert cards.pick_trailer([video("de1", "de")], "fr") == "de1"  # poi quello che c'è
    assert cards.pick_trailer([video("it1", "it", "Featurette"), {**video("v1", "it"), "site": "Vimeo"}], "it") is None


def test_dettagli_che_non_rispondono_lasciano_la_scheda(monkeypatch):
    def get_json(url, headers=None, timeout=8):
        if "/search/movie" in url:
            return {"results": [{"id": 7, "title": "Hellfire", "overview": "Un incendio."}]}
        raise OSError("TMDB non risponde")

    monkeypatch.setattr(cards, "_get_json", get_json)
    film = {"episodio": None}
    asyncio.run(cards.TmdbCards("k").add_info([film], [("f", "Hellfire")]))
    assert film["titolo_scheda"] == "Hellfire" and "trailer" not in film

