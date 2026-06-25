"""README ↔ source-constant defaults snapshot (#20).

Sister to `test_savings_snapshot.py` (which locks the measured bench
output — `docs/savings.{json,md}` and the README table cells). This
module closes the orthogonal axis: README claims that quote **source
constants** in prose.

Source of truth is the live source value — if a test fails, update the
README quote to match (not the other way around).

Pairings locked:

1. README per-model pricing (`haiku @ $1/MTok`, `opus @ $5/MTok`) ↔
   `cost_optimizer.pricing.PRICING[<model>].input_per_mtok`.
2. README `BATCH_DISCOUNT_FACTOR = 0.5` (verbatim in prose) ↔
   `cost_optimizer.batch.BATCH_DISCOUNT_FACTOR`.
3. README `pip install -e '.[dev|dashboard|redis]'` ↔ keys under
   `[project.optional-dependencies]` in `pyproject.toml`.
4. README `LIVE_CACHE_BUDGET_USD` default `$0.10` ↔ the fallback in
   `tests/integration/test_live_cache.py::_DEFAULT_BUDGET_USD`.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

from cost_optimizer.batch import BATCH_DISCOUNT_FACTOR
from cost_optimizer.pricing import get_pricing

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
LIVE_TEST = REPO_ROOT / "tests" / "integration" / "test_live_cache.py"

REGEN_HINT = (
    "Source is the truth: update the README quote to match the new live "
    "value (not the other way around)."
)


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_quotes_live_opus_input_price() -> None:
    """README's `claude-opus-4-7 @ $N/MTok input` must equal
    `PRICING['claude-opus-4-7'].input_per_mtok`."""
    body = _readme()
    match = re.search(r"`claude-opus-4-7`\s*@\s*\$([\d.]+)/MTok\s+input", body)
    assert match, (
        "README Savings-dashboard section must quote opus input price as "
        "'`claude-opus-4-7` @ $<N>/MTok input' so this snapshot can lock it."
    )
    readme_v = float(match.group(1))
    live_v = get_pricing("claude-opus-4-7").input_per_mtok
    assert readme_v == live_v, (
        f"README quotes claude-opus-4-7 input price as ${readme_v}/MTok but "
        f"cost_optimizer.pricing.get_pricing('claude-opus-4-7').input_per_mtok = "
        f"{live_v}. {REGEN_HINT}"
    )


def test_readme_quotes_live_haiku_input_price() -> None:
    """README's `claude-haiku-4-5 @ $N/MTok input` must equal
    `PRICING['claude-haiku-4-5'].input_per_mtok`."""
    body = _readme()
    match = re.search(r"`claude-haiku-4-5`\s*@\s*\$([\d.]+)/MTok\s+input", body)
    assert match, (
        "README Savings-dashboard section must quote haiku input price as "
        "'`claude-haiku-4-5` @ $<N>/MTok input' so this snapshot can lock it."
    )
    readme_v = float(match.group(1))
    live_v = get_pricing("claude-haiku-4-5").input_per_mtok
    assert readme_v == live_v, (
        f"README quotes claude-haiku-4-5 input price as ${readme_v}/MTok but "
        f"cost_optimizer.pricing.get_pricing('claude-haiku-4-5').input_per_mtok = "
        f"{live_v}. {REGEN_HINT}"
    )


def test_readme_batch_discount_factor_matches_module_constant() -> None:
    """README's `BATCH_DISCOUNT_FACTOR = 0.5` quote (Batch-API section)
    must equal `cost_optimizer.batch.BATCH_DISCOUNT_FACTOR`."""
    body = _readme()
    match = re.search(r"`BATCH_DISCOUNT_FACTOR\s*=\s*([\d.]+)`", body)
    assert match, (
        "README Batch-API section must quote the discount as "
        "'`BATCH_DISCOUNT_FACTOR = <N>`' so this snapshot can lock it."
    )
    readme_v = float(match.group(1))
    assert readme_v == BATCH_DISCOUNT_FACTOR, (
        f"README quotes BATCH_DISCOUNT_FACTOR = {readme_v} but "
        f"cost_optimizer.batch.BATCH_DISCOUNT_FACTOR = {BATCH_DISCOUNT_FACTOR}. "
        f"{REGEN_HINT}"
    )


def test_readme_pip_extras_all_exist_in_pyproject() -> None:
    """Every `pip install -e '.[<extra>]'` quoted in the README must be a
    declared optional-dependencies key."""
    body = _readme()
    quoted = set(re.findall(r"pip install -e '\.\[([^\]]+)\]'", body))
    assert quoted, (
        "README must quote at least one `pip install -e '.[<extra>]'` "
        "command for this test to lock anything."
    )
    with PYPROJECT.open("rb") as fh:
        pyproject = tomllib.load(fh)
    live = set(pyproject.get("project", {}).get("optional-dependencies", {}).keys())
    missing = sorted(quoted - live)
    assert not missing, (
        f"README quotes `pip install -e '.[{','.join(missing)}]'` but "
        f"{missing} are not keys under [project.optional-dependencies] in "
        f"pyproject.toml (live keys: {sorted(live)}). {REGEN_HINT}"
    )


def test_readme_live_cache_budget_default_matches_integration_test() -> None:
    """README claims `LIVE_CACHE_BUDGET_USD` default `$0.10`. Must equal
    `_DEFAULT_BUDGET_USD` fallback in tests/integration/test_live_cache.py."""
    body = _readme()
    # Find both budget mentions; assert they agree, then compare against live.
    matches = re.findall(r"`LIVE_CACHE_BUDGET_USD`[^.]*?default\s*[`$]+\s*\$?([\d.]+)`?", body)
    assert matches, (
        "README must quote the live-cache budget guardrail in the form "
        "'`LIVE_CACHE_BUDGET_USD` (default `$0.10`)' so this snapshot can lock it."
    )
    readme_vals = {float(v) for v in matches}
    assert len(readme_vals) == 1, (
        f"README quotes the LIVE_CACHE_BUDGET_USD default inconsistently: "
        f"{sorted(readme_vals)}. Pick one value and align both Quickstart + "
        f"What-this-is mentions."
    )
    readme_v = readme_vals.pop()

    live_src = LIVE_TEST.read_text(encoding="utf-8")
    live_match = re.search(
        r'_DEFAULT_BUDGET_USD\s*=\s*float\(os\.environ\.get\("LIVE_CACHE_BUDGET_USD",\s*"([\d.]+)"\)\)',
        live_src,
    )
    assert live_match, (
        "Could not locate the _DEFAULT_BUDGET_USD fallback in "
        "tests/integration/test_live_cache.py. Has the env-var lookup moved? "
        "Update this test's regex."
    )
    live_v = float(live_match.group(1))
    assert readme_v == live_v, (
        f"README quotes LIVE_CACHE_BUDGET_USD default as ${readme_v} but "
        f"tests/integration/test_live_cache.py::_DEFAULT_BUDGET_USD falls "
        f"back to ${live_v}. {REGEN_HINT}"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(__import__("pytest").main([__file__, "-v"]))
