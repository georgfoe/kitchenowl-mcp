import logging
from urllib.parse import urlsplit

from .. import state
from ..client import KitchenOwlClient
from ..models import (
    Recipe,
    RecipeItem,
    has_unmigrated_steps,
    normalize_tags,
    parse_description,
    serialize_description,
)

logger = logging.getLogger(__name__)


def _ingredient_name(entry: str | dict) -> str:
    name = entry.get("name") if isinstance(entry, dict) else entry
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            "Each ingredient must be a non-empty name string, or a dict with a "
            f"non-empty 'name' string; got {entry!r}"
        )
    return name


def _ingredient_optional(entry: str | dict) -> bool:
    optional = entry.get("optional", False) if isinstance(entry, dict) else False
    if not isinstance(optional, bool):
        raise ValueError(
            f"Ingredient 'optional' must be true or false; got {optional!r}"
        )
    return optional


def _recipe_items_for_write(raw_items: list[dict]) -> list[dict]:
    """Convert API recipe items to KitchenOwl's strict write schema."""
    items = []
    for raw_item in raw_items:
        name = raw_item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Recipe contains an ingredient without a valid name")
        optional = raw_item.get("optional", False)
        if not isinstance(optional, bool):
            raise ValueError(
                f"Recipe ingredient {name!r} has a non-boolean optional value"
            )
        description = raw_item.get("description") or ""
        if not isinstance(description, str):
            raise ValueError(f"Recipe ingredient {name!r} has a non-string description")
        items.append(
            {
                "name": name,
                "description": description,
                "optional": optional,
            }
        )
    return items


def _find_recipe_item(raw_items: list[dict], item_id: int) -> tuple[int, dict]:
    for index, item in enumerate(raw_items):
        if item.get("id") == item_id:
            return index, item
    raise ValueError(f"Item {item_id} is not an ingredient of this recipe")


