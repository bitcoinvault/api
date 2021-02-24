# Bitcoin Vault Api

A simple public JSON API written in Python and Flask. Includes a proposed production deployment system using Docker, NGINX, uWSGI and MongoDB. Deployed under http://api.bitcoinvault.global

The API consists of 4 parts closed in separate Docker containers: the NGINX server redirecting queries to the Flask application, the Flask application executing incoming queries, BTCV node and a database that stores blockchain statistics along with a daemon updating the data.

# NGINX and Flask app

Both components work synergistically to handle requests coming from outside. NGINX is a kind of avant-garde when it comes to handling incoming queries. Sending the request to the API takes place through the handling of the http protocol (port 8080) to the appropriate address on which the service was hosted. Then, using the uWSGI protocol, the query is forwarded to the Flask application running on port 5000. By connecting to the database, the application handles user queries and returns the required results.

At the moment, there are 6 queries that the user can request::

```python
/miningdifficulty
```

The average mining difficulty query for x consecutive blocks in the last y blocks, where x and y are counted based on the given period. The request has one parameter `period`, which can take three values: `week`, `month`, `all` (every other value than `week` and `month`).

`week` (default): average mining difficulty is calculated every 6 consecutive blocks for the last 1008 blocks of blockchain,

`month`: average mining difficulty is calculated every 144 consecutive blocks for the last 4320 blocks of blockchain,

`all`: average mining difficulty is calculated every 1008 consecuitve blocks for all blocks of blockchain.
    
```python
/transactionvolume
```

The average transaction volume query for x consecutive blocks in the last y blocks, where x and y are counted based on the given period. The request has one parameter `period`, which can take three values: `week`, `month`, `all` (every other value than `week` and `month`).

`week` (default): average transaction volume is calculated every 6 consecutive blocks for the last 1008 blocks of blockchain,

`month`: average transaction volume is calculated every 144 consecutive blocks for the last 4320 blocks of blockchain,

`all`: average transaction volume is calculated every 1008 consecuitve blocks for all blocks of blockchain.
    
```python
/nonemptywalletsnumber
```

The query returns the number of current addresses where the BTCV coin balance is greater than 0.

```python
/piechartdata
```

The query returns the address distribution for coinbase transactions from the last 100 blocks.

```python
/richestwallets
```

The query returns a list of 20 addresses with the most BTCV coins at the current moment, along with their balance.

```python
/status
```

The query returns the current blockchain height, network hashrate and the number of all coins currently in circulation.

# Bitcoin Vault node

This component gets the current version of the BTCV node from the Github repository: `https://github.com/bitcoinvault/bitcoinvault`.
The task of this component is to synchronize the BTCV blockchain so that the data on which the analysis is performed is up-to-date.

# MongoDB

This component runs the Mongo database in which blockchain statistics necessary to execute queries for the API are stored. The component actually consists of two parts: the Mongo database and the daemon, which updates this database with data by processing the blockchain. Daemon queries the BTCV node about the blockchain status every 5 seconds and updates the statistics if the database status does not match the blockchain status.

The differences between the state of the base and the state of the blockchain may be due to two reasons: the database does not have statistics from all blocks available in the blockchain (the number of processed blocks for statistics is smaller than the actual number of blocks in the chain) or the reorg occured in blockchain. Both situations are properly handled: the first - by processing the missing blocks and updating the database state, the second - by restoring the last state of the database before the reorg occurence and updating the database from there to the current state of the blockchain. 

Restoring the database is possible thanks to periodic database backups, made every full thousandth block. The database backup files are saved in `/var/backups/mongodb`. For creating and restoring database daemon uses `mongodump` and `mongorestore` commands provided by MongoDB.

The daemon "talks" to the BTCV node using the RPC methods provided by the node.
RPC server parameters are provided in `rpc.py` file:

```python
url = "http://user:password@bitcoind:8332"
```
