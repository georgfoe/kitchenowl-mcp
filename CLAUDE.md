# CLAUDE.md

This file provides context for Claude (and other LLM assistants) working in this repository.

---

## Project identity

**kitchenowl-mcp** — An MCP (Model Context Protocol) server that connects Claude to a household KitchenOwl instance. Enables read/write access to recipes, shopping lists, and meal plans from within a Claude conversation. Deployed alongside KitchenOwl in a home Docker Compose stack, exposed to claude.ai via Traefik.

## Stack

- **Runtime:** Python 3.11+
- **MCP framework:** FastMCP (`>=2.0.0` in pyproject.toml; 3.4.2 pinned via uv.lock), streamable-http transport
- **HTTP client:** httpx (async)
- **Config:** pydantic-settings (reads `KITCHENOWL_*` env vars, plus `CHAT_*`/`AUTHENTIK_*`/`ANTHROPIC_*` when the chat UI is enabled)
- **Linter/formatter:** ruff
- **Tests:** pytest
- **Chat UI (optional):** Starlette, Authlib (OIDC), anthropic SDK, uvicorn, itsdangerous

## Architecture

```
claude.ai
    │  HTTP/SSE (remote MCP, streamable-http)
    ▼
kitchenowl-mcp  (container, port 8000)
    │  HTTP REST + Bearer token
    │  internal Docker network only
    ▼
kitchenowl-back  (existing KitchenOwl container)
```

