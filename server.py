# -*- coding: utf-8 -*-
"""server.py -- El servidor y el cerebro.

Python 3, solo libreria estandar. Escucha en el puerto 4700 y:

  GET  /                 -> sirve UNICAMENTE la carpeta viewer/ (la galaxia).
  POST /chat             -> puntua notas por palabras (titulo x3), top 6, y
                            llama a OpenRouter (formato compatible OpenAI). Devuelve
                            {"answer": "...", "nodes": [indices usados]}.
  POST /remember         -> guarda una nota nueva en notes/captures, reindexa
                            en caliente y devuelve el nodo para agregarlo vivo.
  POST /see              -> responde sobre una imagen (captura de pantalla).
  POST /model            -> cambia el modelo en caliente (se niega si no existe).
  GET  /model            -> modelo actual + etiqueta.
  POST /search           -> ids de las notas relevantes (sin llamar al modelo).
  POST /reindex          -> reconstruye el indice desde el disco.

config.json e index.json viven en la raiz, fuera de viewer/, asi que el
navegador nunca los alcanza. La llamada a la API se hace con urllib, sin
dependencias.
"""
import json
import os
import re
import datetime
import base64
import time
import threading
import tempfile
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import build  # reutilizamos el indexador para reindexar en caliente
import deals  # el motor de facturas / cotizaciones / propuestas

HERE = os.path.dirname(os.path.abspath(__file__))
VIEWER_DIR = os.path.join(HERE, "viewer")
CONFIG_PATH = os.path.join(HERE, "config.json")
INDEX_PATH = os.path.join(HERE, "index.json")
CAPTURES_DIR = os.path.join(build.NOTES_DIR, build.CAPTURES_NAME)
PORT = 4700
API_URL = "https://openrouter.ai/api/v1/chat/completions"
PLACEHOLDER_KEYS = {"", "PUT-YOUR-KEY-HERE"}

# --- ElevenLabs (voz opcional). Si la clave sigue en placeholder, el cliente
#     usa la voz del navegador. La clave vive en config.json, nunca en el chat.
EL_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/%s"
EL_VOICES_URL = "https://api.elevenlabs.io/v1/voices"
EL_PLACEHOLDERS = {"", "PON-TU-CLAVE-ELEVENLABS-AQUI"}
_EL_VOICE_CACHE = {"id": None}

# ===========================================================================
#  LA PERSONA  --  el caracter del asistente vive en UN solo bloque, aqui.
#  Para cambiarle la personalidad (a un operador de mision, un amigo sarcastico,
#  un maestro paciente...) reescribe SOLO este bloque. Nada mas depende de el.
# ===========================================================================
PERSONA = (
    "Eres un mayordomo: cortes hasta lo imposible, britanico de modales, con un "
    "ingenio seco y afilado. Sirves a un solo senor y hablas su idioma (por "
    "defecto, espanol). Reglas de caracter:\n"
    "- Trata al usuario de 'senor' de vez en cuando, no en cada frase; abusar de "
    "ello es la diferencia entre encantador e insoportable.\n"
    "- Responde en UNA frase ingeniosa mas los hechos. Nunca recites la nota "
    "entera: ya esta en su pantalla.\n"
    "- Una linea genuinamente ingeniosa vale mas que tres sosas. Si no hay nada "
    "gracioso a mano, se breve en lugar de forzarlo.\n"
    "- La charla trivial (saludos, bromas, 'buenos dias') se atiende con gracia "
    "pero SIN usar ninguna nota: en esos casos devuelve la lista nodes vacia.\n"
    "- Cuando las notas de verdad no cubran algo, dilo con claridad y con algo de "
    "dignidad. Nunca inventes una fuente, nunca rellenes, nunca hagas pasar una "
    "nota relacionada por la respuesta."
)
# ===========================================================================

FORMAT_RULES = (
    "\n\nResponde SIEMPRE con un unico objeto JSON valido y nada mas, con esta "
    'forma exacta: {"answer": "<tu respuesta breve y con caracter>", '
    '"nodes": [<indices numericos de las notas de la lista NOTAS que realmente '
    "usaste; lista vacia si fue charla trivial o si ninguna nota aplica>]}."
)
SYSTEM_PROMPT = PERSONA + FORMAT_RULES

SEE_SYSTEM = (
    PERSONA +
    "\n\nEl senor esta compartiendo su pantalla. Responde ESPECIFICAMENTE sobre "
    "lo que se ve en la imagen, en 1 o 2 frases con tu tono habitual. Si la "
    "captura es demasiado pequena o borrosa para juzgar, dilo con claridad en "
    "lugar de adivinar."
)

