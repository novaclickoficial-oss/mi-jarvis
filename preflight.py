# -*- coding: utf-8 -*-
"""preflight.py -- El arnes de vuelo (prompt 07).

Ejecuta CADA cadena viva del proyecto de punta a punta contra el servidor que
esta corriendo, e imprime un veredicto. No son pruebas unitarias ni mocks:
son llamadas reales, porque los fallos que duelen son los que ninguna prueba
unitaria ve.

Uso: arranca el servidor (python server.py) en otra terminal y luego:
    python preflight.py

Sale con codigo != 0 si algo falla. Cada incidente real merece un cheque nuevo
aqui abajo: un arnes que crece un cheque por incidente se vuelve el archivo mas
valioso del proyecto.
"""
import json
import os
import sys
import urllib.request
import urllib.error

import build

# La consola de Windows suele ser cp1252 y no sabe imprimir ✓/✗: forzamos UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:4700"
CONFIG_PATH = os.path.join(build.HERE, "config.json")
CAPTURES_DIR = os.path.join(build.NOTES_DIR, build.CAPTURES_NAME)
PLACEHOLDER_KEYS = {"", "PUT-YOUR-KEY-HERE"}

results = []  # (estado, etiqueta, detalle) ; estado in {"pass","fail","warn"}


def record(state, label, detail=""):
    mark = {"pass": "✓", "fail": "✗", "warn": "!"}[state]
    print("  %s  %s%s" % (mark, label, (" -- " + detail) if detail else ""))
    results.append((state, label, detail))


def http(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"content-type": "application/json"} if body is not None else {}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return None, str(e.reason)


def read_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- checks
def check_server_up():
    status, body = http("GET", BASE + "/")
    if status == 200 and ("graph-data.js" in body or "<title" in body):
        record("pass", "El servidor esta arriba y sirve la galaxia.")
        return True
    record("fail", "El servidor NO responde en %s" % BASE,
           "arranca 'python server.py' primero" if status is None else "HTTP %s" % status)
    return False


def check_graph_data():
    status, body = http("GET", BASE + "/graph-data.js")
    if status != 200:
        record("fail", "graph-data.js no carga.", "HTTP %s" % status); return
    try:
        raw = body.split("const GRAPH =", 1)[1].rsplit(";", 1)[0].strip()
        graph = json.loads(raw)
        n = len(graph.get("nodes", []))
    except Exception as e:
        record("fail", "graph-data.js no se pudo parsear.", str(e)); return
    if n > 0:
        record("pass", "La galaxia carga y tiene nodos.", "%d nodos" % n)
    else:
        record("fail", "La galaxia tiene 0 nodos.")


def check_chat_shape():
    status, body = http("POST", BASE + "/chat",
                        {"question": "¿Que incluye el manual del barista?"})
    try:
        data = json.loads(body)
    except Exception:
        record("fail", "/chat no devolvio JSON.", "HTTP %s" % status); return
    if status == 200 and isinstance(data.get("answer"), str) and isinstance(data.get("nodes"), list):
        record("pass", "/chat devuelve una respuesta bien formada con arreglo nodes.")
    else:
        record("fail", "/chat no tiene la forma esperada {answer, nodes}.")


def check_api_key():
    cfg = read_config()
    key = str(cfg.get("api_key", "")).strip()
    if key in PLACEHOLDER_KEYS:
        record("fail", "La clave de OpenRouter sigue siendo el placeholder.",
               "ponla en config.json"); return False
    status, _ = http("GET", "https://openrouter.ai/api/v1/key",
                     headers={"authorization": "Bearer " + key})
    if status == 200:
        record("pass", "La clave de OpenRouter es valida (llamada real a /api/v1/key)."); return True
    record("fail", "La clave de OpenRouter no es valida.", "HTTP %s" % status); return False


def check_model_reachable(_key_ok=None):
    model = read_config().get("model", "")
    status, body = http("GET", "https://openrouter.ai/api/v1/models")
    try:
        ids = {m["id"] for m in json.loads(body).get("data", [])}
    except Exception:
        record("fail", "No pude leer el catalogo de OpenRouter.", "HTTP %s" % status); return
    if model in ids:
        record("pass", "El modelo de config.json existe en el catalogo de OpenRouter.", model)
    else:
        record("fail", "El modelo '%s' no esta en el catalogo de OpenRouter." % model)


def check_remember_roundtrip():
    text = "preflight: la ventana de finalizacion debe ser 900 milisegundos"
    status, body = http("POST", BASE + "/remember", {"text": text})
    try:
        data = json.loads(body)
    except Exception:
        record("fail", "/remember no devolvio JSON.", "HTTP %s" % status); return
    if not data.get("ok"):
        record("fail", "/remember no guardo la nota.", data.get("message", "")); return
    new_id = data["node"]["id"]
    fname = data.get("file", "")
    disk_ok = fname and os.path.isfile(os.path.join(CAPTURES_DIR, fname))
    # recuperable de inmediato (misma recuperacion que usa /chat)
    _s, sbody = http("POST", BASE + "/search", {"question": "ventana finalizacion 900 milisegundos"})
    try:
        found = new_id in json.loads(sbody).get("nodes", [])
    except Exception:
        found = False
    if disk_ok and found:
        record("pass", "/remember escribe un archivo real y es recuperable al instante.")
    else:
        record("fail", "/remember fallo.",
               "archivo=%s recuperable=%s" % (bool(disk_ok), found))
    # limpieza: borrar la nota de prueba y reindexar
    try:
        if fname:
            p = os.path.join(CAPTURES_DIR, fname)
            if os.path.isfile(p):
                os.remove(p)
        http("POST", BASE + "/reindex", {})
    except Exception:
        pass


def check_served_matches_disk():
    status, served = http("GET", BASE + "/graph-data.js")
    disk_path = os.path.join(build.VIEWER_DIR, "graph-data.js")
    try:
        # binario, sin traduccion de saltos de linea, para comparar bytes reales
        with open(disk_path, "rb") as f:
            disk = f.read().decode("utf-8")
    except Exception as e:
        record("fail", "No pude leer graph-data.js del disco.", str(e)); return
    if status == 200 and served == disk:
        record("pass", "El archivo servido coincide con el del disco (nada rancio).")
    else:
        record("fail", "El archivo servido NO coincide con el del disco.",
               "posible archivo rancio")


def check_config_not_served():
    status, _ = http("GET", BASE + "/config.json")
    if status == 404:
        record("pass", "config.json NO es alcanzable desde el navegador.")
    else:
        record("fail", "¡config.json ES alcanzable desde el navegador!", "HTTP %s" % status)


def main():
    print("\nPREFLIGHT -- probando cada cadena viva\n")
    if not check_server_up():
        print("\nResumen: 0 pass, 1 fail, 0 warn\n")
        sys.exit(1)
    check_graph_data()
    check_chat_shape()
    key_ok = check_api_key()
    check_model_reachable(key_ok)
    check_remember_roundtrip()
    check_served_matches_disk()
    check_config_not_served()

    npass = sum(1 for s, _, _ in results if s == "pass")
    nfail = sum(1 for s, _, _ in results if s == "fail")
    nwarn = sum(1 for s, _, _ in results if s == "warn")
    print("\nResumen: %d pass, %d fail, %d warn\n" % (npass, nfail, nwarn))
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
