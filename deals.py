# -*- coding: utf-8 -*-
"""deals.py -- El motor de documentos (factura / cotización / propuesta).

Una frase -> un documento comercial listo para el cliente, en PDF.
Python 3, solo librería estándar. Usa OpenRouter (misma clave que el cerebro)
para redactar, pero la ARITMÉTICA es nuestra: el total se recalcula en Python
y sobrescribe lo que diga el modelo.

Uso desde terminal:
    python deals.py "factura a Mike por 1500 pesos por la página web"
    python deals.py "cotización para Ana, 3 posts a 800 c/u"

O desde el servidor: deals.create(kind, brief).
"""
import json
import os
import re
import subprocess
import datetime
import html as htmllib
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DEALS_DIR = os.path.join(HERE, "deals")
LEDGER_PATH = os.path.join(DEALS_DIR, "ledger.jsonl")
API_URL = "https://openrouter.ai/api/v1/chat/completions"
PLACEHOLDER_KEYS = {"", "PUT-YOUR-KEY-HERE"}

KINDS = {
    "invoice":  {"prefix": "INV", "title": "Factura"},
    "quote":    {"prefix": "QUO", "title": "Cotización"},
    "proposal": {"prefix": "PRO", "title": "Propuesta"},
    "receipt":  {"prefix": "RCB", "title": "Recibo"},
}
# Palabras del brief -> tipo de documento (se evalúa en orden)
KIND_WORDS = [
    ("receipt",  ("recibo", "receipt")),
    ("quote",    ("cotiza", "cotización", "cotizacion", "quote")),
    ("proposal", ("propuesta", "proposal")),
    ("invoice",  ("factura", "invoice", "cobro")),
]


_ENV_MAP = {
    "api_key": "OPENROUTER_API_KEY", "model": "JARVIS_MODEL",
    "invoice_model": "INVOICE_MODEL", "business_name": "BUSINESS_NAME",
    "business_context": "BUSINESS_CONTEXT", "currency": "CURRENCY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN", "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "chrome_path": "CHROME_PATH",
}


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    for key, env in _ENV_MAP.items():
        val = os.environ.get(env)
        if val:
            cfg[key] = val
    return cfg


def detect_kind(brief):
    low = brief.lower()
    for kind, words in KIND_WORDS:
        if any(w in low for w in words):
            return kind
    return "invoice"


def coerce_price(v):
    """'$1,500' / '1.500' / 1500 -> 1500.0 (robusto para briefs simples)."""
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.,]", "", str(v))
    if "," in s and "." in s:
        s = s.replace(",", "")           # coma = miles, punto = decimal
    elif "," in s:
        s = s.replace(",", "")           # coma = miles (RD: "1,500" = 1500)
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


# ------------------------- redacción con OpenRouter -------------------------
def _draft(config, kind, brief):
    currency = config.get("currency", "RD$")
    business = config.get("business_name", "Mi negocio")
    context = config.get("business_context", "")
    system = (
        "Eres un generador de documentos comerciales para el negocio «%s». "
        "A partir de una sola frase, produces un %s. Responde SOLO con un objeto "
        "JSON válido y nada más, con esta forma EXACTA:\n"
        '{"title": "...", "client": "...", "intro": "una frase de contexto", '
        '"items": [{"desc": "...", "qty": <número>, "unit_price": <número>}], '
        '"total": <número>, "terms": "condiciones de pago o validez", '
        '"validity": "vigencia"}\n'
        "Reglas: escribe en español. La moneda es %s. unit_price y qty son NÚMEROS "
        "(sin símbolos). Si la frase da un monto sin desglose, crea un solo ítem con "
        "ese monto y qty 1. No inventes datos que no puedas inferir; deja el cliente "
        'como "Cliente" si no se menciona.%s'
    ) % (business, KINDS[kind]["title"], currency,
         ("\nCONTEXTO DEL NEGOCIO (úsalo para precios y servicios):\n" + context) if context else "")

    payload = {
        "model": config.get("invoice_model", config.get("model", "anthropic/claude-fable-5.1")),
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": brief},
        ],
    }
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={
            "content-type": "application/json",
            "authorization": "Bearer " + config["api_key"],
            "http-referer": "http://127.0.0.1:4700", "x-title": "Mi Jarvis",
        })
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    # extraer el objeto JSON
    try:
        return json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise ValueError("El modelo no devolvió JSON: " + content[:200])
        return json.loads(m.group(0))


# ------------------------- numeración (ledger) -------------------------
def _next_number(kind):
    prefix = KINDS[kind]["prefix"]
    year = datetime.date.today().year
    count = 0
    if os.path.exists(LEDGER_PATH):
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("kind") == kind and str(row.get("number", "")).startswith("%s-%d-" % (prefix, year)):
                    count += 1
    return "%s-%d-%03d" % (prefix, year, count + 1)


