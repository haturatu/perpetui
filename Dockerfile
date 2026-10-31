# syntax=docker/dockerfile:1

FROM golang:1.27.3-bookworm AS go-build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/perpetui ./cmd/perpetui

FROM ghcr.io/astral-sh/uv:0.10.4 AS uv

FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/perpetui/venv

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# pip is not needed at runtime; removing it also drops pip's vendored packages.
RUN uv sync --locked --no-dev --no-editable --no-cache \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip \
              /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
              /usr/local/lib/python3.13/ensurepip \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13

COPY --from=go-build /out/perpetui /usr/local/bin/perpetui
ENV PERPETUI_PYTHON=/opt/perpetui/venv/bin/python

RUN useradd --create-home --uid 10001 perpetui \
    && chown -R perpetui:perpetui /opt/perpetui /app
USER perpetui

ENTRYPOINT ["/usr/local/bin/perpetui"]
