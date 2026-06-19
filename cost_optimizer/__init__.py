"""Production cost-reduction toolkit for LLM workloads."""

from cost_optimizer.batch import (
    BATCH_DISCOUNT_FACTOR,
    AnthropicBatchBackend,
    BatchBackend,
    BatchCostQuote,
    BatchJobMeta,
    BatchRequest,
    BatchResultRow,
    CostComparison,
    IdempotencyConflict,
    InMemoryBatchBackend,
    JobNotComplete,
    JobNotFound,
    compare_realtime_vs_batch,
)
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
    RouterStats,
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
    "RouterStats",
    "SignalReading",
    "UncertaintyRouter",
    # Batch API layer (#4)
    "AnthropicBatchBackend",
    "BATCH_DISCOUNT_FACTOR",
    "BatchBackend",
    "BatchCostQuote",
    "BatchJobMeta",
    "BatchRequest",
    "BatchResultRow",
    "CostComparison",
    "IdempotencyConflict",
    "InMemoryBatchBackend",
    "JobNotComplete",
    "JobNotFound",
    "compare_realtime_vs_batch",
]

__version__ = "0.0.3"
