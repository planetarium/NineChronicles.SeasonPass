FROM python:3.11-slim

ARG POETRY_VERSION=1.6.1

RUN apt update
RUN apt install -y postgresql-client vim

# Set up poetry
RUN pip install -U pip "poetry==${POETRY_VERSION}"
RUN poetry config virtualenvs.create false

COPY ./worker /app/NineChronicles.SeasonPass/worker
COPY ./common /app/NineChronicles.SeasonPass/common
COPY ./season_pass /app/NineChronicles.SeasonPass/season_pass
COPY pyproject.toml /app/NineChronicles.SeasonPass
COPY poetry.lock /app/NineChronicles.SeasonPass
COPY block_tracker.service /etc/systemd/system/block_tracker.service
COPY alembic.ini /app/NineChronicles.SeasonPass

WORKDIR /app/NineChronicles.SeasonPass
RUN poetry install --no-root
