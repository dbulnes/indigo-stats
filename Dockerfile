FROM node:22-alpine AS web
WORKDIR /build
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data WEB_DIR=/app/web/dist
WORKDIR /app
COPY backend/requirements.lock ./requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends gosu jq wget && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=web /build/dist ./web/dist
COPY --chmod=755 deploy/entrypoint.sh /usr/local/bin/indigo-entrypoint
RUN mkdir /data
ENTRYPOINT ["indigo-entrypoint"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4)"
CMD ["uvicorn","backend.app:app","--host","0.0.0.0","--port","8000","--workers","1","--no-access-log"]
