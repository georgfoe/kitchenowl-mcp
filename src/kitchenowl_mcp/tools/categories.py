from .. import state


def _category_for_id(categories: list[dict], category_id: int) -> dict:
    category = next(
        (category for category in categories if category.get("id") == category_id),
        None,
    )
    if category is None:
        raise ValueError(
            f"Category {category_id} was not found in the configured household"
        )
    return category


def _validated_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    if len(name) > 128:
        raise ValueError("name must be at most 128 characters")
    return name


async def list_categories() -> list[dict]:
    """List household item categories in their current display order.

    Each category includes its ID, name, and zero-based ordering value. Use the
    returned IDs with update_category, delete_category, or set_item_category.
    """
    return await state.get_client().list_categories()


async def create_category(name: str, ordering: int | None = None) -> dict:
    """Create a household item category.

    Category names are limited to 128 characters and must be unique within the
    configured household, ignoring letter case. ordering is an optional
    zero-based insertion position; by default the category is appended.
    """
    client = state.get_client()
    name = _validated_name(name)
    categories = await client.list_categories()
    category_count = len(categories)
    if any(
        (category.get("name") or "").casefold() == name.casefold()
        for category in categories
    ):
        raise ValueError(f'A category named "{name}" already exists')
    if ordering is not None and (
        isinstance(ordering, bool) or not 0 <= ordering <= category_count
    ):
        raise ValueError(f"ordering must be between 0 and {category_count}")

    category = await client.create_category({"name": name})
    target_ordering = category_count if ordering is None else ordering
    if category_count:
        category = await client.update_category(
            category["id"], {"ordering": target_ordering}
        )
    return {"created": True, "category": category}


async def update_category(
    category_id: int,
    name: str | None = None,
    ordering: int | None = None,
) -> dict:
    """Rename or reorder an existing household item category.

    First call list_categories to obtain the category ID and current order.
    ordering is a zero-based position and must fit within the current category
    list. Pass name, ordering, or both.
    """
    client = state.get_client()
    categories = await client.list_categories()
    _category_for_id(categories, category_id)
    payload: dict = {}

    if name is not None:
        name = _validated_name(name)
        if any(
            category.get("id") != category_id
            and (category.get("name") or "").casefold() == name.casefold()
            for category in categories
        ):
            raise ValueError(f'A category named "{name}" already exists')
        payload["name"] = name

    if ordering is not None:
        if isinstance(ordering, bool) or not 0 <= ordering < len(categories):
            raise ValueError(f"ordering must be between 0 and {len(categories) - 1}")
        payload["ordering"] = ordering

    if not payload:
        raise ValueError("provide name, ordering, or both")

    category = await client.update_category(category_id, payload)
    return {"updated": True, "category": category}


async def delete_category(category_id: int) -> dict:
    """Delete a household item category.

    First call list_categories to obtain the category ID. Items assigned to the
    deleted category remain in the catalogue and become uncategorized. Returns
    the number of affected items.
    """
    client = state.get_client()
    categories = await client.list_categories()
    category = _category_for_id(categories, category_id)
    items = await client.list_items()
    affected_items = [
        item
        for item in items
        if item.get("category_id") == category_id
        or (
            isinstance(item.get("category"), dict)
            and item["category"].get("id") == category_id
        )
    ]

    await client.delete_category(category_id)
    return {
        "deleted": True,
        "category": {"id": category_id, "name": category.get("name")},
        "items_uncategorized": len(affected_items),
    }


async def set_item_category(item_id: int, category_id: int | None) -> dict:
    """Assign or clear the category of a household catalogue item.

    First call search_items for the item ID and list_categories for the category
    ID. Pass null for category_id to make the item uncategorized.
    """
    client = state.get_client()
    items = await client.list_items()
    if not any(item.get("id") == item_id for item in items):
        raise ValueError(f"Item {item_id} was not found in the configured household")

    category_payload = None
    if category_id is not None:
        categories = await client.list_categories()
        _category_for_id(categories, category_id)
        category_payload = {"id": category_id}

    item = await client.update_item(item_id, {"category": category_payload})
    return {"updated": True, "item": item}
