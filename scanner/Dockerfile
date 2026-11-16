FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY trscanner/ trscanner/
COPY universe/ universe/
COPY --from=web /web/dist frontend/dist
ENV TRS_DATA_DIR=/app/data
EXPOSE 8100
CMD ["python", "-m", "trscanner", "serve"]
