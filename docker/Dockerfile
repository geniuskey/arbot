FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source code
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Copy configs
COPY configs/ configs/

EXPOSE 8080

CMD ["python", "-m", "arbot.main"]
