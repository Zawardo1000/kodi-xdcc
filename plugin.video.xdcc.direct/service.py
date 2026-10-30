"""
Servizio di Zawardo XDCC Direct: parte con Kodi e fa tutto il lavoro (resources/lib/core.py): liste, IRC, streaming,
con il server locale da cui leggono l'addon e il player. Porta e gettone del server locale finiscono nelle proprietà
della finestra principale, da dove li legge main.py.

Alla prima partenza sceglie a caso il nome utente IRC (impostazioni, si può cambiare).
Aggiornamenti: appena c'è la rete fa rileggere i repository a Kodi (il suo controllo all'avvio spesso arriva prima
della rete); toglie dalla cache di Kodi gli zip delle versioni vecchie di questi addon.
"""

import logging
import re
import socket

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib import core, names, packages

ADDON_ID = "plugin.video.xdcc.direct"
TITLE = "Zawardo XDCC Direct"
PROP_PORT, PROP_TOKEN = "xdccdirect.porta", "xdccdirect.gettone"
_UPDATE_TRIES = 8
REPO_HOST = "raw.githubusercontent.com"  # dove sta il repository
_ONLINE_WAIT = 300  # secondi: senza rete per tanto, lascia stare (Kodi riprova da solo tra un giorno)
SETTINGS = ("pagina", "server_irc", "ssl", "canale", "nick", "nick_ripiego", "nick_occupato", "chiave_tmdb")


class _KodiLog(logging.Handler):
    def emit(self, record):
        level = xbmc.LOGWARNING if record.levelno >= logging.WARNING else xbmc.LOGINFO
        xbmc.log(f"[{TITLE}] {self.format(record)}", level)


def read_settings():
    addon = xbmcaddon.Addon(ADDON_ID)  # nuova a ogni lettura: vede le modifiche appena salvate
    return {key: addon.getSetting(key) for key in SETTINGS}


def save_setting(key, value):
    xbmcaddon.Addon(ADDON_ID).setSetting(key, value)


def notify(text):
    xbmcgui.Dialog().notification(TITLE, text, xbmcgui.NOTIFICATION_ERROR, 8000)


def ensure_nick():
    addon = xbmcaddon.Addon(ADDON_ID)
    if not addon.getSetting("nick"):
        addon.setSetting("nick", names.random_name())


def push_update(core):
    """Versione nuova vista dall'addon: GitHub tiene l'indice in cache fino a 5 minuti, e Kodi da solo ricontrolla i
    repository una volta al giorno. Si fanno rileggere i repository una volta al minuto, al massimo _UPDATE_TRIES
    volte; Kodi poi la installa da solo (chiudendo questo servizio e avviando quello nuovo)."""
    if not core.update_target or core.update_tries >= _UPDATE_TRIES:
        return
    installed = xbmcaddon.Addon(ADDON_ID).getAddonInfo("version")
    if _version(installed) >= _version(core.update_target):
        core.update_target = None
        return
    core.update_tries += 1
    xbmc.log(f"[{TITLE}] versione {core.update_target} disponibile: rileggo i repository ({core.update_tries})",
             xbmc.LOGINFO)
    xbmc.executebuiltin("UpdateAddonRepos")


def _version(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def refresh_repos_when_online(monitor):
    """All'avvio Kodi rilegge i repository subito, spesso prima che la rete sia pronta (sulla TV: «Could not resolve
    hostname»), e poi non ci riprova per un giorno: gli aggiornamenti non arrivano. Appena il nome del repository si
    risolve, gli si fa rileggere una volta. Le richieste le fa Kodi, come a ogni avvio; il servizio nessuna."""
    for _ in range(_ONLINE_WAIT // 5):
        try:
            socket.getaddrinfo(REPO_HOST, 443)
        except OSError:
            if monitor.waitForAbort(5):
                return
            continue
        if not monitor.waitForAbort(10):  # la rete appena arrivata: qualche secondo per assestarsi
            xbmc.log(f"[{TITLE}] rete pronta: Kodi rilegge i repository", xbmc.LOGINFO)
            xbmc.executebuiltin("UpdateAddonRepos")
        return


def prune_packages():
    removed = packages.prune(xbmcvfs.translatePath("special://home/addons/packages/"))
    if removed:
        xbmc.log(f"[{TITLE}] tolte dalla cache di Kodi le versioni vecchie: {', '.join(removed)}", xbmc.LOGINFO)


def main():
    logger = logging.getLogger("xdcc.direct")
    logger.addHandler(_KodiLog())
    logger.setLevel(logging.INFO)
    logger.propagate = False
    ensure_nick()
    runner = core.Runner(core.Core(read_settings, save_setting, notify))
    try:
        port = runner.start()
    except OSError as e:
        xbmc.log(f"[{TITLE}] servizio non avviato: {e}", xbmc.LOGERROR)
        return
    home = xbmcgui.Window(10000)
    home.setProperty(PROP_PORT, str(port))
    home.setProperty(PROP_TOKEN, runner.core.token)
    xbmc.log(f"[{TITLE}] servizio pronto", xbmc.LOGINFO)
    monitor = xbmc.Monitor()
    refresh_repos_when_online(monitor)
    while not monitor.abortRequested():
        prune_packages()
        if monitor.waitForAbort(60):
            break
        push_update(runner.core)
    home.clearProperty(PROP_PORT)
    home.clearProperty(PROP_TOKEN)
    runner.stop()


if __name__ == "__main__":
    main()