def _append_ledger(row):
    os.makedirs(DEALS_DIR, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ------------------------- render HTML + PDF -------------------------
def _money(n, currency):
    return "%s%s" % (currency + " ", "{:,.2f}".format(n))


def _render_html(config, kind, number, doc, total):
    e = htmllib.escape
    currency = config.get("currency", "RD$")
    business = config.get("business_name", "Mi negocio")
    today = datetime.date.today().strftime("%d/%m/%Y")
    rows = ""
    for it in doc.get("items", []):
        qty = it.get("qty", 1)
        price = coerce_price(it.get("unit_price", 0))
        sub = qty * price
        rows += (
            "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td></tr>"
        ) % (e(str(it.get("desc", ""))), e(str(qty)),
             e(_money(price, currency)), e(_money(sub, currency)))

    return """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<style>
  @page {{ margin: 0; }}
  body {{ margin: 0; background: #F7F3EA; color: #16130D;
    font-family: Georgia, 'Times New Roman', serif; }}
  .page {{ padding: 64px 60px; }}
  .eyebrow {{ font-size: 11px; letter-spacing: 3px; text-transform: uppercase;
    color: #0E6B4F; font-family: Arial, sans-serif; }}
  h1 {{ font-style: italic; font-weight: normal; font-size: 40px; margin: 6px 0 2px; }}
  .meta {{ color: #6b6455; font-size: 13px; font-family: Arial, sans-serif; }}
  .row {{ display: flex; justify-content: space-between; align-items: flex-start; margin-top: 34px; }}
  .to .eyebrow {{ margin-bottom: 4px; }}
  .to .name {{ font-size: 18px; }}
  .intro {{ margin: 30px 0 18px; font-size: 15px; line-height: 1.7; color: #2c2820; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-family: Arial, sans-serif; }}
  th {{ text-align: left; font-size: 10.5px; letter-spacing: 2px; text-transform: uppercase;
    color: #0E6B4F; border-bottom: 2px solid #0E6B4F; padding: 8px 6px; }}
  td {{ padding: 12px 6px; border-bottom: 1px solid #e2dccb; font-size: 13.5px; }}
  td.num, th.num {{ text-align: right; }}
  .total {{ display: flex; justify-content: flex-end; margin-top: 18px; }}
  .total .box {{ min-width: 250px; }}
  .total .line {{ display: flex; justify-content: space-between; font-size: 18px;
    font-weight: bold; border-top: 2px solid #16130D; padding-top: 10px; }}
  .terms {{ margin-top: 40px; font-size: 12.5px; color: #4a4438; line-height: 1.7;
    font-family: Arial, sans-serif; }}
  .terms .eyebrow {{ margin-bottom: 6px; }}
  .foot {{ margin-top: 54px; padding-top: 16px; border-top: 1px solid #e2dccb;
    font-family: Arial, sans-serif; font-size: 12px; color: #6b6455; }}
</style></head><body><div class="page">
  <div class="eyebrow">{kindlabel}</div>
  <h1>{title}</h1>
  <div class="meta">{number} · {today}</div>
  <div class="row">
    <div class="to"><div class="eyebrow">Para</div><div class="name">{client}</div></div>
  </div>
  <div class="intro">{intro}</div>
  <table>
    <thead><tr><th>Descripción</th><th class="num">Cant.</th>
      <th class="num">Precio</th><th class="num">Importe</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="total"><div class="box"><div class="line">
    <span>Total</span><span>{total}</span></div></div></div>
  <div class="terms"><div class="eyebrow">Condiciones</div>{terms}<br>{validity}</div>
  <div class="foot">{business}</div>
</div></body></html>""".format(
        kindlabel=e(KINDS[kind]["title"].upper()),
        title=e(str(doc.get("title", KINDS[kind]["title"]))),
        number=e(number), today=e(today),
        client=e(str(doc.get("client", "Cliente"))),
        intro=e(str(doc.get("intro", ""))),
        rows=rows, total=e(_money(total, currency)),
        terms=e(str(doc.get("terms", ""))), validity=e(str(doc.get("validity", ""))),
        business=e(business))


def _find_chrome(config):
    if config.get("chrome_path") and os.path.exists(config["chrome_path"]):
        return config["chrome_path"]
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),  # Edge también imprime PDF
        "/usr/bin/chromium", "/usr/bin/chromium-browser",         # Linux (Docker/VPS)
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _html_to_pdf(config, html_path, pdf_path):
    chrome = _find_chrome(config)
    if not chrome:
        return False, "No encontré Chrome ni Edge para imprimir el PDF (pon 'chrome_path' en config.json)."
    url = "file:///" + html_path.replace("\\", "/")
    cmd = [chrome, "--headless", "--disable-gpu", "--no-sandbox",
           "--disable-dev-shm-usage", "--no-pdf-header-footer",
           "--print-to-pdf=" + pdf_path, url]
    try:
        subprocess.run(cmd, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, "Chrome falló al imprimir: %s" % e
    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
        return True, None
    return False, "Chrome no generó el PDF (¿ruta con permisos?)."


# ------------------------- Telegram (opcional) -------------------------
def _send_telegram(config, pdf_path, caption):
    token = str(config.get("telegram_bot_token", "")).strip()
    chat_id = str(config.get("telegram_chat_id", "")).strip()
    if not token or not chat_id:
        return None  # Telegram no configurado -> se omite
    url = "https://api.telegram.org/bot%s/sendDocument" % token
    boundary = "----jarvis%d" % datetime.datetime.now().microsecond
    with open(pdf_path, "rb") as f:
        filedata = f.read()
    parts = []
    for name, value in (("chat_id", chat_id), ("caption", caption)):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, name, value)).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"document\"; "
                  "filename=\"%s\"\r\nContent-Type: application/pdf\r\n\r\n"
                  % (boundary, os.path.basename(pdf_path))).encode("utf-8"))
    parts.append(filedata)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    data = b"".join(parts)
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"content-type": "multipart/form-data; boundary=%s" % boundary})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            ok = json.loads(resp.read().decode("utf-8")).get("ok", False)
        return bool(ok)
    except Exception:
        return False