def _validate_nonnegative_int(field: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


async def resolve_ingredient_items(
    client: KitchenOwlClient, ingredients: list[str | dict]
) -> list[RecipeItem]:
    """Resolve ingredient entries against the household item catalog,
    creating new catalog entries for unmatched names.

    Each entry is either a bare name string (no quantity) or a dict. Dicts may
    also set optional=true; optional defaults to false.
    {"name": ..., "amount": ..., "unit": ...} — amount/unit are folded into
    the item's description, the only place KitchenOwl's recipe-item schema
    has to carry quantity, mirroring the shopping-list item convention in
    client.py's add_shopping_item.
    """
    names = [_ingredient_name(e) for e in ingredients]
    optional_values = [_ingredient_optional(e) for e in ingredients]
    catalog = await client.list_items() if names else []
    catalog_by_key: dict[str, dict] = {
        key: item
        for item in catalog
        for key in (
            (item.get("name") or "").lower(),
            (item.get("default_key") or "").lower(),
        )
        if key
    }

    items = []
    for entry, ingredient_name, optional in zip(
        ingredients, names, optional_values, strict=True
    ):
        lookup_key = ingredient_name.lower().strip()
        existing = catalog_by_key.get(lookup_key)
        if existing:
            resolved = existing
        else:
            logger.warning(
                "Ingredient %r not found in catalog; creating new item", ingredient_name
            )
            resolved = await client.create_item(
                {
                    "name": ingredient_name.strip(),
                    "default_key": lookup_key.replace(" ", "_"),
                }
            )
        quantity = ""
        if isinstance(entry, dict):
            quantity = f"{entry.get('amount', '')} {entry.get('unit', '')}".strip()
        items.append(
            RecipeItem(
                name=resolved.get("name", ingredient_name.strip()),
                description=quantity,
                optional=optional,
            )
        )
    return items


async def search_recipes(
    query: str = "",
    tags: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search KitchenOwl recipes by name or keyword.

    Returns a list of matching recipes with id, name, description (free-text
    only), steps (separate ordered list), and tags (list of name strings).
    Use get_recipe() with the returned id to fetch full details including
    ingredients. Pass tags to filter by tag name (client-side filter).
    """
    client = state.get_client()
    recipes = await client.list_recipes(search=query, limit=limit)
    if tags:
        tags_lower = {t.lower() for t in tags}
        recipes = [
            r
            for r in recipes
            if any(t.get("name", "").lower() in tags_lower for t in r.get("tags", []))
        ]
    return [_normalize_recipe(r) for r in recipes[:limit]]


async def get_recipe(recipe_id: int) -> dict:
    """Get full recipe details including ingredients, steps, and metadata.

    description contains only free-text notes; steps is a separate ordered
    list. Use search_recipes() first to find the recipe_id.
    """
    raw = await state.get_client().get_recipe(recipe_id)
    return _normalize_recipe(raw)


def _normalize_recipe(raw: dict) -> dict:
    description, steps = parse_description(raw.get("description") or "")
    result = dict(raw)
    result["description"] = description
    result["steps"] = steps
    result["tags"] = normalize_tags(raw.get("tags") or [])
    return result


async def create_recipe(
    name: str,
    description: str = "",
    ingredients: list[str | dict] | None = None,
    steps: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Create a new recipe in KitchenOwl.

    Each ingredient is either a bare name string (e.g. "eggs", no quantity)
    or a dict carrying quantity: {"name": "flour", "amount": "2", "unit": "cups"}.
    Always prefer the dict form when the source recipe states a quantity —
    omitting amount/unit silently drops it, which is the #1 cause of
    imported recipes looking wrong in KitchenOwl (bare ingredient names with
    no measurements). "amount" and "unit" are optional within the dict (e.g.
    {"name": "salt", "amount": "to taste"} is fine). Dict ingredients may
    also set optional=true; optional defaults to false.
    The tool looks up each name in the household item catalog and creates a
    new catalog entry if none matches. Steps are plain text strings in order.
    Tags are tag name strings. Returns the created recipe including its new id.
    """
    client = state.get_client()
    items = await resolve_ingredient_items(client, ingredients or [])
    recipe = Recipe(
        name=name,
        description=description,
        steps=steps or [],
        items=items,
        tags=list(tags or []),
    )
    return await client.create_recipe(recipe.to_wire_payload())


async def update_recipe(
    recipe_id: int,
    name: str | None = None,
    description: str | None = None,
    ingredients: list[str | dict] | None = None,
    steps: list[str] | None = None,
    tags: list[str] | None = None,
    prep_time: int | None = None,
    cook_time: int | None = None,
    total_time: int | None = None,
    yields: int | None = None,
    source: str | None = None,
    visibility: int | None = None,
) -> dict:
    """Update fields of an existing recipe in KitchenOwl.

    Only provided fields are changed; omitted fields are left as-is.
    description and steps update independently without clobbering each
    other — pass steps=[] to clear steps while keeping description, or
    description="" to clear description while keeping steps.
    ingredients, if passed, REPLACES the full existing ingredient list —
    each entry is either a bare name string (no quantity) or a dict
    {"name": "flour", "amount": "2", "unit": "cups"}; prefer the dict form
    whenever a quantity is known, since a bare name silently drops it.
    Dict ingredients may set optional=true; optional defaults to false. For a
    targeted change that automatically preserves every other ingredient, use
    update_recipe_ingredient().
    Tags replace the full existing tag set (pass [] to clear all tags).
    prep_time, cook_time, total_time, and yields are non-negative integers;
    total_time maps to KitchenOwl's `time` field. visibility is 0 (private),
    1 (shared by link), or 2 (public). source is the source name or URL.
    Use search_recipes() to find the recipe_id.
    """
    client = state.get_client()
    payload: dict = {}

    for field, value in (
        ("prep_time", prep_time),
        ("cook_time", cook_time),
        ("time", total_time),
        ("yields", yields),
    ):
        if value is not None:
            _validate_nonnegative_int(field, value)
            payload[field] = value

    if visibility is not None:
        if isinstance(visibility, bool) or visibility not in (0, 1, 2):
            raise ValueError("visibility must be 0 (private), 1 (link), or 2 (public)")
        payload["visibility"] = visibility

    if name is not None:
        payload["name"] = name

    if description is not None or steps is not None:
        if description is None or steps is None:
            current = await client.get_recipe(recipe_id)
            current_description, current_steps = parse_description(
                current.get("description") or ""
            )
        free_text = description if description is not None else current_description
        step_list = steps if steps is not None else current_steps
        payload["description"] = serialize_description(free_text, step_list)

    if tags is not None:
        payload["tags"] = list(tags)

    if ingredients is not None:
        items = await resolve_ingredient_items(client, ingredients)
        payload["items"] = [i.model_dump() for i in items]

    if source is not None:
        payload["source"] = source

    if not payload:
        raise ValueError("Provide at least one recipe field to update")

    return await client.update_recipe(recipe_id, payload)


async def add_recipe_ingredient(
    recipe_id: int,
    name: str,
    amount: str = "",
    unit: str = "",
    optional: bool = False,
) -> dict:
    """Add one ingredient without replacing the recipe's existing ingredients.

    amount and unit are combined into KitchenOwl's quantity text. Set optional
    to true when the ingredient is not required. The ingredient is resolved
    against the household item catalog and created there if necessary.
    Use get_recipe() first to find the recipe_id.
    """
    client = state.get_client()
    current = await client.get_recipe(recipe_id)
    raw_items = current.get("items") or []
    if any(
        (item.get("name") or "").casefold() == name.strip().casefold()
        for item in raw_items
    ):
        raise ValueError(f"Recipe already contains ingredient {name!r}")

    new_items = await resolve_ingredient_items(
        client,
        [{"name": name, "amount": amount, "unit": unit, "optional": optional}],
    )
    items = _recipe_items_for_write(raw_items)
    items.append(new_items[0].model_dump())
    return await client.update_recipe(recipe_id, {"items": items})


async def update_recipe_ingredient(
    recipe_id: int,
    item_id: int,
    optional: bool | None = None,
    quantity: str | None = None,
) -> dict:
    """Change one existing recipe ingredient without altering the others.

    Use get_recipe() to obtain both recipe_id and the ingredient's item_id.
    Set optional to true or false to make the ingredient optional or required.
    quantity replaces the complete quantity/note text stored for the ingredient
    (for example "2 cups" or "to taste"); pass an empty string to clear it.
    Omitted fields are preserved.
    """
    if optional is None and quantity is None:
        raise ValueError("Provide optional and/or quantity to update")
    if optional is not None and not isinstance(optional, bool):
        raise ValueError("optional must be true or false")
    if quantity is not None and not isinstance(quantity, str):
        raise ValueError("quantity must be a string")

    client = state.get_client()
    current = await client.get_recipe(recipe_id)
    raw_items = current.get("items") or []
    index, _ = _find_recipe_item(raw_items, item_id)
    items = _recipe_items_for_write(raw_items)
    if optional is not None:
        items[index]["optional"] = optional
    if quantity is not None:
        items[index]["description"] = quantity
    return await client.update_recipe(recipe_id, {"items": items})


async def remove_recipe_ingredient(recipe_id: int, item_id: int) -> dict:
    """Remove one ingredient without altering the recipe's other ingredients.

    Use get_recipe() to obtain both recipe_id and the ingredient's item_id.
    This removes the ingredient only from the recipe; the household catalog
    item itself is not deleted.
    """
    client = state.get_client()
    current = await client.get_recipe(recipe_id)
    raw_items = current.get("items") or []
    index, _ = _find_recipe_item(raw_items, item_id)
    items = _recipe_items_for_write(raw_items)
    del items[index]
    return await client.update_recipe(recipe_id, {"items": items})


async def set_recipe_image(recipe_id: int, image_url: str) -> dict:
    """Download an HTTPS image and set it as an existing recipe's photo.

    Use search_recipes() to find the recipe_id. image_url must be a direct,
    publicly reachable HTTPS URL. KitchenOwl downloads and persists the image,
    then returns the updated recipe including its stored photo filename.
    Replaces the current recipe image when one already exists.
    """
    image_url = image_url.strip()
    if len(image_url) > 2048:
        raise ValueError("image_url must not exceed 2048 characters")

    parsed = urlsplit(image_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("image_url must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("image_url must not contain embedded credentials")

    client = state.get_client()
    current = await client.get_recipe(recipe_id)
    updated = await client.update_recipe(recipe_id, {"photo": image_url})
    if updated.get("photo") == current.get("photo"):
        raise ValueError(
            "KitchenOwl did not accept the image URL; ensure it points directly "
            "to a supported public image"
        )
    return updated


async def list_tags() -> list[dict]:
    """List all recipe tags defined in this KitchenOwl household.

    Returns a list of tag objects with id and name.
    Use the name strings with search_recipes(tags=[...]) or create_recipe(tags=[...]).
    """
    return await state.get_client().list_tags()


async def mark_recipe_made(recipe_id: int) -> dict:
    """Record that a recipe was just cooked.

    Increments the cook count and logs the timestamp in KitchenOwl's
    cooking history. Use search_recipes() to find the recipe_id.
    Returns the updated recipe.
    """
    return await state.get_client().cook_recipe(recipe_id)


async def delete_recipe(recipe_id: int) -> dict:
    """Delete a recipe by ID.

    Returns confirmation with the deleted recipe_id.
    Use search_recipes() to find the recipe_id first.
    """
    await state.get_client().delete_recipe(recipe_id)
    return {"deleted_recipe_id": recipe_id}


async def audit_recipe_schema() -> dict:
    """Audit every recipe in this household against the canonical schema.

    Flags recipes that predate the '## Steps' description convention
    (numbered steps embedded directly in description with no heading —
    these read back as unstructured free text with no separate steps list
    until the recipe is next edited via update_recipe), recipes with no
    ingredients, ingredient items with a blank name, and recipes where
    every ingredient is missing quantity info (description empty on every
    item). This is a heuristic, not a certainty — it also catches recipes
    that legitimately have no recorded quantities — but it's a reasonable
    starting point for finding recipes imported before create_recipe/
    update_recipe supported the {"name", "amount", "unit"} ingredient form,
    back when quantities were silently dropped. Read-only — fixing flagged
    recipes is a separate update_recipe() call. Returns a summary plus the
    list of flagged recipes with reasons.
    """
    recipes = await state.get_client().list_recipes(limit=500)

    flagged = []
    for r in recipes:
        issues = []
        if has_unmigrated_steps(r.get("description") or ""):
            issues.append("legacy_steps_not_migrated")
        items = r.get("items") or []
        if not items:
            issues.append("no_ingredients")
        if any(not (i.get("name") or "").strip() for i in items):
            issues.append("item_missing_name")
        if items and all(not (i.get("description") or "").strip() for i in items):
            issues.append("all_ingredients_missing_quantity")
        if issues:
            flagged.append({"id": r.get("id"), "name": r.get("name"), "issues": issues})

    return {
        "total_recipes": len(recipes),
        "flagged_count": len(flagged),
        "flagged": flagged,
    }
