# Core Decisions (AI-readable, YAML, append-only)
# Schema: see .skills/portfolio-memory/SKILL.md

- id: D-001
  date: 2026-05-10
  decision: scope_per_portfolio_handoff_section_2
  rationale: locked_scope_prevents_drift
  alternatives_rejected: []
  reversibility: expensive
  related_issues: []
  superseded_by: null

- id: D-002
  date: 2026-05-14
  decision: dep_free_wrapper_layer_anthropic_sdk_duck_typed
  rationale: importable_without_api_key_hermetic_in_ci_no_sdk_dep_imposed_on_consumers
  alternatives_rejected: [hard_import_anthropic_sdk, vendor_a_typed_protocol_module]
  reversibility: cheap
  related_issues: [1]
  superseded_by: null

- id: D-003
  date: 2026-05-14
  decision: in_repo_pricing_table_unknown_models_raise
  rationale: savings_math_must_be_traceable_to_documented_rate_never_fabricated
  alternatives_rejected: [fetch_pricing_from_anthropic_at_runtime, infer_pricing_from_model_name_prefix]
  reversibility: cheap
  related_issues: [1]
  superseded_by: null
