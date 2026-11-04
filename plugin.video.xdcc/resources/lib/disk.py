"""
Le schede di TMDB (resources/lib/cards.py) su disco, nella cartella dati dell'addon: l'addon riparte a ogni lista, e
senza le schede di prima ogni apertura rifarebbe tutte le richieste. Ognuna vale quanto in memoria (un giorno; 10
minuti se TMDB non aveva risposto). Un file illeggibile vale come vuoto.
"""

import json
import os
import time


def load(provider, path):
    """Mette nella memoria di provider (cards.TmdbCards) le schede del file ancora valide."""
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return
    now, clock = time.time(), time.monotonic()
    for row in rows if isinstance(rows, list) else []:
        try:
            key, value, expires = row
            if key[0] == "season" and isinstance(value, dict):  # episodi per numero: in JSON il numero è testo
                value = {int(number): episode for number, episode in value.items()}
            if expires > now:
                provider._cache[tuple(key)] = (value, clock + expires - now)
        except (TypeError, ValueError, IndexError, KeyError):
            continue


def save(provider, path):
    """Scrive le schede ancora valide della memoria di provider; in un colpo solo (due liste aperte insieme non si
    rovinano il file a vicenda: l'ultima vince). Se non si può, niente: la prossima volta si richiedono."""
    now, clock = time.time(), time.monotonic()
    rows = [[list(key), value, now + expires - clock] for key, (value, expires) in provider._cache.items()
            if expires > clock]
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            os.remove(tmp)
        except OSError:
            pass
