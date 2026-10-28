"""
Servizio di Zawardo XDCC Direct: parte con Kodi e fa tutto il lavoro (resources/lib/core.py): liste, IRC, streaming,
con il server locale da cui leggono l'addon e il player. Porta e gettone del server locale finiscono nelle proprietà
della finestra principale, da dove li legge main.py.

Alla prima partenza sceglie a caso il nome utente IRC (impostazioni, si può cambiare).
"""

import logging

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import core, names

ADDON_ID = "plugin.video.xdcc.direct"
TITLE = "Zawardo XDCC Direct"
PROP_PORT, PROP_TOKEN = "xdccdirect.porta", "xdccdirect.gettone"
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
    while not monitor.abortRequested():
        if monitor.waitForAbort(10):
            break
    home.clearProperty(PROP_PORT)
    home.clearProperty(PROP_TOKEN)
    runner.stop()


if __name__ == "__main__":
    main()
