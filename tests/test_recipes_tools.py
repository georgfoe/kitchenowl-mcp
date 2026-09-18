import asyncio
from contextlib import contextmanager

import pytest

from kitchenowl_mcp import state
from kitchenowl_mcp.tools import recipes


class FakeKitchenOwlClient:
    def __init__(
        self, recipe: dict | None = None, recipes: list[dict] | None = None
    ) -> None:
        self._recipe = recipe
        self._recipes = recipes if recipes is not None else ([recipe] if recipe else [])
        self.update_payload: dict | None = None

    async def get_recipe(self, recipe_id: int) -> dict:
        return self._recipe

    async def update_recipe(self, recipe_id: int, payload: dict) -> dict:
        self.update_payload = payload
        return {**self._recipe, **payload}

    async def create_recipe(self, payload: dict) -> dict:
        return payload

    async def list_recipes(self, search: str = "", limit: int = 50) -> list[dict]:
        return self._recipes

    async def list_items(self) -> list[dict]:
        return []

    async def create_item(self, payload: dict) -> dict:
        return payload


class RejectingImageClient(FakeKitchenOwlClient):
    async def update_recipe(self, recipe_id: int, payload: dict) -> dict:
        self.update_payload = payload
        return self._recipe


@contextmanager
def _active_client(client: FakeKitchenOwlClient):
    state._client = client
    try:
        yield client
    finally:
        state._client = None


def _make_fake_client() -> FakeKitchenOwlClient:
    return FakeKitchenOwlClient(
        recipe={
            "id": 1,
            "name": "Omelet",
            "description": "Original notes.\n\n## Steps\n1. Old step one.\n2. Old step two.",
        }
    )


def test_update_steps_only_preserves_existing_description() -> None:
    with _active_client(_make_fake_client()) as client:
        asyncio.run(recipes.update_recipe(1, steps=["New step."]))

    assert (
        client.update_payload["description"]
        == "Original notes.\n\n## Steps\n1. New step."
    )


def test_update_description_only_preserves_existing_steps() -> None:
    with _active_client(_make_fake_client()) as client:
        asyncio.run(recipes.update_recipe(1, description="New notes."))

    assert (
        client.update_payload["description"]
        == "New notes.\n\n## Steps\n1. Old step one.\n2. Old step two."
    )


def test_create_recipe_carries_quantity_for_dict_ingredients() -> None:
    with _active_client(FakeKitchenOwlClient()):
        result = asyncio.run(
            recipes.create_recipe(
                name="Pancakes",
                ingredients=[
                    {"name": "flour", "amount": "2", "unit": "cups"},
                    "salt",
                    {"name": "eggs", "amount": "3"},
                ],
            )
        )

    items_by_name = {i["name"]: i for i in result["items"]}
    assert items_by_name["flour"]["description"] == "2 cups"
    assert items_by_name["salt"]["description"] == ""
    assert items_by_name["eggs"]["description"] == "3"


def test_update_recipe_carries_quantity_for_dict_ingredients() -> None:
    with _active_client(_make_fake_client()) as client:
        asyncio.run(
            recipes.update_recipe(
                1,
                ingredients=[
                    {"name": "flour", "amount": "2", "unit": "cups"},
                    "salt",
                ],
            )
        )

    items_by_name = {i["name"]: i for i in client.update_payload["items"]}
    assert items_by_name["flour"]["description"] == "2 cups"
    assert items_by_name["salt"]["description"] == ""


def test_set_recipe_image_updates_only_photo() -> None:
    with _active_client(_make_fake_client()) as client:
        result = asyncio.run(
            recipes.set_recipe_image(
                1, "  https://images.example.test/omelet.webp?size=large  "
            )
        )

    assert client.update_payload == {
        "photo": "https://images.example.test/omelet.webp?size=large"
    }
    assert result["photo"] == "https://images.example.test/omelet.webp?size=large"


@pytest.mark.parametrize(
    "image_url",
    [
        "",
        "http://images.example.test/omelet.jpg",
        "https://",
        "https://user:password@images.example.test/omelet.jpg",
    ],
)
def test_set_recipe_image_rejects_unsafe_url(image_url: str) -> None:
    with _active_client(_make_fake_client()) as client:
        with pytest.raises(ValueError, match="image_url"):
            asyncio.run(recipes.set_recipe_image(1, image_url))

    assert client.update_payload is None


def test_set_recipe_image_reports_kitchenowl_rejection() -> None:
    recipe = _make_fake_client()._recipe
    with _active_client(RejectingImageClient(recipe=recipe)):
        with pytest.raises(ValueError, match="did not accept"):
            asyncio.run(
                recipes.set_recipe_image(1, "https://images.example.test/not-an-image")
            )


def test_resolve_ingredient_items_rejects_malformed_dict_entry() -> None:
    with _active_client(FakeKitchenOwlClient()) as client:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            asyncio.run(recipes.resolve_ingredient_items(client, [{"amount": "2"}]))


def test_audit_flags_legacy_recipe_missing_ingredients_and_blank_item_name() -> None:
    fake_recipes = [
        {
            "id": 1,
            "name": "Migrated Recipe",
            "description": "Notes.\n\n## Steps\n1. Do a thing.",
            "items": [{"name": "eggs", "description": "2"}],
        },
        {
            "id": 2,
            "name": "Legacy Recipe",
            "description": "1. Crack eggs.\n2. Whisk them.",
            "items": [{"name": "eggs", "description": "2"}],
        },
        {
            "id": 3,
            "name": "No Ingredients Recipe",
            "description": "Just notes.",
            "items": [],
        },
        {
            "id": 4,
            "name": "Blank Item Recipe",
            "description": "Just notes.",
            "items": [{"name": ""}],
        },
        {
            "id": 5,
            "name": "No Quantities Recipe",
            "description": "Notes.\n\n## Steps\n1. Do a thing.",
            "items": [{"name": "eggs"}, {"name": "flour"}],
        },
    ]
    with _active_client(FakeKitchenOwlClient(recipes=fake_recipes)):
        report = asyncio.run(recipes.audit_recipe_schema())

    assert report["total_recipes"] == 5
    assert report["flagged_count"] == 4
    flagged_by_id = {f["id"]: f["issues"] for f in report["flagged"]}
    assert flagged_by_id[2] == ["legacy_steps_not_migrated"]
    assert flagged_by_id[3] == ["no_ingredients"]
    assert flagged_by_id[4] == ["item_missing_name", "all_ingredients_missing_quantity"]
    assert flagged_by_id[5] == ["all_ingredients_missing_quantity"]
    assert 1 not in flagged_by_id
