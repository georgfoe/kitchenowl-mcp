import asyncio

import pytest

from kitchenowl_mcp.chat import dispatch, tool_schemas
from kitchenowl_mcp.tools import registry


def test_dispatch_matches_registered_tools() -> None:
    """Guards against drift between what /mcp registers and what chat can call."""
    assert set(dispatch.TOOL_FUNCTIONS) == {fn.__name__ for fn in registry.ALL_TOOLS}


def test_destructive_tools_are_a_subset_of_registered_tools() -> None:
    assert dispatch.DESTRUCTIVE_TOOLS <= set(dispatch.TOOL_FUNCTIONS)


def test_recipe_image_updates_require_confirmation() -> None:
    assert "set_recipe_image" in dispatch.DESTRUCTIVE_TOOLS


def test_category_deletion_requires_confirmation() -> None:
    assert "delete_category" in dispatch.DESTRUCTIVE_TOOLS


def test_get_anthropic_tools_raises_before_priming() -> None:
    tool_schemas._CACHED_TOOLS = None
    with pytest.raises(RuntimeError, match="not primed"):
        tool_schemas.get_anthropic_tools()


def test_prime_tool_schema_cache_builds_anthropic_tool_defs() -> None:
    from fastmcp import FastMCP

    server = FastMCP("test")
    for fn in registry.ALL_TOOLS:
        server.add_tool(fn)

    asyncio.run(tool_schemas.prime_tool_schema_cache(server))
    tools = tool_schemas.get_anthropic_tools()

    assert {t["name"] for t in tools} == {fn.__name__ for fn in registry.ALL_TOOLS}
    for t in tools:
        assert t["input_schema"]["type"] == "object"
