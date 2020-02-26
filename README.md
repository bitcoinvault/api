# Bitcoin Vault Api

A simple public JSON API written in Python and Flask. Includes a proposed production deployment system using Docker, NGINX and uWSGI. Requires a running Bitcoinvault daemon with RPC server enabled to work.

#### RPC Configuration
RPC server parameters are provided in ```blockchain_analyzer.py``` file:
```python
url = "http://user:password@localhost:8332"
```

#### Build and deployment using Docker
```sh
docker build -t flask_image -f docker/Dockerfile .
docker run --name flask_container -p 80:80 flask_image
```
