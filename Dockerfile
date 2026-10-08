FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Chromium + its OS-level deps, used to render prep sheets (HTML -> PDF).
# --with-deps installs the apt packages Chromium needs on this base image.
RUN playwright install --with-deps chromium

COPY app/ app/

EXPOSE 8000

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
