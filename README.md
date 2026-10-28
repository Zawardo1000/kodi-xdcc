# Addon Kodi per un server personale

`plugin.video.xdcc` è il client Kodi di un server personale: sfoglia novità e ricerche e guarda i video che il
server mette a disposizione, anche mentre li sta ancora preparando. Senza una chiave rilasciata dal server non fa
niente: ogni richiesta viene rifiutata.

## Installare

Una volta sola, il repository (poi Kodi aggiorna da solo l'addon):

1. Kodi → Impostazioni → Sistema → Add-on: attiva **Sorgenti sconosciute**.
2. Scarica [`repository.zawardo-1.0.1.zip`](https://raw.githubusercontent.com/Zawardo1000/kodi-xdcc/main/repo/repository.zawardo/repository.zawardo-1.0.1.zip) e copialo sul
   dispositivo (chiavetta, cartella condivisa, ...).
3. Kodi → Add-on → icona del pacchetto → **Installa da file zip** → lo zip.
4. **Installa da repository** → Repository zawardo → Add-on video → **XDCC** → Installa.

## Collegare il server (una volta per dispositivo)

Nell'addon: **Collega a Telegram**. Compare un codice da mandare al bot del server con `/xdcc kodi CODICE`: l'addon
riceve la sua chiave da solo. Ogni Kodi ha la sua chiave, revocabile dal bot; il server ne conserva solo l'impronta.

## Sviluppo

- `plugin.video.xdcc/main.py`: menu, liste, attesa, riproduzione, collegamento.
- `plugin.video.xdcc/resources/lib/api.py`: client dell'API del server (solo libreria standard).
- `repository.zawardo/`: l'addon repository; `repo/`: la cartella pubblicata (la prepara `python build.py`).
- Nuova versione: alza `version` in `addon.xml`, `python build.py`, commit e push.
- Test senza Kodi (finto Kodi in `tests/fake_kodi.py`), con il Python di Kodi 21 (3.11):

  ```
  docker run --rm -v "C:/Workspace/irc:/src:ro" -w /src python:3.11-slim sh -c "pip install -q pytest && python -m pytest -q -p no:cacheprovider tests"
  ```
- Kodi 21 "Omega" e 22 "Piers" (API Python 3.0.1); per Kodi 19 c'è il ripiego su `setInfo`.
