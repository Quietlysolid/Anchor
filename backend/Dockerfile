FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==2.3.2

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install Python deps (skip the local anchor package — source not copied yet)
RUN poetry config virtualenvs.create false \
    && poetry lock \
    && poetry install --only main --no-interaction --no-ansi --no-root

# Copy source
COPY . .

# Install the anchor package itself now that the source is present
RUN poetry install --only main --no-interaction --no-ansi

# Default command (overridden by docker-compose)
CMD ["uvicorn", "anchor.main:app", "--host", "0.0.0.0", "--port", "8000"]
