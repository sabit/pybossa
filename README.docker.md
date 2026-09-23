docker run --name pybossa-postgres \
  -e POSTGRES_USER=pybossa \
  -e POSTGRES_PASSWORD=xxxx \
  -e POSTGRES_DB=db \
  -p 25432:5432 \
  -v pybossa-pgdata:/var/lib/postgresql \
  -d postgres:alpine

docker run -d --name pybossa-redis \
    --restart unless-stopped \
    -p 16379:6379 \
    -v pybossa-redis-data:/data \
    redis:8
