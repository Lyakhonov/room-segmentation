FROM python:3.11

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# --- Установка системных библиотек для OpenCV, Torch и SciPy ---
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# --- Устанавливаем только requirements, чтобы кешировать слой ---
COPY requirements.txt .

# Чтобы pip использовал pre-built wheels (ускоряет сборку)
RUN pip install --upgrade pip && \
    pip install --use-deprecated=legacy-resolver -r requirements.txt

# --- Копируем код ---
COPY . .

EXPOSE 8000
