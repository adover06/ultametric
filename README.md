# System Status

Simple FastAPI status page backed by Prometheus queries.

## Pipeline (end to end)

1. A service exposes health/metrics data (`/metrics`) directly, or via an exporter (for example cAdvisor, blackbox, postgres-exporter).
2. Prometheus scrapes that target based on `prometheus/prometheus.yml` (`scrape_configs`).
3. Prometheus stores those time series (for example `up`, `probe_success`, `container_last_seen`).
4. `system_status/services.yml` defines which PromQL query each row (or child row) should use.
5. `system_status/server.py` runs each query against Prometheus (`/api/v1/query_range`) for the last 24 hours.
6. The app converts query results into UP/DOWN/NO DATA + history.
7. `system_status/templates/index.html` renders the final status page.

## What to edit when adding a new service

1. Make sure the service has a metric source (native `/metrics` or an exporter).
2. Add/confirm a scrape job in `prometheus/prometheus.yml`.
3. Add a row in `system_status/services.yml` with a query that returns 0/1 style status.
4. Restart affected containers:

```bash
docker compose restart prometheus system-status
```

## Run

```bash
docker compose up -d --build system-status
```

Open: `http://localhost:9110`

JSON mode: `http://localhost:9110/?json=1`
