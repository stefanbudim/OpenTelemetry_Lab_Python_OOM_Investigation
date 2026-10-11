FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app.py /app/
COPY templates /app/templates

# Ensure OpenShift arbitrary UID compatibility (group 0 write access)
RUN chgrp -R 0 /app && \
    chmod -R g=u /app

# Switch to non-root user (UID 1001)
USER 1001

EXPOSE 5000

ENV PORT=5000 \
    HOST=0.0.0.0 \
    OTEL_EXPORTER_OTLP_ENDPOINT="otel-collector:4317" \
    OTEL_EXPORTER_OTLP_INSECURE="true"

CMD ["python", "app.py"]
