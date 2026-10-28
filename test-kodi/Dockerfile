# Kodi usa e getta per i test dell'addon: schermo virtuale (Xvfb), niente audio, comandi da JSON-RPC (porta 9090
# solo dentro il container) e kodi-send.
FROM debian:trixie-slim
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    kodi kodi-eventclients-kodi-send xvfb xauth libgl1-mesa-dri python3 ca-certificates imagemagick curl unzip \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 kodi
COPY entrypoint.sh rpc.py guisettings.xml /opt/
USER kodi
ENV DISPLAY=:99 KODI_AE_SINK=NULL LIBGL_ALWAYS_SOFTWARE=1
ENTRYPOINT ["/bin/sh", "/opt/entrypoint.sh"]
