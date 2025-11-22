#!/bin/bash
# Start the Mizan AI Backend Server
# Uses the .venv312 virtual environment

cd "$(dirname "$0")"

if [ -d ".venv312" ]; then
    echo "✅ Using .venv312 virtual environment"
    ./.venv312/bin/python manage.py runserver
elif [ -d ".venv" ]; then
    echo "⚠️  .venv312 not found, trying .venv..."
    ./.venv/bin/python manage.py runserver
else
    echo "❌ No virtual environment found! Please run 'pip install -r requirements.txt' first."
    exit 1
fi
