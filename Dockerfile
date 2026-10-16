FROM python:3.12-slim

LABEL org.opencontainers.image.title="Centrol" \
      org.opencontainers.image.description="Toutes les interfaces web du serveur dans une seule fenêtre" \
      org.opencontainers.image.authors="Pierre Desenfant" \
      org.opencontainers.image.version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CENTROL_DATA=/data
WORKDIR /app

# kinit : connexion Kerberos (Active Directory, FreeIPA, MIT)
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends krb5-user \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# xterm.js embarqué dans l'image (aucun CDN nécessaire pour le terminal)
ARG XTERM_VERSION=5.5.0
ARG XTERM_FIT_VERSION=0.10.0
ADD https://cdn.jsdelivr.net/npm/@xterm/xterm@${XTERM_VERSION}/lib/xterm.js /app/static/vendor/xterm.js
ADD https://cdn.jsdelivr.net/npm/@xterm/xterm@${XTERM_VERSION}/css/xterm.css /app/static/vendor/xterm.css
ADD https://cdn.jsdelivr.net/npm/@xterm/addon-fit@${XTERM_FIT_VERSION}/lib/addon-fit.js /app/static/vendor/addon-fit.js

COPY VERSION ./
COPY app ./app
COPY static ./static
RUN chmod -R a+rX /app && useradd -u 1000 -M -d /data centrol

USER 1000:1000
EXPOSE 8322
HEALTHCHECK --interval=30s --timeout=4s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8322/healthz')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8322", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header", "--no-access-log"]
