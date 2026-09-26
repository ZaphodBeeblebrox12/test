#!/usr/bin/env bash
# Render build: deps -> static -> migrate (DATABASE_URL is injected by Render)
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --noinput
python manage.py migrate --noinput
