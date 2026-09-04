# Mi Jarvis 🎩

Un segundo cerebro personal: tus notas en markdown convertidas en una **galaxia 3D**
que puedes explorar y a la que le puedes **hablar**. Responde solo desde tus notas,
con la personalidad de un mayordomo, en español y con voz real.

Construido siguiendo el *Build Your Own Jarvis — Fable 5.1 Prompt Pack*.

---

## 🚀 Cómo encenderlo (activar)

**La forma fácil:** doble clic en **`iniciar.bat`**.
Eso arranca el servidor y abre el navegador solo.

**La forma manual** (una terminal en esta carpeta):

```bash
python server.py
```

Luego abre 👉 **http://127.0.0.1:4700** en **Google Chrome**
(la voz y el micrófono solo funcionan bien en Chrome).

Para apagarlo: cierra la ventana del servidor, o pulsa `Ctrl+C` en la terminal.

> La voz habla en tu **primer clic** en la página (el navegador bloquea el audio
> hasta que interactúas). Es normal.

---

## 🔑 Las claves (viven en `config.json`)

```json
{
  "api_key": "sk-or-...",              // tu clave de OpenRouter (el cerebro)
  "model": "anthropic/claude-haiku-4.5",  // cerebro barato; di "cambia a Fable" para tareas difíciles
  "elevenlabs_api_key": "sk_...",      // tu clave de ElevenLabs (la voz)
  "voice_id": "RnKqZYEeVQciORlpiCz0",  // la voz elegida (Baldo, España)
  "tts_model": "eleven_multilingual_v2",
  "business_name": "Mi Negocio",       // sale en el pie de las facturas
  "currency": "RD$",
  "telegram_bot_token": "",            // opcional: recibir el PDF por Telegram
  "telegram_chat_id": ""
}
```

- Las claves las pones **tú** en este archivo, **nunca en un chat**. Si una clave
  pasa por un chat, se considera quemada: bórrala y crea otra.
- El servidor relee `config.json` en cada pregunta: cambias algo, guardas, y
  aplica en la siguiente pregunta **sin reiniciar**.
- ¿No quieres pagar OpenRouter? El pack trae una ruta gratis (usar tu suscripción
  de Claude Code con `claude -p`); pídemelo y te la reconecto.

---

## 💬 Cómo usarlo

| Acción | Cómo |
|---|---|
| **Preguntar** | Escribe abajo y pulsa *Preguntar* (o Enter). |
| **Hablarle** 🎤 | Pulsa el micrófono y habla. Haz una pausa y él responde. |
| **Que mire tu pantalla** 🖥️ | Pulsa el botón de pantalla, comparte, y pregunta "¿qué opinas de esto?". |
| **Guardar una nota** | Di o escribe *"recuerda que…"* → nace una estrella nueva al instante. |
| **Recordatorio** | Di *"recuérdame llamar a Juan mañana a las 3"* → te avisa a esa hora (con la app abierta). |
| **Resumir notas** | Di *"resume mis notas de finanzas"* o *"analiza…"* → resumen con lo importante. |
| **Adjuntar archivo** 📎 | Botón del clip → sube un .txt/.md/.csv/.pdf y pregúntale sobre él. |
| **Redactar** | *"redáctame un correo para el proveedor…"* → texto listo, copiado al portapapeles. |
| **Traducir** | *"traduce al inglés: …"* → traducción, copiada al portapapeles. |
| **Documentos** | *"factura / recibo / cotización / propuesta a X por N por…"* → PDF que se abre. |
| **Cambiar de cerebro** | Di *"cambia a Opus 5"*, *"ponte en Haiku"*, *"vuelve a tu cerebro normal"*. |
| **Facturar** | Di o escribe *"factura a Mike por 1500 por la web"* → genera un PDF y lo abre. También *cotización* y *propuesta*. |
| **Ver una nota** | Clic en cualquier estrella: la cámara vuela y abre el panel. |

Cuando responde desde una nota, la galaxia **vuela a la fuente** y la ilumina. Si es
charla trivial (un saludo), no mueve la cámara.

