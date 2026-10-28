import importlib
import sys
import urllib.request
from pathlib import Path

import pytest

ADDON_DIR = Path(__file__).resolve().parent.parent / "plugin.video.xdcc"
sys.path.insert(0, str(ADDON_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_kodi  # noqa: E402

fake_kodi.install()


@pytest.fixture
def addon(monkeypatch):
    """main.py importato come lo avvia Kodi (handle 5), con il finto Kodi azzerato."""
    fake_kodi.reset()
    monkeypatch.setattr(sys, "argv", ["plugin://plugin.video.xdcc/", "5", ""])

    def no_network(*args, **kwargs):  # il controllo degli aggiornamenti non va su GitHub (i test che lo provano
        raise OSError("niente rete nei test")  # mettono una risposta finta)

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    import main
    return importlib.reload(main)
