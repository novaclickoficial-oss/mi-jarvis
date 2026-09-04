# Imagen para desplegar Jarvis en la nube (EasyPanel / cualquier Docker).
FROM python:3.12-slim

# Chromium: imprime facturas/recibos a PDF. ffmpeg: convierte la voz a nota de voz de Telegram.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app

# En la nube el servidor escucha en todas las interfaces (el chequeo de salud
# de EasyPanel llega al contenedor). Las CLAVES llegan por variables de entorno.
ENV JARVIS_HOST=0.0.0.0
EXPOSE 4700

# Indexa las notas y arranca el servidor (+ el bot de Telegram si hay token).
CMD ["sh", "-c", "python build.py && python server.py"]
