FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy source and install
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# Copy configs
COPY configs/ configs/

EXPOSE 8080

CMD ["python", "-m", "arbot.main"]