# Modelos (slugs de OpenRouter) que SABEMOS que existen. La regla de oro
# (prompt 08): si el senor nombra un modelo que no esta aqui, NOS NEGAMOS;
# jamas caemos al mas parecido.
KNOWN_MODELS = {
    "anthropic/claude-fable-5.1", "anthropic/claude-fable-5",
    "anthropic/claude-opus-5", "anthropic/claude-opus-4.8",
    "anthropic/claude-opus-4.7", "anthropic/claude-opus-4.6",
    "anthropic/claude-opus-4.5", "anthropic/claude-sonnet-5",
    "anthropic/claude-sonnet-4.6", "anthropic/claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5",
}
MODEL_LABELS = {
    "anthropic/claude-fable-5.1": "Fable 5.1", "anthropic/claude-fable-5": "Fable 5",
    "anthropic/claude-opus-5": "Opus 5", "anthropic/claude-opus-4.8": "Opus 4.8",
    "anthropic/claude-opus-4.7": "Opus 4.7", "anthropic/claude-opus-4.6": "Opus 4.6",
    "anthropic/claude-opus-4.5": "Opus 4.5", "anthropic/claude-sonnet-5": "Sonnet 5",
    "anthropic/claude-sonnet-4.6": "Sonnet 4.6", "anthropic/claude-sonnet-4.5": "Sonnet 4.5",
    "anthropic/claude-haiku-4.5": "Haiku 4.5",
}
# Familia hablada -> el id mas nuevo que conocemos de esa familia (UN solo
# diccionario), usado SOLO cuando el senor nombra la familia SIN version.
DEFAULT_BY_FAMILY = {
    "fable": "anthropic/claude-fable-5.1", "opus": "anthropic/claude-opus-5",
    "sonnet": "anthropic/claude-sonnet-5", "haiku": "anthropic/claude-haiku-4.5",
}
FAMILIES = ["fable", "opus", "sonnet", "haiku"]

STOPWORDS = set("""
el la los las un una unos unas de del a al y o u que qué en con por para su sus mi
mis me te se lo le les es son era como cual cuales donde cuando quien quienes hay
this that the a an of to and or is are was for with what which where when who how
""".split())

# --- estado en memoria ---
NOTES = []            # lista de {id,label,group,path,text}
CONVERSATION = []     # historial corto de mensajes {role, content} (limpios)
CURRENT_MODEL = None  # override en caliente; None => usar el de config.json
REMINDERS_PATH = os.path.join(HERE, "reminders.json")
ATTACHMENT = {"name": None, "text": ""}  # archivo adjunto para preguntar sobre él


ENV_MAP = {
    "api_key": "OPENROUTER_API_KEY", "model": "JARVIS_MODEL",
    "elevenlabs_api_key": "ELEVENLABS_API_KEY", "voice_id": "VOICE_ID",
    "tts_model": "TTS_MODEL", "business_name": "BUSINESS_NAME",
    "currency": "CURRENCY", "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID", "chrome_path": "CHROME_PATH",
}


def load_config():
    """Lee config.json (si existe) y superpone variables de entorno (para la nube,
    donde las claves viven en el panel y NO en el repo)."""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    for key, env in ENV_MAP.items():
        val = os.environ.get(env)
        if val:
            cfg[key] = val
    return cfg


def effective_model(config):
    return CURRENT_MODEL or config.get("model", "anthropic/claude-fable-5.1")


def load_notes():
    global NOTES
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            NOTES = json.load(f).get("notes", [])
    else:
        NOTES = []
    return NOTES


def set_notes_from_build(nodes):
    global NOTES
    NOTES = [{"id": n["id"], "label": n["label"], "group": n["group"],
              "path": n["path"], "text": n["text"]} for n in nodes]


# ------------------------- recuperacion -------------------------
def score_notes(question, top_k=6):
    """Puntua por coincidencia de palabras; el titulo pesa el triple."""
    tokens = [t for t in re.findall(r"\w+", question.lower())
              if len(t) > 2 and t not in STOPWORDS]
    scored = []
    for note in NOTES:
        title = note["label"].lower()
        body = note["text"].lower()
        score = 0
        for tok in tokens:
            if tok in title:
                score += 3
            score += min(body.count(tok), 3)
        if score > 0:
            scored.append((score, note))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [note for _score, note in scored[:top_k]]


