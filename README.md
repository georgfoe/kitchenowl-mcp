# kitchenowl-mcp

An MCP (Model Context Protocol) server that connects Claude to a household [KitchenOwl](https://kitchenowl.org) instance. Enables read/write access to recipes, shopping lists, and meal plans from within a Claude conversation.

Deployed alongside KitchenOwl in a home Docker Compose stack, exposed to claude.ai via Traefik.

Optionally also serves a small embedded browser chat (`/chat`, off by default) so family members without a claude.ai account can talk to the same tools — see [Chat UI](#chat-ui-optional) below.

---

## Tools

| Tool | Description |
|------|-------------|
| `search_recipes` | List all recipes or filter by keyword / tag |
| `get_recipe` | Fetch a recipe by ID (includes ingredients and steps) |
| `create_recipe` | Create a new recipe with ingredients, steps, and tags |
| `update_recipe` | Update a recipe's description, steps, or other fields |
| `set_recipe_image` | Download an HTTPS image and set it as a recipe's photo |
| `list_tags` | List all household recipe tags |
| `mark_recipe_made` | Log a cook event (sets `planned=true` on the recipe) |
| `delete_recipe` | Delete a recipe by ID |
| `audit_recipe_schema` | Flag recipes not yet migrated to the `## Steps` description convention, missing ingredients, or with blank item names |
| `get_shopping_list` | Read the current shopping list |
| `search_items` | Search or list household catalogue items, including icons |
| `set_item_icon` | Set or clear a catalogue item's KitchenOwl icon |
| `add_shopping_list_items` | Add items with optional amounts and units |
| `update_shopping_list_item` | Change an existing item's amount and unit |
| `clear_checked_items` | Remove checked items from the shopping list |
| `get_meal_plan` | Fetch planned meals for a date range |
| `add_meal_plan_entry` | Add a recipe to the meal plan for a specific day |

## Stack

- **Runtime:** Python 3.11+
- **MCP framework:** FastMCP (`>=2.0.0` in pyproject.toml; 3.4.2 pinned via uv.lock), streamable-http transport
- **HTTP client:** httpx (async)
- **Config:** pydantic-settings (`KITCHENOWL_*` env vars, plus `CHAT_*`/`AUTHENTIK_*`/`ANTHROPIC_*` if the chat UI is enabled)
- **Linter/formatter:** ruff
- **Tests:** pytest
- **Chat UI (optional):** Starlette, Authlib (OIDC), the `anthropic` SDK, uvicorn — no frontend framework or build step (plain HTML/CSS/JS)

## Quick Start

```bash
# 1. Clone and enter the repo
git clone https://github.com/ncastaldi/kitchenowl-mcp
cd kitchenowl-mcp

# 2. Copy environment template and fill in values
cp .env.example .env

# 3. Install dependencies
pip install -e ".[dev]"

# 4. Run the server
kitchenowl-mcp
```

The server listens on port 8000 (streamable-http transport).

## Docker Compose deployment

See `compose.yaml` in this repo for the actual deployment used in production (Traefik labels, network wiring, full env var passthrough including the optional chat UI vars). The essentials:

```yaml
services:
  kitchenowl-mcp:
    build: .
    ports:
      - "8000:8000"
    environment:
      KITCHENOWL_API_URL: http://front:80    # the KitchenOwl *frontend/nginx* container, not the backend
      KITCHENOWL_API_TOKEN: your_token_here
      KITCHENOWL_HOUSEHOLD_ID: 1
      KITCHENOWL_DEFAULT_LIST_ID: 1
```

> **Important:** `KITCHENOWL_API_URL` must point at KitchenOwl's nginx frontend container (serves plain HTTP, typically port 80), not the backend container directly — the backend speaks the raw uWSGI protocol, not HTTP, and requests to it fail with a connection error or protocol mismatch (`httpx.RemoteProtocolError` / `ConnectError`), not a clean HTTP error.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `KITCHENOWL_API_URL` | Yes | Base URL of the KitchenOwl **frontend** (nginx) container |
| `KITCHENOWL_API_TOKEN` | Yes | Bearer token for KitchenOwl API auth |
| `KITCHENOWL_HOUSEHOLD_ID` | No | Household ID (default: `1`) |
| `KITCHENOWL_DEFAULT_LIST_ID` | No | ID of the shopping list to use (default: `1`) |
| `MCP_PORT` | No | Server port (default: `8000`) |

### Chat UI (optional, all off by default)

| Variable | Required | Description |
|----------|----------|-------------|
| `ENABLE_CHAT_UI` | No | Set `true` to enable the embedded `/chat` browser UI (default: `false`) |
| `CHAT_SHARED_PASSWORD` | One of this or Authentik trio | Shared household login password |
| `CHAT_SESSION_SECRET` | Yes, if chat enabled | Session cookie signing secret — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `CHAT_SESSION_COOKIE_SECURE` | No | Marks the session cookie `Secure`/HTTPS-only (default: `true`); set `false` only for local plain-HTTP testing |
| `CHAT_PUBLIC_BASE_URL` | Yes, if Authentik enabled | Public base URL of this deployment, used to build the Authentik redirect URI |
| `AUTHENTIK_ISSUER` | One of this trio or password | Authentik application's OIDC issuer URL — app-slug-scoped, e.g. `https://sso.example.com/application/o/<app-slug>/` |
| `AUTHENTIK_CLIENT_ID` / `AUTHENTIK_CLIENT_SECRET` | With `AUTHENTIK_ISSUER` | OIDC client credentials from the Authentik provider |
| `ANTHROPIC_API_KEY` | Yes, if chat enabled | Anthropic API key powering the chat agent — separate, metered billing, not a claude.ai login |
| `ANTHROPIC_MODEL` | No | Model for the chat agent (default: `claude-sonnet-5`) |
| `CHAT_MAX_TOKENS` | No | Max tokens per agent turn (default: `4096`) |
| `CHAT_MAX_TOOL_ROUNDS` | No | Max tool-calling rounds before the agent stops and asks the user to rephrase (default: `8`) |

## Architecture

```
claude.ai
    │  HTTP/SSE (remote MCP, streamable-http)
    ▼
kitchenowl-mcp  (container, port 8000)
    │  HTTP REST + Bearer token
    │  internal Docker network only
    ▼
kitchenowl-front  (KitchenOwl's nginx frontend — not the backend directly)
```

## Project structure

```
src/kitchenowl_mcp/
  config.py      pydantic-settings config
  auth.py        token seam (v2: per-user lookup)
  client.py      all KitchenOwl HTTP calls
  state.py       shared client singleton
  server.py      FastMCP app, lifespan, tool registration, optional chat ASGI wrapping
  tools/
    registry.py  ALL_TOOLS — single source of truth for /mcp registration + chat dispatch
    recipes.py   search, get, create, update, set_image, list_tags, mark_made, delete, audit_schema
    shopping.py  get_list, search_items, set_item_icon, add_items, update_item, clear_checked
    meal_plan.py get_plan, add_entry
  chat/          optional embedded browser chat (off by default, see below)
    sessions.py, dispatch.py, tool_schemas.py, agent.py, auth.py, middleware.py, routes.py
  static/chat/   chat frontend — plain HTML/CSS/JS, no build step
```

## Chat UI (optional)

An embedded browser chat at `/chat`, off by default (`ENABLE_CHAT_UI=false`), for family members who don't have a claude.ai account. Same container, same port as `/mcp` — enabling it wraps the MCP app in a slightly larger Starlette app; `/mcp` itself is completely unaffected either way.

- **Agent:** Anthropic Messages API, wired directly to the same 17 tool functions `/mcp` registers (in-process calls, not through MCP's JSON-RPC transport). Needs its own `ANTHROPIC_API_KEY` — separate, metered billing, not a reuse of any personal claude.ai/Claude subscription.
- **Login:** shared household password, Authentik OIDC SSO, or both — either is sufficient.
- **Safety:** `delete_recipe`, `clear_checked_items`, `update_recipe`, and `set_recipe_image` always stop and show a confirm/cancel prompt before executing — the agent can propose them but never runs them unconfirmed.
- **History:** ephemeral, in-memory only (lost on restart), bounded by a 24h TTL / 500-session cap so a long-running deployment doesn't grow unboundedly.
- **UI niceties:** assistant replies render as real markdown (lists, bold, links, code — not raw `**`/`-` characters), an animated indicator shows while the agent is working, and a "New chat" button resets both the visible conversation and the server-side session state.

See the "Chat UI (optional)" environment variable table above for full configuration.

## Development

```bash
# Lint
ruff check .
ruff format .

# Tests
pytest
```

## Known gaps

- `clear_checked_items` cannot be fully stress-tested end-to-end — there is no MCP tool to mark a shopping list item as checked. Add `check_shopping_item` to close the loop.
- `mark_recipe_made` sets `planned=true` and appends to `planned_cooking_dates` rather than writing to a discrete cook-history log. This matches observed KitchenOwl API behavior.
