"""
Addon Kodi di XDCC: menu, liste e riproduzione. Tutta la logica sta sul server (API del bot LVI, /xdcc/api/...):
qui solo l'interfaccia.

Percorsi (plugin://plugin.video.xdcc/?azione=...):
- (nessuna)                    menu principale
- film_novita / serie_novita   ultimi aggiunti (solo file video singoli: si guardano mentre arrivano)
- serie&q=...                  stagioni della serie (o subito gli episodi, se la stagione è una)
- stagione&q=...&n=...         episodi di una stagione
- cerca&tipo=film|serie        tastiera, poi risultati
- server                       file già sul server
- guarda&id=...                fa partire (o si aggancia a) il download e apre il video appena può
- collega                      abbinamento con Telegram (anche dal pulsante nelle impostazioni)
"""

import sys
import time
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

from resources.lib.api import ApiError, XdccApi

ADDON = xbmcaddon.Addon()
BASE = sys.argv[0] if sys.argv else "plugin://plugin.video.xdcc/"
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else -1
TITLE = "XDCC"
DEFAULT_SERVER = "https://xdcc.zawardo.duckdns.org"
_MIME = {"mkv": "video/x-matroska", "avi": "video/x-msvideo", "mp4": "video/mp4", "m4v": "video/mp4",
         "ts": "video/mp2t", "mpg": "video/mpeg", "mpeg": "video/mpeg", "wmv": "video/x-ms-wmv",
         "mov": "video/quicktime", "divx": "video/x-msvideo"}


def plugin_url(**params):
    return BASE + "?" + urlencode(params)


def client(with_key=True):
    server = ADDON.getSetting("server") or DEFAULT_SERVER
    return XdccApi(server, ADDON.getSetting("chiave") if with_key else "")


def lines(text):
    """A capo nei dialoghi di Kodi."""
    return text.replace("\n", "[CR]")


def show_error(error):
    if error.status == 401:
        if xbmcgui.Dialog().yesno(TITLE, lines(error.message + "\n\nCollegarlo adesso?")):
            pair()
    else:
        xbmcgui.Dialog().ok(TITLE, lines(error.message))


def size_text(size):
    return f"{size / 1024 ** 3:.1f} GB".replace(".", ",") if size >= 1024 ** 3 else f"{size / 1024 ** 2:.0f} MB"


def set_info(item, info):
    """Titolo, tipo, stagione ed episodio: InfoTagVideo da Kodi 20, setInfo per Kodi 19."""
    try:
        tag = item.getVideoInfoTag()
    except AttributeError:
        item.setInfo("video", {k: v for k, v in info.items() if v is not None})
        return
    tag.setTitle(info["title"])
    tag.setMediaType(info["mediatype"])
    tag.setPlot(info["plot"])
    if info.get("season") is not None:
        tag.setSeason(info["season"])
        tag.setEpisode(info["episode"])


def folder(label, **params):
    return plugin_url(**params), xbmcgui.ListItem(label=label), True


def playable(entry):
    """Un file da guardare: si apre con azione=guarda, che risolve il link al momento (setResolvedUrl). Il percorso
    resta lo stesso tra una volta e l'altra, così Kodi ricorda il punto in cui si era arrivati."""
    item = xbmcgui.ListItem(label=entry["titolo"])
    item.setProperty("IsPlayable", "true")
    plot = f"{entry['nome']}\n{size_text(entry['dimensione'])}" + (f" · {entry['giorno']}" if entry.get("giorno") else "")
    set_info(item, {"title": entry["titolo"], "plot": plot,
                    "mediatype": "episode" if entry.get("episodio") else "movie",
                    "season": entry.get("stagione"), "episode": entry.get("episodio")})
    return plugin_url(azione="guarda", id=entry["id"]), item, False


def end(items, content="videos"):
    xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.addDirectoryItems(HANDLE, items, len(items))
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def main_menu():
    items = []
    if not ADDON.getSetting("chiave"):
        items.append(folder("[B]Collega a Telegram[/B] (serve una volta sola)", azione="collega"))
    items += [folder("Film · novità", azione="film_novita"),
              folder("Serie · novità", azione="serie_novita"),
              folder("Cerca un film", azione="cerca", tipo="film"),
              folder("Cerca una serie", azione="cerca", tipo="serie"),
              folder("Già sul server", azione="server")]
    end(items, "files")


def show_list(shows):
    end([folder(f"{s['titolo']} ({s['episodi']})", azione="serie", q=s["cerca"]) for s in shows], "tvshows")


def show_seasons(query):
    seasons = client().seasons(query)
    if not seasons:
        xbmcgui.Dialog().notification(TITLE, "Nessun episodio guardabile (solo file singoli)")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    elif len(seasons) == 1:
        end([playable(e) for e in seasons[0]["episodi"]], "episodes")
    else:
        end([folder(f"Stagione {s['numero']} ({len(s['episodi'])} episodi)", azione="stagione", q=query, n=s["numero"])
             for s in seasons], "seasons")


