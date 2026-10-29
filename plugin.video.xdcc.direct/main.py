"""
Addon Kodi Zawardo XDCC Direct: menu, liste e riproduzione, senza server. Il lavoro lo fa il servizio che gira
dentro Kodi (service.py, resources/lib/core.py); qui solo l'interfaccia, che parla al servizio come l'altro addon
parla al server.

Percorsi (plugin://plugin.video.xdcc.direct/?azione=...):
- (nessuna)                    menu principale (senza pagina, server e canale: solo come configurare)
- film_novita / serie_novita   ultimi aggiunti (solo file video singoli: si guardano mentre arrivano)
- serie&q=...                  stagioni della serie (o subito gli episodi, se la stagione è una)
- stagione&q=...&n=...         episodi di una stagione
- cerca&tipo=film|serie        tastiera, poi risultati
- guarda&id=...                fa partire lo streaming e apre il video appena può
- collega                      compila pagina, server, canale e chiave TMDB da Telegram (chi è registrato sul bot)
- impostazioni                 apre le impostazioni
- irc                          verifica la connessione IRC (server, canale, risposta del server) e mostra com'è

Con la chiave di Telegram, all'apertura (una volta al giorno) rilegge dal server pagina, server, canale e chiave
TMDB: se cambiano, si aggiornano da soli. Le schede (locandine, trame) il servizio le chiede direttamente a TMDB. All'apertura controlla anche se c'è una versione più nuova dell'addon.
"""

import os
import random
import re
import sqlite3
import sys
import time
import urllib.request
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib import names
from resources.lib.api import USER_AGENT, ApiError, ServerApi, ServiceApi

ADDON = xbmcaddon.Addon()
ADDON_ID = "plugin.video.xdcc.direct"
BASE = sys.argv[0] if sys.argv else f"plugin://{ADDON_ID}/"
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else -1
TITLE = "Zawardo XDCC Direct"
PROP_PORT, PROP_TOKEN = "xdccdirect.porta", "xdccdirect.gettone"
REPOSITORY = "repository.zawardo"
UPDATE_INDEX = "https://raw.githubusercontent.com/Zawardo1000/kodi-xdcc/main/repo/addons.xml"
_UPDATE_EVERY = 600
_CONFIG_EVERY = 24 * 3600
DEFAULT_SERVER = "https://xdcc.zawardo.duckdns.org"
DEFAULT_DEVICE = "Kodi Direct"
_SOURCES = ("pagina", "server_irc", "canale")
_MIME = {"mkv": "video/x-matroska", "avi": "video/x-msvideo", "mp4": "video/mp4", "m4v": "video/mp4",
         "ts": "video/mp2t", "mpg": "video/mpeg", "mpeg": "video/mpeg", "wmv": "video/x-ms-wmv",
         "mov": "video/quicktime", "divx": "video/x-msvideo"}


_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # senza 0/O e 1/I, che si confondono
_PLATFORMS = (("System.Platform.Android", "Android"), ("System.Platform.TVOS", "Apple TV"),
              ("System.Platform.IOS", "iOS"), ("System.Platform.OSX", "Mac"), ("System.Platform.UWP", "Xbox"),
              ("System.Platform.Windows", "Windows"), ("System.Platform.WebOS", "webOS"),
              ("System.Platform.Linux.RaspberryPi", "Raspberry Pi"), ("System.Platform.Linux", "Linux"))


def device_label():
    """Nome di questo Kodi su Telegram: quello delle impostazioni, il nome del dispositivo in Kodi (se cambiato), il
    sistema e un codice fisso di questa installazione, per distinguere più TV della stessa persona."""
    code = ADDON.getSetting("codice")
    if not code:
        code = "".join(random.choice(_CODE_CHARS) for _ in range(4))
        ADDON.setSetting("codice", code)
    name = ADDON.getSetting("dispositivo").strip() or DEFAULT_DEVICE
    # il nome di Kodi, senza la parte tra parentesi: quando non è cambiato, Kodi ci mette l'IP di casa
    friendly = re.sub(r"\s*\(.*?\)", "", xbmc.getInfoLabel("System.FriendlyName")).strip()
    friendly = "" if re.search(r"\d+\.\d+\.\d+\.\d+", friendly) else friendly
    parts = [name]
    if friendly and friendly.lower() != "kodi" and friendly.lower() not in name.lower():
        parts.append(friendly)
    parts.append(next((label for condition, label in _PLATFORMS if xbmc.getCondVisibility(condition)), ""))
    parts.append(code)
    return " · ".join(p for p in parts if p)[:60]


