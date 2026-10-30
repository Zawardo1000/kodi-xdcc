"""Z-videoteca: film e serie salvati in un file, sostituti dei film spariti, a chi appartiene un file."""
import json

from resources.lib import library

FILM = {"id": "AAA:Hellfire.2026.iTA.1080p.mkv", "nome": "Hellfire.2026.iTA.1080p.mkv", "dimensione": 1_700_000_000,
        "titolo": "Hellfire 2026 · 1080p · 1,6 GB", "giorno": "oggi", "stagione": None, "episodio": None,
        "titolo_scheda": "Hellfire", "trama": "Fuoco.", "anno": 2026, "locandina": "https://img.test/p.jpg"}
SHOW = {"titolo": "The Last of Us", "cerca": "The Last of Us", "episodi": 9, "titolo_scheda": "The Last of Us",
        "locandina": "https://img.test/s.jpg"}


def test_aggiunge_e_ricorda(tmp_path):
    path = str(tmp_path / "dati" / "videoteca.json")
    lib = library.Library(path)
    assert lib.add_film(FILM) and lib.add_show(SHOW)
    assert not lib.add_film(FILM) and not lib.add_show({"cerca": "the last of us"})  # c'erano già
    again = library.Library(path)  # riaperta (un'altra apertura dell'addon)
    assert [i["tipo"] for i in again.items] == ["film", "serie"]
    film = again.film("Hellfire.2026.iTA.1080p.mkv")
    assert film["id"] == FILM["id"] and film["locandina"] == "https://img.test/p.jpg" and film["anno"] == 2026
    assert "giorno" not in film and "episodi" not in again.show("The Last of Us")  # solo quello che non cambia
    assert again.newest_first()[0]["aggiunto"] >= again.newest_first()[1]["aggiunto"]
    assert again.remove_film(FILM["nome"]) and again.remove_show("THE LAST OF US")
    assert library.Library(path).items == []


def test_file_rovinato_messo_da_parte(tmp_path):
    path = tmp_path / "videoteca.json"
    path.write_text("{non è json", encoding="utf-8")
    lib = library.Library(str(path))
    assert lib.items == []
    assert (tmp_path / "videoteca.json.illeggibile").read_text(encoding="utf-8") == "{non è json"
    lib.add_show(SHOW)
    assert json.loads(path.read_text(encoding="utf-8"))["elementi"][0]["cerca"] == "The Last of Us"


def test_sostituto_di_un_film_sparito(tmp_path):
    lib = library.Library(str(tmp_path / "videoteca.json"))
    lib.add_film(FILM)
    other = {"id": "BBB:Hellfire.2026.iTA.720p.mkv", "nome": "Hellfire.2026.iTA.720p.mkv", "dimensione": 900_000_000,
             "titolo": "Hellfire 2026 · 720p · 858 MB", "trama": "non serve"}
    lib.replace(FILM["id"], other)
    film = library.Library(lib.path).film_by_id(FILM["id"])
    assert film["sostituto"] == {"id": other["id"], "nome": other["nome"], "dimensione": 900_000_000,
                                 "titolo": "Hellfire 2026 · 720p · 858 MB"}
    assert lib.film("Hellfire.2026.iTA.720p.mkv")["id"] == FILM["id"]  # il sostituto è dello stesso film
    assert library.same_film("Hellfire.2026.iTA.720p.mkv", FILM["nome"])
    assert not library.same_film("Hellfire.2025.iTA.1080p.mkv", FILM["nome"])  # un altro anno: un altro film


def test_di_chi_e_un_file(tmp_path):
    lib = library.Library(str(tmp_path / "videoteca.json"))
    lib.add_film(FILM)
    lib.add_show(SHOW)
    assert lib.owner("Hellfire.2026.iTA.1080p.mkv")["tipo"] == "film"
    assert lib.owner("The.Last.of.Us.S02E03.iTA.1080p.mkv")["cerca"] == "The Last of Us"
    assert lib.owner("The.Last.of.Us.2023.iTA.1080p.mkv") is None  # stesso titolo, ma non un episodio
    assert lib.owner("Dexter.S01E01.iTA.mkv") is None
