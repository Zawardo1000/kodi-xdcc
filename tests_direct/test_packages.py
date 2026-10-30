"""Aggiornamenti: zip vecchi tolti dalla cache di Kodi, repository riletti appena c'è la rete."""
import os
import socket
import time

import fake_kodi

from resources.lib import packages


def test_zip_vecchi_tolti_dalla_cache(tmp_path):
    old = time.time() - 3600
    names = ["plugin.video.xdcc.direct-0.1.6.zip", "plugin.video.xdcc.direct-0.2.1.zip", "plugin.video.xdcc-0.3.2.zip",
             "repository.zawardo-1.0.2.zip", "plugin.video.xdcc.altro-1.0.0.zip", "plugin.video.s4me-1.7.8.zip",
             "plugin.video.xdcc.direct-0.2.2.zip"]
    for name in names:
        (tmp_path / name).write_bytes(b"zip")
        if name != "plugin.video.xdcc.direct-0.2.2.zip":  # appena scaricato: Kodi lo sta ancora installando
            os.utime(tmp_path / name, (old, old))
    assert packages.prune(str(tmp_path)) == ["plugin.video.xdcc-0.3.2.zip", "plugin.video.xdcc.direct-0.1.6.zip",
                                             "plugin.video.xdcc.direct-0.2.1.zip", "repository.zawardo-1.0.2.zip"]
    assert sorted(os.listdir(tmp_path)) == ["plugin.video.s4me-1.7.8.zip", "plugin.video.xdcc.altro-1.0.0.zip",
                                            "plugin.video.xdcc.direct-0.2.2.zip"]  # gli altri addon non si toccano
    assert packages.prune(str(tmp_path / "non-esiste")) == []


def test_repository_riletti_appena_c_e_la_rete(monkeypatch):
    import service
    fake_kodi.reset()
    answers = [OSError("Could not resolve hostname"), OSError("Could not resolve hostname"), [("ip",)]]
    asked = []

    def getaddrinfo(host, port):
        asked.append(host)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    service.refresh_repos_when_online(fake_kodi.Monitor())
    assert asked == ["raw.githubusercontent.com"] * 3
    assert [c for c in fake_kodi.calls if c[0] == "builtin"] == [("builtin", "UpdateAddonRepos")]  # una volta sola
