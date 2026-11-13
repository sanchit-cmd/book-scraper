# Use an official Python runtime as base image
FROM python:3.12-slim-bookworm

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PORT=10000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependency definition files first for efficient Docker layer caching
COPY pyproject.toml uv.lock ./

# Install Python dependencies into virtual environment
RUN uv sync --frozen --no-install-project --no-dev

# Place virtual environment in PATH
ENV PATH="/app/.venv/bin:$PATH"

# Install Chromium and required OS dependencies for Playwright
RUN playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

# Copy the rest of the application code
COPY . .

# Expose Render's default port
EXPOSE 10000

# Run Streamlit, dynamically binding to Render's $PORT environment variable
CMD ["sh", "-c", "streamlit run main.py --server.port=${PORT:-10000} --server.address=0.0.0.0"]

