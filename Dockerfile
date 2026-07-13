FROM python:3.12-slim

ARG MEDIA_ROUTER_APP_VERSION=v0.8.1
ARG MEDIA_ROUTER_GIT_BRANCH=Unavailable
ARG MEDIA_ROUTER_GIT_COMMIT=Unavailable

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ROUTER_APP_VERSION=${MEDIA_ROUTER_APP_VERSION} \
    MEDIA_ROUTER_GIT_BRANCH=${MEDIA_ROUTER_GIT_BRANCH} \
    MEDIA_ROUTER_GIT_COMMIT=${MEDIA_ROUTER_GIT_COMMIT}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY sample_data ./sample_data

RUN mkdir -p /data

EXPOSE 8088

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