# ------------------------- OpenRouter (formato compatible con OpenAI) -------------------------
def _api_call(config, payload):
    payload = dict(payload)
    payload["model"] = effective_model(config)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, method="POST", headers={
        "content-type": "application/json",
        "authorization": "Bearer " + config["api_key"],
        "http-referer": "http://127.0.0.1:4700",   # opcional (rankings de OpenRouter)
        "x-title": "Mi Jarvis",
    })
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    msg = (body.get("choices") or [{}])[0].get("message", {}) or {}
    content = msg.get("content", "")
    if isinstance(content, list):  # algunos proveedores devuelven bloques
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return (content or "").strip()


def call_chat(config, question, candidates):
    if candidates:
        notes_block = "\n\n".join(
            "[%d] %s\n%s" % (n["id"], n["label"], n["text"].strip())
            for n in candidates)
    else:
        notes_block = "(no hay notas relevantes para esta pregunta)"
    attach = ""
    if ATTACHMENT.get("text"):
        attach = "ARCHIVO ADJUNTO («%s»):\n%s\n\n" % (ATTACHMENT["name"], ATTACHMENT["text"])
    user_turn = "%sPREGUNTA: %s\n\nNOTAS:\n%s" % (attach, question, notes_block)
    messages = ([{"role": "system", "content": SYSTEM_PROMPT}]
                + list(CONVERSATION)
                + [{"role": "user", "content": user_turn}])
    return _api_call(config, {"max_tokens": 1024, "messages": messages})


