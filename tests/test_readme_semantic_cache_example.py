"""The README's semantic-cache example runs as written (#306).

D-015 (#192) narrowed `SemanticCache.put` to payloads that survive a JSON
round-trip. The README example still passed the SDK response itself:

    response = client.messages.create(...)  # call the model
    cache.put("how do I refund a charge", response, ...)

Measured with anthropic 1.13.0 over an `httpx2.MockTransport`, that is
`ValueError: payload is a Message (...), which has no JSON representation` on
every miss, which is the path that fills the cache. No test ran the block.

The fake response here is what matters about the SDK's `Message`: not a JSON
type, and it has `model_dump(mode="json")`. The repo venv and CI have no
`anthropic`, so this test can't import the real class.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

from cost_optimizer import SemanticCache

README = Path(__file__).resolve().parent.parent / "README.md"

_DUMPED = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Open Billing > Refunds."}],
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


class _SdkLikeMessage:
    """Shaped like `anthropic.types.Message` where `put` can tell."""

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        assert mode == "json", mode
        return {k: v for k, v in _DUMPED.items()}


class _Messages:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, *args: Any, **kwargs: Any) -> _SdkLikeMessage:
        self.calls += 1
        return _SdkLikeMessage()


class _Client:
    def __init__(self) -> None:
        self.messages = _Messages()


def _semantic_cache_block() -> str:
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.DOTALL)
    found = [b for b in blocks if "SemanticCache(" in b and "cache.put(" in b]
    assert len(found) == 1, f"expected one semantic-cache example in README, found {len(found)}"
    return found[0]


def test_the_readme_example_stores_on_a_miss_and_hits_after() -> None:
    client = _Client()
    namespace: dict[str, Any] = {"client": client}
    exec(compile(_semantic_cache_block(), "README.md", "exec"), namespace)  # noqa: S102
    assert client.messages.calls == 1, "the example never reached put -- the arm is vacuous"
    cache = namespace["cache"]
    assert len(cache.storage) == 1
    again = cache.lookup("how do I refund a charge", model="claude-haiku-4-5")
    assert again.hit
    assert again.payload == _DUMPED


def test_the_readme_example_tags_what_its_invalidate_line_drops() -> None:
    client = _Client()
    namespace: dict[str, Any] = {"client": client}
    exec(compile(_semantic_cache_block(), "README.md", "exec"), namespace)  # noqa: S102
    assert namespace["cache"].invalidate(tag="policy") == 1


def test_the_feature_line_names_the_invalidation_the_api_has() -> None:
    bullet = next(
        line
        for line in README.read_text(encoding="utf-8").splitlines()
        if line.startswith("- **Semantic response cache**")
    )
    assert "exact-prompt invalidation" not in bullet
    assert "tag-based invalidation" in bullet
    params = list(inspect.signature(SemanticCache.invalidate).parameters)
    assert params == ["self", "tag"], params
