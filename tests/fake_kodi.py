"""Finto Kodi (moduli xbmc, xbmcaddon, xbmcgui, xbmcplugin) per provare l'addon senza Kodi: registra cosa
l'addon mostra e cosa risolve, e risponde ai dialoghi con valori preparati dal test."""
import sys
import types

calls = []  # (funzione, argomenti) di xbmcplugin e dei dialoghi
settings = {}
answers = {"input": "", "yesno": False, "canceled": []}  # canceled: iscanceled() a ogni giro (poi False)


def reset():
    calls.clear()
    settings.clear()
    settings.update({"server": "https://server.test", "chiave": "", "dispositivo": "Kodi prova"})
    answers.update({"input": "", "yesno": False, "canceled": []})


class _Tag:
    def __init__(self):
        self.info = {}

    def __getattr__(self, name):  # setTitle, setMediaType, setSeason, ...
        return lambda value: self.info.__setitem__(name[3:].lower(), value)


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


class Addon:
    def getSetting(self, key):
        return settings.get(key, "")

    def setSetting(self, key, value):
        settings[key] = value


def _plugin_call(name):
    return lambda *args, **kwargs: calls.append((name, args, kwargs))


def install():
    """Mette i finti moduli in sys.modules (prima di importare main.py)."""
    xbmc = types.ModuleType("xbmc")
    xbmc.Monitor = Monitor
    xbmc.executebuiltin = lambda command: calls.append(("builtin", command))
    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = Addon
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.ListItem, xbmcgui.Dialog, xbmcgui.DialogProgress = ListItem, Dialog, DialogProgress
    xbmcgui.NOTIFICATION_INFO, xbmcgui.NOTIFICATION_ERROR = "info", "error"
    xbmcplugin = types.ModuleType("xbmcplugin")
    for name in ("setContent", "addDirectoryItems", "endOfDirectory", "setResolvedUrl"):
        setattr(xbmcplugin, name, _plugin_call(name))
    sys.modules.update({"xbmc": xbmc, "xbmcaddon": xbmcaddon, "xbmcgui": xbmcgui, "xbmcplugin": xbmcplugin})
