import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest

from kitchenowl_mcp import state
from kitchenowl_mcp.client import KitchenOwlClient
from kitchenowl_mcp.tools import categories, registry


class FakeKitchenOwlClient:
    def __init__(
        self, category_rows: list[dict], items: list[dict] | None = None
    ) -> None:
        self.categories = category_rows
        self.items = items or []
        self.created_payload: dict | None = None
        self.updated_args: tuple[int, dict] | None = None
        self.deleted_id: int | None = None
        self.item_update_args: tuple[int, dict] | None = None

    async def list_categories(self) -> list[dict]:
        return self.categories

    async def create_category(self, payload: dict) -> dict:
        self.created_payload = payload
        category = {
            "id": max((row["id"] for row in self.categories), default=0) + 1,
            "name": payload["name"],
            "ordering": 0,
        }
        self.categories.append(category)
        return category

    async def update_category(self, category_id: int, payload: dict) -> dict:
        self.updated_args = (category_id, payload)
        category = next(row for row in self.categories if row["id"] == category_id)
        if "name" in payload:
            category["name"] = payload["name"]
        if "ordering" in payload:
            self.categories.remove(category)
            self.categories.insert(payload["ordering"], category)
            for index, row in enumerate(self.categories):
                row["ordering"] = index
        return category

    async def delete_category(self, category_id: int) -> None:
        self.deleted_id = category_id
        self.categories = [row for row in self.categories if row["id"] != category_id]

    async def list_items(self) -> list[dict]:
        return self.items

    async def update_item(self, item_id: int, payload: dict) -> dict:
        self.item_update_args = (item_id, payload)
        item = next(row for row in self.items if row["id"] == item_id)
        item.update(payload)
        return item


@contextmanager
def _active_client(client: FakeKitchenOwlClient):
    state._client = client
    try:
        yield client
    finally:
        state._client = None


def test_list_categories_preserves_kitchenowl_order() -> None:
    rows = [
        {"id": 2, "name": "Gemüse", "ordering": 0},
        {"id": 1, "name": "Getränke", "ordering": 1},
    ]
    fake = FakeKitchenOwlClient(rows)

    with _active_client(fake):
        result = asyncio.run(categories.list_categories())

    assert result == rows


def test_create_category_trims_name() -> None:
    fake = FakeKitchenOwlClient([])

    with _active_client(fake):
        result = asyncio.run(categories.create_category(" Getränke "))

    assert fake.created_payload == {"name": "Getränke"}
    assert result["created"] is True
    assert result["category"]["ordering"] == 0


def test_create_category_appends_by_default() -> None:
    fake = FakeKitchenOwlClient([{"id": 1, "name": "Gemüse", "ordering": 0}])

    with _active_client(fake):
        result = asyncio.run(categories.create_category("Getränke"))

    assert fake.updated_args == (2, {"ordering": 1})
    assert result["category"]["ordering"] == 1
    assert [row["id"] for row in fake.categories] == [1, 2]


def test_create_category_can_insert_at_requested_position() -> None:
    fake = FakeKitchenOwlClient([{"id": 1, "name": "Gemüse", "ordering": 0}])

    with _active_client(fake):
        result = asyncio.run(categories.create_category("Getränke", ordering=0))

    assert fake.updated_args == (2, {"ordering": 0})
    assert result["category"]["ordering"] == 0
    assert [row["id"] for row in fake.categories] == [2, 1]


def test_create_category_rejects_case_insensitive_duplicate() -> None:
    fake = FakeKitchenOwlClient([{"id": 1, "name": "Getränke", "ordering": 0}])

    with _active_client(fake):
        with pytest.raises(ValueError, match="already exists"):
            asyncio.run(categories.create_category("getränke"))

    assert fake.created_payload is None


@pytest.mark.parametrize("name", ["  ", "x" * 129])
def test_create_category_rejects_invalid_name(name: str) -> None:
    fake = FakeKitchenOwlClient([])

    with _active_client(fake):
        with pytest.raises(ValueError, match="name must"):
            asyncio.run(categories.create_category(name))

    assert fake.created_payload is None


@pytest.mark.parametrize("ordering", [-1, 2, True])
def test_create_category_rejects_invalid_ordering(ordering: int) -> None:
    fake = FakeKitchenOwlClient([{"id": 1, "name": "Gemüse", "ordering": 0}])

    with _active_client(fake):
        with pytest.raises(ValueError, match="ordering must be between"):
            asyncio.run(categories.create_category("Getränke", ordering=ordering))

    assert fake.created_payload is None


def test_update_category_renames_and_reorders() -> None:
    fake = FakeKitchenOwlClient(
        [
            {"id": 1, "name": "Gemüse", "ordering": 0},
            {"id": 2, "name": "Drinks", "ordering": 1},
        ]
    )

    with _active_client(fake):
        result = asyncio.run(
            categories.update_category(2, name=" Getränke ", ordering=0)
        )

    assert fake.updated_args == (2, {"name": "Getränke", "ordering": 0})
    assert result == {
        "updated": True,
        "category": {"id": 2, "name": "Getränke", "ordering": 0},
    }
    assert [row["id"] for row in fake.categories] == [2, 1]


def test_update_category_requires_a_change() -> None:
    fake = FakeKitchenOwlClient([{"id": 1, "name": "Gemüse", "ordering": 0}])

    with _active_client(fake):
        with pytest.raises(ValueError, match="provide name"):
            asyncio.run(categories.update_category(1))

    assert fake.updated_args is None


