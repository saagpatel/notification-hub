# notification-hub-mcp

A thin FastMCP wrapper around the local notification-hub daemon at
`http://127.0.0.1:9199`. It proxies to the daemon over HTTP and does not start
the daemon itself.

## Components

### Tools

Source of truth: `server.py`.

- `post_event` - posts an event to `POST /events`; this can create real local
  daemon activity and should be used deliberately.
- `get_health` - reads `GET /health`.
- `get_health_details` - reads `GET /health/details`.
- `get_review_data` - reads `GET /review/data`.
- `get_coordination_readiness` - reads `GET /review/coordination-readiness`.
- `get_import_queue` - reads `GET /review/import-queue`.
- `get_burn_in_reports` - reads `GET /review/burn-in-reports`.

Verify the inventory from the repo root before changing client-facing docs:

```bash
rg -n '@mcp\.tool|^async def ' mcp_server/server.py
```

## Setup

Prerequisites:

- Python 3.12+
- uv
- FastMCP 3.x

Install and test:

```bash
uv sync --frozen
uv run --frozen pytest
```

## Run Locally

Direct Python:

```bash
uv run python server.py
```

FastMCP stdio:

```bash
fastmcp run server.py
```

FastMCP HTTP:

```bash
fastmcp run fastmcp.json --transport http --host 127.0.0.1 --port 8000
```

## Remote MCP Readiness

Deployment profiles:

- Local stdio: use the `config.json` command for desktop clients.
- Local HTTP: run `fastmcp.json` on `127.0.0.1` for connector testing.
- Production HTTP: treat the profile in `fastmcp.json` as a placeholder until
  TLS, logging, and an explicit auth boundary are designed.
- Remote connector: expose only the `/mcp/` endpoint after reviewing exactly
  which daemon data the seven tools can return.

Readiness checklist:

- Use `MCPFORGE_SERVER_API_KEY`, JWT, or a gateway policy before exposing HTTP publicly.
- Keep downstream API keys separate from MCP client tokens; do not pass client bearer tokens downstream.
- Require human approval for sensitive write, delete, external-send, or cross-account actions.
- Log request timing and tool names, but avoid logging raw secrets or full private payloads.

## MCP Client Configuration

Local stdio client config:

```json
{
  "mcpServers": {
    "notification-hub-mcp": {
      "command": "uv",
      "args": ["--directory", ".", "run", "python", "server.py"]
    }
  }
}
```

HTTP client config:

```json
{
  "mcpServers": {
    "notification-hub-mcp": {
      "url": "http://localhost:8000/mcp/",
      "transport": "streamable-http"
    }
  }
}
```

## Security Notes

- Keep secrets in environment variables; never paste API keys into prompts.
- Require approval for sensitive write, delete, or external-send actions.
- Connect remote MCP clients only to trusted servers and review shared data.
- Do not pass MCP client bearer tokens through to downstream APIs.
- Log and review data sent to third-party services when running remotely.
