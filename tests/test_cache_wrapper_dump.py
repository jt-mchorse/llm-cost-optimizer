"""Tests for ``CacheTelemetry.to_dict`` and ``PromptCacheWrapper.dump_aggregate_json`` (#50).

The runtime layer was missing a serialization affordance: aggregate
telemetry rolled up across every call through the wrapper, but no
JSON-stable dict for an observability sink or a tail-able file. These
tests lock the two new surfaces:

- ``CacheTelemetry.to_dict`` returns the exact field set the dataclass
  carries (key-set lock catches a field added without a serializer
  update or vice versa). The dict round-trips through ``json.dumps``
  losslessly.
- ``PromptCacheWrapper.dump_aggregate_json`` writes the current aggregate
  to ``path`` via the package-level atomic-write helper (no half-written
  files on SIGINT / disk-full / OOM). The on-disk shape is sorted-keys
  JSON with a trailing newline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cost_optimizer import CacheTelemetry, PromptCacheWrapper


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class _FakeMessages:
    """Same hand-rolled fake the existing wrapper tests use; duplicated
    locally so a future move of the shared fixture doesn't ripple."""

    def __init__(self) -> None:
        self._script: list[_Usage] = []

    def queue(self, usage: _Usage) -> None:
        self._script.append(usage)

    def create(self, **_: Any) -> Any:
        usage = self._script.pop(0) if self._script else _Usage()
        return SimpleNamespace(usage=usage, content="ok")


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


# --- CacheTelemetry.to_dict ------------------------------------------------


def test_to_dict_returns_full_field_set() -> None:
    """Key set must match the dataclass fields exactly. If a new field
    lands on ``CacheTelemetry`` without ``to_dict`` learning about it
    the dict would silently drop the new value."""
    t = CacheTelemetry(
        hits=3, misses=1, tokens_cached=2000, tokens_written=200, dollars_saved=0.012
    )
    payload = t.to_dict()
    expected = {f.name for f in fields(t)}
    assert set(payload) == expected
    assert payload == {
        "hits": 3,
        "misses": 1,
        "tokens_cached": 2000,
        "tokens_written": 200,
        "dollars_saved": 0.012,
    }


def test_to_dict_round_trips_through_json_dumps() -> None:
    """Round-trip safety — every value the dataclass carries must
    survive a ``json.dumps`` / ``json.loads`` cycle. Anything else
    would silently lose precision or fail at the sink."""
    t = CacheTelemetry(hits=7, misses=2, tokens_cached=5000, tokens_written=500, dollars_saved=0.5)
    serialized = json.dumps(t.to_dict(), sort_keys=True)
    parsed = json.loads(serialized)
    assert parsed == t.to_dict()


def test_to_dict_on_zero_telemetry_is_all_zero_keys() -> None:
    """Cold-start case: a fresh wrapper has zeroed telemetry. The dict
    must still carry every key so a consumer scanning ``payload["hits"]``
    doesn't KeyError on the first observation."""
    payload = CacheTelemetry.zero().to_dict()
    assert payload == {
        "hits": 0,
        "misses": 0,
        "tokens_cached": 0,
        "tokens_written": 0,
        "dollars_saved": 0.0,
    }


# --- PromptCacheWrapper.dump_aggregate_json --------------------------------


def test_dump_aggregate_json_writes_file_with_aggregate_shape(tmp_path: Path) -> None:
    """Writer produces the dict shape on disk with sorted keys and a
    trailing newline. The file is a self-contained JSON document a
    log-tailer can parse."""
    client = _FakeClient()
    client.messages.queue(_Usage(input_tokens=10, cache_creation_input_tokens=1000))
    client.messages.queue(_Usage(input_tokens=10, cache_read_input_tokens=1000))
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    w.create(system="sysprompt", messages=[{"role": "user", "content": "hi"}])
    w.create(system="sysprompt", messages=[{"role": "user", "content": "again"}])

    out = tmp_path / "agg.json"
    w.dump_aggregate_json(out)
    body = out.read_text(encoding="utf-8")
    assert body.endswith("\n"), "must end with a trailing newline"
    payload = json.loads(body)
    assert set(payload) == {"hits", "misses", "tokens_cached", "tokens_written", "dollars_saved"}
    # Both calls flowed through, so the aggregate must reflect that.
    assert payload["misses"] == 1  # the cache-write call
    assert payload["hits"] == 1  # the cache-read call
    assert payload["tokens_written"] == 1000
    assert payload["tokens_cached"] == 1000


def test_dump_aggregate_json_creates_parent_dirs(tmp_path: Path) -> None:
    """``atomic_write_text`` does ``parent.mkdir(parents=True)``;
    confirm the writer inherits that behavior so callers don't have
    to pre-create a nested observability directory."""
    client = _FakeClient()
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    out = tmp_path / "nested" / "sink" / "agg.json"
    w.dump_aggregate_json(out)
    assert out.exists()
    assert out.parent.is_dir()


def test_dump_aggregate_json_overwrites_atomically(tmp_path: Path) -> None:
    """Two successive dumps to the same path leave the second payload —
    not the concatenation, not a half-written file. ``os.replace``
    semantics make this atomic on POSIX.
    """
    client = _FakeClient()
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    out = tmp_path / "agg.json"
    w.dump_aggregate_json(out)
    body1 = out.read_text(encoding="utf-8")
    client.messages.queue(_Usage(input_tokens=10, cache_creation_input_tokens=2000))
    w.create(system="sys", messages=[{"role": "user", "content": "hi"}])
    w.dump_aggregate_json(out)
    body2 = out.read_text(encoding="utf-8")
    assert body1 != body2
    # No tempfiles left behind under the destination's parent.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], leftovers
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".agg.json.")]
    assert leftovers == [], leftovers


def test_dump_aggregate_json_zero_telemetry_writes_empty_shape(tmp_path: Path) -> None:
    """A wrapper that's never been called still produces a valid JSON
    document — useful for canary-mode observability checks."""
    client = _FakeClient()
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    out = tmp_path / "agg.json"
    w.dump_aggregate_json(out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload == {
        "hits": 0,
        "misses": 0,
        "tokens_cached": 0,
        "tokens_written": 0,
        "dollars_saved": 0.0,
    }


# --- backwards-compat: scripts/_io re-export ------------------------------


def test_scripts_io_atomic_write_text_is_the_package_helper() -> None:
    """``scripts/_io.atomic_write_text`` must be the same callable as
    ``cost_optimizer.io_utils.atomic_write_text`` — the scripts shim
    is the historical name; identity check guarantees no parallel
    implementations exist."""
    from cost_optimizer.io_utils import atomic_write_text as pkg_helper
    from scripts._io import atomic_write_text as scripts_helper

    assert scripts_helper is pkg_helper