# ------------------------- API pública -------------------------
def create(kind, brief, open_pdf=False):
    """Genera el documento. Devuelve siempre un dict; nunca lanza."""
    try:
        config = load_config()
        if str(config.get("api_key", "")).strip() in PLACEHOLDER_KEYS:
            return {"ok": False, "message": "La clave de OpenRouter en config.json es el placeholder."}
        if kind not in KINDS:
            kind = "invoice"

        doc = _draft(config, kind, brief)
        # ARITMÉTICA nuestra: recalculamos el total
        total = 0.0
        for it in doc.get("items", []):
            total += float(it.get("qty", 1)) * coerce_price(it.get("unit_price", 0))

        number = _next_number(kind)
        os.makedirs(DEALS_DIR, exist_ok=True)
        html_path = os.path.join(DEALS_DIR, number + ".html")
        pdf_path = os.path.join(DEALS_DIR, number + ".pdf")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(_render_html(config, kind, number, doc, total))

        pdf_ok, pdf_err = _html_to_pdf(config, html_path, pdf_path)

        client = str(doc.get("client", "Cliente"))
        currency = config.get("currency", "RD$")
        caption = "%s · %s · %s" % (number, client, _money(total, currency))

        tg = None
        if pdf_ok:
            tg = _send_telegram(config, pdf_path, caption)
            if open_pdf and os.name == "nt":
                try: os.startfile(pdf_path)  # abre el PDF en tu visor
                except Exception: pass

        _append_ledger({"number": number, "kind": kind, "client": client,
                        "total": round(total, 2), "date": datetime.date.today().isoformat(),
                        "pdf": pdf_path})

        # mensaje honesto (recibos o no pasó)
        parts = ["%s por %s para %s" % (number, _money(total, currency), client)]
        if not pdf_ok:
            parts.append("pero el PDF falló: " + (pdf_err or "error"))
        elif tg is True:
            parts.append("enviada a Telegram")
        elif tg is False:
            parts.append("(no pude enviarla a Telegram; el PDF está en %s)" % pdf_path)
        message = ". ".join(parts) + "."

        return {"ok": pdf_ok, "number": number, "kind": kind, "client": client,
                "total": round(total, 2), "currency": currency,
                "pdf_path": pdf_path if pdf_ok else None, "html_path": html_path,
                "telegram_sent": tg, "pdf_error": pdf_err, "message": message}
    except urllib.error.HTTPError as e:
        detail = ""
        try: detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception: pass
        return {"ok": False, "message": "OpenRouter respondió error %s. %s" % (e.code, detail)}
    except Exception as e:
        return {"ok": False, "message": "No pude generar el documento: %s" % e}


def main():
    import sys
    brief = " ".join(sys.argv[1:]).strip()
    if not brief:
        print('Uso: python deals.py "factura a Mike por 1500 por la página web"')
        return
    kind = detect_kind(brief)
    print("Generando %s..." % KINDS[kind]["title"])
    res = create(kind, brief, open_pdf=True)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
