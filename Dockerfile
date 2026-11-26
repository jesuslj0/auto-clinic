FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# libpango*/fuentes: las pide WeasyPrint para el PDF de las exportaciones de datos
# (`exports/pdf.py`). Sin una fuente del sistema el PDF saldría sin letras.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]