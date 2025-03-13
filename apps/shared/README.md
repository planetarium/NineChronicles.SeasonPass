python
```
python -m venv .venv
pip3 install -r requirements.txt
flit install --extras all
```

db
```
export DATABASE_URI=postgresql://local_test:password@127.0.0.1/season_pass
alembic upgrade head
```