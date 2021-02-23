FROM python:3.7.3-stretch
WORKDIR /app

ENV FLASK_APP=api.py
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_ENV=production
ENV FLASK_DEBUG=0
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src .

# run daemon
CMD [ "python3", "daemon.py" ]

# run api
CMD [ "gunicorn", "-w", "3", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "api:app" ]