---

## 📱 Controlar desde Telegram (opcional, gratis)

Habla con Jarvis desde tu teléfono por Telegram. **Solo tú** puedes controlarlo
(ignora a cualquier otro chat).

1. En Telegram abre **@BotFather** → `/newbot` → sigue los pasos → te da un
   **token** (`123456:ABC...`).
2. Pon el token en `config.json` → `telegram_bot_token` (con Notepad; nunca en un chat).
3. **Reinicia Jarvis** y escríbele **cualquier cosa a tu bot**: te responderá con
   **tu chat_id**.
4. Copia ese número en `config.json` → `telegram_chat_id` y **reinicia otra vez**.

Listo. Escríbele por Telegram como si fuera el chat de la app: *"resume mis notas
de finanzas"*, *"factura a Ana por 3000…"* (llega el PDF), *"recuérdame…"*,
*"traduce al inglés…"*, *"cambia a Fable"*. La voz y ver tu pantalla siguen siendo
solo del navegador.

## 🔊 Cambiar la voz

Edita `voice_id` en `config.json` con uno de estos, guarda y recarga la página:

| Voz | `voice_id` | Estilo |
|---|---|---|
| Baldo (actual) | `RnKqZYEeVQciORlpiCz0` | 🇪🇸 España · masculina · seria |
| Prodigio | `mV9T7gqupfSUeWJGyEnB` | 🇩🇴 dominicana · masculina |
| Prodigio p1 | `13hJax5gKqQiB7THor5t` | 🇩🇴 dominicana · masculina, mayor |
| Daisy Fuentes | `VDLvh5okmWyHDYHxlp8d` | latina · femenina · cálida |
| Sara Martin | `KHCvMklQZZo0O30ERnVn` | 🇪🇸 España · femenina · pausada |

Tu cuenta de ElevenLabs tiene 55 voces. Si `voice_id` queda vacío (`""`), usa la
primera de tu cuenta. Si no hay clave de ElevenLabs, usa la voz del navegador.

---

## 🎭 Cambiar la personalidad

El carácter del mayordomo vive en **un solo bloque** llamado `PERSONA`, arriba de
`server.py`. Reescríbelo y cámbialo por un operador de misión, un amigo sarcástico,
un maestro paciente… lo que quieras. Nada más depende de ese bloque.

---

## ✅ Comprobar que todo está bien (preflight)

Con el servidor encendido, en otra terminal:

```bash
python preflight.py
```

Prueba cada pieza de verdad (servidor, galaxia, cerebro, clave, voz, capturas) e
imprime un resumen. **Todo bien = "8 pass, 0 fail".** Cuando algo se te rompa,
agrégale un chequeo nuevo: el arnés crece contigo.

---

## 🎚️ Ajustar la voz que te escucha

En `viewer/index.html`, arriba del todo, está `FINISH_MS = 1500`: cuántos
milisegundos de silencio espera antes de dar por terminada tu frase.
Si te corta a media idea, súbelo; si se siente lento, bájalo. Ajústalo tras
un día de uso real, no de una sentada.

---

## 📁 Qué hay en la carpeta

```
my-jarvis/
├─ notes/            tus notas .md (y notes/captures/ para las que dictas)
├─ viewer/
│  ├─ index.html     la galaxia + voz + chat (todo el frontend)
│  └─ graph-data.js  generado por build.py (no editar a mano)
├─ build.py          el indexador: notas -> galaxia
├─ server.py         el servidor + el cerebro (puerto 4700)
├─ deals.py          motor de facturas/cotizaciones/propuestas -> PDF
├─ deals/            PDFs generados + ledger de numeración (se crea al usar)
├─ preflight.py      el arnés de pruebas
├─ config.json       tus claves y el modelo/voz
├─ index.json        índice interno (lo usa el servidor; no lo toques)
└─ iniciar.bat       doble clic para encender
```

Si editas o agregas notas a mano en `notes/`, corre `python build.py` para
reindexar. Las que dictas con *"recuerda que…"* se indexan solas.
