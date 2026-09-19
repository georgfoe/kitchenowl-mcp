from collections.abc import Callable

from ..tools import registry

TOOL_FUNCTIONS: dict[str, Callable] = {fn.__name__: fn for fn in registry.ALL_TOOLS}

DESTRUCTIVE_TOOLS: frozenset[str] = frozenset(
    {
        "delete_recipe",
        "delete_category",
        "clear_checked_items",
        "update_recipe",
        "add_recipe_ingredient",
        "update_recipe_ingredient",
        "remove_recipe_ingredient",
        "set_recipe_image",
    }
)
