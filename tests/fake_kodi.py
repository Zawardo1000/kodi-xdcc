"""Finto Kodi (moduli xbmc, xbmcaddon, xbmcgui, xbmcplugin) per provare l'addon senza Kodi: registra cosa
l'addon mostra e cosa risolve, e risponde ai dialoghi con valori preparati dal test."""
import sys
import types

calls = []  # (funzione, argomenti) di xbmcplugin e dei dialoghi
settings = {}
info = {"id": "plugin.video.xdcc", "version": "0.3.0"}
window = {}  # proprietà della finestra principale (Window(10000))
home = {"path": ""}  # cartella vera al posto di special://home/
labels = {}  # InfoLabel di Kodi (System.FriendlyName, ...)
conditions = set()  # condizioni vere (System.Platform.Android, ...)
answers = {"input": "", "yesno": False, "select": -1, "canceled": []}  # canceled: iscanceled() a ogni giro (poi False)


def reset():
    calls.clear()
    settings.clear()
    settings.update({"server": "https://server.test", "chiave": "", "dispositivo": "Kodi prova"})
    answers.update({"input": "", "yesno": False, "select": -1, "canceled": []})
    info.update({"version": "0.3.0"})
    window.clear()
    home["path"] = ""
    labels.clear()
    conditions.clear()


class _Tag:
    def __init__(self):
        self.info = {}

    def __getattr__(self, name):  # setTitle, setMediaType, setSeason, setResumePoint(secondi, durata), ...
        return lambda *values: self.info.__setitem__(name[3:].lower(), values[0] if len(values) == 1 else values)


class ListItem:
    def __init__(self, label="", path=""):
        self.label, self.path, self.props, self.tag, self.mime = label, path, {}, _Tag(), None

    def setProperty(self, key, value):
        self.props[key] = value

    def getVideoInfoTag(self):
        return self.tag

    def setContentLookup(self, value):
        self.props["contentlookup"] = value

    def setMimeType(self, value):
        self.mime = value

    def setArt(self, art):
        self.props["art"] = art

    def addContextMenuItems(self, items):
        self.props.setdefault("menu", []).extend(items)


class Dialog:
    def ok(self, heading, message):
        calls.append(("ok", message))
        return True

    def yesno(self, heading, message):
        calls.append(("yesno", message))
        return answers["yesno"]

    def notification(self, heading, message, *args):
        calls.append(("notification", message))

    def input(self, heading):
        calls.append(("input", heading))
        return answers["input"]

    def select(self, heading, options):
        calls.append(("select", heading, list(options)))
        return answers["select"]


class DialogProgress:
    def create(self, heading, message=""):
        calls.append(("progress", message))

    def update(self, percent, message=""):
        calls.append(("update", percent, message))

    def iscanceled(self):
        return answers["canceled"].pop(0) if answers["canceled"] else False

    def close(self):
        calls.append(("close",))


class Monitor:
    def abortRequested(self):
        return False

    def waitForAbort(self, seconds):
        return False


class Window:
    def __init__(self, window_id):
        pass

    def getProperty(self, key):
        return window.get(key, "")

    def setProperty(self, key, value):
        window[key] = value

    def clearProperty(self, key):
        window.pop(key, None)


class Addon:
    def getAddonInfo(self, key):
        return info.get(key, "")

    def getSetting(self, key):
        return settings.get(key, "")

    def setSetting(self, key, value):
        settings[key] = value

    def openSettings(self):
        calls.append(("impostazioni",))


def _plugin_call(name):
    return lambda *args, **kwargs: calls.append((name, args, kwargs))


def install():
    """Mette i finti moduli in sys.modules (prima di importare main.py)."""
    xbmc = types.ModuleType("xbmc")
    xbmc.Monitor = Monitor
    xbmc.executebuiltin = lambda command: calls.append(("builtin", command))
    xbmc.log = lambda message, level=0: calls.append(("log", message))
    xbmc.LOGINFO, xbmc.LOGWARNING = 1, 2
    xbmc.getInfoLabel = lambda label: labels.get(label, "")
    xbmc.getCondVisibility = lambda condition: condition in conditions
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda path: path.replace("special://home/", home["path"])
    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = Addon
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.ListItem, xbmcgui.Dialog, xbmcgui.DialogProgress, xbmcgui.Window = ListItem, Dialog, DialogProgress, Window
    xbmcgui.NOTIFICATION_INFO, xbmcgui.NOTIFICATION_ERROR = "info", "error"
    xbmcplugin = types.ModuleType("xbmcplugin")
    for name in ("setContent", "addDirectoryItems", "endOfDirectory", "setResolvedUrl"):
        setattr(xbmcplugin, name, _plugin_call(name))
    sys.modules.update({"xbmc": xbmc, "xbmcaddon": xbmcaddon, "xbmcgui": xbmcgui, "xbmcplugin": xbmcplugin,
                        "xbmcvfs": xbmcvfs})
