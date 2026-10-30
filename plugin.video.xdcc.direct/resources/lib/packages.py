"""
Zip scaricati da Kodi (special://home/addons/packages/): Kodi tiene quello di ogni versione installata e lo mostra in
«Versioni» come cache locale. Quelli di questi addon si tolgono: le versioni si vedono solo dal repository.
"""

import os
import re
import time

ADDONS = ("plugin.video.xdcc", "plugin.video.xdcc.direct", "repository.zawardo")
_RECENT = 120  # uno zip appena scaricato può servire ancora a Kodi per installarlo
_ZIP_RE = re.compile("(?:%s)-\\d[\\w.~+-]*\\.zip" % "|".join(re.escape(a) for a in ADDONS))


def prune(folder, now=None):
    """Toglie gli zip di questi addon (non quelli degli ultimi 2 minuti). -> i nomi tolti."""
    now = time.time() if now is None else now
    removed = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in names:
        if not _ZIP_RE.fullmatch(name):
            continue
        path = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(path) >= _RECENT:
                os.remove(path)
                removed.append(name)
        except OSError:
            pass
    return sorted(removed)
