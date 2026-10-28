"""JSON-RPC di Kodi via TCP (127.0.0.1:9090): python3 rpc.py Metodo '{"parametri": ...}' -> stampa la risposta."""
import json
import socket
import sys


def call(method, params=None, timeout=120):
    request = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        request["params"] = params
    with socket.create_connection(("127.0.0.1", 9090), timeout=timeout) as sock:
        sock.sendall(json.dumps(request).encode())
        buffer, decoder = "", json.JSONDecoder()
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("Kodi ha chiuso la connessione")
            buffer += chunk.decode("utf-8", "replace")
            while buffer.strip():
                try:
                    obj, end = decoder.raw_decode(buffer.lstrip())
                except ValueError:
                    break  # oggetto ancora incompleto
                buffer = buffer.lstrip()[end:]
                if obj.get("id") == 1:  # le notifiche (Player.OnPlay...) non hanno id
                    return obj


if __name__ == "__main__":
    print(json.dumps(call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else None), indent=1,
                     ensure_ascii=False))
