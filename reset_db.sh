#!/bin/bash
# STOP on error
set -e

echo "------------------------------------------"
echo "Stopping Django server (if running)..."
pkill -f runserver || true

echo "------------------------------------------"
echo "Checking Redis server..."
if ! pgrep redis-server > /dev/null; then
    echo "Redis not running. Starting Redis..."
    brew services start redis || redis-server &
else
    echo "Redis is already running."
fi

echo "------------------------------------------"
echo "Dropping database and user if they exist..."
psql -U macbookpro -c "DROP DATABASE IF EXISTS mizan;"
psql -U macbookpro -c "DROP ROLE IF EXISTS mizan_user;"

echo "------------------------------------------"
echo "Recreating user and database..."
psql -U macbookpro -c "CREATE USER mizan_user WITH PASSWORD 'mizan_password123';"
psql -U macbookpro -c "CREATE DATABASE mizan OWNER mizan_user;"

echo "------------------------------------------"
source venv/bin/activate
echo "Applying migrations..."
python manage.py makemigrations
python manage.py migrate

echo "------------------------------------------"
echo "Creating superuser..."
python manage.py createsuperuser --username admin --email admin@example.com --noinput
python manage.py shell -c "from django.contrib.auth.models import User; u=User.objects.get(username='admin'); u.set_password('admin123'); u.save()"

echo "------------------------------------------"
echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "------------------------------------------"
echo "Setup complete!"
echo "Run the server: python manage.py runserver"
echo "Login at /admin with username: admin, password: admin123"
echo "Redis and Channels are ready for WebSockets."
echo "------------------------------------------"