def call_see(config, question, image_b64):
    content = [
        {"type": "text", "text": question or "¿Que estoy mirando?"},
        {"type": "image_url",
         "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
    ]
    messages = [{"role": "system", "content": SEE_SYSTEM},
                {"role": "user", "content": content}]
    return _api_call(config, {"max_tokens": 1024, "messages": messages})


def parse_answer(text, candidates):
    """Extrae {answer, nodes}. Respeta una lista nodes vacia explicita del
    modelo (charla trivial); solo recurre a los candidatos si NO hubo JSON."""
    fallback_ids = [n["id"] for n in candidates]
    obj = None
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None
    if not isinstance(obj, dict) or "answer" not in obj:
        return {"answer": text or "(sin respuesta)", "nodes": fallback_ids}

    answer = str(obj.get("answer", "")).strip()
    valid = {n["id"] for n in NOTES}
    if isinstance(obj.get("nodes"), list):
        nodes = [int(x) for x in obj["nodes"]
                 if str(x).lstrip("-").isdigit() and int(x) in valid]
    else:
        nodes = fallback_ids  # el modelo omitio 'nodes' -> usamos candidatos
    return {"answer": answer or "(sin respuesta)", "nodes": nodes}


def _placeholder_or(config):
    """Devuelve un mensaje si la clave sigue siendo el placeholder, o None."""
    key = str(config.get("api_key", "")).strip()
    if key in PLACEHOLDER_KEYS:
        return ("Todavia no puedo pensar, senor: la clave de OpenRouter en config.json "
                "sigue siendo el marcador de posicion. Ponga su clave real de "
                "OpenRouter ahi y volvere a la vida.")
    return None


def _api_error_message(e):
    if isinstance(e, urllib.error.HTTPError):
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            pass
        return "La API respondio con un error %s. %s" % (e.code, detail)
    if isinstance(e, urllib.error.URLError):
        return "No pude conectar con la API: %s" % e.reason
    return "Ocurrio un error inesperado: %s" % e


def answer_question(question):
    """Nunca lanza: devuelve siempre un dict bien formado {answer, nodes}."""
    config = load_config()
    msg = _placeholder_or(config)
    if msg:
        return {"answer": msg, "nodes": []}
    candidates = score_notes(question)
    try:
        text = call_chat(config, question, candidates)
    except Exception as e:
        return {"answer": _api_error_message(e), "nodes": []}
    result = parse_answer(text, candidates)
    CONVERSATION.append({"role": "user", "content": question})
    CONVERSATION.append({"role": "assistant", "content": result["answer"]})
    del CONVERSATION[:-8]
    return result


def see_question(question, image_b64):
    config = load_config()
    msg = _placeholder_or(config)
    if msg:
        return {"answer": msg}
    if not image_b64:
        return {"answer": "No recibi ninguna imagen de la pantalla, senor."}
    try:
        text = call_see(config, question, image_b64)
    except Exception as e:
        return {"answer": _api_error_message(e)}
    return {"answer": text or "(sin respuesta)"}


# ------------------------- recuerda que (prompt 05) -------------------------
def _slugify(text):
    words = re.findall(r"\w+", text.lower())[:6]
    slug = "-".join(words) or "captura"
    return slug[:60]


def remember(text):
    """Guarda una nota nueva, reindexa y devuelve el nodo para agregarlo vivo.
    Nunca falla en silencio: en error devuelve ok=False con un mensaje."""
    try:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "message": "No entendi que debia recordar, senor."}
        os.makedirs(CAPTURES_DIR, exist_ok=True)
        today = datetime.date.today().isoformat()
        title = text.split("\n", 1)[0].strip()
        if len(title) > 60:
            title = title[:60].rsplit(" ", 1)[0] + "..."
        stem = _slugify(text)
        path = os.path.join(CAPTURES_DIR, stem + ".md")
        n = 2
        while os.path.exists(path):
            path = os.path.join(CAPTURES_DIR, "%s-%d.md" % (stem, n))
            n += 1
        content = "# %s\n\n_Capturado el %s_\n\n%s\n" % (title, today, text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        # reindexa en caliente (sin que el senor toque build.py)
        nodes, links = build.build_all()
        set_notes_from_build(nodes)

        target = os.path.normpath(path)
        new = next((x for x in nodes if os.path.normpath(x["path"]) == target), None)
        if new is None:
            return {"ok": False,
                    "message": "Guarde el archivo pero no logre reindexarlo, senor."}
        new_id = new["id"]
        new_links = [l for l in links if l["source"] == new_id or l["target"] == new_id]
        # nota mas relacionada (para nacer a su lado si no hubo enlaces)
        anchors = score_notes(text, top_k=1)
        anchor = anchors[0]["id"] if anchors else None
        return {
            "ok": True,
            "node": {"id": new_id, "label": new["label"], "group": new["group"],
                     "excerpt": new["excerpt"]},
            "links": new_links,
            "anchor": anchor,
            "file": os.path.basename(path),
            "message": "Anotado, senor. Lo guarde como «%s»." % title,
        }
    except Exception as e:
        return {"ok": False, "message": "No pude guardar la captura, senor: %s" % e}


# ------------------------- cambio de cerebro (prompt 08) -------------------------
def resolve_model(spoken):
    """Nombre hablado -> id real, o None si no existe (para NEGARSE).

    La regla que hace esto seguro (prompt 08): si el senor nombra familia +
    version, construimos el id y lo comprobamos contra KNOWN_MODELS; si no esta,
    devolvemos None y nos negamos. JAMAS caemos al mas parecido. Solo cuando NO
    se nombra version usamos el modelo mas nuevo conocido de esa familia.
    """
    s = build.normalize(spoken)
    if any(w in s for w in ("normal", "config", "de siempre", "por defecto")):
        return "__config__"
    fam = next((f for f in FAMILIES if f in s), None)
    if not fam:
        return None
    m = re.search(r"(\d+)(?:[.,](\d+))?", s)
    if m:
        ver = m.group(1) + ("." + m.group(2) if m.group(2) else "")
        candidate = "anthropic/claude-%s-%s" % (fam, ver)
        return candidate if candidate in KNOWN_MODELS else None
    return DEFAULT_BY_FAMILY.get(fam)


def set_model(spoken):
    global CURRENT_MODEL
    config = load_config()
    resolved = resolve_model(spoken)
    if resolved == "__config__":
        CURRENT_MODEL = None
        model = config.get("model", "claude-fable-5-1")
        label = MODEL_LABELS.get(model, model)
        return {"ok": True, "model": model, "label": label,
                "line": "De vuelta a mi cerebro de siempre, %s, señor." % label}
    if resolved is None or resolved not in KNOWN_MODELS:
        available = ", ".join(sorted(MODEL_LABELS.values()))
        return {"ok": False,
                "message": ("No conozco ese modelo, senor, y prefiero un error "
                            "honesto a una suposicion. Puedo ofrecerle: %s." % available)}
    CURRENT_MODEL = resolved
    label = MODEL_LABELS.get(resolved, resolved)
    return {"ok": True, "model": resolved, "label": label,
            "line": "A partir de ahora pienso con %s, señor." % label}


def current_model_state():
    config = load_config()
    model = effective_model(config)
    return {"model": model, "label": MODEL_LABELS.get(model, model)}


# ------------------------- voz con ElevenLabs (opcional) -------------------------
def _el_key(config):
    k = str(config.get("elevenlabs_api_key", "")).strip()
    return None if k in EL_PLACEHOLDERS else k


def el_voices(config):
    key = _el_key(config)
    if not key:
        return []
    req = urllib.request.Request(EL_VOICES_URL, headers={"xi-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [{"voice_id": v.get("voice_id"), "name": v.get("name", ""),
             "labels": v.get("labels", {})} for v in data.get("voices", [])]


def _resolve_voice_id(config):
    vid = str(config.get("voice_id", "")).strip()
    if vid:
        return vid
    if _EL_VOICE_CACHE["id"]:
        return _EL_VOICE_CACHE["id"]
    voices = el_voices(config)
    _EL_VOICE_CACHE["id"] = voices[0]["voice_id"] if voices else None
    return _EL_VOICE_CACHE["id"]


def synthesize(config, text):
    """(audio_bytes, None) si sono; (None, error) si fallo; (None, None) si EL
    no esta configurado (el cliente usara la voz del navegador)."""
    key = _el_key(config)
    if not key:
        return None, None
    vid = _resolve_voice_id(config)
    if not vid:
        return None, "no hay voces en la cuenta de ElevenLabs"
    payload = {"text": text,
               "model_id": config.get("tts_model", "eleven_multilingual_v2"),
               "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    req = urllib.request.Request(
        EL_TTS_URL % urllib.parse.quote(vid),
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"xi-api-key": key, "content-type": "application/json",
                 "accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read(), None


def list_voices_state():
    try:
        return {"voices": el_voices(load_config())}
    except Exception as e:
        return {"voices": [], "error": str(e)}


# ------------------------- recordatorios (poder local) -------------------------
def _load_reminders():
    if os.path.exists(REMINDERS_PATH):
        try:
            with open(REMINDERS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_reminders(rows):
    with open(REMINDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def parse_due(text):
    """Extrae una fecha/hora en español del texto. Devuelve datetime o None."""
    now = datetime.datetime.now()
    t = text.lower()
    m = re.search(r"en\s+(\d+)\s*(minutos?|mins?|horas?|h)\b", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = datetime.timedelta(minutes=n) if unit.startswith("min") or unit == "m" \
            else datetime.timedelta(hours=n)
        return now + delta
    day = now
    if "pasado mañana" in t:
        day = now + datetime.timedelta(days=2)
    elif "mañana" in t:
        day = now + datetime.timedelta(days=1)
    hm = re.search(r"a\s+la[s]?\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|de la tarde|de la noche|de la mañana)?", t)
    if hm:
        h = int(hm.group(1))
        mnt = int(hm.group(2) or 0)
        ap = hm.group(3) or ""
        if ("p" in ap or "tarde" in ap or "noche" in ap) and h < 12:
            h += 12
        if ("a.m" in ap or "de la mañana" in ap) and h == 12:
            h = 0
        due = day.replace(hour=h % 24, minute=mnt, second=0, microsecond=0)
        if "mañana" not in t and "pasado" not in t and due < now:
            due += datetime.timedelta(days=1)
        return due
    if "mañana" in t or "pasado mañana" in t:
        return day.replace(hour=9, minute=0, second=0, microsecond=0)
    return None


def _human_when(due):
    if not due:
        return "Lo guardé en tu lista, señor."
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    hoy = datetime.datetime.now().date()
    if due.date() == hoy:
        cuando = "hoy"
    elif due.date() == hoy + datetime.timedelta(days=1):
        cuando = "mañana"
    else:
        cuando = "el %s %d" % (dias[due.weekday()], due.day)
    return "Te lo recordaré %s a las %s, señor." % (cuando, due.strftime("%H:%M"))


def reminders_action(body):
    action = body.get("action")
    rows = _load_reminders()
    if action == "add":
        text = str(body.get("text", "")).strip()
        if not text:
            return {"ok": False, "message": "¿Qué le recuerdo, señor?"}
        due = parse_due(text)
        item = {
            "id": int(datetime.datetime.now().timestamp() * 1000) % 100000000,
            "text": text, "due": due.isoformat() if due else None,
            "done": False, "created": datetime.datetime.now().isoformat(),
        }
        rows.append(item)
        _save_reminders(rows)
        return {"ok": True, "reminder": item, "message": _human_when(due)}
    if action == "list":
        return {"ok": True, "reminders": [r for r in rows if not r.get("done")]}
    if action == "done":
        rid = body.get("id")
        for r in rows:
            if r.get("id") == rid:
                r["done"] = True
        _save_reminders(rows)
        return {"ok": True}
    return {"ok": False, "message": "acción desconocida"}


# ------------------------- resumen de notas (poder local) -------------------------
SUMMARY_SYSTEM = (
    "Eres el asistente del usuario y resumes/analizas SUS notas con criterio. "
    "Responde en español, claro y conciso (4 a 6 frases o viñetas cortas), sobre "
    "los hechos de las notas. Usa SOLO las notas entregadas; si no alcanzan para el "
    "tema, dilo con honestidad en vez de inventar."
)


def call_summary(config, topic, notes):
    block = "\n\n".join("[%d] %s\n%s" % (n["id"], n["label"], n["text"].strip())
                        for n in notes) or "(sin notas relevantes)"
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": "Tema: %s\n\nNOTAS:\n%s\n\nResume y destaca lo importante." % (topic, block)},
    ]
    return _api_call(config, {"max_tokens": 900, "messages": messages})


def summarize(topic):
    config = load_config()
    msg = _placeholder_or(config)
    if msg:
        return {"answer": msg, "nodes": []}
    notes = score_notes(topic, top_k=8)
    try:
        text = call_summary(config, topic, notes)
    except Exception as e:
        return {"answer": _api_error_message(e), "nodes": []}
    return {"answer": text or "(sin respuesta)", "nodes": [n["id"] for n in notes]}


# ------------------------- redactar / traducir (poderes locales) -------------------------
COMPOSE_SYSTEM = (
    "Eres el asistente del usuario. Redacta lo que te pida (correo, mensaje, "
    "publicación, carta, anuncio...) en español claro, listo para enviar, con un "
    "tono profesional y cálido. Devuelve SOLO el texto redactado: sin comentarios, "
    "sin comillas, sin explicar lo que hiciste."
)
TRANSLATE_SYSTEM = (
    "Eres un traductor. Traduce lo que el usuario pida. Si indica un idioma destino "
    "(por ejemplo 'al inglés'), úsalo; si no indica ninguno, traduce al inglés. "
    "Devuelve SOLO la traducción, sin comentarios ni comillas."
)


def _one_shot(config, system, request, max_tokens=900):
    return _api_call(config, {"max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": request}]})


def compose(request):
    config = load_config()
    msg = _placeholder_or(config)
    if msg:
        return {"ok": False, "text": msg}
    if not request:
        return {"ok": False, "text": "¿Qué le redacto, señor?"}
    try:
        return {"ok": True, "text": _one_shot(config, COMPOSE_SYSTEM, request) or "(sin respuesta)"}
    except Exception as e:
        return {"ok": False, "text": _api_error_message(e)}


def translate(request):
    config = load_config()
    msg = _placeholder_or(config)
    if msg:
        return {"ok": False, "text": msg}
    if not request:
        return {"ok": False, "text": "¿Qué le traduzco, señor?"}
    try:
        return {"ok": True, "text": _one_shot(config, TRANSLATE_SYSTEM, request, 700) or "(sin respuesta)"}
    except Exception as e:
        return {"ok": False, "text": _api_error_message(e)}


# ------------------------- leer un archivo (poder local) -------------------------
def read_file(name, b64):
    global ATTACHMENT
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return {"ok": False, "message": "No pude decodificar el archivo, señor."}
    ext = os.path.splitext(name)[1].lower()
    text = ""
    if ext in (".txt", ".md", ".csv", ".log", ".json"):
        text = raw.decode("utf-8", "replace")
    elif ext == ".pdf":
        try:
            import io
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(raw))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except ImportError:
            return {"ok": False, "message": "Para leer PDF necesito el paquete pypdf. Usa un .txt/.md/.csv por ahora."}
        except Exception as e:
            return {"ok": False, "message": "No pude leer el PDF: %s" % e}
    else:
        return {"ok": False, "message": "Formato no soportado (%s). Usa txt, md, csv o pdf." % ext}
    text = text.strip()
    if not text:
        return {"ok": False, "message": "El archivo no tenía texto legible, señor."}
    ATTACHMENT = {"name": name, "text": text[:20000]}
    return {"ok": True, "name": name, "chars": len(ATTACHMENT["text"]),
            "message": "Archivo «%s» cargado, señor. Pregúnteme lo que quiera sobre él." % name}


def forget_file():
    global ATTACHMENT
    ATTACHMENT = {"name": None, "text": ""}
    return {"ok": True}


# ------------------------- control por texto (compartido con Telegram) -------------------------
REMINDER_TRIGGERS_PY = ["recuérdame", "recuerdame", "avísame", "avisame",
                        "recordatorio", "ponme un recordatorio"]
REMEMBER_TRIGGERS_PY = ["recuerda que", "anota que", "anota", "apunta que", "apunta",
                        "nota para mí", "nota para mi", "no me dejes olvidar"]


def route_text(text):
    """Enruta una orden de texto al poder correcto y devuelve la respuesta.
    Es el mismo cerebro que usa la interfaz, pero para canales de solo-texto."""
    text = (text or "").strip()
    if not text:
        return "¿Sí, señor?"
    low = text.lower()
    for t in REMINDER_TRIGGERS_PY:
        if low.startswith(t):
            return reminders_action({"action": "add", "text": text[len(t):].strip(" ,:.") or text}).get("message", "")
    for t in REMEMBER_TRIGGERS_PY:
        if low.startswith(t):
            return remember(text[len(t):].strip(" ,:.")).get("message", "")
    if re.search(r"\b(cambia|cambiar|usa|ponte|switch|prueba|vuelve|regresa)\b", low) and \
       re.search(r"\b(fable|opus|sonnet|haiku|cerebro|normal)\b", low):
        r = set_model(text)
        return r.get("line") or r.get("message", "")
    if re.search(r"(factura|factúr|recibo|cotiz|propuesta)", low) and re.search(r"\d", low):
        return deals.create(deals.detect_kind(text), text, open_pdf=False).get("message", "")
    if re.search(r"\b(redacta|redáctame|redactame|escríbeme|escribeme|redactar)\b", low):
        return compose(text).get("text", "")
    if re.search(r"\b(traduce|tradúceme|traduceme|traducir)\b", low):
        return translate(text).get("text", "")
    if re.search(r"\b(resume|resúmeme|resumeme|resumen|analiza)\b", low):
        return summarize(text).get("answer", "")
    return answer_question(text).get("answer", "")


# ------------------------- Telegram (control remoto, opcional) -------------------------
def _tg(token, method, payload):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))