def show_season(query, number):
    season = next((s for s in client().seasons(query) if str(s["numero"]) == str(number)), None)
    end([playable(e) for e in season["episodi"]] if season else [], "episodes")


def search(kind):
    query = xbmcgui.Dialog().input("Cerca un film" if kind == "film" else "Cerca una serie")
    if not query:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    results = client().search(query, kind)
    if kind == "serie":
        show_list(results)
    else:
        end([playable(e) for e in results], "movies")


def wait_until_ready(api, job, item_id):
    """Finestra di attesa: aggiorna con quello che dice il server finché il video può partire (o errore, o Annulla)."""
    monitor = xbmc.Monitor()
    dialog = xbmcgui.DialogProgress()
    dialog.create(TITLE, "In partenza…")
    try:
        while not monitor.abortRequested():
            if dialog.iscanceled():
                api.cancel(job)
                return None
            status = api.status(job, item_id)
            if status.get("stato") in ("pronto", "errore"):
                return status
            total = status.get("totale") or 0
            percent = int(status.get("ricevuti", 0) * 100 / total) if total else 0
            dialog.update(min(percent, 100), lines(status.get("messaggio") or "In partenza…"))
            if monitor.waitForAbort(2):
                break
    finally:
        dialog.close()
    return None


def watch(item_id):
    api = client()
    result = None
    try:
        result = api.watch(item_id)
        if result.get("stato") == "avvio":
            result = wait_until_ready(api, result["lavoro"], item_id)
    except ApiError as e:
        show_error(e)
        result = None
    if result and result.get("stato") == "pronto":
        item = xbmcgui.ListItem(path=result["url"])
        item.setContentLookup(False)  # niente richieste di prova: il tipo si sa dall'estensione
        item.setMimeType(_MIME.get(item_id.rsplit(".", 1)[-1].lower(), "video/x-matroska"))
        xbmcplugin.setResolvedUrl(HANDLE, True, item)
        return
    if result and result.get("messaggio"):  # occupato, errore
        xbmcgui.Dialog().ok(TITLE, lines(result["messaggio"]))
    xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())


def pair():
    """Collega questo Kodi a Telegram: il server dà un codice, la persona scrive al bot "/xdcc kodi CODICE" e qui
    arriva la chiave, che si salva nelle impostazioni. Niente da digitare col telecomando."""
    api = client(with_key=False)
    try:
        started = api.pair_start(ADDON.getSetting("dispositivo") or "Kodi")
    except ApiError as e:
        xbmcgui.Dialog().ok(TITLE, lines(e.message))
        return False
    code, token, lifetime = started["codice"], started["token"], started["scade_tra"]
    deadline = time.time() + lifetime
    monitor = xbmc.Monitor()
    dialog = xbmcgui.DialogProgress()
    dialog.create("Collega a Telegram", lines(f"Scrivi al bot LVI su Telegram:\n[B]/xdcc kodi {code}[/B]"))
    try:
        while time.time() < deadline and not monitor.abortRequested():
            if dialog.iscanceled():
                return False
            status = api.pair_status(code, token)
            if status.get("stato") == "ok":
                ADDON.setSetting("chiave", status["chiave"])
                xbmcgui.Dialog().notification(TITLE, "Collegato a Telegram")
                xbmc.executebuiltin("Container.Refresh")
                return True
            if status.get("stato") == "scaduto":
                break
            left = deadline - time.time()
            dialog.update(int(100 * (1 - left / lifetime)), lines(
                f"Scrivi al bot LVI su Telegram:\n[B]/xdcc kodi {code}[/B]\nIl codice vale ancora {int(left // 60) + 1} min."))
            if monitor.waitForAbort(3):
                return False
    except ApiError as e:
        xbmcgui.Dialog().ok(TITLE, lines(e.message))
        return False
    finally:
        dialog.close()
    xbmcgui.Dialog().ok(TITLE, "Codice scaduto: riprova da «Collega a Telegram».")
    return False


def router(paramstring):
    params = dict(parse_qsl(paramstring.lstrip("?")))
    action = params.get("azione", "")
    if action == "guarda":
        watch(params["id"])
        return
    if action == "collega":
        pair()
        if HANDLE >= 0:  # dal menu (cartella); dalle impostazioni arriva senza handle
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    try:
        if action == "film_novita":
            end([playable(e) for e in client().latest_films()], "movies")
        elif action == "serie_novita":
            show_list(client().latest_shows())
        elif action == "serie":
            show_seasons(params["q"])
        elif action == "stagione":
            show_season(params["q"], params["n"])
        elif action == "cerca":
            search(params.get("tipo", "film"))
        elif action == "server":
            end([playable(e) for e in client().on_server()], "movies")
        else:
            main_menu()
    except ApiError as e:
        show_error(e)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == "__main__":
    router(sys.argv[2] if len(sys.argv) > 2 else "")
