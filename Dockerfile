FROM python:3.12-slim

WORKDIR /app
RUN python -m venv /app/.venv

COPY pyproject.toml README.md ./
COPY app ./app
RUN /app/.venv/bin/python -m pip install --no-cache-dir . \
    && /app/.venv/bin/python -m pip check \
    && /app/.venv/bin/python -m uvicorn --version

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

CMD ["/bin/sh", "-c", "exec /app/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
