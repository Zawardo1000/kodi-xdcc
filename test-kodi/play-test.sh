# prova di riproduzione veloce: si ferma appena il video parte o appena è chiaro che non parte (max 40 s)
X="sudo docker exec kodi-test"
R="$X python3 /opt/rpc.py"
URL="$1"
$R Player.Open "{\"item\":{\"file\":\"$URL\"}}" | grep -qE '"OK"' || echo "Player.Open rifiutato"
for i in $(seq 1 20); do
  sleep 2
  if $R Player.GetActivePlayers | grep -q playerid; then
    T=$($R Player.GetProperties '{"playerid":1,"properties":["time","totaltime"]}' | tr -d ' \n' | grep -o '"time":{[^}]*}' | grep -o '"minutes":[0-9]*,"seconds":[0-9]*')
    [ -n "$T" ] && [ "$T" != '"minutes":0,"seconds":0' ] && { echo "PARTE dopo $((i*2)) s, posizione $T"; break; }
  elif [ $i -ge 3 ]; then echo "NON PARTE (nessun player dopo $((i*2)) s)"; break; fi
done
