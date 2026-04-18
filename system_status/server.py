import argparse
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from urllib.parse import urljoin


@dataclass
class ServiceStatus:
    name: str
    query: str
    status: str
    detail: str
    history: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--target", default="http://prometheus:9090")
    parser.add_argument("--config", default="/app/services.yml")
    return parser.parse_args()


args = parse_args()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def load_services(path: str) -> list[dict[str, Any]]:
    config_path = Path(path)
    if not config_path.exists():
        return []

    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    services = payload.get("services", [])
    if not isinstance(services, list):
        return []
    return services


def query_prometheus(prometheus_base_url: str, query: str) -> dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc)
    start = int((now - datetime.timedelta(hours=24)).timestamp())
    end = int(now.timestamp())
    url = urljoin(prometheus_base_url.rstrip("/") + "/", "api/v1/query_range")
    response = requests.get(
        url,
        params={
            "query": query,
            "start": start,
            "end": end,
            "step": "1h",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def build_status_rows() -> list[ServiceStatus]:
    services = load_services(args.config)
    rows: list[ServiceStatus] = []

    for service in services:
        name = str(service.get("name", "unnamed-service"))
        query = str(service.get("query", ""))
        if not query:
            rows.append(
                ServiceStatus(
                    name=name,
                    query=query,
                    status="NO QUERY",
                    detail="Missing query in services.yml",
                    history=[],
                )
            )
            continue

        try:
            payload = query_prometheus(args.target, query)
            result = payload.get("data", {}).get("result", [])
            if not result:
                rows.append(
                    ServiceStatus(
                        name=name,
                        query=query,
                        status="NO DATA",
                        detail="No matching time series",
                        history=[],
                    )
                )
                continue

            values = result[0].get("values", [])
            history = []
            for _, value in values:
                history.append("up" if str(value) == "1" else "down")

            last_value = values[-1][1] if values else "0"
            is_up = str(last_value) == "1"
            metric_labels = result[0].get("metric", {})
            detail = ", ".join(f"{k}={v}" for k, v in metric_labels.items() if v)
            rows.append(
                ServiceStatus(
                    name=name,
                    query=query,
                    status="UP" if is_up else "DOWN",
                    detail=detail or "No metric labels",
                    history=history,
                )
            )
        except requests.RequestException as exc:
            rows.append(
                ServiceStatus(
                    name=name,
                    query=query,
                    status="ERROR",
                    detail=f"Prometheus request failed: {exc}",
                    history=[],
                )
            )

    return rows


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    rows = build_status_rows()
    rendered_at = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    if "json" in request.query_params:
        return JSONResponse(
            content=[
                {
                    "name": row.name,
                    "query": row.query,
                    "status": row.status,
                    "detail": row.detail,
                    "history": row.history,
                }
                for row in rows
            ]
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "rows": rows,
            "rendered_at": rendered_at,
        },
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)
