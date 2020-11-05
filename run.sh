cp -r src/flask/* docker/flask/
cp -r src/db/* docker/flask/
cp -r src/blockchain/* docker/flask/
cp -r src/daemon/* docker/mongo/
cp -r src/db/* docker/mongo/
cp -r src/blockchain/* docker/mongo/
cd docker
docker-compose build
docker-compose up