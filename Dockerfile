FROM python:3.11-slim

WORKDIR /app

COPY webapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY webapp/ .

EXPOSE 10000

CMD gunicorn --bind 0.0.0.0:${PORT:-10000} --timeout 120 --workers 2 app:app