def plugin_url(**params):
    return BASE + "?" + urlencode(params)


def client():
    home = xbmcgui.Window(10000)
    port, token = home.getProperty(PROP_PORT), home.getProperty(PROP_TOKEN)
    if not port or not token:
        raise ApiError(0, "Il servizio dell'addon non è partito: riavvia Kodi.")
    return ServiceApi(port, token)


def server(with_key=True):
    return ServerApi(ADDON.getSetting("server") or DEFAULT_SERVER, ADDON.getSetting("chiave") if with_key else "")


def configured():
    return all(ADDON.getSetting(key).strip() for key in _SOURCES)


def log(text):
    xbmc.log(f"[{TITLE}] {text}", xbmc.LOGINFO)


def lines(text):
    """A capo nei dialoghi di Kodi."""
    return text.replace("\n", "[CR]")


def show_error(error):
    if error.status == 400 and "impostazioni" in error.message:
        if xbmcgui.Dialog().yesno(TITLE, lines(error.message + "\n\nAprire le impostazioni?")):
            ADDON.openSettings()
    else:
        xbmcgui.Dialog().ok(TITLE, lines(error.message))


def size_text(size):
    return f"{size / 1024 ** 3:.1f} GB".replace(".", ",") if size >= 1024 ** 3 else f"{size / 1024 ** 2:.0f} MB"


def set_info(item, info):
    """Titolo, tipo, trama, anno, voto, serie, stagione ed episodio: InfoTagVideo da Kodi 20, setInfo per Kodi 19."""
    try:
        tag = item.getVideoInfoTag()
    except AttributeError:
        item.setInfo("video", {k: v for k, v in info.items() if v is not None})
        return
    tag.setTitle(info["title"])
    tag.setMediaType(info["mediatype"])
    tag.setPlot(info.get("plot") or "")
    if info.get("year"):
        tag.setYear(info["year"])
    if info.get("rating"):
        tag.setRating(info["rating"])
    if info.get("tvshowtitle"):
        tag.setTvShowTitle(info["tvshowtitle"])
    if info.get("season") is not None:
        tag.setSeason(info["season"])
    if info.get("episode") is not None:
        tag.setEpisode(info["episode"])


def set_art(item, card, show=None):
    """Locandina, sfondo e miniatura dalla scheda; quelle mancanti dalla scheda della serie (show)."""
    show = show or {}
    poster = card.get("locandina") or show.get("locandina")
    art = {"poster": poster, "fanart": card.get("sfondo") or show.get("sfondo"),
           "thumb": card.get("miniatura") or poster}
    art = {k: v for k, v in art.items() if v}
    if art:
        item.setArt(art)


def folder(label, **params):
    return plugin_url(**params), xbmcgui.ListItem(label=label), True


def playable(entry):
    """Un file da guardare: si apre con azione=guarda, che risolve l'indirizzo al momento (setResolvedUrl). Il
    percorso resta lo stesso tra una volta e l'altra, così Kodi ricorda il punto in cui si era arrivati."""
    item = xbmcgui.ListItem(label=entry["titolo"])
    item.setProperty("IsPlayable", "true")
    details = f"{entry['nome']}\n{size_text(entry['dimensione'])}" + (f" · {entry['giorno']}" if entry.get("giorno") else "")
    episode = entry.get("episodio") is not None
    set_info(item, {"title": entry.get("titolo_episodio" if episode else "titolo_scheda") or entry["titolo"],
                    "plot": f"{entry['trama']}\n\n{details}" if entry.get("trama") else details,
                    "mediatype": "episode" if episode else "movie", "year": entry.get("anno"),
                    "rating": entry.get("voto"), "tvshowtitle": entry.get("titolo_scheda") if episode else None,
                    "season": entry.get("stagione"), "episode": entry.get("episodio")})
    set_art(item, entry)
    return plugin_url(azione="guarda", id=entry["id"]), item, False


