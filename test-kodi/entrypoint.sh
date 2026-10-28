#!/bin/sh
mkdir -p "$HOME/.kodi/userdata/addon_data/plugin.video.xdcc"
[ -f "$HOME/.kodi/userdata/guisettings.xml" ] || cp /opt/guisettings.xml "$HOME/.kodi/userdata/"
# nome del dispositivo per l'abbinamento: si riconosce (e si revoca) facilmente su Telegram
[ -f "$HOME/.kodi/userdata/addon_data/plugin.video.xdcc/settings.xml" ] || printf '%s\n' \
  '<settings version="2">' '    <setting id="dispositivo">test automatici</setting>' '</settings>' \
  > "$HOME/.kodi/userdata/addon_data/plugin.video.xdcc/settings.xml"
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
sleep 2
exec kodi --standalone --windowing=x11
