FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json web/.npmrc ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY pyproject.toml ./
COPY app/ app/
RUN pip install .
COPY alembic.ini ./
COPY alembic/ alembic/
COPY --from=web /web/dist app/static
RUN useradd --system --uid 1000 corsa && mkdir /data && chown corsa /data
USER corsa
