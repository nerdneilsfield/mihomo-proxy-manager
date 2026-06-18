# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.14
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system app && \
    adduser --system --ingroup app --home /app app

COPY requirements.txt pyproject.toml README.md LICENSE ./

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY src ./src
COPY examples/config.toml ./config.toml

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install . --no-deps && \
    mkdir -p /app/data/cache /app/logs && \
    chown -R app:app /app

USER app

EXPOSE 8080

ENTRYPOINT ["mpm"]
CMD ["serve", "-c", "/app/config.toml"]