def end(items, content="videos"):
    xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.addDirectoryItems(HANDLE, items, len(items))
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def _version(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def _update_index():
    """Indirizzo dell'indice (addons.xml) scritto nel repository installato: segue il repository se cambia posto."""
    try:
        path = xbmcvfs.translatePath(f"special://home/addons/{REPOSITORY}/addon.xml")
        with open(path, encoding="utf-8") as f:
            return re.search(r"<info[^>]*>([^<]+)</info>", f.read())[1].strip()
    except (OSError, TypeError):
        return UPDATE_INDEX


_RAW_RE = re.compile(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)")


def _fresh_index_url(url):
    """L'indice all'ultimo commit pubblicato (indirizzo che non cambia mai, quindi mai vecchio): l'indirizzo di
    sempre GitHub lo tiene in cache fino a 5 minuti, e subito dopo una pubblicazione dice ancora la versione vecchia.
    Se GitHub non risponde (o il repository non è su GitHub), l'indirizzo di sempre."""
    m = _RAW_RE.fullmatch(url)
    if not m:
        return url
    owner, repo, branch, path = m.groups()
    try:
        request = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}",
                                         headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github.sha"})
        with urllib.request.urlopen(request, timeout=5) as response:
            sha = response.read().decode("ascii", "replace").strip()
    except (OSError, ValueError):
        return url
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}" if re.fullmatch(r"[0-9a-f]{40}", sha) else url


def check_update():
    """Versione più nuova nel repository: Kodi rilegge i repository e la installa (aggiornamento automatico, la
    scelta predefinita di Kodi). Finché non è installata si riprova a ogni apertura (subito dopo una pubblicazione la
    cache di GitHub può dare ancora l'indice vecchio a Kodi). Senza rete o se qualcosa va storto, niente."""
    home = xbmcgui.Window(10000)
    last = home.getProperty("xdccdirect.aggiornamenti")
    if last and time.time() - float(last) < _UPDATE_EVERY:
        return
    home.setProperty("xdccdirect.aggiornamenti", str(time.time()))
    try:
        request = urllib.request.Request(_fresh_index_url(_update_index()), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=5) as response:
            index = response.read().decode("utf-8")
    except (OSError, ValueError) as e:
        xbmc.log(f"[{TITLE}] controllo aggiornamenti non riuscito: {e}", xbmc.LOGINFO)
        return
    tag = re.search(r'<addon\s[^>]*\bid="plugin\.video\.xdcc\.direct"[^>]*>', index)
    found = re.search(r'\bversion="([^"]+)"', tag[0]) if tag else None
    if found and _version(found[1]) > _version(ADDON.getAddonInfo("version")):
        home.clearProperty("xdccdirect.aggiornamenti")  # alla prossima apertura si insiste, finché non arriva
        xbmc.log(f"[{TITLE}] versione {found[1]} disponibile: Kodi rilegge i repository", xbmc.LOGINFO)
        xbmcgui.Dialog().notification(TITLE, f"Aggiornamento alla versione {found[1]}: arriva da solo tra poco")
        xbmc.executebuiltin("UpdateAddonRepos")
        try:
            client().update_wanted(found[1])  # il servizio insiste finché arriva (la cache di GitHub)
        except ApiError:
            pass


def apply_config(config):
    """Valori dal server nelle impostazioni. -> True se qualcosa è cambiato."""
    values = {"pagina": config.get("pagina"), "server_irc": config.get("server"), "canale": config.get("canale"),
              "ssl": "true" if config.get("ssl", True) else "false", "chiave_tmdb": config.get("tmdb")}
    changed = False
    for key, value in values.items():
        if value and ADDON.getSetting(key) != value:
            ADDON.setSetting(key, value)
            changed = True
    ADDON.setSetting("aggiornato_il", str(int(time.time())))
    return changed


def refresh_config():
    """Con la chiave di Telegram, una volta al giorno: i valori nuovi del server (se cambiano server o canale)."""
    if not ADDON.getSetting("chiave"):
        return
    try:
        if time.time() - float(ADDON.getSetting("aggiornato_il") or 0) < _CONFIG_EVERY:
            return
    except ValueError:
        pass
    try:
        if apply_config(server().direct_config()):
            xbmcgui.Dialog().notification(TITLE, "Impostazioni aggiornate dal server")
    except ApiError as e:
        xbmc.log(f"[{TITLE}] impostazioni dal server non lette: {e.message}", xbmc.LOGINFO)


def main_menu():
    if not ADDON.getSetting("nick"):
        ADDON.setSetting("nick", names.random_name())
    refresh_config()
    if not configured():
        items = [folder("[B]Compila da Telegram[/B] (se sei registrato sul bot)", azione="collega"),
                 folder("[B]Impostazioni[/B]: pagina delle liste, server IRC e canale", azione="impostazioni")]
        end(items, "files")
    else:
        end([folder("Film · novità", azione="film_novita"),
             folder("Serie · novità", azione="serie_novita"),
             folder("Cerca un film", azione="cerca", tipo="film"),
             folder("Cerca una serie", azione="cerca", tipo="serie"),
             folder(irc_label(), azione="irc")], "files")
        try:
            client().prefetch()  # le novità si preparano mentre si sceglie
        except ApiError:
            pass
    check_update()  # dopo il menu, che intanto è già sullo schermo


