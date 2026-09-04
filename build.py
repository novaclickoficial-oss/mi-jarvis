# -*- coding: utf-8 -*-
"""build.py -- El indexador.

Python 3, solo libreria estandar. Recorre cada archivo .md dentro de ./notes
y escribe:

  - viewer/graph-data.js  ->  const GRAPH = {nodes:[...], links:[...]};
      Para el navegador. Cada nodo tiene un id NUMERICO igual a su indice en el
      arreglo nodes (los prompts siguientes dependen de buscar nodos por indice).

  - index.json  ->  {"notes_dir": "...", "notes": [{id,label,group,path,text}]}
      Solo para el servidor (no vive dentro de viewer/, asi que el navegador
      nunca lo alcanza). Guarda el texto completo para puntuar en /chat.

Un enlace entre dos notas se crea cuando una menciona el titulo de la otra
o cuando comparten un [[wikilink]].

Las notas capturadas por voz (carpeta notes/captures) se colocan SIEMPRE al
final, para que los ids de las notas base nunca cambien al agregar capturas.
build_all() puede ser llamado por el servidor para reindexar en caliente.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
NOTES_DIR = os.path.join(HERE, "notes")
VIEWER_DIR = os.path.join(HERE, "viewer")
CAPTURES_NAME = "captures"

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


def normalize(s):
    """minusculas, guiones/guion bajo -> espacio, espacios colapsados."""
    s = s.lower().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def make_excerpt(text, limit=700):
    """Texto plano-ish para el panel lateral: quita marcas de markdown y
    corchetes de wikilink, colapsa espacios y recorta a ~700 caracteres."""
    t = WIKILINK_RE.sub(lambda m: m.group(1).replace("-", " "), text)
    t = re.sub(r"[#*`>_]", "", t)          # marcas de markdown
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0] + "..."
    return t


def _is_capture(path):
    return os.path.basename(os.path.dirname(path)) == CAPTURES_NAME


def scan_notes():
    """Devuelve la lista de nodos. Notas base primero (alfabetico), capturas al
    final: asi los ids de las notas base son estables entre reindexados."""
    files = []
    for root, _dirs, names in os.walk(NOTES_DIR):
        for name in names:
            if name.lower().endswith(".md"):
                files.append(os.path.join(root, name))
    files.sort(key=lambda p: (_is_capture(p), p))

    nodes = []
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        stem = os.path.splitext(os.path.basename(path))[0]
        label = stem.replace("-", " ").replace("_", " ").title()
        parent = os.path.basename(os.path.dirname(path))
        group = parent if os.path.dirname(path) != NOTES_DIR else "general"
        nodes.append({
            "id": len(nodes),                      # id NUMERICO = indice
            "label": label,
            "group": group,
            "path": path,
            "text": text,
            "stem": stem,
            "excerpt": make_excerpt(text),
        })
    return nodes


def build_links(nodes):
    """Enlaces por [[wikilink]] o por mencion del titulo de otra nota."""
    by_stem = {normalize(n["stem"]): n["id"] for n in nodes}
    by_label = {normalize(n["label"]): n["id"] for n in nodes}

    pairs = set()
    for n in nodes:
        src = n["id"]
        raw = n["text"]
        raw_lower = raw.lower()

        # 1) wikilinks explicitos
        for target in WIKILINK_RE.findall(raw):
            key = normalize(target)
            tid = by_stem.get(key, by_label.get(key))
            if tid is not None and tid != src:
                pairs.add(frozenset((src, tid)))

        # 2) mencion del titulo de otra nota en el cuerpo
        for other in nodes:
            if other["id"] == src:
                continue
            phrase = normalize(other["label"])
            if len(phrase) >= 6 and phrase in normalize(raw_lower):
                pairs.add(frozenset((src, other["id"])))

    links = []
    for pair in pairs:
        a, b = tuple(pair) if len(pair) == 2 else (next(iter(pair)),) * 2
        links.append({"source": a, "target": b})
    return links


def write_outputs(nodes, links):
    os.makedirs(VIEWER_DIR, exist_ok=True)

    # --- viewer/graph-data.js (para el navegador; sin texto completo) ---
    graph = {
        "nodes": [
            {"id": n["id"], "label": n["label"], "group": n["group"],
             "excerpt": n["excerpt"]}
            for n in nodes
        ],
        "links": links,
    }
    js = "// Generado por build.py -- no editar a mano.\n"
    js += "const GRAPH = " + json.dumps(graph, ensure_ascii=False, indent=2) + ";\n"
    with open(os.path.join(VIEWER_DIR, "graph-data.js"), "w", encoding="utf-8") as f:
        f.write(js)

    # --- index.json (solo para el servidor; con texto completo) ---
    index = {
        "notes_dir": NOTES_DIR,
        "notes": [
            {"id": n["id"], "label": n["label"], "group": n["group"],
             "path": n["path"], "text": n["text"]}
            for n in nodes
        ],
    }
    with open(os.path.join(HERE, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def build_all():
    """Reindexa todo y reescribe los dos archivos. Devuelve (nodes, links).
    El servidor lo llama para reindexar en caliente tras una captura."""
    if not os.path.isdir(NOTES_DIR):
        raise SystemExit("No existe la carpeta de notas: %s" % NOTES_DIR)
    nodes = scan_notes()
    links = build_links(nodes)
    write_outputs(nodes, links)
    return nodes, links


def main():
    nodes, links = build_all()
    groups = sorted({n["group"] for n in nodes})
    print("Notas indexadas: %d" % len(nodes))
    print("Enlaces creados: %d" % len(links))
    print("Grupos: %s" % ", ".join(groups))


if __name__ == "__main__":
    main()
