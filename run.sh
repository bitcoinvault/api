cp -r src/flask/* docker/flask/
cp -r src/db/* docker/flask/
cp -r src/blockchain/* docker/flask/
cp -r src/daemon/* docker/daemon/
cp -r src/db/* docker/daemon/
cp -r src/blockchain/* docker/daemon/
cd docker
docker-compose build
docker-compose up