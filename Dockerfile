FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Python packages with C/C++ extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    g++ \
    gcc \
    python3-dev \
    libsndfile1-dev \
    portaudio19-dev \
    libboost-all-dev \
    binutils \
    libproj-dev \
    gdal-bin \
    libgdal-dev \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY ./requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the backend source code
COPY ./ ./

CMD ["sh", "-c", "until nc -z db 5432; do echo '⏳ Waiting for database...'; sleep 2; done && \
    python3 manage.py makemigrations && \
    python3 manage.py migrate && \
    daphne -b 0.0.0.0 -p 8000 mizan.asgi:application"]
