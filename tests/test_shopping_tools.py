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

    async def get_shopping_list_items(self, list_id: int) -> list[dict]:
        return self.items

    async def update_shopping_item(
        self, list_id: int, item_id: int, description: str
    ) -> dict:
        self.update_args = (list_id, item_id, description)
        item = next(item for item in self.items if item["id"] == item_id)
        return {**item, "description": description}


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


def test_update_shopping_list_item_is_registered() -> None:
    assert shopping.update_shopping_list_item in registry.ALL_TOOLS


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
