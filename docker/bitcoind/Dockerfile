FROM ubuntu:18.04

RUN apt-get update && \
    apt-get -y install --no-install-recommends --no-install-suggests \
    build-essential \
    curl \
    libboost-all-dev \
    libevent-dev \
    libssl-dev \
    libzmq3-dev \
    pkg-config \
    git \
    unzip \
    autoconf
RUN apt-get -y install automake libtool

WORKDIR /source
RUN curl -L -k -f  https://github.com/bitcoinvault/bitcoinvault/archive/master.zip -o master.zip
RUN unzip master.zip
RUN rm master.zip
WORKDIR /source/bitcoinvault-master
RUN env CFLAGS=-O2 CXXFLAGS=-O2 \
    ./autogen.sh
RUN ./configure --disable-bench --disable-tests --disable-wallet --with-gui=no
RUN make -j`nproc` && make install

RUN mkdir -p /bitcoin

# logrotate
COPY logrotate-bitcoind /etc/logrotate.d/bitcoind
COPY bitcoin.conf /bitcoin

CMD ["bvaultd","-conf=/bitcoin/bitcoin.conf"]