def _mp3_to_ogg(mp3_bytes):
    """Convierte el MP3 de ElevenLabs a OGG/OPUS (lo que Telegram quiere para una
    NOTA de voz). Necesita ffmpeg. Devuelve bytes o None si falla/no está."""
    fd, mp3p = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    oggp = mp3p[:-4] + ".ogg"
    try:
        with open(mp3p, "wb") as f:
            f.write(mp3_bytes)
        subprocess.run(["ffmpeg", "-y", "-i", mp3p, "-c:a", "libopus", "-b:a", "48k", oggp],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if os.path.exists(oggp) and os.path.getsize(oggp) > 0:
            with open(oggp, "rb") as f:
                return f.read()
    except Exception:
        pass
    finally:
        for p in (mp3p, oggp):
            try:
                os.remove(p)
            except Exception:
                pass
    return None


def _tg_voice(token, chat_id, ogg_bytes):
    """Envía una nota de voz (sendVoice, multipart)."""
    boundary = "----jarvisvoice%d" % datetime.datetime.now().microsecond
    parts = [
        ("--%s\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n%s\r\n"
         % (boundary, chat_id)).encode("utf-8"),
        ("--%s\r\nContent-Disposition: form-data; name=\"voice\"; filename=\"jarvis.ogg\"\r\n"
         "Content-Type: audio/ogg\r\n\r\n" % boundary).encode("utf-8"),
        ogg_bytes,
        ("\r\n--%s--\r\n" % boundary).encode("utf-8"),
    ]
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendVoice" % token,
        data=b"".join(parts), method="POST",
        headers={"content-type": "multipart/form-data; boundary=%s" % boundary})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8")).get("ok", False)
    except Exception:
        return False


