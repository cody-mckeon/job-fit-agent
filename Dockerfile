FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-fonts-recommended \
    ca-certificates \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock README.md ./
COPY src ./src
COPY config ./config
COPY profile ./profile

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    python -m playwright install --with-deps chromium

COPY . .

CMD ["python", "-m", "job_fit_agent.main", "--help"]
