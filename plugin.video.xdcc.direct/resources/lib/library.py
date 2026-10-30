"""
Z-videoteca: film e serie scelti dalla persona (tenendo premuto OK → «Aggiungi alla Z-videoteca»), in un file JSON
nella cartella dati dell'addon. Ogni Kodi ha la sua; gli aggiornamenti dell'addon non la toccano.

- Film: si tiene l'id della lista ("ABC:nome del file"), così l'indirizzo per guardarlo è lo stesso delle novità e
  della ricerca, e Kodi ricorda punto di ripresa e "visto" in tutti e due i posti. Se quel file sparisce dai bot si
  sceglie un'altra versione dello stesso film: diventa il "sostituto", ma l'indirizzo resta quello di sempre.
- Serie: si tiene il titolo da cercare, come le cartelle delle serie: aprendola, gli episodi nuovi ci sono da soli.
- Le schede (locandina, trama, anno, voto) si salvano all'aggiunta: la videoteca si apre senza chiedere niente.
"""

import json
import os
import time

from . import lists

_CARD_KEYS = ("titolo_scheda", "trama", "anno", "voto", "locandina", "sfondo")
FILM_KEYS = ("id", "nome", "dimensione", "titolo") + _CARD_KEYS
SHOW_KEYS = ("titolo", "cerca") + _CARD_KEYS
_FILE_KEYS = ("id", "nome", "dimensione", "titolo")  # di un sostituto


def film_data(entry):
    """I campi di un film da salvare (dall'elemento della lista, con la sua scheda)."""
    return {k: entry[k] for k in FILM_KEYS if entry.get(k) is not None}


def show_data(show):
    """I campi di una serie da salvare (dalla cartella della serie, con la sua scheda)."""
    return {k: show[k] for k in SHOW_KEYS if show.get(k) is not None}


def _title_key(name):
    """Chiave del titolo di un film (anno compreso: un remake è un altro film)."""
    return "".join(c for c in lists.short_title(name).lower() if c.isalnum())


def same_film(name, other):
    return _title_key(name) == _title_key(other)


class Library:
    def __init__(self, path):
        self.path = path
        self.items = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            # illeggibile: si mette da parte (non si sovrascrive al prossimo salvataggio) e si riparte vuoti
            try:
                os.replace(self.path, self.path + ".illeggibile")
            except OSError:
                pass
            return []
        items = data.get("elementi") if isinstance(data, dict) else None
        return [i for i in items or [] if isinstance(i, dict) and
                ((i.get("tipo") == "film" and i.get("id") and i.get("nome")) or (i.get("tipo") == "serie" and i.get("cerca")))]

    def _save(self):
        """Scrittura sicura anche se va via la corrente: file nuovo completo, poi al posto del vecchio."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temp = self.path + ".nuovo"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump({"versione": 1, "elementi": self.items}, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)

    # --- cosa c'è ---

    def films(self):
        return [i for i in self.items if i["tipo"] == "film"]

    def shows(self):
        return [i for i in self.items if i["tipo"] == "serie"]

    def newest_first(self):
        """Gli ultimi aggiunti prima (aggiunti nello stesso secondo: l'ultimo prima)."""
        return list(reversed(sorted(self.items, key=lambda i: i.get("aggiunto", 0))))

    def film(self, name):
        """Il film con questo nome del file (quello aggiunto o il suo sostituto)."""
        return next((f for f in self.films() if name in (f["nome"], (f.get("sostituto") or {}).get("nome"))), None)

    def film_by_id(self, item_id):
        """Il film aggiunto con questo id (l'indirizzo della videoteca)."""
        return next((f for f in self.films() if f["id"] == item_id), None)

    def show(self, query):
        key = lists.show_key(query)
        return next((s for s in self.shows() if lists.show_key(s["cerca"]) == key), None)

    def owner(self, filename):
        """Di chi è un file: il film (aggiunto o sostituto) o la serie di cui è un episodio; None se di nessuno."""
        film = self.film(filename)
        if film:
            return film
        if lists.episode_of(filename):
            return next((s for s in self.shows() if lists.in_show(s["cerca"], filename)), None)
        return None

    # --- modifiche ---

    def add_film(self, entry):
        """-> False se c'era già."""
        data = film_data(entry)
        if not data.get("id") or not data.get("nome") or self.film(data["nome"]):
            return False
        self.items.append({"tipo": "film", **data, "aggiunto": int(time.time())})
        self._save()
        return True

    def add_show(self, show):
        """-> False se c'era già."""
        data = show_data(show)
        if not data.get("cerca") or self.show(data["cerca"]):
            return False
        self.items.append({"tipo": "serie", "titolo": data["cerca"], **data, "aggiunto": int(time.time())})
        self._save()
        return True

    def remove_film(self, name):
        film = self.film(name)
        if film:
            self.items.remove(film)
            self._save()
        return film

    def remove_show(self, query):
        show = self.show(query)
        if show:
            self.items.remove(show)
            self._save()
        return show

    def replace(self, item_id, other):
        """Il file del film item_id non c'è più: da ora si guarda other (un'altra versione dello stesso film)."""
        film = self.film_by_id(item_id)
        if film:
            film["sostituto"] = {k: other[k] for k in _FILE_KEYS if other.get(k) is not None}
            self._save()
        return film
