import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from kitchenowl_mcp import state
from kitchenowl_mcp.chat.dispatch import DESTRUCTIVE_TOOLS, TOOL_FUNCTIONS
from kitchenowl_mcp.client import KitchenOwlClient
from kitchenowl_mcp.tools import registry, shopping


def _query_history(
    monkeypatch: pytest.MonkeyPatch,
    payload: list[dict] | dict,
    *,
    limit: Any = 50,
    status: int = 200,
) -> tuple[list[dict], list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=payload)

    async def run() -> list[dict]:
        client = KitchenOwlClient("https://kitchenowl.example", "test", household_id=3)
        await client.close()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(state, "_client", client)
        monkeypatch.setattr(
            shopping,
            "get_settings",
            lambda: SimpleNamespace(kitchenowl_default_list_id=7),
        )
        try:
            return await shopping.get_shopping_history(limit=limit)
        finally:
            await client.close()

    return asyncio.run(run()), requests


@pytest.mark.parametrize("limit", [1, 50, 120])
def test_history_reads_only_configured_household_and_list(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    items = [
        {"id": 42, "name": "Milch", "description": "2 L", "icon": "milk"},
        {"id": 12, "name": "Brot", "description": "1"},
    ]
    result, requests = _query_history(
        monkeypatch,
        [
            {"id": 8, "recentItems": [{"id": 99, "name": "Other list"}]},
            {"id": 7, "items": [{"id": 1}], "recentItems": items},
        ],
        limit=limit,
    )

    assert result == items  # Preserve the API's order and quantity/note.
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == (
        f"https://kitchenowl.example/api/household/3/shoppinglist?recent_limit={limit}"
    )


def test_history_default_limit_and_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests = _query_history(monkeypatch, [{"id": 7, "recentItems": []}])
    assert result == []
    assert requests[0].url.params["recent_limit"] == "50"


def test_history_missing_list_is_not_reported_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="configured household"):
        _query_history(monkeypatch, [{"id": 8, "recentItems": []}])


@pytest.mark.parametrize("status", [401, 403, 500])
def test_history_propagates_api_errors(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _query_history(monkeypatch, {"msg": "Request failed"}, status=status)


@pytest.mark.parametrize("limit", [0, -1, 121, True, 2.5, "50", None])
def test_history_rejects_invalid_limits_before_api_call(
    monkeypatch: pytest.MonkeyPatch, limit: Any
) -> None:
    def unexpected_client() -> None:
        pytest.fail("Invalid limits must not access the API")

    monkeypatch.setattr(state, "get_client", unexpected_client)
    with pytest.raises(ValueError, match="integer between 1 and 120"):
        asyncio.run(shopping.get_shopping_history(limit=limit))


def test_history_available_to_mcp_and_chat_without_write_confirmation() -> None:
    assert shopping.get_shopping_history in registry.ALL_TOOLS
    assert TOOL_FUNCTIONS["get_shopping_history"] is shopping.get_shopping_history
    assert "get_shopping_history" not in DESTRUCTIVE_TOOLS