def irc_label():
    """Voce del menu con lo stato della connessione IRC (chiesto al servizio, che non si collega per questo)."""
    try:
        status = client().irc_status()
    except ApiError:
        return "Connessione IRC: servizio non attivo"
    if status.get("nel_canale"):
        return f"Connessione IRC: collegato come {status['nick']} in {status['canale']} · [I]verifica[/I]"
    if status.get("errore"):
        return f"Connessione IRC: [COLOR orange]{status['errore']}[/COLOR] · [I]verifica[/I]"
    return "Connessione IRC: non collegato (si collega al primo video) · [I]verifica[/I]"


def check_irc():
    """Verifica scelta dalla persona: il servizio si collega (se serve), entra nel canale e manda un PING."""
    dialog = xbmcgui.DialogProgress()
    dialog.create(TITLE, "Verifico la connessione IRC…")
    try:
        status = client().irc_status(True)
    except ApiError as e:
        status = {"errore": e.message}
    finally:
        dialog.close()
    log(f"verifica IRC: {status}")
    if status.get("nel_canale"):
        heard = status.get("sentito_da")
        text = (f"Collegato a {status['server']} come [B]{status['nick']}[/B], nel canale [B]{status['canale']}[/B].\n"
                "Il server risponde" + (f" (ultima risposta {heard} s fa)." if heard is not None else "."))
    else:
        text = ("[B]Non collegato[/B]" + (f": {status['errore']}" if status.get("errore") else "") + "\n\n"
                "Se dice «bannato» o «rifiutato», il server o il canale non ci vogliono: controlla server e canale "
                "nelle impostazioni, o riprova più tardi.")
    xbmcgui.Dialog().ok(TITLE, lines(text))


def show_folder(show):
    url, item, is_folder = folder(f"{show['titolo']} ({show['episodi']})", azione="serie", q=show["cerca"])
    set_info(item, {"title": show.get("titolo_scheda") or show["titolo"], "plot": show.get("trama"),
                    "mediatype": "tvshow", "year": show.get("anno"), "rating": show.get("voto")})
    set_art(item, show)
    return url, item, is_folder


def season_folder(query, season, show):
    url, item, is_folder = folder(f"Stagione {season['numero']} ({len(season['episodi'])} episodi)",
                                  azione="stagione", q=query, n=season["numero"])
    set_info(item, {"title": f"Stagione {season['numero']}", "plot": show.get("trama"), "mediatype": "season",
                    "tvshowtitle": show.get("titolo_scheda"), "season": season["numero"]})
    set_art(item, {}, show)
    return url, item, is_folder


def show_list(shows):
    end([show_folder(s) for s in shows], "tvshows")


def show_seasons(query):
    page = client().show_page(query)
    seasons = page["stagioni"]
    if not seasons:
        xbmcgui.Dialog().notification(TITLE, "Nessun episodio guardabile (solo file singoli)")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    elif len(seasons) == 1:
        end([playable(e) for e in seasons[0]["episodi"]], "episodes")
    else:
        end([season_folder(query, s, page["serie"]) for s in seasons], "seasons")


def show_season(query, number):
    season = next((s for s in client().show_page(query)["stagioni"] if str(s["numero"]) == str(number)), None)
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
    """Finestra di attesa: aggiorna con quello che dice il servizio finché il video può partire (o errore, o
    Annulla)."""
    monitor = xbmc.Monitor()
    dialog = xbmcgui.DialogProgress()
    dialog.create(TITLE, "In partenza…")
    try:
        while not monitor.abortRequested():
            if dialog.iscanceled():
                log(f"guarda {job}: annullato dalla finestra di attesa")
                api.cancel(job)
                return None
            status = api.status(job, item_id)
            if status.get("stato") in ("pronto", "errore"):
                log(f"guarda {job}: {status['stato']} {status.get('messaggio') or ''}")
                return status
            total = status.get("totale") or 0
            percent = int(status.get("ricevuti", 0) * 100 / total) if total else 0
            dialog.update(min(percent, 100), lines(status.get("messaggio") or "In partenza…"))
            if monitor.waitForAbort(1):
                break
    finally:
        dialog.close()
    return None


