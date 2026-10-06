FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user; static/uploads/issues is where citizen photos land.
RUN useradd --create-home appuser \
    && mkdir -p /app/static/uploads/issues \
    && chown -R appuser:appuser /app/static/uploads
USER appuser

EXPOSE 8000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