def _tg_maybe_voice(token, chat_id, reply):
    """Si ElevenLabs está configurado y la respuesta no es muy larga, manda
    también una nota de voz con la misma voz del mayordomo."""
    try:
        cfg = load_config()
        if not _el_key(cfg) or not (0 < len(reply) <= 700):
            return
        mp3, _err = synthesize(cfg, reply)
        if not mp3:
            return
        ogg = _mp3_to_ogg(mp3)
        if ogg:
            _tg_voice(token, chat_id, ogg)
    except Exception:
        pass


def _telegram_loop():
    token = str(load_config().get("telegram_bot_token", "")).strip()
    if not token:
        return
    offset = 0
    try:  # drena mensajes viejos para no responder al historial al arrancar
        ups = _tg(token, "getUpdates", {"timeout": 0}).get("result", [])
        if ups:
            offset = ups[-1]["update_id"] + 1
    except Exception:
        pass
    print("Telegram: bot en línea.")
    while True:
        try:
            res = _tg(token, "getUpdates", {"timeout": 30, "offset": offset}).get("result", [])
            for up in res:
                offset = up["update_id"] + 1
                msg = up.get("message") or up.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = msg["text"]
                allowed = str(load_config().get("telegram_chat_id", "")).strip()
                if not allowed:  # aún no configurado: dile su chat_id y no ejecutes nada
                    _tg(token, "sendMessage", {"chat_id": chat_id,
                        "text": "Hola. Su chat_id es %s. Póngalo en config.json "
                                "(telegram_chat_id) y reiníciame para activarme, señor." % chat_id})
                    continue
                if str(chat_id) != allowed:   # seguridad: solo el dueño
                    continue
                if text.strip() in ("/start", "/help"):
                    _tg(token, "sendMessage", {"chat_id": chat_id,
                        "text": "A sus órdenes, señor. Pregúnteme por sus notas, dicte "
                                "«recuerda que…», pida una factura, un resumen, una "
                                "traducción, o cambie mi cerebro."})
                    continue
                try:
                    reply = route_text(text) or "(sin respuesta)"
                except Exception as e:
                    reply = "Ocurrió un error, señor: %s" % e
                _tg(token, "sendMessage", {"chat_id": chat_id, "text": reply[:4000]})
                _tg_maybe_voice(token, chat_id, reply)
        except Exception:
            time.sleep(3)


