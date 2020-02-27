# Bitcoin Vault Api

A simple public JSON API written in Python and Flask. Includes a proposed production deployment system using Docker, NGINX and uWSGI. Deployed under http://api.bitcoinvault.global

#### RPC Configuration
RPC server parameters are provided in ```blockchain_analyzer.py``` file:
```python
url = "http://user:password@bitcoind:8332"
```

#### Build and deployment using docker-compose
```sh
cp -r src/* docker/flask/
cd docker
docker-compose build
docker-compose up
```
