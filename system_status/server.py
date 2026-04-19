import argparse
import datetime
import re
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
    history: list[dict[str, str]]
    children: list["ServiceStatus"] | None = None
    group_id: str | None = None


def normalize_history(values: list[Any], width: int = 24) -> list[dict[str, str]]:
    points: list[dict[str, str]] = []
    for epoch, value in values:
        try:
            label = (
                datetime.datetime.fromtimestamp(float(epoch), tz=datetime.timezone.utc)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M")
            )
        except (TypeError, ValueError, OSError):
            label = "Unknown time"

        points.append(
            {
                "state": "up" if str(value) == "1" else "down",
                "label": label,
            }
        )

    if len(points) >= width:
        return points[-width:]

    padding = [{"state": "no_data", "label": "No data"}] * (width - len(points))
    return padding + points


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "service"


def aggregate_states(states: list[str]) -> str:
    if "down" in states:
        return "down"
    if "no_data" in states:
        return "no_data"
    return "up"


def aggregate_group_history(
    children: list[ServiceStatus], width: int = 24
) -> list[dict[str, str]]:
    if not children:
        return [{"state": "no_data", "label": "No data"}] * width

    group_history: list[dict[str, str]] = []
    for index in range(width):
        states = [child.history[index]["state"] for child in children]
        labels = [
            child.history[index]["label"]
            for child in children
            if child.history[index]["label"] != "No data"
        ]
        group_history.append(
            {
                "state": aggregate_states(states),
                "label": labels[0] if labels else "No data",
            }
        )
    return group_history


def build_service_status(name: str, query: str) -> ServiceStatus:
    if not query:
        return ServiceStatus(
            name=name,
            query=query,
            status="NO QUERY",
            detail="Missing query in services.yml",
            history=[],
        )

    try:
        payload = query_prometheus(args.target, query)
        result = payload.get("data", {}).get("result", [])
        if not result:
            return ServiceStatus(
                name=name,
                query=query,
                status="NO DATA",
                detail="No matching time series",
                history=[{"state": "no_data", "label": "No data"}] * 24,
            )

        values = result[0].get("values", [])
        history = normalize_history(values)

        last_value = values[-1][1] if values else "0"
        is_up = str(last_value) == "1"
        metric_labels = result[0].get("metric", {})
        detail = ", ".join(f"{k}={v}" for k, v in metric_labels.items() if v)
        return ServiceStatus(
            name=name,
            query=query,
            status="UP" if is_up else "DOWN",
            detail=detail or "No metric labels",
            history=history,
        )
    except requests.RequestException as exc:
        return ServiceStatus(
            name=name,
            query=query,
            status="ERROR",
            detail=f"Prometheus request failed: {exc}",
            history=[{"state": "no_data", "label": "No data"}] * 24,
        )


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

    for index, service in enumerate(services):
        name = str(service.get("name", "unnamed-service"))
        children = service.get("children", [])

        if isinstance(children, list) and children:
            child_rows: list[ServiceStatus] = []
            for child in children:
                child_name = str(child.get("name", "unnamed-child"))
                child_query = str(child.get("query", ""))
                child_rows.append(build_service_status(child_name, child_query))

            group_history = aggregate_group_history(child_rows)
            current_state = group_history[-1]["state"] if group_history else "no_data"
            up_count = sum(1 for child in child_rows if child.status == "UP")
            down_count = sum(1 for child in child_rows if child.status == "DOWN")
            no_data_count = sum(
                1
                for child in child_rows
                if child.status in {"NO DATA", "ERROR", "NO QUERY"}
            )

            rows.append(
                ServiceStatus(
                    name=name,
                    query="",
                    status={"up": "UP", "down": "DOWN", "no_data": "NO DATA"}[
                        current_state
                    ],
                    detail=(
                        f"{up_count}/{len(child_rows)} up"
                        + (f", {down_count} down" if down_count else "")
                        + (f", {no_data_count} no data" if no_data_count else "")
                    ),
                    history=group_history,
                    children=child_rows,
                    group_id=f"group-{index}-{slugify(name)}",
                )
            )
            continue

        query = str(service.get("query", ""))
        rows.append(build_service_status(name, query))

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
