"""The ``tools`` marker never lands on a ``defer_loading`` tool (#378).

``_mark_tools`` marked ``tools[-1]``. The tool-search quick start puts the search
tool first and the deferred tools after it, so the marker went on a deferred
tool. The tool-search docs: "A tool with ``defer_loading: true`` can't also
carry ``cache_control``: the API returns a 400. Put the cache breakpoint on a
non-deferred tool." Deferred tools are also left out of the prompt prefix, so a
breakpoint there would cache nothing.

Every case checks that no deferred tool carries a marker and that the marker is
on the last non-deferred tool. When no tool is deferred, the output must equal
the pre-#378 ``_mark_tools``, which is copied below as the oracle.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from cost_optimizer.cache_wrapper import (
    PromptCacheWrapper,
    _mark_tools,
    _with_cache_control,
)

_5M = {"type": "ephemeral"}
_1H = {"type": "ephemeral", "ttl": "1h"}


def _pre_378_mark_tools(tools: list[Any], *, ttl: str | None = None) -> list[Any]:
    """``_mark_tools`` as it was before #378: the target is ``tools[-1]``."""
    if not tools:
        return tools
    new = [dict(t) for t in tools]
    new[-1] = _with_cache_control(new[-1], ttl)
    return new


def _tool(name: str, *, deferred: bool | None = None) -> dict[str, Any]:
    t: dict[str, Any] = {"name": name, "input_schema": {"type": "object"}}
    if deferred is not None:
        t["defer_loading"] = deferred
    return t


_SEARCH = {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"}


def _quick_start() -> list[dict[str, Any]]:
    """The tool-search docs' quick-start ``tools``: search tool, then two deferred."""
    return [
        dict(_SEARCH),
        _tool("get_weather", deferred=True),
        _tool("search_files", deferred=True),
    ]


def _marked(tools: list[Any]) -> list[str]:
    return [t["name"] for t in tools if t.get("cache_control")]


def _wrapper(segments: tuple[str, ...]) -> PromptCacheWrapper:
    return PromptCacheWrapper(client=None, model="claude-opus-5-5", cache_segments=segments)  # type: ignore[arg-type]


def _assert_no_deferred_marker(tools: list[Any]) -> None:
    for t in tools:
        if t.get("defer_loading") is True:
            assert "cache_control" not in t, t


@pytest.mark.parametrize("segments", [("tools",), ("system", "tools", "messages_prefix")])
def test_quick_start_layout_marks_the_search_tool(segments: tuple[str, ...]) -> None:
    """The #378 report, under both segment sets it names."""
    req = {
        "system": "S",
        "tools": _quick_start(),
        "messages": [{"role": "user", "content": "What is the weather in San Francisco?"}],
        "max_tokens": 16,
    }
    out = _wrapper(segments)._apply_cache_control(copy.deepcopy(req))
    _assert_no_deferred_marker(out["tools"])
    assert _marked(out["tools"]) == ["tool_search_tool_regex"]
    assert out["tools"][0]["cache_control"] == _5M
    # The deferred tools go out exactly as written.
    assert out["tools"][1:] == req["tools"][1:]


def test_mixed_list_marks_the_last_non_deferred_tool_in_the_middle() -> None:
    tools = [
        dict(_SEARCH),
        _tool("a", deferred=True),
        _tool("hot"),
        _tool("b", deferred=True),
        _tool("c", deferred=True),
    ]
    out = _mark_tools(copy.deepcopy(tools))
    _assert_no_deferred_marker(out)
    assert _marked(out) == ["hot"]
    assert [t for i, t in enumerate(out) if i != 2] == [t for i, t in enumerate(tools) if i != 2]


def test_explicit_defer_loading_false_is_not_deferred() -> None:
    tools = [_tool("a", deferred=True), _tool("b", deferred=False)]
    assert _marked(_mark_tools(tools)) == ["b"]


def test_all_deferred_list_is_returned_unmarked() -> None:
    """The API rejects this list on its own; we add nothing to it."""
    tools = [_tool("a", deferred=True), _tool("b", deferred=True)]
    assert _mark_tools(copy.deepcopy(tools)) == tools


def test_all_deferred_list_through_the_wrapper_adds_no_breakpoint() -> None:
    tools = [_tool("a", deferred=True), _tool("b", deferred=True)]
    req = {"tools": tools, "messages": [{"role": "user", "content": "q"}], "max_tokens": 16}
    out = _wrapper(("tools",))._apply_cache_control(copy.deepcopy(req))
    assert out == req


def test_1h_upgrade_lands_on_the_non_deferred_tool() -> None:
    """A caller's 1h system marker still upgrades our tools marker (#283), on the right tool."""
    req = {
        "system": [{"type": "text", "text": "S", "cache_control": dict(_1H)}],
        "tools": _quick_start(),
        "messages": [{"role": "user", "content": "q"}],
        "max_tokens": 16,
    }
    out = _wrapper(("tools",))._apply_cache_control(copy.deepcopy(req))
    _assert_no_deferred_marker(out["tools"])
    assert out["tools"][0]["cache_control"] == _1H


def test_caller_marker_on_the_non_deferred_tool_is_kept() -> None:
    tools = _quick_start()
    tools[0]["cache_control"] = dict(_1H)
    out = _mark_tools(copy.deepcopy(tools))
    assert out == tools


def test_tuple_of_tools_with_deferred_tail() -> None:
    out = _mark_tools(tuple(_quick_start()))  # type: ignore[arg-type]
    _assert_no_deferred_marker(out)
    assert _marked(out) == ["tool_search_tool_regex"]


def test_input_is_not_mutated() -> None:
    tools = _quick_start()
    before = copy.deepcopy(tools)
    _mark_tools(tools)
    assert tools == before


def test_mcp_toolset_entry_is_marked_as_before() -> None:
    """Docs: ``cache_control`` goes on the ``mcp_toolset`` entry itself.

    Its members defer through ``default_config``, not a top-level
    ``defer_loading``. The docs do not say what an all-deferred toolset does
    with a marker, so the entry is treated as non-deferred, as before #378.
    """
    tools = [
        _tool("local", deferred=True),
        {
            "type": "mcp_toolset",
            "mcp_server_name": "calendar",
            "default_config": {"defer_loading": True},
        },
    ]
    out = _mark_tools(copy.deepcopy(tools))
    assert out == _pre_378_mark_tools(copy.deepcopy(tools))
    assert out[1]["cache_control"] == _5M


@pytest.mark.parametrize(
    "tools",
    [
        [_tool("t1")],
        [_tool("t1"), _tool("t2")],
        [dict(_SEARCH), _tool("t1"), _tool("t2", deferred=False)],
        [_tool("t1"), {**_tool("t2"), "cache_control": dict(_1H)}],
    ],
    ids=["one", "two", "search+explicit-false", "caller-1h-last"],
)
@pytest.mark.parametrize("ttl", [None, "1h"])
def test_no_deferred_tools_is_byte_identical_to_pre_378(
    tools: list[dict[str, Any]], ttl: str | None
) -> None:
    """Control: with nothing deferred, the output is what ``tools[-1]`` marking gave."""
    assert _mark_tools(copy.deepcopy(tools), ttl=ttl) == _pre_378_mark_tools(
        copy.deepcopy(tools), ttl=ttl
    )
