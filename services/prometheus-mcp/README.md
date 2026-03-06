# prometheus-mcp

A minimal, read-only MCP server that exposes Prometheus metrics to an AI agent.

Built with [FastMCP](https://github.com/jlowin/fastmcp), [httpx](https://www.python-httpx.org/), and Pydantic.

---

## Overview

This service sits between the HomeAgent and a Prometheus instance. It exposes five
read-only MCP tools that let the agent query current values, trends, and metric
metadata without direct access to Prometheus.

All tool outputs are normalised Pydantic models — not raw Prometheus API responses.
The `prom_query_range` output includes pre-computed summary statistics (min, max, avg,
latest) so future anomaly detection jobs can consume it directly.

---

## Tools

| Tool | Description |
|------|-------------|
| `prom_query` | Instant PromQL query — current values |
| `prom_query_range` | Range PromQL query — time series with summary stats |
| `prom_list_metrics` | List available metric names (optional prefix filter) |
| `prom_label_values` | List values for a label (e.g. all job names) |
| `prom_series` | Series metadata for anomaly detection baseline setup |

---

## Guardrails

All limits are configured via environment variables (see `.env.example`):

| Guardrail | Default | Env var |
|-----------|---------|---------|
| Query timeout | 10 s | `PROM_TIMEOUT_SECONDS` |
| Max range window | 24 h | `PROM_MAX_RANGE_HOURS` |
| Min step size | 60 s | `PROM_MIN_STEP_SECONDS` |
| Max series per query | 50 | `PROM_MAX_SERIES` |
| Max datapoints (pre-check) | 10 000 | `PROM_MAX_DATAPOINTS` |
| Max response body | 2 MB | `PROM_MAX_RESPONSE_BYTES` |
| Metric prefix allowlist | (allow all) | `PROM_METRIC_PREFIX_ALLOWLIST` |

---

## Setup

### Requirements

- Python 3.12+
- Access to a Prometheus HTTP API endpoint

### Install

```bash
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env — set PROMETHEUS_URL at minimum
```

### Run

```bash
python app/main.py
```

The server listens on `http://0.0.0.0:9000/mcp` by default.

To connect from HomeAgent, set in the HomeAgent `.env`:

```
PROMETHEUS_MCP_URL=http://192.168.1.x:9000/mcp
```

---

## Example queries

### Current house power consumption

```
prom_query(query='node_power_watts_total')
```

### Last 24 h power trend (5-minute resolution)

```
prom_query_range(
    query='node_power_watts_total',
    start='2024-01-15T00:00:00Z',
    end='2024-01-16T00:00:00Z',
    step=300
)
```

### List all available metrics

```
prom_list_metrics()
```

### List all metrics starting with `node_`

```
prom_list_metrics(prefix='node_')
```

### Get all values for the `room` label

```
prom_label_values(label='room')
```

### Enumerate series for a future anomaly baseline

```
prom_series(match=['{__name__=~"node_.*"}'])
```

---

## Architecture

```
HomeAgent
  └── MCPServerStreamableHTTP(url="http://host:9000/mcp", tool_prefix="prom")
        └── prometheus-mcp  (this service)
              └── Prometheus HTTP API  (read-only)
```

The MCP server connects to Prometheus on the LAN and exposes normalised tool
results. HomeAgent's agent sees the tools prefixed with `prom_` and calls them
like any other tool. No polling, no background workers, no state.
