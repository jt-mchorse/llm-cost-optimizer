"""Tests for ``CacheStats.to_dict`` and ``SemanticCache.dump_stats_json`` (#52).

Mirrors the locks in ``tests/test_cache_wrapper_dump.py`` but for the
semantic-cache layer. The two cache layers now expose the same
observability surface: a stable-keys dict for in-process consumers, an
atomic-write JSON file for tail-based consumers, sorted keys + trailing
newline byte-shape parity across both.

Coverage matrix:

- ``CacheStats.to_dict`` returns the raw counter fields exhaustively
  (via ``dataclasses.fields``) plus the two derived properties
  (``total_lookups``, ``hit_rate``). Round-trips through ``json.dumps``.
  Zero-state output carries all six keys.
- Derived-field correctness: ``hit_rate == hits / total_lookups`` and
  ``total_lookups == hits + misses`` reproduced in the serialized dict.
- ``SemanticCache.dump_stats_json`` writes the dict shape to disk with
  sorted keys + trailing newline, creates parent directories, overwrites
  atomically, and works on zero-state caches.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from cost_optimizer.semantic_cache import (
    CacheStats,
    HashEmbedder,
    InMemoryStorage,
    SemanticCache,
)


def _fresh_cache() -> SemanticCache:
    return SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage())


# --- CacheStats.to_dict ----------------------------------------------------


def test_to_dict_returns_full_raw_field_set_plus_derived() -> None:
    """Raw counter fields must match the dataclass exhaustively
    (catches a new field landing without a serializer update); plus
    the two derived properties so log consumers don't recompute."""
    s = CacheStats(hits=3, misses=1, invalidations=2, expired_purged=4)
    payload = s.to_dict()
    raw_field_names = {f.name for f in fields(s)}
    assert raw_field_names.issubset(set(payload)), (
        f"to_dict must include every raw counter field; missing: {raw_field_names - set(payload)}"
    )
    # Plus the derived properties.
    assert "total_lookups" in payload
    assert "hit_rate" in payload
    assert payload == {
        "hits": 3,
        "misses": 1,
        "invalidations": 2,
        "expired_purged": 4,
        "total_lookups": 4,
        "hit_rate": 0.75,
    }


def test_to_dict_round_trips_through_json_dumps() -> None:
    """Every value the dataclass carries must survive a
    ``json.dumps`` / ``json.loads`` cycle. Anything else would silently
    lose precision or fail at the sink."""
    s = CacheStats(hits=7, misses=2, invalidations=1, expired_purged=0)
    serialized = json.dumps(s.to_dict(), sort_keys=True)
    parsed = json.loads(serialized)
    assert parsed == s.to_dict()


def test_to_dict_on_zero_stats_is_well_defined() -> None:
    """Cold-start case: a fresh cache has zeroed stats. The dict must
    still carry every key (so a consumer scanning ``payload["hit_rate"]``
    doesn't KeyError on the first observation), and ``hit_rate`` must
    be ``0.0`` not NaN (n_lookups == 0 short-circuit)."""
    payload = CacheStats().to_dict()
    assert payload == {
        "hits": 0,
        "misses": 0,
        "invalidations": 0,
        "expired_purged": 0,
        "total_lookups": 0,
        "hit_rate": 0.0,
    }


def test_derived_fields_in_dict_match_property_definitions() -> None:
    """If the hit_rate formula ever changes (e.g., excluding expired
    purges from denominator), this lock fails loudly. Same for
    total_lookups."""
    s = CacheStats(hits=10, misses=5, invalidations=0, expired_purged=99)
    payload = s.to_dict()
    assert payload["total_lookups"] == s.total_lookups == 15
    assert payload["hit_rate"] == s.hit_rate
    # Plain calculation as a triangulation point (not derived from .property).
    assert payload["hit_rate"] == 10 / 15


# --- SemanticCache.dump_stats_json -----------------------------------------


def test_dump_stats_json_writes_file_with_stats_shape(tmp_path: Path) -> None:
    """Writer produces the dict shape on disk with sorted keys and a
    trailing newline. The file is a self-contained JSON document a
    log-tailer can parse."""
    cache = _fresh_cache()
    # Exercise hits + misses so the on-disk payload is non-trivial.
    cache.put("hello", payload="world", model="m")
    cache.lookup("hello", model="m")  # hit
    cache.lookup("never_seen", model="m")  # miss

    out = tmp_path / "stats.json"
    cache.dump_stats_json(out)
    body = out.read_text(encoding="utf-8")
    assert body.endswith("\n"), "must end with a trailing newline"
    # Sorted-keys property — first key alphabetically is `expired_purged`.
    parsed_keys = list(json.loads(body))
    assert parsed_keys == sorted(parsed_keys)

    payload = json.loads(body)
    assert set(payload) == {
        "hits",
        "misses",
        "invalidations",
        "expired_purged",
        "total_lookups",
        "hit_rate",
    }
    assert payload["hits"] == 1
    assert payload["misses"] == 1
    assert payload["total_lookups"] == 2
    assert payload["hit_rate"] == 0.5


def test_dump_stats_json_creates_parent_dirs(tmp_path: Path) -> None:
    """``atomic_write_text`` does ``parent.mkdir(parents=True)``; the
    writer inherits that so observability sinks under a nested dir
    don't require pre-creation."""
    cache = _fresh_cache()
    out = tmp_path / "nested" / "sink" / "stats.json"
    cache.dump_stats_json(out)
    assert out.exists()
    assert out.parent.is_dir()


def test_dump_stats_json_overwrites_atomically(tmp_path: Path) -> None:
    """Two successive dumps leave the second payload (not a
    concatenation, not a half-written file). ``os.replace`` semantics
    make this atomic on POSIX. No tempfile leftovers in the parent dir."""
    cache = _fresh_cache()
    out = tmp_path / "stats.json"
    cache.dump_stats_json(out)
    body1 = out.read_text(encoding="utf-8")

    cache.put("k", payload="v", model="m")
    cache.lookup("k", model="m")  # hit
    cache.dump_stats_json(out)
    body2 = out.read_text(encoding="utf-8")

    assert body1 != body2
    payload2 = json.loads(body2)
    assert payload2["hits"] == 1
    # No tempfiles left in the dir.
    leftovers_tmp = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers_tmp == [], leftovers_tmp
    leftovers_dot = [p.name for p in tmp_path.iterdir() if p.name.startswith(".stats.json.")]
    assert leftovers_dot == [], leftovers_dot


def test_dump_stats_json_zero_state_writes_well_defined_shape(tmp_path: Path) -> None:
    """A cache that's never been used still produces a valid JSON
    document with every key present — useful for canary observability
    checks where the sink must always be parseable."""
    cache = _fresh_cache()
    out = tmp_path / "stats.json"
    cache.dump_stats_json(out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload == {
        "hits": 0,
        "misses": 0,
        "invalidations": 0,
        "expired_purged": 0,
        "total_lookups": 0,
        "hit_rate": 0.0,
    }
