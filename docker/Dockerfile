FROM python:3.6-slim

WORKDIR /srv/flask_app

RUN apt-get clean \
    && apt-get -y update

RUN apt-get -y install nginx \
    && apt-get -y install python3-dev \
    && apt-get -y install build-essential

COPY docker/requirements.txt ./
RUN pip install -r requirements.txt --src /usr/local/src

COPY docker/nginx.conf /etc/nginx
COPY docker/start.sh ./
COPY docker/uwsgi.ini ./
COPY src ./

RUN chmod +x ./start.sh
CMD ["./start.sh"]
