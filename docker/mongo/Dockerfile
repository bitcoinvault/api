FROM ubuntu:18.04

RUN apt-get -y update && \
	apt-get install -y ca-certificates \
					   jq \
					   numactl \
					   gnupg && \
	apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv 4B7C549A058F8B6B && \
    echo "deb [arch=amd64,arm64] https://repo.mongodb.org/apt/ubuntu bionic/mongodb-org/4.2 multiverse" | tee /etc/apt/sources.list.d/mongodb-org4.2.list && \
    apt-get -y update && \
    apt-get install -y mongodb-org && \
    apt-get install -y python3-pip && \
    apt-get install -y supervisor && \
    rm -rf /var/lib/apt/lists/*
    
RUN mkdir -p /var/log/supervisor
COPY supervisord.conf /etc/supervisor/conf./supervisord.conf
VOLUME ["/data/db"]
VOLUME ["/var/backups/mongodb"]

EXPOSE 27017
EXPOSE 28017

WORKDIR /app
COPY . /app
RUN pip3 install -r requirements.txt
CMD ["/usr/bin/supervisord"]