def test_update_category_rejects_unknown_category() -> None:
    fake = FakeKitchenOwlClient([])

    with _active_client(fake):
        with pytest.raises(ValueError, match="configured household"):
            asyncio.run(categories.update_category(99, name="Getränke"))

    assert fake.updated_args is None


def test_update_category_rejects_duplicate_name() -> None:
    fake = FakeKitchenOwlClient(
        [
            {"id": 1, "name": "Gemüse", "ordering": 0},
            {"id": 2, "name": "Getränke", "ordering": 1},
        ]
    )

    with _active_client(fake):
        with pytest.raises(ValueError, match="already exists"):
            asyncio.run(categories.update_category(2, name="gemüse"))

    assert fake.updated_args is None


@pytest.mark.parametrize("ordering", [-1, 2, True])
def test_update_category_rejects_invalid_ordering(ordering: int) -> None:
    fake = FakeKitchenOwlClient(
        [
            {"id": 1, "name": "Gemüse", "ordering": 0},
            {"id": 2, "name": "Getränke", "ordering": 1},
        ]
    )

    with _active_client(fake):
        with pytest.raises(ValueError, match="ordering must be between"):
            asyncio.run(categories.update_category(1, ordering=ordering))

    assert fake.updated_args is None


def test_delete_category_reports_uncategorized_items() -> None:
    fake = FakeKitchenOwlClient(
        [{"id": 3, "name": "Gemüse", "ordering": 0}],
        [
            {"id": 10, "name": "Karotten", "category": {"id": 3}},
            {"id": 11, "name": "Brokkoli", "category_id": 3},
            {"id": 12, "name": "Milch", "category": {"id": 4}},
        ],
    )

    with _active_client(fake):
        result = asyncio.run(categories.delete_category(3))

    assert fake.deleted_id == 3
    assert result == {
        "deleted": True,
        "category": {"id": 3, "name": "Gemüse"},
        "items_uncategorized": 2,
    }


def test_delete_category_rejects_unknown_category() -> None:
    fake = FakeKitchenOwlClient([])

    with _active_client(fake):
        with pytest.raises(ValueError, match="configured household"):
            asyncio.run(categories.delete_category(99))

    assert fake.deleted_id is None


def test_set_item_category_assigns_category() -> None:
    fake = FakeKitchenOwlClient(
        [{"id": 3, "name": "Gemüse", "ordering": 0}],
        [{"id": 10, "name": "Karotten", "category": None}],
    )

    with _active_client(fake):
        result = asyncio.run(categories.set_item_category(10, 3))

    assert fake.item_update_args == (10, {"category": {"id": 3}})
    assert result["updated"] is True


def test_set_item_category_can_clear_category() -> None:
    fake = FakeKitchenOwlClient(
        [{"id": 3, "name": "Gemüse", "ordering": 0}],
        [{"id": 10, "name": "Karotten", "category": {"id": 3}}],
    )

    with _active_client(fake):
        asyncio.run(categories.set_item_category(10, None))

    assert fake.item_update_args == (10, {"category": None})


def test_set_item_category_rejects_unknown_item() -> None:
    fake = FakeKitchenOwlClient([{"id": 3, "name": "Gemüse", "ordering": 0}])

    with _active_client(fake):
        with pytest.raises(ValueError, match="Item 99"):
            asyncio.run(categories.set_item_category(99, 3))

    assert fake.item_update_args is None


def test_set_item_category_rejects_unknown_category() -> None:
    fake = FakeKitchenOwlClient(
        [],
        [{"id": 10, "name": "Karotten", "category": None}],
    )

    with _active_client(fake):
        with pytest.raises(ValueError, match="Category 99"):
            asyncio.run(categories.set_item_category(10, 99))

    assert fake.item_update_args is None


def test_category_tools_are_registered() -> None:
    assert categories.list_categories in registry.ALL_TOOLS
    assert categories.create_category in registry.ALL_TOOLS
    assert categories.update_category in registry.ALL_TOOLS
    assert categories.delete_category in registry.ALL_TOOLS
    assert categories.set_item_category in registry.ALL_TOOLS


def test_client_category_methods_use_kitchenowl_endpoints() -> None:
    async def run() -> tuple[list[dict], dict, dict, list[httpx.Request]]:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json=[{"id": 1, "name": "Gemüse", "ordering": 0}],
                )
            if request.method == "DELETE":
                return httpx.Response(200, json={"msg": "DONE"})
            payload = json.loads(request.content)
            return httpx.Response(200, json={"id": 1, **payload})

        client = KitchenOwlClient("https://kitchenowl.example", "token", household_id=3)
        await client.close()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            listed = await client.list_categories()
            created = await client.create_category({"name": "Gemüse"})
            updated = await client.update_category(1, {"ordering": 0})
            await client.delete_category(1)
        finally:
            await client.close()
        return listed, created, updated, requests

    listed, created, updated, requests = asyncio.run(run())

    assert listed[0]["name"] == "Gemüse"
    assert created["name"] == "Gemüse"
    assert updated["ordering"] == 0
    assert [(request.method, str(request.url)) for request in requests] == [
        ("GET", "https://kitchenowl.example/api/household/3/category"),
        ("POST", "https://kitchenowl.example/api/household/3/category"),
        ("POST", "https://kitchenowl.example/api/category/1"),
        ("DELETE", "https://kitchenowl.example/api/category/1"),
    ]
