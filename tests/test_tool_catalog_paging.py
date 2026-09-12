"""MAD-919: `list_tools` pages the catalog so it never truncates.

Live evidence (v1.0.52): the catalog mounted correctly but the tool result's
structured extras were ~15.3k chars, and `format_tool_result` caps that block
at 8,000 chars — the model saw a mid-JSON dump cut off at ~42 of 81 entries.
Every page must now fit the model-visible budget, and paging must cover the
whole catalog exactly once.
"""
from __future__ import annotations

import asyncio
import json

from src.agent_tools.admin_tools import do_manage_settings
from src.tool_catalog import builtin_tool_names, catalog_page
from src.tool_execution import format_tool_result

MODEL_VISIBLE_LIMIT = 8000


def _list_tools(**args) -> dict:
    return asyncio.run(
        do_manage_settings(json.dumps({"action": "list_tools", **args}), owner=None)
    )


def test_default_page_fits_model_visible_budget():
    result = _list_tools()
    assert result["tools"], result
    assert result["next_offset"] is not None
    formatted = format_tool_result("manage_settings", result)
    assert "truncated" not in formatted
    assert len(formatted) < MODEL_VISIBLE_LIMIT, len(formatted)


def test_paging_covers_catalog_exactly_once_and_fits():
    seen: list[str] = []
    offset = 0
    pages = 0
    while offset is not None:
        result = _list_tools(offset=offset, limit=20)
        formatted = format_tool_result("manage_settings", result)
        assert "truncated" not in formatted
        assert len(formatted) < MODEL_VISIBLE_LIMIT, (offset, len(formatted))
        seen.extend(entry["id"] for entry in result["tools"])
        pages += 1
        assert pages < 20, "paging did not terminate"
        offset = result["next_offset"]
    assert seen == sorted(seen), "catalog pages must stay in deterministic order"
    assert len(seen) == len(set(seen)), "a tool appeared on two pages"
    assert set(seen) == builtin_tool_names()


def test_total_answers_count_without_paging():
    result = _list_tools(limit=1)
    assert result["count"] == 1
    assert result["total"] == len(builtin_tool_names())
    assert str(result["total"]) in result["response"]


def test_category_filter_returns_only_that_category():
    result = _list_tools(category="Code")
    assert result["next_offset"] is None
    assert {entry["id"] for entry in result["tools"]} == {
        "bash",
        "python",
        "read_file",
        "write_file",
    }


def test_search_filter_matches_id_and_description():
    result = _list_tools(search="research")
    ids = {entry["id"] for entry in result["tools"]}
    assert {"manage_research", "trigger_research"} <= ids
    assert result["total"] == len(ids) or result["next_offset"] is not None


def test_catalog_page_helper_is_pure_and_bounded():
    page = catalog_page(limit=5)
    assert page["count"] == 5
    assert page["offset"] == 0
    assert page["next_offset"] == 5
    assert page["total"] == len(builtin_tool_names())
    # clamp guards
    assert catalog_page(offset=-5, limit=0)["offset"] == 0
    assert catalog_page(limit=10_000)["count"] <= 50
