"""
Le schede di TMDB (resources/lib/cards.py) su disco, nella cartella dati dell'addon: l'addon riparte a ogni lista, e
senza le schede di prima ogni apertura rifarebbe tutte le richieste. Ognuna vale quanto in memoria (main.py: un
mese; 10 minuti se TMDB non aveva risposto), ma una scheda incompleta al massimo INCOMPLETE_TTL: TMDB di solito la
completa nei giorni dopo l'uscita. Un file illeggibile vale come vuoto.
"""

import json
import os
import time

INCOMPLETE_TTL = 24 * 3600  # secondi: una scheda a cui manca qualcosa si richiede il giorno dopo


def incomplete(key, value):
    """Manca qualcosa che TMDB aggiunge di solito dopo l'uscita: trama, locandina o trailer di un film o di una serie,
    la trama di un episodio. Una risposta vuota (None) no: ha già la sua attesa breve."""
    if not isinstance(value, dict):
        return False
    if key[0] == "season":
        return any(not (episode or {}).get("trama") for episode in value.values())
    return not all(value.get(field) for field in ("trama", "locandina", "trailer"))


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
    """Scrive le schede ancora valide della memoria di provider (le incomplete fino a INCOMPLETE_TTL da adesso, mai
    oltre la scadenza che avevano: riscritta ogni volta, non si allunga); in un colpo solo (due liste aperte insieme
    non si rovinano il file a vicenda: l'ultima vince). Se non si può, niente: la prossima volta si richiedono."""
    now, clock = time.time(), time.monotonic()
    rows = [[list(key), value, now + (min(expires, clock + INCOMPLETE_TTL) if incomplete(key, value) else expires) - clock]
            for key, (value, expires) in provider._cache.items() if expires > clock]
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
