FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy environment docs (skill/policy)
COPY skill.md /app/skill.md
COPY heartbeat.md /app/heartbeat.md
COPY messaging.md /app/messaging.md
COPY rules.md /app/rules.md

# Copy application code
COPY backend/app/ ./app/
RUN mkdir -p /app/data/state /app/data/runs

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
