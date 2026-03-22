FROM python:3.11-slim AS base

WORKDIR /app

# Install curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY services/agent-launcher/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Runtime image
FROM base AS runtime

COPY services/agent-launcher/app/ ./app/
COPY runtimes/ ./runtimes/
COPY environments/ ./environments/

# Create agents directory
RUN mkdir -p /agents

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Test image
FROM base AS test

COPY services/agent-launcher/requirements-test.txt ./requirements-test.txt
RUN pip install --no-cache-dir -r requirements-test.txt

COPY services/agent-launcher/app/ ./app/
COPY services/agent-launcher/tests/ ./tests/
COPY services/agent-launcher/pytest.ini ./pytest.ini
COPY runtimes/ ./runtimes/
COPY environments/ ./environments/

RUN mkdir -p /agents

CMD ["pytest", "-q"]

FROM runtime AS final
