"""Production cost-reduction toolkit for LLM workloads."""

from cost_optimizer.cache_wrapper import (
    CacheTelemetry,
    CallResult,
    PromptCacheWrapper,
)
from cost_optimizer.pricing import ModelPricing, get_pricing
from cost_optimizer.router import (
    CheapAdapter,
    EntropySignal,
    EscalationSignal,
    JudgeConfidenceSignal,
    RouterDecision,
    SignalReading,
    UncertaintyRouter,
)
from cost_optimizer.semantic_cache import (
    CacheLookupResult,
    CacheRecord,
    CacheStats,
    Embedder,
    FalsePositiveSample,
    HashEmbedder,
    InMemoryStorage,
    RedisStorage,
    SemanticCache,
    Storage,
    cosine,
    measure_false_positive_rate,
)

__all__ = [
    # Prompt-cache layer (#1)
    "CacheTelemetry",
    "CallResult",
    "ModelPricing",
    "PromptCacheWrapper",
    "get_pricing",
    # Semantic cache layer (#2)
    "CacheLookupResult",
    "CacheRecord",
    "CacheStats",
    "Embedder",
    "FalsePositiveSample",
    "HashEmbedder",
    "InMemoryStorage",
    "RedisStorage",
    "SemanticCache",
    "Storage",
    "cosine",
    "measure_false_positive_rate",
    # Model routing layer (#3)
    "CheapAdapter",
    "EntropySignal",
    "EscalationSignal",
    "JudgeConfidenceSignal",
    "RouterDecision",
    "SignalReading",
    "UncertaintyRouter",
]

__version__ = "0.0.3"
