# Kodi usa e getta per provare l'addon

Kodi 21 (Debian trixie) in Docker con schermo virtuale, senza audio, comandato via JSON-RPC (porta 9090, solo
dentro il container). Serve una macchina con Docker e accesso al server dell'addon.

```sh
docker build -t local/kodi-test .
docker run -d --name kodi-test --memory=2g --shm-size=256m local/kodi-test
docker exec kodi-test python3 /opt/rpc.py JSONRPC.Ping                       # risponde "pong" quando è pronto
docker exec kodi-test import -window root /tmp/x.png && docker cp kodi-test:/tmp/x.png .   # schermata
```

- Installazione come su una TV: scarica lo zip del repository da GitHub dentro `~/.kodi/addons`, poi
  `kodi-send -a UpdateLocalAddons`, `Addons.SetAddonEnabled` del repository, `kodi-send -a UpdateAddonRepos`,
  `kodi-send -a "InstallAddon(plugin.video.xdcc)"` e "Yes" con `Input.Left` + `Input.Select`.
- Il nome del dispositivo per l'abbinamento è già "test automatici" (`entrypoint.sh`): dopo le prove va revocato.
- `play-test.sh URL` e `watch-test.sh ID`: avviano un video e si fermano appena parte o appena è chiaro che non parte.
- `sim.py FILE MB_INIZIALI`: imita un file ancora in download (2 MB/s) su `http://127.0.0.1:8099/seek/…` (salti
  ammessi, 416 oltre la parte arrivata) o `/noseek/…` (Accept-Ranges: none), per provare il player senza scaricare.

Alla fine: `docker rm -f kodi-test && docker rmi local/kodi-test`.
