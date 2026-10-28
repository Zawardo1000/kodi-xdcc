"""Lettura delle liste con la sola libreria standard e etichette uguali a quelle del server."""
import pytest

from resources.lib import lists

# Risposta vera di lista.php?func=1 (accorciata): mirror con lo stesso pack, spazi dopo il pack, un nick non valido
LISTA = """<table class="lista"><tr class="header"><td>Bot</td><td style="display: none;">Pack</td><td>Dim.</td>
<td style="display: none;">Aggiunto il</td><td colspan="2" style="width: 100%;">Nome File</td></tr>
<tr><td>AAA|FILM|001</td><td style="display: none;">#4378 </td><td>1.6G</td><td class="data">27-10-2026</td>
<td><a class="dl" href="#">Hellfire.2026.iTA.WEBRip.AC3.x264-WRM.mkv</a></td><td><button>Download</button></td></tr>
<tr><td>AAA|FILM|002</td><td style="display: none;">#4377 </td><td>1.6G</td><td class="data">27-10-2026</td>
<td><a class="dl" href="#">Hellfire.2026.iTA.WEBRip.AC3.x264-WRM.mkv</a></td><td><button>Download</button></td></tr>
<tr><td>AAA|HD|011</td><td>#10 </td><td>1.9G</td><td class="data">26-10-2026</td>
<td><a class="dl" href="#">Film.2026.1080p-X.part2.rar</a></td><td><button>Download</button></td></tr>
<tr><td>AAA|HD|011</td><td>#9 </td><td>1.9G</td><td class="data">26-10-2026</td>
<td><a class="dl" href="#">Film.2026.1080p-X.part1.rar</a></td><td><button>Download</button></td></tr>
<tr><td>BOT CATTIVO\r\nQUIT</td><td>#1</td><td>1G</td><td>01-01-2026</td>
<td><a class="dl" href="#">x.mkv</a></td><td><button>Download</button></td></tr>
</table>"""


def novita(days):
    """Pagina di lista.php?func=3 come quella vera: una tabella per giorno, intestazione a lettere spaziate."""
    return "".join(f'<table class="lista"><tr><th colspan="2">{day}</th></tr><tr class="header"><td>Dimensione</td>'
                   '<td style="width: 100%;">Nome del file</td></tr>'
                   + "".join(f'<tr><td> 1.6G</td><td><a class="ricerca" href="#">{name}\r</a></td></tr>' for name in names)
                   + "</table>" for day, names in days)


def test_pagina_di_partenza():
    html = ('<a href="http://bbb.example/copia/A.php"><div><span>B.B.B.</span></div></a>'
            '<a href="./x.html">Home</a><a href="http://aaa.example/cerco/B.php"><div><span>A.A.A.</span></div></a>')
    assert lists.parse_start_page(html, "https://partenza.example/p.html") == {
        "BBB": "http://bbb.example/copia/A.php", "AAA": "http://aaa.example/cerco/B.php"}


def test_ricerca_letta_e_raggruppata():
    rows = lists.parse_search(LISTA, "AAA")
    assert len(rows) == 4 and rows[0][2] == "#4378"  # il nick con a capo è scartato, spazio del pack tolto
    releases = lists.group_releases(rows)
    assert [r.name for r in releases] == ["Hellfire.2026.iTA.WEBRip.AC3.x264-WRM.mkv", "Film.2026.1080p-X"]
    single, film = releases
    assert single.playable and single.parts[0].sources == [("AAA|FILM|001", "#4378"),
                                                           ("AAA|FILM|002", "#4377")]
    assert film.multipart and not film.playable
    assert lists.search_results(releases, "film") == [lists.item("AAA", single.name, single.size, "27-10-2026")]


