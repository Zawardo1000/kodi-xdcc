"""Prepara la cartella repo/, il repository Kodi pubblicato su GitHub (raw.githubusercontent.com):

  repo/addons.xml, repo/addons.xml.md5            indice degli addon e suo controllo
  repo/<id>/<id>-<versione>.zip (+ .sha256)       gli addon, con l'impronta che Kodi verifica
  repo/<id>/<icona, ...>                          le immagini di <assets>: Kodi le mostra prima di installare

Addon: plugin.video.xdcc, plugin.video.xdcc.direct e repository.zawardo (quello da installare una volta a mano, poi
Kodi aggiorna da solo).
La versione è quella di ogni addon.xml: alzala a ogni modifica, se no Kodi non aggiorna. Poi commit e push.
"""

import hashlib
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADDONS = ["plugin.video.xdcc", "plugin.video.xdcc.direct", "repository.zawardo"]
REPO = ROOT / "repo"


def _version(addon_xml: str) -> str:
    return re.search(r'<addon[^>]*\bversion="([^"]+)"', addon_xml)[1]


def _entry(name: str, folder: bool) -> zipfile.ZipInfo:
    """Voce dello zip come la scriverebbe "zip -r" su Linux (cartelle comprese, permessi Unix), anche se lo zip si
    prepara su Windows. Data fissa: lo stesso codice dà lo stesso zip (e la stessa impronta) a ogni build."""
    info = zipfile.ZipInfo(name + ("/" if folder else ""), date_time=(2020, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = ((0o40755 << 16) | 0x10) if folder else (0o100644 << 16)
    info.compress_type = zipfile.ZIP_STORED if folder else zipfile.ZIP_DEFLATED
    return info


def build_zip(addon_id: str) -> Path:
    source = ROOT / addon_id
    version = _version((source / "addon.xml").read_text(encoding="utf-8"))
    out = REPO / addon_id / f"{addon_id}-{version}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_entry(addon_id, folder=True), b"")
        for path in sorted(source.rglob("*")):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            name = path.relative_to(ROOT).as_posix()
            archive.writestr(_entry(name, path.is_dir()), b"" if path.is_dir() else path.read_bytes())
    out.with_name(out.name + ".sha256").write_text(hashlib.sha256(out.read_bytes()).hexdigest(), encoding="utf-8")
    for asset in _assets((source / "addon.xml").read_text(encoding="utf-8")):
        (out.parent / asset).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / asset, out.parent / asset)
    return out


def _assets(addon_xml: str) -> list[str]:
    block = re.search(r"<assets>(.*?)</assets>", addon_xml, re.S)
    return re.findall(r"<(?:icon|fanart|banner|clearlogo|screenshot)>([^<]+)</", block[1]) if block else []


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