**Key modules:**
- `src/kitchenowl_mcp/config.py` — `Settings` via pydantic-settings, accessed lazily via `get_settings()`
- `src/kitchenowl_mcp/auth.py` — `get_token(request_context=None)` — the auth seam; swap implementation for per-user lookup in v2 without touching tool handlers
- `src/kitchenowl_mcp/models.py` — canonical `Recipe`/`RecipeItem` pydantic models; `parse_description`/`serialize_description` implement the steps-in-description convention (KitchenOwl has no native steps field); `normalize_tags` flattens read-shape tags (`[{id,name}]`) to write-shape (`list[str]`)
- `src/kitchenowl_mcp/client.py` — `KitchenOwlClient` — ALL KitchenOwl HTTP calls live here; one place to fix when the API changes; stays a pure dict-in/dict-out transport layer, schema-agnostic by design — model construction/serialization happens in the tool layer
- `src/kitchenowl_mcp/state.py` — module-level `_client` singleton; initialized in server lifespan, accessed by tools via `get_client()`
- `src/kitchenowl_mcp/tools/` — one file per domain (recipes, shopping, categories, meal_plan); plain async functions registered in server.py via `mcp.add_tool()`; `tools/registry.py` holds `ALL_TOOLS`, the single list both `server.py` and the chat agent build from, so they can never drift apart
- `src/kitchenowl_mcp/server.py` — FastMCP app, lifespan hook (health-checks KitchenOwl at startup), tool registration, `main()` entry point; `_build_asgi_app()` wraps the MCP app in a bigger Starlette app when `ENABLE_CHAT_UI=true` (see below)
- `src/kitchenowl_mcp/chat/` — optional embedded browser chat (off by default). `sessions.py` (in-memory `ChatSession`s, TTL/max-size pruning, `reset_session()` for the "New chat" action), `dispatch.py` (`TOOL_FUNCTIONS`/`DESTRUCTIVE_TOOLS`), `tool_schemas.py` (builds Anthropic tool defs from `server.list_tools()`, primed once at startup), `agent.py` (manual tool-calling loop with destructive-tool confirmation gating), `auth.py` (shared-password + Authentik/Authlib OIDC), `middleware.py` (`ChatAuthGateMiddleware`, scoped strictly to `/chat*`), `routes.py` (page/login/oidc/api handlers, including `api_clear`)
- `src/kitchenowl_mcp/static/chat/` — the chat frontend: plain HTML/CSS/JS, no build step, no framework. Assistant replies render through a small hand-rolled markdown-to-HTML pass in `app.js` (bold/italic/code/headings/lists/links; input is HTML-escaped first so tool-result content can't inject markup); a thinking indicator shows while a request is in flight; header has "New chat" (calls `POST /chat/api/clear`, disabled for the same span as the composer to avoid resetting mid-request) and "Log out"

## Constraints (non-negotiable)

- Never commit `.env` or any token/secret — only `.env.example`
- All KitchenOwl HTTP calls MUST go through `KitchenOwlClient` in `client.py` — no direct httpx calls in tool handlers
- `get_token()` is the only place the API token is read — never reference `settings.kitchenowl_api_token` directly in tools
- `get_settings()` is lazy (lru_cache) — do not call at module import time; call inside functions so import tests pass
- Server must fail loudly at startup if KitchenOwl is unreachable (lifespan health check enforces this)
- Conventional Commits format required for all commits

## Code style

- ruff, 88 char line length, double quotes, isort
- No comments unless the WHY is non-obvious
- No module-level settings access (breaks import tests and delays startup error reporting)
- Type hints required on all function signatures

## Current state

### Done

- All 13 MCP tools implemented and stress-tested (v3 run: 0 failures, 15 operations; `audit_recipe_schema` added after v3, not yet stress-tested):
  - Recipes: `search_recipes`, `get_recipe`, `create_recipe`, `update_recipe`, `list_tags`, `mark_recipe_made`, `delete_recipe`, `audit_recipe_schema`
  - Shopping: `get_shopping_list`, `add_shopping_list_items`, `clear_checked_items`
  - Meal plan: `get_meal_plan`, `add_meal_plan_entry`
- KitchenOwl recipe item schema confirmed: `{name, description, optional}` only — `id` and `ordering` must be omitted
- Canonical recipe schema defined in `models.py` (`Recipe`/`RecipeItem`): steps round-trip via a `## Steps` heading section appended to `description` (KitchenOwl has no native steps column) instead of being silently flattened in three different places; `get_recipe`/`search_recipes` return `steps` as a separate structured field and `tags` normalized to `list[str]` on both read and write
- Fixed: `update_recipe` previously recomputed `description` from only the current call's `description`/`steps` args, silently discarding whatever was embedded in the other on a partial update. It now fetches the current recipe and merges the untouched half before re-serializing — `steps=[]`/`description=""` still explicitly clear a field
- `audit_recipe_schema` tool: read-only report flagging recipes not yet migrated to the `## Steps` convention (`models.has_unmigrated_steps` — numbered list in `description` with no heading, ≥2 numbered lines), recipes with no ingredients, items with a blank name, and recipes where every ingredient is missing quantity info (`all_ingredients_missing_quantity` — a heuristic, not a certainty, since it also catches recipes that legitimately have no recorded quantities; still a reasonable starting point for finding recipes imported before ingredients supported quantity). Does not fix anything itself; a flagged recipe needs a follow-up `update_recipe(recipe_id, steps=[...])` or `update_recipe(recipe_id, ingredients=[...])` call to migrate it
- Fixed: `ingredients` on `create_recipe`/`update_recipe` was `list[str]` (bare names only) — the recipe-item schema has no quantity field of its own, so every imported/created recipe silently dropped amounts and units (e.g. "2 cups flour" became just "flour"), which is why imported recipes were rendering unclearly in KitchenOwl. `ingredients` now accepts `list[str | dict]`: a bare string (no quantity) or `{"name", "amount", "unit"}`, with amount/unit folded into the ingredient item's `description` field — mirroring the existing shopping-list item convention (`client.py:add_shopping_item`) rather than inventing a new one. Tool docstrings steer callers toward the dict form whenever a quantity is known
- `mark_recipe_made` sets `planned=true` and appends to `planned_cooking_dates`; no discrete cook-history log exists in the API
- `add_meal_plan_entry` response is the updated recipe object, not a standalone planner entry; meal plan data is embedded on recipes via `planned_days` / `planned_cooking_dates`
- Ingredient names are lowercased server-side on create (KitchenOwl behavior, not a bug)
- Dockerfile for container deployment
- Deployed via Docker Compose
- CI: ruff + pytest
- Embedded family chat UI (`ENABLE_CHAT_UI`, off by default): browser chat backed by the Anthropic Messages API, wired directly to the same 13 tool functions the MCP server registers (in-process calls, not through MCP's JSON-RPC transport). Two parallel login methods, either sufficient — shared household password, or Authentik OIDC (Authlib). `delete_recipe`, `clear_checked_items`, and `update_recipe` are gated behind an explicit confirm/cancel step in the UI before they execute — the LLM can propose them but never runs them unconfirmed. Chat history is ephemeral/in-memory only (lost on restart, and bounded in a running process by a 24h TTL / 500-session cap in `chat/sessions.py`). `/mcp` (claude.ai's path) is untouched: `ChatAuthGateMiddleware` only intercepts paths starting with `/chat`, and when the feature is disabled none of the Starlette/middleware wrapping is constructed at all — `main()` calls `server.run(...)` exactly as before. Verified with `pytest` (unit tests for the confirmation-gating loop with a stubbed Anthropic client, and `TestClient`-based HTTP tests including the `/mcp`-is-never-gated isolation guarantee and a full login→message→confirm round trip)
- **Chat UI is deployed and confirmed working on heimdall** — both login methods verified live (shared password, and Authentik OIDC after correcting the application slug in `AUTHENTIK_ISSUER` and adding the redirect URI to the provider's allow-list)
- Chat UI UX follow-ups, all shipped: a "Log out" button in the header; markdown rendering for assistant replies (hand-rolled parser in `app.js`, no external library — bold/italic/code/headings/lists/links, HTML-escaped first); an animated "thinking" indicator while a request is in flight; a "New chat" button (`POST /chat/api/clear` + `chat/sessions.py:reset_session`) that resets both the visible conversation and the server-side session state, with the button disabled for the same span as the composer to prevent a mid-request reset from letting a stale response leak back into the cleared session

### Not started

- `check_shopping_item` tool (needed to fully validate `clear_checked_items` end-to-end)
- Structured logging
- Per-user token mapping (v2, OpenWebUI)

## Open questions

1. Does KitchenOwl's API token expire? Plan assumes permanent (no refresh logic). Verify in KitchenOwl settings before prod deploy.
2. `get_meal_plan` regressed (TypeError) in stress test v2 and recovered in v3 with no deployment change — possible environment flakiness. Monitor across future runs; consider running it 2–3× per test cycle.
3. Clarify `mark_recipe_made` semantics with KitchenOwl docs — current behavior (sets `planned=true`) does not obviously represent a cook-history entry.

## Decision log

- **Ingredient quantity as `list[str | dict]` with a `{"name", "amount", "unit"}` dict form, not a separate parallel array or free-text parsing** — a parallel `ingredients_detail` array risks drifting out of sync with `ingredients` by index; parsing amount/unit back out of a single free-text string like "2 cups flour" is fragile and pushes ambiguity onto regex instead of onto the caller (an LLM), which already knows the parsed quantity when importing a recipe. The dict form also reuses the exact `amount`/`unit` → `description` convention `client.py:add_shopping_item` already established for shopping-list items, instead of inventing a second one
- **FastMCP over bare `mcp` library** — higher-level API, built-in streamable-http transport, auto schema generation from type hints
- **`get_token(request_context=None)` signature** — accepts context param as v2 seam for per-user lookup without refactoring tool handlers
- **Module-level `state._client` singleton** — avoids circular imports while giving tools access to the shared httpx client initialized in lifespan
- **`KITCHENOWL_DEFAULT_LIST_ID` env var** — explicit config over auto-discovery; simpler, no extra API call per operation
- **Steps live inside `description` behind a `## Steps` marker, not a fabricated field** — KitchenOwl's API has no steps column; scope was deliberately kept to fields KitchenOwl natively supports rather than inventing extended metadata (source, time, image, servings-on-recipe) it can't persist. The marker keeps descriptions readable if viewed directly in KitchenOwl's own UI. Known limitation: recipes created before this convention show old step-like text as part of free-text `description` until their next edit — no automatic migration is performed
- **`client.py` stays schema-agnostic** — recipe model construction/serialization lives in `tools/recipes.py`, not `client.py`, keeping the client a pure dict-in/dict-out transport layer consistent with every other method in that file
- **Chat agent calls tool functions directly, never through MCP's JSON-RPC transport** — same process, same functions FastMCP already registers via `tools/registry.py`; going through the MCP protocol for an in-process caller would add a serialization hop for no benefit
- **A manual tool-calling loop in `chat/agent.py`, not the Anthropic SDK's built-in tool runner** — the runner can't pause mid-turn, return an HTTP response, and resume from a separate request once a human confirms a destructive action; that pause/resume is exactly what the confirmation gate requires
- **Destructive tools (`delete_recipe`, `delete_category`, `clear_checked_items`, `update_recipe`, `set_recipe_image`) require explicit UI confirmation before executing** — a multi-user family chat makes a misfired tool call (e.g. Claude picking the wrong same-named recipe) both easy to trigger and expensive to notice after the fact; this repo has already hit one silent-data-loss bug in `update_recipe` from a prior version
- **Two parallel chat login methods (shared password, Authentik OIDC), either sufficient** — not every family member has an Authentik account; requiring both would be needless friction for a home LAN app
- **Chat history is ephemeral/in-memory for v1** — no SQLite/database; acceptable to lose history on restart, avoids a new storage dependency
- **`ChatAuthGateMiddleware` checks `path.startswith("/chat")` first, passing everything else straight through** — the isolation guarantee that keeps claude.ai's `/mcp` traffic (and the separate not-yet-built Cloud Run/Gemini OAuth path in `docs/plans/google-oauth.md`) completely unaffected by the chat feature, whether or not it's enabled

---

*Last updated: 2026-07-14 | Session: chat UI deployed and verified live (password + Authentik); added logout button, markdown rendering, thinking indicator, and New chat (`/chat/api/clear`)*
