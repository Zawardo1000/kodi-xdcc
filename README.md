<p align="center"><img src="plugin.video.xdcc/resources/icon.png" alt="Zawardo XDCC" width="240"></p>

# Addon Kodi per un server personale

`plugin.video.xdcc` è il client Kodi di un server personale: sfoglia novità e ricerche e guarda i video che il
server mette a disposizione, anche mentre li sta ancora preparando. Senza una chiave rilasciata dal server non fa
niente: ogni richiesta viene rifiutata.

## Installare

Una volta sola, il repository (poi Kodi aggiorna da solo l'addon):

1. Kodi → Impostazioni → Sistema → Add-on: attiva **Sorgenti sconosciute**.
2. Scarica [`repository.zawardo-1.0.2.zip`](https://raw.githubusercontent.com/Zawardo1000/kodi-xdcc/main/repo/repository.zawardo/repository.zawardo-1.0.2.zip) e copialo sul
   dispositivo (chiavetta, cartella condivisa, ...).
3. Kodi → Add-on → icona del pacchetto → **Installa da file zip** → lo zip.
4. **Installa da repository** → Repository zawardo → Add-on video → **Zawardo XDCC** → Installa.

## Collegare il server (una volta per dispositivo)

Nell'addon: **Collega a Telegram**. Compare un codice da mandare al bot del server con `/xdcc kodi CODICE`: l'addon
riceve la sua chiave da solo. Ogni Kodi ha la sua chiave, revocabile dal bot; il server ne conserva solo l'impronta.

## Zawardo XDCC Direct (senza server)

`plugin.video.xdcc.direct` fa tutto da Kodi: legge le liste, si collega a IRC e guarda i file video singoli in
streaming dai bot, senza salvarli (in memoria solo qualche MB intorno al punto in cui si guarda). Si installa dallo
stesso repository (Add-on video → **Zawardo XDCC Direct**).

- Nelle impostazioni: indirizzo della pagina delle liste, server IRC (`indirizzo:porta`), canale e chiave TMDB. Chi è
  registrato sul bot del server li compila con **Compila da Telegram** (`/xdcc kodi CODICE`, come l'altro addon) e
  li riceve aggiornati da soli.
- Nome utente IRC: un nome a caso alla prima apertura, modificabile. Se è già preso, lo stesso nome con 3 lettere in
  fondo.
- Locandine e trame: direttamente da TMDB, con la chiave delle impostazioni (senza chiave, solo i nomi dei file).
- Con i bot: una richiesta alla volta, almeno 3 secondi tra una e l'altra; i salti ravvicinati diventano una
  richiesta sola. In pausa per più di 90 secondi il trasferimento si chiude e libera il posto del bot.

## Sviluppo

- `plugin.video.xdcc/main.py`: menu, liste, attesa, riproduzione, collegamento.
- `plugin.video.xdcc/resources/lib/api.py`: client dell'API del server (solo libreria standard).
- `plugin.video.xdcc.direct/`: `main.py` (menu, uguale all'altro) e `service.py`, che avvia il servizio in
  `resources/lib/`: `core.py` (liste, impostazioni, API locale), `irc.py`, `stream.py` (richieste ai bot e streaming
  in RAM), `web.py` (server locale su 127.0.0.1 per il player), `lists.py`, `cards.py`.
- `repository.zawardo/`: l'addon repository; `repo/`: la cartella pubblicata (la prepara `python build.py`).
- Nuova versione: alza `version` in `addon.xml`, `python build.py`, commit e push.
- Test senza Kodi (finto Kodi in `tests/fake_kodi.py`; per Direct anche una finta rete IRC con bot XDCC in
  `tests_direct/fake_irc.py`), con il Python di Kodi 21 (3.11):

  ```
  docker run --rm -v "C:/Workspace/irc:/src:ro" -w /src python:3.11-slim sh -c "pip install -q pytest && python -m pytest -q -p no:cacheprovider tests && python -m pytest -q -p no:cacheprovider tests_direct"
  ```
- Kodi 21 "Omega" e 22 "Piers" (API Python 3.0.1); per Kodi 19 c'è il ripiego su `setInfo`.
