# Contract Automation ETL Pipeline
# Python 3.10+ with all dependencies

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create volume mount points
VOLUME ["/app/config", "/app/data"]

# Default command runs the watcher
CMD ["python", "scripts/run_watcher.py"]
