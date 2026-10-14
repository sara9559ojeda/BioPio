# BioGuard - single image: 5 gRPC microservices + Envoy (gRPC-Web) + web UI.

# 1) Web UI: TypeScript clients are generated from the same .proto contract.
FROM node:22-alpine AS web
WORKDIR /src
COPY proto ./proto
COPY frontend/package.json frontend/package-lock.json ./frontend/
WORKDIR /src/frontend
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

# 2) Envoy proxy binary.
FROM envoyproxy/envoy:v1.32.1 AS envoy

# 3) Runtime.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    STATIC_DIR=/app/web \
    PORT=8080
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY proto ./proto
COPY backend ./backend
# Python stubs are generated from the contract at build time.
RUN python backend/scripts/gen_protos.py
COPY --from=envoy /usr/local/bin/envoy /usr/local/bin/envoy
COPY gateway/envoy.yaml ./gateway/envoy.yaml
COPY --from=web /src/frontend/dist ./web
COPY deploy/start.sh ./start.sh
RUN chmod +x ./start.sh && mkdir -p /data
EXPOSE 8080
CMD ["./start.sh"]