def test_novita_in_ordine_di_giorno():
    aaa = lists.parse_latest(novita([("O G G I", ["A.mkv"]), ("I E R I", ["B.mkv"]),
                                     ("L ' A L T R O &nbsp; I E R I", ["C.mkv"])]), "AAA")
    bbb = lists.parse_latest(novita([("O G G I", ["D.mkv"]), ("I E R I", ["E.mkv"])]), "BBB")
    assert aaa[0] == ("AAA", "oggi", int(1.6 * 1024 ** 3), "A.mkv") and aaa[2][1] == "l'altro ieri"
    assert [f[3] for f in lists.by_day(aaa + bbb)] == ["A.mkv", "D.mkv", "B.mkv", "E.mkv", "C.mkv"]


def test_film_e_serie_delle_novita():
    files = [("AAA", "oggi", 1_700_000_000, "Hellfire.2026.iTA.WEBRip.AC3.x264-WRM.mkv"),
             ("AAA", "oggi", 1_900_000_000, "Hellfire.2026.1080p.BluRay.HEVC.x265-THD.part3.rar"),
             ("BBB", "oggi", 900_000_000, "Nord.Sud.Ovest.Est.S01E05.iTA.WEBRip.AC3.x264-WRM.mkv"),
             ("BBB", "ieri", 880_000_000, "Nord.Sud.Ovest.Est.S01E04.iTA.WEBRip.AC3.x264-WRM.mkv"),
             ("BBB", "ieri", 20_000_000, "Corriere.della.Sera.28.Ottobre.2026.pdf")]
    films = lists.latest_films(files)
    assert [f["id"] for f in films] == ["AAA:Hellfire.2026.iTA.WEBRip.AC3.x264-WRM.mkv"]
    assert films[0]["titolo"] == "Hellfire 2026 · 1,6 GB · WRM"
    assert lists.latest_shows(files) == [{"titolo": "Nord Sud Ovest Est", "cerca": "Nord Sud Ovest Est", "episodi": 2}]


def test_stagioni():
    rows = [("BBB", "BBB|X|001", "#1", "900M", "27-10-2026", name) for name in (
        "Nord.Sud.Ovest.Est.S01E05.iTA.WEBRip.AC3.x264-WRM.mkv", "Nord.Sud.Ovest.Est.S01E04.iTA.WEBRip.AC3.x264-WRM.mkv",
        "Nord.Sud.Ovest.Est.S02E01.iTA.WEBRip.AC3.x264-WRM.mkv", "Altra.Serie.S01E01.iTA.mkv",
        "Nord.Sud.Ovest.Est.S01E06.iTA.part1.rar")]
    seasons = lists.seasons("Nord Sud Ovest Est", lists.group_releases(rows))
    assert [(s["numero"], [e["episodio"] for e in s["episodi"]]) for s in seasons] == [(1, [4, 5]), (2, [1])]


@pytest.mark.parametrize("name,expected", [
    ("Iron.Maiden.Burning.Ambition.Eng.Sub.iTA.AC3.DOC.WebRip.1080p.x264-TBR.mkv", "SUB ITA"),
    ("Buddy.2026.iTALiAN.Subbed.WEBRip.x264-NeWZoNe.mkv", "SUB ITA"),
    ("Pink.Floyd.Live.At.Pompeii.Directors.Cut.2003.CONCERTO.DVDRip.ENG.AC3.x264.RE-TaG.mkv", "ENG"),
    ("VisionQuest.S01E05.Firewall.ITA.ENG.1080p.DSNP.WEB-DL.DDP5.1.H.264-MeM.GP.mkv", None),
    ("Hellfire.2026.iTA.WEBRip.AC3.x264-WRM.mkv", None),
])
def test_lingua(name, expected):
    assert lists.language_of(name) == expected


def test_etichette():
    assert lists.media_label("Minerva.La.Scuola.1x08.Il.Ballo.ITA.ENG.1080p.NFRip.AAC.x265-Pir8.mkv", 734 * 1024 ** 2) \
        == "Minerva La Scuola · S01E08 · 1080p · x265 · 734 MB · Pir8"
    assert lists.episode_of("1670.2026.iTA.AC3.WebRip.1080p.x264.S03.E01-TBR.mkv") == (3, 1)
    assert lists.show_key("Brothers 2026") == lists.show_key("Brothers")
