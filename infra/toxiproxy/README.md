# Toxiproxy

A TCP proxy that injects network faults between the gateway and catalog. It runs only in experiments:
the normal `docker compose up` and CI do not start it.

```
docker compose -f docker-compose.yml -f infra/toxiproxy/compose.yml up -d --wait
```

Faults go through its API on `localhost:8474`:

```
# every response 15 s late
curl -s -X POST localhost:8474/proxies/catalog/toxics \
  -d '{"name": "slow", "type": "latency", "attributes": {"latency": 15000}}'
# remove it
curl -s -X DELETE localhost:8474/proxies/catalog/toxics/slow
# cut the link: new connections are refused
curl -s -X POST localhost:8474/proxies/catalog -d '{"enabled": false}'
```

Other toxics: `timeout` (holds the connection, then closes it), `reset_peer`, `bandwidth`, `slicer`.
The image runs as root and has no health check; it is never part of the normal stack.
