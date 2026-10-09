"""``atomic_write_text`` writes THROUGH a symlinked destination (#279).

``os.replace`` renames onto the link itself, so a symlinked destination used
to become a regular file while the file it pointed at kept its old contents.
``Path.write_text``, which this helper replaced and whose behaviour #243
restored for file mode, writes through the link. The write-through cases run
both writers on identical layouts, so the lock is parity with
``Path.write_text`` rather than a hand-written guess at it. Sibling of
python-async-llm-pipelines#157.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cost_optimizer.io_utils import atomic_write_text  # noqa: E402
from scripts.tune_threshold import main as tune_main  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="symlinks and permission bits are POSIX here"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _layout(root: Path, *, absolute: bool, name: str = "f.md") -> tuple[Path, Path]:
    real_dir = root / "real"
    real_dir.mkdir(parents=True, exist_ok=True)
    real = real_dir / name
    real.write_text("old\n")
    real.chmod(0o640)
    link = root / name
    link.symlink_to(real if absolute else Path("real") / name)
    return link, real


def _write(writer: str, path: Path, text: str) -> None:
    if writer == "atomic":
        atomic_write_text(path, text)
    else:
        path.write_text(text)


@pytest.mark.parametrize("absolute", [False, True], ids=["relative-link", "absolute-link"])
@pytest.mark.parametrize("writer", ["atomic", "write_text"])
def test_write_goes_through_the_link(tmp_path: Path, absolute: bool, writer: str) -> None:
    link, real = _layout(tmp_path, absolute=absolute)
    _write(writer, link, "new\n")
    assert link.is_symlink(), "the link was replaced by a regular file"
    assert real.read_text() == "new\n", "the linked file kept its old contents"
    assert link.read_text() == "new\n"
    # The LINKED file keeps its own mode, as an in-place truncate would.
    assert _mode(real) == 0o640
    # No temp file left behind in either directory.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.md", "real"]
    assert sorted(p.name for p in real.parent.iterdir()) == ["f.md"]


def test_temp_name_cap_applies_through_a_link(tmp_path: Path) -> None:
    """A near-NAME_MAX linked file still writes: the cap sees the resolved name."""
    link, real = _layout(tmp_path, absolute=False, name="r" * 250 + ".md")
    atomic_write_text(link, "new\n")
    assert link.is_symlink()
    assert real.read_text() == "new\n"


@pytest.mark.parametrize("writer", ["atomic", "write_text"])
def test_dangling_link_creates_its_target(tmp_path: Path, writer: str) -> None:
    (tmp_path / "real").mkdir()
    real = tmp_path / "real" / "new.md"
    link = tmp_path / "link.md"
    link.symlink_to(Path("real") / "new.md")
    _write(writer, link, "fresh\n")
    assert link.is_symlink()
    assert real.read_text() == "fresh\n"


@pytest.mark.parametrize("writer", ["atomic", "write_text"])
def test_link_loop_raises_oserror(tmp_path: Path, writer: str) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.symlink_to("b.md")
    b.symlink_to("a.md")
    # ELOOP's strerror on Linux and macOS alike.
    with pytest.raises(OSError, match="symbolic links") as excinfo:
        _write(writer, a, "x\n")
    assert excinfo.value.errno == errno.ELOOP
    assert a.is_symlink()
    assert b.is_symlink()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.md", "b.md"]


def test_plain_destination_is_unchanged_behaviour(tmp_path: Path) -> None:
    dest = tmp_path / "plain.md"
    dest.write_text("old\n")
    dest.chmod(0o640)
    atomic_write_text(dest, "new\n")
    assert not dest.is_symlink()
    assert dest.read_text() == "new\n"
    assert _mode(dest) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["plain.md"]


def test_tune_threshold_out_through_a_link_updates_the_linked_report(
    tmp_path: Path,
) -> None:
    """End to end: ``tune_threshold --out <stem>`` where ``<stem>.json`` is a link."""
    link, real = _layout(tmp_path, absolute=False, name="demo.json")
    assert tune_main(["--dry", "--out", str(tmp_path / "demo")]) == 0
    assert link.is_symlink(), "--out replaced the link with a regular file"
    assert real.read_text() != "old\n", "the linked report kept its old contents"
    assert real.read_text().lstrip().startswith(("{", "["))
    assert _mode(real) == 0o640
