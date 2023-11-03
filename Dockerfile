FROM python:3.11-slim

ARG POETRY_VERSION=1.6.1

RUN apt update
RUN apt install -y postgresql-client vim

# Set up poetry
RUN pip install -U pip "poetry==${POETRY_VERSION}"
RUN poetry config virtualenvs.create false

COPY ./worker /app/worker
COPY ./common /app/worker/common
COPY pyproject.toml /app
COPY poetry.lock /app
COPY block_tracker.service /etc/systemd/system/block_tracker.service

WORKDIR /app
RUN poetry install --no-root --no-dev