# ------------------------- servidor HTTP -------------------------
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _handle_speak(self, text):
        text = (text or "").strip()
        if not text:
            self.send_response(204); self.end_headers(); return
        try:
            audio, err = synthesize(load_config(), text)
        except Exception as e:
            audio, err = None, str(e)
        if audio:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
        elif err:
            self._send_json({"error": err}, status=502)
        else:
            self.send_response(204); self.end_headers()  # EL no configurado

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/model":
            self._send_json(current_model_state())
            return
        if path == "/voices":
            self._send_json(list_voices_state())
            return
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(VIEWER_DIR, rel))
        if os.path.commonpath([target, VIEWER_DIR]) != VIEWER_DIR or not os.path.isfile(target):
            self.send_error(404, "No encontrado")
            return
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as f:
            payload = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self._read_json_body()
        except Exception:
            self._send_json({"error": "peticion invalida"}, status=400)
            return

        if path == "/chat":
            q = str(body.get("question", "")).strip()
            self._send_json(answer_question(q) if q
                            else {"answer": "No entendi la pregunta, senor.", "nodes": []})
        elif path == "/search":
            q = str(body.get("question", "")).strip()
            self._send_json({"nodes": [n["id"] for n in score_notes(q)] if q else []})
        elif path == "/remember":
            self._send_json(remember(str(body.get("text", ""))))
        elif path == "/see":
            self._send_json(see_question(str(body.get("question", "")),
                                         str(body.get("image", ""))))
        elif path == "/model":
            self._send_json(set_model(str(body.get("spoken", ""))))
        elif path == "/reindex":
            nodes, _links = build.build_all()
            set_notes_from_build(nodes)
            self._send_json({"ok": True, "count": len(nodes)})
        elif path == "/speak":
            self._handle_speak(str(body.get("text", "")))
        elif path == "/invoice":
            brief = str(body.get("brief", "")).strip()
            kind = str(body.get("kind", "")).strip() or deals.detect_kind(brief)
            self._send_json(deals.create(kind, brief, open_pdf=True) if brief
                            else {"ok": False, "message": "No entendí qué documento crear, señor."})
        elif path == "/reminders":
            self._send_json(reminders_action(body))
        elif path == "/summary":
            topic = str(body.get("topic", "")).strip()
            self._send_json(summarize(topic) if topic
                            else {"answer": "¿Sobre qué tema, señor?", "nodes": []})
        elif path == "/readfile":
            self._send_json(read_file(str(body.get("name", "archivo")), str(body.get("b64", ""))))
        elif path == "/forget_file":
            self._send_json(forget_file())
        elif path == "/compose":
            self._send_json(compose(str(body.get("request", "")).strip()))
        elif path == "/translate":
            self._send_json(translate(str(body.get("request", "")).strip()))
        else:
            self.send_error(404, "No encontrado")


def main():
    load_notes()
    print("Notas cargadas: %d" % len(NOTES))
    if str(load_config().get("telegram_bot_token", "")).strip():
        threading.Thread(target=_telegram_loop, daemon=True).start()
    host = os.environ.get("JARVIS_HOST", "127.0.0.1")   # 0.0.0.0 en la nube (Docker)
    server = ThreadingHTTPServer((host, PORT), Handler)
    print("Sirviendo la galaxia en http://127.0.0.1:%d" % PORT)
    print("(Ctrl+C para detener)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")


if __name__ == "__main__":
    main()
