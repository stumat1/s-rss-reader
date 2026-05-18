FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/

ENV DB_PATH=/data/rss.db
ENV FETCH_INTERVAL_MIN=30
ENV MAX_ARTICLES_PER_FEED=200

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
