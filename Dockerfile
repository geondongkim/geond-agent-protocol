FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY schemas ./schemas

RUN pip install --no-cache-dir uv \
    && uv pip install --system --no-cache -e .

CMD ["geond-mcp"]
