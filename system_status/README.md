# System Status

Small FastAPI service that renders a simple status page from Prometheus `up` metrics.

## Configure displayed services

Edit `system_status/services.yml`.

Each entry needs:

- `name`: label shown in UI
- `query`: PromQL expression that returns a 0/1 style series

Example:

```yaml
services:
  - name: Authentik
    query: up{job="authentik"}

  - name: Traefik
    query: up{job="traefik"}
```

## Run

From repo root:

```bash
docker compose up -d --build system-status
```

Open `http://localhost:9110`.

Append `?json=1` to get JSON output.

## How to add metrics to services in the future

1. Expose a `/metrics` endpoint from the service (native Prometheus client libraries are easiest).
2. Add a Prometheus scrape job in `prometheus/prometheus.yml` under `scrape_configs`.
3. Add a status entry in `system_status/services.yml` with an `up{job="..."}` query.
4. Restart Prometheus and system-status.

For services that do not support Prometheus, run an exporter (blackbox, postgres-exporter, redis-exporter, etc.) and scrape that exporter.
