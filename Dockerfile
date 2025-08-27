# Use Python 3.11 slim as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install yt-dlp, beets, and pylast
RUN pip install --no-cache-dir yt-dlp beets pylast

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY templates/ ./templates/

# Get host user ID and group ID (will be passed as build args)
ARG USER_ID=1000
ARG GROUP_ID=1000

# Create a user with the same UID/GID as the host user
RUN groupadd -g ${GROUP_ID} appuser && \
    useradd -m -s /bin/bash -u ${USER_ID} -g ${GROUP_ID} appuser

# Create necessary directories and set permissions
RUN mkdir -p /app/Music /app/config /home/appuser/.config && \
    chown -R appuser:appuser /app /home/appuser

# Switch to the app user
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/appuser

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

# Run the application
CMD ["python", "app.py"]