def watch(item_id):
    result = None
    try:
        api = client()
        resume = "resume:true" in sys.argv[3:]
        result = api.watch(item_id, resume, resume_point() if resume else 0.0)
        log(f"guarda {item_id} ({' '.join(sys.argv[3:]) or 'senza ripresa'}): {result.get('stato')} "
            f"{result.get('lavoro') or result.get('messaggio') or ''}")
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
    if result and result.get("messaggio"):
        xbmcgui.Dialog().ok(TITLE, lines(result["messaggio"]))
    xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())


def resume_point():
    """Secondi del punto di ripresa che Kodi ha salvato per questo video (dal suo database, in sola lettura); 0 se
    non c'è. Il servizio scarica quel punto prima di far partire il video."""
    url = BASE + (sys.argv[2] if len(sys.argv) > 2 else "")
    try:
        folder = xbmcvfs.translatePath("special://database/")
        names = [n for n in os.listdir(folder) if re.fullmatch(r"MyVideos\d+\.db", n)]
        newest = max(names, key=lambda n: int(re.sub(r"\D", "", n)))
        con = sqlite3.connect(f"file:{os.path.join(folder, newest)}?mode=ro", uri=True, timeout=3)
        try:
            row = con.execute("SELECT b.timeInSeconds FROM bookmark b JOIN files f ON b.idFile = f.idFile "
                              "WHERE b.type = 1 AND f.strFilename = ? ORDER BY b.idBookmark DESC LIMIT 1",
                              (url,)).fetchone()
        finally:
            con.close()
        return float(row[0]) if row else 0.0
    except (OSError, ValueError, sqlite3.Error) as e:
        log(f"punto di ripresa non letto ({type(e).__name__})")
        return 0.0


def pair():
    """Compila pagina, server, canale e chiave TMDB da Telegram: il server dà un codice, la persona scrive al bot
    "/xdcc kodi CODICE" e qui arriva la chiave, con cui si chiedono i valori. Niente da digitare col telecomando."""
    api = server(with_key=False)
    try:
        started = api.pair_start(device_label())
    except ApiError as e:
        xbmcgui.Dialog().ok(TITLE, lines(e.message))
        return False
    code, token, lifetime = started["codice"], started["token"], started["scade_tra"]
    deadline = time.time() + lifetime
    monitor = xbmc.Monitor()
    dialog = xbmcgui.DialogProgress()
    seen_as = started.get("dispositivo") or device_label()
    dialog.create("Compila da Telegram", lines(f"Scrivi al bot LVI su Telegram:\n[B]/xdcc kodi {code}[/B]"))
    try:
        while time.time() < deadline and not monitor.abortRequested():
            if dialog.iscanceled():
                return False
            status = api.pair_status(code, token)
            if status.get("stato") == "ok":
                ADDON.setSetting("chiave", status["chiave"])
                if status.get("utente"):  # a quale persona su Telegram è collegato questo Kodi
                    ADDON.setSetting("utente_telegram", str(status["utente"]))
                apply_config(server().direct_config())
                xbmcgui.Dialog().notification(TITLE, "Impostazioni compilate da Telegram")
                xbmc.executebuiltin("Container.Refresh")
                return True
            if status.get("stato") == "scaduto":
                break
            left = deadline - time.time()
            dialog.update(int(100 * (1 - left / lifetime)), lines(
                f"Scrivi al bot LVI su Telegram:\n[B]/xdcc kodi {code}[/B]\nIl codice vale ancora {int(left // 60) + 1} min."
                f"\nSu Telegram questo Kodi sarà: {seen_as}"))
            if monitor.waitForAbort(3):
                return False
    except ApiError as e:
        xbmcgui.Dialog().ok(TITLE, lines(e.message))
        return False
    finally:
        dialog.close()
    xbmcgui.Dialog().ok(TITLE, "Codice scaduto: riprova da «Compila da Telegram».")
    return False


def router(paramstring):
    params = dict(parse_qsl(paramstring.lstrip("?")))
    action = params.get("azione", "")
    if action == "guarda":
        watch(params["id"])
        return
    if action in ("collega", "impostazioni", "irc"):
        if action == "collega":
            pair()
        elif action == "irc":
            check_irc()
        else:
            ADDON.openSettings()
        if HANDLE >= 0:  # dal menu (cartella); dalle impostazioni arriva senza handle
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            xbmc.executebuiltin("Container.Refresh")
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
        else:
            main_menu()
    except ApiError as e:
        show_error(e)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == "__main__":
    router(sys.argv[2] if len(sys.argv) > 2 else "")
