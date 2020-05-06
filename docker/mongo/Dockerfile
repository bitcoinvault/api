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
    rm -rf /var/lib/apt/lists/*
    
VOLUME ["/data/db"]
WORKDIR /data
CMD ["mongod", "--bind_ip_all"]

EXPOSE 27017
EXPOSE 28017
