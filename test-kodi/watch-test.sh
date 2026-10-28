# prova vera di "guarda" dall'addon: si ferma appena il video parte o appena la finestra d'attesa sparisce senza video
X="sudo docker exec kodi-test"
R="$X python3 /opt/rpc.py"
ID="$1"
T0=$(date +%s)
($R Player.Open "{\"item\":{\"file\":\"plugin://plugin.video.xdcc/?azione=guarda&id=$ID\"}}" > /dev/null 2>&1 &)
seen=0
while [ $(( $(date +%s) - T0 )) -lt 180 ]; do
  sleep 2
  B=$($R XBMC.GetInfoBooleans '{"booleans":["Window.IsVisible(progressdialog)","Player.HasVideo","Player.Playing"]}' | tr -d ' \n')
  E=$(( $(date +%s) - T0 ))
  case "$B" in *'progressdialog)":true'*) seen=1; [ $((E % 10)) -lt 2 ] && echo "$E s: in attesa";; esac
  case "$B" in *'"Player.HasVideo":true'*'"Player.Playing":true'*)
    sleep 6; P=$($R Player.GetProperties '{"playerid":1,"properties":["time","totaltime"]}' | tr -d ' \n' | grep -o '"time":{[^}]*}')
    echo "PARTE dopo $E s; dopo altri 6 s: $P"; exit 0;; esac
  if [ $seen = 1 ] && ! echo "$B" | grep -q 'progressdialog)":true' && ! echo "$B" | grep -q '"Player.HasVideo":true'; then
    sleep 3
    $R XBMC.GetInfoBooleans '{"booleans":["Player.HasVideo"]}' | grep -q true && continue
    echo "NON PARTE: finestra d'attesa chiusa senza video dopo $E s"
    $X sh -c 'tail -40 ~/.kodi/temp/kodi.log | grep -iE "error|warning|eof|CloseFile" | grep -vE "ActiveAE|Sink|GL_|Shader" | cut -c1-200 | tail -8'
    exit 1
  fi
done
echo "TEMPO SCADUTO (180 s)"
