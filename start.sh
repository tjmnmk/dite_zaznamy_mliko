#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Inicializace databaze
python3 -c "from app import init_db; init_db()"

# Spusteni pres gunicorn
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 8 \
    --access-logfile - \
    --error-logfile - \
    app:app
