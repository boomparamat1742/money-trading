# Worker image for Railway/Render/Fly.io (design §13). Long-running process.
FROM python:3.12-slim

WORKDIR /app

# install deps first for layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# copy the worker package (build context = repo root)
COPY worker ./worker

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "worker.app.main"]
