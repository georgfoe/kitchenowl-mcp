import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from kitchenowl_mcp import state
from kitchenowl_mcp.client import KitchenOwlClient
from kitchenowl_mcp.tools import registry, shopping


class FakeKitchenOwlClient:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.update_args: tuple[int, int, str] | None = None
        self.item_update_args: tuple[int, dict] | None = None
        self.search_query: str | None = None

    async def get_shopping_list_items(self, list_id: int) -> list[dict]:
        return self.items

    async def update_shopping_item(
        self, list_id: int, item_id: int, description: str
    ) -> dict:
        self.update_args = (list_id, item_id, description)
        item = next(item for item in self.items if item["id"] == item_id)
        return {**item, "description": description}

    async def list_items(self) -> list[dict]:
        return self.items

    async def search_items(self, query: str) -> list[dict]:
        self.search_query = query
        return [item for item in self.items if query.lower() in item["name"].lower()]

    async def update_item(self, item_id: int, payload: dict) -> dict:
        self.item_update_args = (item_id, payload)
        item = next(item for item in self.items if item["id"] == item_id)
        item.update(payload)
        return item


@contextmanager
def _active_client(client: FakeKitchenOwlClient):
    state._client = client
    try:
        yield client
    finally:
        state._client = None


def _use_list(monkeypatch: pytest.MonkeyPatch, list_id: int = 7) -> None:
    monkeypatch.setattr(
        shopping,
        "get_settings",
        lambda: SimpleNamespace(kitchenowl_default_list_id=list_id),
    )


def test_search_items_uses_catalogue_search() -> None:
    fake = FakeKitchenOwlClient(
        [
            {"id": 1, "name": "Kaffee", "icon": "coffee"},
            {"id": 2, "name": "Bananen", "icon": "banana"},
        ]
    )

    with _active_client(fake):
        result = asyncio.run(shopping.search_items(" kaffee "))

    assert fake.search_query == "kaffee"
    assert result == [{"id": 1, "name": "Kaffee", "icon": "coffee"}]


def test_search_items_with_empty_query_lists_catalogue() -> None:
    items = [{"id": 1, "name": "Aufbackbrezeln", "icon": None}]
    fake = FakeKitchenOwlClient(items)

    with _active_client(fake):
        result = asyncio.run(shopping.search_items("  "))

    assert fake.search_query is None
    assert result == items


def test_set_item_icon_updates_household_item() -> None:
    fake = FakeKitchenOwlClient(
        [{"id": 1, "name": "Aufbackbrezeln", "icon": None}]
    )

    with _active_client(fake):
        result = asyncio.run(shopping.set_item_icon(1, " pretzel "))

    assert fake.item_update_args == (1, {"icon": "pretzel"})
    assert result["updated"] is True
    assert result["item"]["icon"] == "pretzel"


def test_set_item_icon_can_clear_icon() -> None:
    fake = FakeKitchenOwlClient(
        [{"id": 1, "name": "Aufbackbrezeln", "icon": "pretzel"}]
    )

    with _active_client(fake):
        asyncio.run(shopping.set_item_icon(1, None))

    assert fake.item_update_args == (1, {"icon": None})


def test_set_item_icon_rejects_unknown_item() -> None:
    fake = FakeKitchenOwlClient([])

    with _active_client(fake):
        with pytest.raises(ValueError, match="configured household"):
            asyncio.run(shopping.set_item_icon(99, "pretzel"))

    assert fake.item_update_args is None


def test_set_item_icon_rejects_blank_icon() -> None:
    fake = FakeKitchenOwlClient([{"id": 1, "name": "Brezeln", "icon": None}])

    with _active_client(fake):
        with pytest.raises(ValueError, match="non-empty string or null"):
            asyncio.run(shopping.set_item_icon(1, "  "))

    assert fake.item_update_args is None


def test_update_shopping_list_item_updates_amount_and_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_list(monkeypatch)
    fake = FakeKitchenOwlClient(
        [{"id": 42, "name": "Bananen", "description": "5 Stück"}]
    )

    with _active_client(fake):
        result = asyncio.run(
            shopping.update_shopping_list_item(42, amount="2", unit="Stück")
        )

    assert fake.update_args == (7, 42, "2 Stück")
    assert result == {
        "updated": True,
        "item": {"id": 42, "name": "Bananen", "description": "2 Stück"},
    }


def test_update_shopping_list_item_rejects_unknown_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_list(monkeypatch)
    fake = FakeKitchenOwlClient(
        [{"id": 42, "name": "Bananen", "description": "5 Stück"}]
    )

    with _active_client(fake):
        with pytest.raises(ValueError, match="was not found"):
            asyncio.run(
                shopping.update_shopping_list_item(99, amount="2", unit="Stück")
            )

    assert fake.update_args is None


def test_update_shopping_list_item_rejects_empty_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_list(monkeypatch)
    fake = FakeKitchenOwlClient(
        [{"id": 42, "name": "Bananen", "description": "5 Stück"}]
    )

    with _active_client(fake):
        with pytest.raises(ValueError, match="must not be empty"):
            asyncio.run(shopping.update_shopping_list_item(42, amount="  "))

    assert fake.update_args is None


def test_item_catalogue_tools_are_registered() -> None:
    assert shopping.search_items in registry.ALL_TOOLS
    assert shopping.set_item_icon in registry.ALL_TOOLS
    assert shopping.update_shopping_list_item in registry.ALL_TOOLS


def test_client_search_items_uses_kitchenowl_endpoint() -> None:
    async def run() -> tuple[list[dict], httpx.Request]:
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "Kaffee", "icon": "coffee"}],
            )

        client = KitchenOwlClient(
            "https://kitchenowl.example", "token", household_id=3
        )
        await client.close()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.search_items("Kaffee")
        finally:
            await client.close()

        assert captured_request is not None
        return result, captured_request

    result, request = asyncio.run(run())

    assert request.method == "GET"
    assert str(request.url) == (
        "https://kitchenowl.example/api/household/3/item/search?query=Kaffee"
    )
    assert result[0]["icon"] == "coffee"


def test_client_update_item_uses_kitchenowl_endpoint() -> None:
    async def run() -> tuple[dict, httpx.Request]:
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={"id": 1, "name": "Brezeln", "icon": "pretzel"},
            )

        client = KitchenOwlClient("https://kitchenowl.example", "token")
        await client.close()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.update_item(1, {"icon": "pretzel"})
        finally:
            await client.close()

        assert captured_request is not None
        return result, captured_request

    result, request = asyncio.run(run())

    assert request.method == "POST"
    assert str(request.url) == "https://kitchenowl.example/api/item/1"
    assert json.loads(request.content) == {"icon": "pretzel"}
    assert result["icon"] == "pretzel"


def test_client_update_shopping_item_uses_kitchenowl_endpoint() -> None:
    async def run() -> tuple[dict, httpx.Request]:
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={"id": 42, "name": "Bananen", "description": "2 Stück"},
            )

        client = KitchenOwlClient("https://kitchenowl.example", "token")
        await client.close()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.update_shopping_item(7, 42, "2 Stück")
        finally:
            await client.close()

        assert captured_request is not None
        return result, captured_request

    result, request = asyncio.run(run())

    assert request.method == "PUT"
    assert str(request.url) == "https://kitchenowl.example/api/shoppinglist/7/item/42"
    assert json.loads(request.content) == {"description": "2 Stück"}
    assert result["description"] == "2 Stück"
