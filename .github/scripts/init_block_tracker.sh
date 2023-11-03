#!/bin/bash

apt install git
git clone https://github.com/planetarium/NineChronicles.SeasonPass
cd NineChronicles.SeasonPass
git checkout [BRANCH]
git pull
pip install -U pip poetry==1.6.1
poetry config virtualenvs.create false
poetry install --no-root --no-dev
ln -s block_tracker.service /etc/systemd/system/block_tracker.service
systemctl daemon-reload
systemctl start block_tracker
