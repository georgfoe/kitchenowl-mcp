from .. import state
from ..config import get_settings


async def get_shopping_list() -> list[dict]:
    """Return all current items on the household shopping list.

    Items include name, amount, unit, icon, and whether they are checked off.
    """
    settings = get_settings()
    return await state.get_client().get_shopping_list_items(
        settings.kitchenowl_default_list_id
    )


async def search_items(query: str = "") -> list[dict]:
    """Search the household item catalogue, including each item's icon.

    Pass a name or partial name to use KitchenOwl's fuzzy search. An empty query
    returns the full catalogue. Search before adding a shopping-list item so an
    existing item with a suitable icon can be reused.
    """
    client = state.get_client()
    query = query.strip()
    if query:
        return await client.search_items(query)
    return await client.list_items()


async def set_item_icon(item_id: int, icon: str | None) -> dict:
    """Set or clear the KitchenOwl icon for a household catalogue item.

    First call search_items to obtain the catalogue item's ID and inspect its
    current icon. Reuse an icon identifier returned by search_items so it is
    known to exist in this KitchenOwl installation. Pass null for icon to clear
    it. Arbitrary image uploads are not supported for catalogue items.
    """
    client = state.get_client()
    items = await client.list_items()
    if not any(item.get("id") == item_id for item in items):
        raise ValueError(f"Item {item_id} was not found in the configured household")

    if isinstance(icon, str):
        icon = icon.strip()
        if not icon:
            raise ValueError("icon must be a non-empty string or null")
        if len(icon) > 128:
            raise ValueError("icon must be at most 128 characters")

    item = await client.update_item(item_id, {"icon": icon})
    return {"updated": True, "item": item}


async def add_shopping_list_items(items: list[dict]) -> dict:
    """Add one or more items to the household shopping list.

    Each item dict should include: name (required), amount (optional string),
    unit (optional string). Search the catalogue first to reuse the exact name
    of an existing item with an icon.
    Example: [{"name": "milk", "amount": "2", "unit": "L"}]
    Returns a summary of added items.
    """
    settings = get_settings()
    client = state.get_client()
    list_id = settings.kitchenowl_default_list_id
    results = []
    for item in items:
        payload = {
            "name": item.get("name", ""),
            "amount": str(item.get("amount", "")),
            "unit": item.get("unit", ""),
        }
        result = await client.add_shopping_item(list_id, payload)
        results.append(result)
    return {"added": len(results), "items": results}


async def update_shopping_list_item(item_id: int, amount: str, unit: str = "") -> dict:
    """Update the quantity of an existing item on the household shopping list.

    First call get_shopping_list to obtain the item's ID and current unit. Pass
    item_id, the complete new amount, and the unit to keep (for example,
    item_id=42, amount="2", unit="Stück"). Omitting unit clears the unit.
    """
    settings = get_settings()
    client = state.get_client()
    list_id = settings.kitchenowl_default_list_id

    amount = amount.strip()
    unit = unit.strip()
    if not amount:
        raise ValueError("amount must not be empty")

    items = await client.get_shopping_list_items(list_id)
    if not any(item.get("id") == item_id for item in items):
        raise ValueError(
            f"Shopping-list item {item_id} was not found on configured list {list_id}"
        )

    description = f"{amount} {unit}".strip()
    item = await client.update_shopping_item(list_id, item_id, description)
    return {"updated": True, "item": item}


async def clear_checked_items() -> dict:
    """Remove all checked-off items from the household shopping list.

    Returns the count of items removed.
    """
    settings = get_settings()
    client = state.get_client()
    list_id = settings.kitchenowl_default_list_id
    items = await client.get_shopping_list_items(list_id)
    checked = [i for i in items if i.get("checked", False)]
    for item in checked:
        await client.remove_shopping_item(list_id, item["id"])
    return {"removed": len(checked)}
