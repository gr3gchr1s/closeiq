FROM python:3.14-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

COPY pyproject.toml ./

RUN pip install --no-cache-dir \
    "psycopg[binary]" \
    python-dotenv \
    "fastapi[standard]" \
    "mcp[cli]>=2.1,<3"

COPY src ./src
COPY data ./data

CMD ["sh", "-c", "python -c \"from closeiq.seed_accounts import seed_accounts; seed_accounts('data/chart_of_accounts.csv')\" && python -m uvicorn closeiq.api:app --host 0.0.0.0 --port 8000"]