FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user; public/uploads/issues is where local-development photos land.
RUN useradd --create-home appuser \
    && mkdir -p /app/public/uploads/issues \
    && chown -R appuser:appuser /app/public/uploads
USER appuser

EXPOSE 8000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
