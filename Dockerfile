FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*

COPY app/ ./app/
COPY static/ ./static/
COPY entrypoint.sh /entrypoint.sh

ENV DB_PATH=/data/rss.db
ENV FETCH_INTERVAL_MIN=30
ENV MAX_ARTICLES_PER_FEED=200

RUN adduser --disabled-password --gecos "" appuser && \
    mkdir -p /data && \
    chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
