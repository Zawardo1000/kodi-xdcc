import importlib
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin.video.xdcc.direct"))
sys.path.insert(0, str(ROOT / "tests"))  # il finto Kodi è lo stesso dell'altro addon
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_kodi  # noqa: E402

fake_kodi.install()

SETTINGS = {"pagina": "https://liste.test/start.html", "server_irc": "irc.test:6697", "ssl": "true",
            "canale": "#canale", "nick": "giulia", "chiave": "", "chiave_tmdb": "", "dispositivo": "Kodi prova",
            "server": "https://server.test"}


@pytest.fixture
def addon(monkeypatch):
    """main.py importato come lo avvia Kodi (handle 5), col finto Kodi azzerato e le impostazioni compilate."""
    fake_kodi.reset()
    fake_kodi.settings.clear()
    fake_kodi.settings.update(SETTINGS)
    fake_kodi.info.update({"id": "plugin.video.xdcc.direct", "version": "0.1.0"})
    fake_kodi.window.update({"xdccdirect.porta": "1234", "xdccdirect.gettone": "g"})
    monkeypatch.setattr(sys, "argv", ["plugin://plugin.video.xdcc.direct/", "5", ""])

    def no_network(*args, **kwargs):
        raise OSError("niente rete nei test")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    import main
    return importlib.reload(main)
