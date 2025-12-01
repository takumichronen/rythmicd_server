# Rythmicd - Rythmium-compatible server for Miqrochain
# Multi-stage build for minimal production image

# ============================================================================
# Build stage
# ============================================================================
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir build && \
    pip wheel --no-cache-dir --wheel-dir /wheels .

# ============================================================================
# Production stage
# ============================================================================
FROM python:3.11-slim as production

# Create non-root user for security
RUN groupadd -r rythmicd && useradd -r -g rythmicd rythmicd

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder and install
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application code
COPY rythmicd/ ./rythmicd/

# Create config and data directories
RUN mkdir -p /etc/rythmicd /var/lib/rythmicd && \
    chown -R rythmicd:rythmicd /etc/rythmicd /var/lib/rythmicd

# Switch to non-root user
USER rythmicd

# Default environment variables
# NOTE: Set RYTHMICD_DB_PASSWORD via environment or secrets in production
ENV RYTHMICD_RPC_HOST=miqrod \
    RYTHMICD_RPC_PORT=9834 \
    RYTHMICD_DB_HOST=postgres \
    RYTHMICD_DB_PORT=5432 \
    RYTHMICD_DB_NAME=rythmicd \
    RYTHMICD_DB_USERNAME=rythmicd \
    RYTHMICD_RYTHMIUM_HOST=0.0.0.0 \
    RYTHMICD_RYTHMIUM_PORT=50001 \
    RYTHMICD_LOG_LEVEL=INFO

# Expose Rythmium protocol ports
EXPOSE 50001 50002

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.connect(('localhost', 50001)); s.close()"

# Default command
ENTRYPOINT ["python", "-m", "rythmicd.main"]
CMD []
