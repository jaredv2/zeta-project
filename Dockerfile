FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY bot/requirements.txt ./bot/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r bot/requirements.txt

COPY checks.py config.py cosmetics.py db.py keygen.py main.py run_services.py ./
COPY bot/main.py bot/schema.sql bot/ui.py ./bot/
COPY bot/cogs/ ./bot/cogs/

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["python", "/app/run_services.py"]
