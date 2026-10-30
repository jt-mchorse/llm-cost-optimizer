"""``atomic_write_text`` refuses a read-only existing destination (#377).

``os.replace`` swaps the directory entry, so the target's own write bit was
never consulted and a ``chmod 444`` file was silently rewritten -- while
``Path.write_text``, the call the helper replaced, raises ``PermissionError``.
The ``--out`` write seams in ``bench_savings`` and ``tune_threshold`` document
an unwritable output as a clean ``::error::`` line and exit 2. Helper cases run
both writers on the same layout, so the lock is parity with
``Path.write_text``. Sibling of prompt-regression-suite#316.

Root bypasses permission bits (``Path.write_text`` succeeds too), so these
skip as root. GitHub's ubuntu runners run as the non-root ``runner`` user, so
CI exercises them.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cost_optimizer.io_utils import atomic_write_text  # noqa: E402
from scripts._io import artifact_path  # noqa: E402
from scripts.bench_savings import main as bench_main  # noqa: E402
from scripts.tune_threshold import main as tune_main  # noqa: E402

pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits"),
    pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses file permission bits, so a 0444 file stays writable",
    ),
]


def _write(writer: str, path: Path, text: str) -> None:
    if writer == "atomic":
        atomic_write_text(path, text)
    else:
        path.write_text(text, encoding="utf-8")


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def frozen(tmp_path: Path) -> Iterator[Path]:
    """A 0444 file holding ``old``; made writable again for tmp_path cleanup."""
    path = tmp_path / "r.json"
    path.write_text("old\n", encoding="utf-8")
    path.chmod(0o444)
    yield path
    path.chmod(0o644)


@pytest.mark.parametrize("writer", ["atomic", "write_text"])
def test_readonly_file_is_refused(frozen: Path, writer: str) -> None:
    with pytest.raises(PermissionError) as excinfo:
        _write(writer, frozen, "new\n")
    assert excinfo.value.errno == errno.EACCES
    assert frozen.read_text(encoding="utf-8") == "old\n"
    assert _mode(frozen) == 0o444


def test_refusal_leaves_no_temp_file(frozen: Path) -> None:
    with pytest.raises(PermissionError):
        atomic_write_text(frozen, "new\n")
    assert sorted(p.name for p in frozen.parent.iterdir()) == ["r.json"]


@pytest.mark.parametrize("writer", ["atomic", "write_text"])
def test_readonly_file_behind_a_symlink_is_refused(tmp_path: Path, writer: str) -> None:
    # The check runs on the RESOLVED file (#279): a link name over a read-only
    # file must not get through.
    real = tmp_path / "real.md"
    real.write_text("old\n", encoding="utf-8")
    real.chmod(0o444)
    link = tmp_path / "link.md"
    link.symlink_to(real)
    try:
        with pytest.raises(PermissionError):
            _write(writer, link, "new\n")
        assert real.read_text(encoding="utf-8") == "old\n"
        assert link.is_symlink()
    finally:
        real.chmod(0o644)


@pytest.mark.parametrize("writer", ["atomic", "write_text"])
def test_writable_and_missing_targets_still_write(tmp_path: Path, writer: str) -> None:
    # Controls: the refusal is scoped to an existing unwritable file.
    owner_only = tmp_path / "owner.md"
    owner_only.write_text("old\n", encoding="utf-8")
    owner_only.chmod(0o600)
    _write(writer, owner_only, "new\n")
    assert owner_only.read_text(encoding="utf-8") == "new\n"
    assert _mode(owner_only) == 0o600

    missing = tmp_path / "new.md"
    _write(writer, missing, "fresh\n")
    assert missing.read_text(encoding="utf-8") == "fresh\n"


def test_bench_savings_out_on_a_readonly_json_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stem = tmp_path / "savings"
    out_json = artifact_path(stem, ".json")
    out_json.write_text("old\n", encoding="utf-8")
    out_json.chmod(0o444)
    try:
        rc = bench_main(["--dry", "--n", "50", "--out", str(stem)])
        err = capsys.readouterr().err
        assert rc == 2
        assert "::error::could not write bench artifacts" in err
        assert "Traceback" not in err
        assert out_json.read_text(encoding="utf-8") == "old\n"
    finally:
        out_json.chmod(0o644)


def test_tune_threshold_out_on_a_readonly_json_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stem = tmp_path / "sweep"
    out_json = artifact_path(stem, ".json")
    out_json.write_text("old\n", encoding="utf-8")
    out_json.chmod(0o444)
    try:
        rc = tune_main(["--dry", "--out", str(stem), "--thresholds", "0.5,1.5"])
        err = capsys.readouterr().err
        assert rc == 2
        assert "::error::could not write sweep artifacts" in err
        assert out_json.read_text(encoding="utf-8") == "old\n"
    finally:
        out_json.chmod(0o644)
