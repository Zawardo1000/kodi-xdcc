"""Prepara la cartella repo/, il repository Kodi pubblicato su GitHub (raw.githubusercontent.com):

  repo/addons.xml, repo/addons.xml.md5            indice degli addon e suo controllo
  repo/<id>/<id>-<versione>.zip (+ .sha256)       gli addon, con l'impronta che Kodi verifica

Addon: plugin.video.xdcc e repository.zawardo (quello da installare una volta a mano, poi Kodi aggiorna da solo).
La versione è quella di ogni addon.xml: alzala a ogni modifica, se no Kodi non aggiorna. Poi commit e push.
"""

import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADDONS = ["plugin.video.xdcc", "repository.zawardo"]
REPO = ROOT / "repo"


def _version(addon_xml: str) -> str:
    return re.search(r'<addon[^>]*\bversion="([^"]+)"', addon_xml)[1]


def build_zip(addon_id: str) -> Path:
    source = ROOT / addon_id
    version = _version((source / "addon.xml").read_text(encoding="utf-8"))
    out = REPO / addon_id / f"{addon_id}-{version}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                # data fissa: lo stesso codice dà lo stesso zip (e la stessa impronta) a ogni build
                info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    out.with_name(out.name + ".sha256").write_text(hashlib.sha256(out.read_bytes()).hexdigest(), encoding="utf-8")
    return out


def build() -> list[Path]:
    zips = [build_zip(addon_id) for addon_id in ADDONS]
    entries = []
    for addon_id in ADDONS:
        xml = (ROOT / addon_id / "addon.xml").read_text(encoding="utf-8")
        entries.append(re.sub(r"^<\?xml[^>]*\?>\s*", "", xml).strip())
    index = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + "\n".join(entries) + "\n</addons>\n"
    (REPO / "addons.xml").write_text(index, encoding="utf-8", newline="\n")
    (REPO / "addons.xml.md5").write_text(hashlib.md5(index.encode("utf-8")).hexdigest(), encoding="utf-8")
    return zips


if __name__ == "__main__":
    for path in build():
        print(path.relative_to(ROOT))
