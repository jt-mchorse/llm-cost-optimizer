"""``--launch-streamlit`` opens the URL its own dashboard reports (#259).

The script opened the hard-coded ``http://localhost:8501``. Streamlit moves to
the next free port when 8501 is taken and the port was not set explicitly --
and on a second take it is taken, by the first take's dashboard, which
outlives the script by design. Measured on main: take 1 bound 8501, take 2
bound 8502, and the browser would have opened take 1's dashboard reading take
1's JSON. The child's banner also went to the recorded terminal, External URL
(the operator's public IP) included.

A fake ``streamlit`` on PATH stands in for the real one: it writes the banner
the real one writes and stays up, or dies the way a busy explicit port makes
the real one die.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tests.test_capture_demo_smoke import _load_capture_module

BANNER = """
  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8502
  Network URL: http://192.168.0.2:8502
  External URL: http://203.0.113.7:8502
"""


def _fake_streamlit(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir()
    exe = bin_dir / "streamlit"
    exe.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    exe.chmod(0o755)


@pytest.fixture
def children(monkeypatch: pytest.MonkeyPatch):
    """Every Popen the capture makes, terminated after the test."""
    capture_demo = _load_capture_module()
    made: list[subprocess.Popen[bytes]] = []
    real = subprocess.Popen

    def record(*args, **kwargs):
        child = real(*args, **kwargs)
        made.append(child)
        return child

    monkeypatch.setattr(capture_demo.subprocess, "Popen", record)
    yield capture_demo
    for child in made:
        if child.poll() is None:
            child.kill()
        child.wait()


def _run(capture_demo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opened: list[str]):
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(capture_demo.webbrowser, "open", lambda url: opened.append(url))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = capture_demo.main(
            [
                "--pause-seconds",
                "0",
                "--output-dir",
                str(tmp_path / "out"),
                "--launch-streamlit",
                "--skip-dashboard-cheatsheet",
            ]
        )
    return rc, out.getvalue(), err.getvalue()


def test_opens_the_port_the_child_bound_not_8501(children, tmp_path, monkeypatch) -> None:
    _fake_streamlit(
        tmp_path / "bin",
        f"""
        import sys, time
        print({BANNER!r}, flush=True)
        time.sleep(60)
        """,
    )
    opened: list[str] = []
    rc, out, err = _run(children, tmp_path, monkeypatch, opened)
    assert rc == 0, err
    assert opened == ["http://localhost:8502"]
    assert "on http://localhost:8502" in out


def test_the_childs_banner_stays_out_of_the_recorded_terminal(
    children, tmp_path, monkeypatch
) -> None:
    _fake_streamlit(
        tmp_path / "bin",
        f"""
        import time
        print({BANNER!r}, flush=True)
        time.sleep(60)
        """,
    )
    rc, out, err = _run(children, tmp_path, monkeypatch, [])
    assert rc == 0, err
    assert "203.0.113.7" not in out + err
    assert "External URL" in (tmp_path / "out" / "streamlit.log").read_text()


def test_a_child_that_dies_before_reporting_fails_the_capture(
    children, tmp_path, monkeypatch
) -> None:
    _fake_streamlit(
        tmp_path / "bin",
        """
        import sys
        print("Port 8501 is already in use", flush=True)
        sys.exit(1)
        """,
    )
    opened: list[str] = []
    rc, _out, err = _run(children, tmp_path, monkeypatch, opened)
    assert rc == 2
    assert opened == []
    assert "Port 8501 is already in use" in err


def test_wait_for_local_url_times_out_to_none(tmp_path: Path) -> None:
    capture_demo = _load_capture_module()
    log = tmp_path / "streamlit.log"
    log.write_text("  Network URL: http://192.168.0.2:8501\n")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert capture_demo._wait_for_local_url(child, log, timeout=0.5) is None
    finally:
        child.kill()
        child.wait()


# --- every banner shape Streamlit prints (#331) ---------------------------
#
# `streamlit/web/bootstrap.py::_print_url`: `server.baseUrlPath` appends a path,
# `server.sslCertFile` switches to https, and `browser.serverAddress` or a
# non-wildcard `server.address` prints a single `URL:` line. Each used to time
# out after 30 s, terminate the healthy dashboard, and print the log tail.


def _short_wait(capture_demo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap the 30 s wait so an unmatched banner fails fast instead of slowly."""
    real = capture_demo._wait_for_local_url
    monkeypatch.setattr(
        capture_demo,
        "_wait_for_local_url",
        lambda child, log_path, timeout: real(child, log_path, timeout=min(timeout, 5.0)),
    )


@pytest.mark.parametrize(
    ("line", "url"),
    [
        ("Local URL: http://localhost:8502/demo", "http://localhost:8502/demo"),
        ("Local URL: https://localhost:8502", "https://localhost:8502"),
        ("URL: http://dash.example.com:8502", "http://dash.example.com:8502"),
        ("URL: http://127.0.0.1:8502/demo", "http://127.0.0.1:8502/demo"),
    ],
    ids=["base-url-path", "https", "browser-server-address", "server-address-with-path"],
)
def test_opens_the_url_in_every_banner_shape(
    children, tmp_path, monkeypatch, line: str, url: str
) -> None:
    banner = f"\n  You can now view your Streamlit app in your browser.\n\n  {line}\n"
    if line.startswith("Local"):
        banner += (
            "  Network URL: http://192.168.0.2:8502\n  External URL: http://203.0.113.7:8502\n"
        )
    _fake_streamlit(
        tmp_path / "bin",
        f"""
        import time
        print({banner!r}, flush=True)
        time.sleep(60)
        """,
    )
    _short_wait(children, monkeypatch)
    opened: list[str] = []
    rc, out, err = _run(children, tmp_path, monkeypatch, opened)
    assert rc == 0, err
    assert opened == [url]
    assert "203.0.113.7" not in out + err


def test_a_failing_childs_address_lines_stay_out_of_the_recorded_terminal(
    children, tmp_path, monkeypatch
) -> None:
    """The failure tail is printed; the host's addresses in it must not be."""
    _fake_streamlit(
        tmp_path / "bin",
        """
        import sys
        print("  Network URL: http://192.168.0.2:8502/demo", flush=True)
        print("  External URL: http://203.0.113.7:8502/demo", flush=True)
        print("RuntimeError: dashboard crashed on startup", flush=True)
        sys.exit(1)
        """,
    )
    rc, out, err = _run(children, tmp_path, monkeypatch, [])
    assert rc == 2
    assert "dashboard crashed on startup" in err  # the diagnostic still reaches the operator
    assert "203.0.113.7" not in out + err
    assert "192.168.0.2" not in out + err
    assert "External URL" in (tmp_path / "out" / "streamlit.log").read_text()


@pytest.mark.parametrize(
    "log",
    [
        "  Network URL: http://192.168.0.2:8501\n",
        "  External URL: http://203.0.113.7:8501\n",
        "  Unix Socket: /tmp/streamlit.sock\n",
    ],
    ids=["network-only", "external-only", "unix-socket"],
)
def test_address_lines_are_never_taken_as_the_url(tmp_path: Path, log: str) -> None:
    """Control: widening the match must not pick up a host-address line."""
    capture_demo = _load_capture_module()
    path = tmp_path / "streamlit.log"
    path.write_text(log)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert capture_demo._wait_for_local_url(child, path, timeout=0.3) is None
    finally:
        child.kill()
        child.wait()
