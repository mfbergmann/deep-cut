FROM python:3.12-slim

# ffmpeg merges YouTube's separate audio/video streams; deno is the JavaScript
# runtime yt-dlp needs to solve YouTube's player challenges.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip tzdata; \
    curl -fsSL -o /tmp/deno.zip https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip; \
    unzip -q /tmp/deno.zip -d /usr/local/bin; chmod +x /usr/local/bin/deno; rm /tmp/deno.zip; \
    curl -fsSL -o /usr/local/bin/yt-dlp https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp; \
    chmod +x /usr/local/bin/yt-dlp; \
    apt-get purge -y unzip; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*

# curl_cffi: yt-dlp's browser impersonation; Vimeo rejects non-browser clients.
RUN pip install --no-cache-dir curl_cffi

COPY deepcut/ /app/deepcut/
COPY web/ /app/web/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    HOME=/config \
    STATE_DIR=/config \
    STAGING_DIR=/data/deepcut \
    WEB_PORT=8473

WORKDIR /app
EXPOSE 8473
HEALTHCHECK --interval=60s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8473/health',timeout=4)"
CMD ["python3", "-m", "deepcut.server"]
