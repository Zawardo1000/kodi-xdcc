"""
Le schede di TMDB (resources/lib/cards.py) su disco, nella cartella dati dell'addon: l'addon riparte a ogni lista (il
Direct a ogni avvio di Kodi), e senza le schede di prima rifarebbe tutte le richieste. Ognuna con la sua scadenza
(cards.py). Un file illeggibile vale come vuoto. Lo stesso file nei due addon: si cambiano insieme.
"""

import json
import os
import time


def load(provider, path):
    """Mette nella memoria di provider (cards.TmdbCards) le schede del file ancora valide."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    now, clock = time.time(), time.monotonic()
    for row in data if isinstance(data, list) else []:
        try:
            key, value, expires = row
            if key[0] == "season" and isinstance(value, dict):  # episodi per numero: in JSON il numero è testo
                value = {int(number): episode for number, episode in value.items()}
            if expires > now:
                provider._cache[tuple(key)] = (value, clock + expires - now)
        except (TypeError, ValueError, IndexError, KeyError):
            continue


def rows(provider):
    """Le schede ancora valide di provider, pronte da scrivere (da prendere dove la memoria cambia: nel Direct, nel
    giro di asyncio)."""
    now, clock = time.time(), time.monotonic()
    return [[list(key), value, now + expires - clock] for key, (value, expires) in list(provider._cache.items())
            if expires > clock]


def write(path, data):
    """Scrive in un colpo solo (due liste aperte insieme non si rovinano il file a vicenda: l'ultima vince). Se non
    si può, niente: la prossima volta si richiedono."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            os.remove(tmp)
        except OSError:
            pass


def save(provider, path):
    write(path, rows(provider))
