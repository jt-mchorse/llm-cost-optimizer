"""File-mode contract for ``atomic_write_text`` (#243, portfolio-ops#81).

The helper used to create its temp file with ``tempfile.NamedTemporaryFile``,
which always creates 0600, and ``os.replace`` carried that mode onto the
target. Measured on main with umask 022: a new file came out 0600 and an
existing 0644 file was demoted to 0600 by an overwrite. ``Path.write_text``,
which the helper replaced, gives ``0o666 & ~umask`` for a new file and leaves
an existing file's mode alone. These tests pin that behaviour:

- a new file honours the umask (022 -> 0644, and 077 -> 0600, which proves
  the umask is applied rather than a hard-coded 0644);
- an overwrite keeps the target's existing mode (0644, 0600 and 0640);
- the same holds through the NAME_MAX-capped temp-name path and through real
  callers (``SemanticCache.dump_stats_json`` and ``tune_threshold --out``).
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cost_optimizer import io_utils as io_mod  # noqa: E402
from cost_optimizer.io_utils import atomic_write_text  # noqa: E402
from cost_optimizer.semantic_cache import (  # noqa: E402
    HashEmbedder,
    InMemoryStorage,
    SemanticCache,
)
from scripts.tune_threshold import main as tune_main  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX permission bits and umask semantics"
)


SetUmask = Callable[[int], None]


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def umask() -> Iterator[Callable[[int], None]]:
    """Set the process umask for one test and restore the previous one."""
    saved = os.umask(0o022)
    os.umask(saved)

    def _set(value: int) -> None:
        os.umask(value)

    try:
        yield _set
    finally:
        os.umask(saved)


def _no_temp_left(directory: Path) -> None:
    leftovers = [p.name for p in directory.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_new_file_honours_umask_022(tmp_path: Path, umask: SetUmask) -> None:
    umask(0o022)
    out = tmp_path / "report.json"
    atomic_write_text(out, "{}")
    assert _mode(out) == 0o644
    _no_temp_left(tmp_path)


def test_new_file_honours_umask_077(tmp_path: Path, umask: SetUmask) -> None:
    """077 must give 0600: a hard-coded 0644 would pass the 022 test above."""
    umask(0o077)
    out = tmp_path / "report.json"
    atomic_write_text(out, "{}")
    assert _mode(out) == 0o600


def test_new_file_matches_write_text(tmp_path: Path, umask: SetUmask) -> None:
    """The mode is the one ``Path.write_text`` gives under the same umask."""
    umask(0o027)
    reference = tmp_path / "reference.txt"
    reference.write_text("x", encoding="utf-8")
    out = tmp_path / "atomic.txt"
    atomic_write_text(out, "x")
    assert _mode(out) == _mode(reference) == 0o640


@pytest.mark.parametrize("existing", [0o644, 0o600, 0o640])
def test_overwrite_keeps_existing_mode(tmp_path: Path, umask: SetUmask, existing: int) -> None:
    umask(0o022)
    out = tmp_path / "report.json"
    out.write_text("old", encoding="utf-8")
    out.chmod(existing)
    atomic_write_text(out, "new")
    assert out.read_text(encoding="utf-8") == "new"
    assert _mode(out) == existing
    _no_temp_left(tmp_path)


def test_overwrite_keeps_mode_through_capped_temp_name(tmp_path: Path, umask: SetUmask) -> None:
    """A basename near NAME_MAX takes the ``_cap_base_for_temp`` branch."""
    umask(0o022)
    out = tmp_path / ("a" * 250)
    out.write_text("old", encoding="utf-8")
    out.chmod(0o640)
    atomic_write_text(out, "new")
    assert out.read_text(encoding="utf-8") == "new"
    assert _mode(out) == 0o640


def test_helper_does_not_touch_the_process_umask(
    tmp_path: Path, umask: SetUmask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the umask needs ``os.umask(0)``, which is process-global; the
    kernel applies it for us when the temp is opened with 0o666."""
    umask(0o022)

    def forbidden(_mask: int) -> int:
        raise AssertionError("atomic_write_text must not call os.umask")

    monkeypatch.setattr(io_mod.os, "umask", forbidden)
    out = tmp_path / "report.json"
    atomic_write_text(out, "{}")
    assert _mode(out) == 0o644


def test_semantic_cache_dump_keeps_mode(tmp_path: Path, umask: SetUmask) -> None:
    """Real library caller: new file 0644 under 022, overwrite keeps 0640."""
    umask(0o022)
    cache = SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage())
    out = tmp_path / "stats.json"
    cache.dump_stats_json(out)
    assert _mode(out) == 0o644
    out.chmod(0o640)
    cache.dump_stats_json(out)
    assert _mode(out) == 0o640


def test_tune_threshold_out_is_umask_mode(tmp_path: Path, umask: SetUmask) -> None:
    """Real script caller: ``tune_threshold --dry --out`` writes a 0644 artifact."""
    umask(0o022)
    stem = tmp_path / "sweep"
    assert tune_main(["--dry", "--out", str(stem)]) == 0
    assert _mode(stem.with_suffix(".json")) == 0o644
