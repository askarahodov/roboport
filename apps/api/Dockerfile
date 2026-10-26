FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements-dev.txt apps/api/constraints-py313.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt -c constraints-py313.txt

COPY apps/api/alembic.ini .
RUN sed -i 's|script_location = apps/api/alembic|script_location = alembic|' alembic.ini
COPY apps/api/alembic ./alembic
COPY apps/api/roboport_api ./roboport_api
COPY scripts ./scripts

ENV PYTHONPATH=/app
ENV ROBOport_SYNTHETIC_AUTH_API=1

EXPOSE 8000

CMD ["sh", "-c", "alembic -c alembic.ini upgrade head && uvicorn roboport_api.main:app --host 0.0.0.0 --port 8000"]