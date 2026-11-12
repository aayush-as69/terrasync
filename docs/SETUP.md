# TerraSync Setup

## Requirements

- Python 3.12+
- MySQL 8
- Optional Gemini API key
- Optional Twilio account for live SMS

## Local setup

~~~bash
python -m venv venv
pip install -r requirements.txt
cp env.example .env
mysql -u root -p < schema.sql
python seed_data.py
flask --app app run --debug
~~~

Windows Command Prompt:

~~~cmd
python -m venv venv
venv\\Scripts\\activate.bat
pip install -r requirements.txt
copy env.example .env
mysql -u root -p < schema.sql
python seed_data.py
flask --app app run --debug
~~~

Open http://127.0.0.1:5000.

## Environment

Configure a development SECRET_KEY and database values. Add GEMINI_API_KEY to enable live Gemini analysis. For SMS development, keep SMS_PROVIDER=console.

## Database updates

Fresh installs use schema.sql. Existing installations should apply numbered migrations in order.

## Seed data

~~~bash
python seed_data.py
python seed_data.py --reset
~~~

Seeded credentials are for local demonstration only.

## Docker

~~~bash
docker compose up -d --build
docker compose run --rm web python seed_data.py --reset
~~~

A successful Docker build does not prove cloud deployment readiness.
