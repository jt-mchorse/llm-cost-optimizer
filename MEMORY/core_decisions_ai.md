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

- id: D-004
  date: 2026-05-15
  decision: semantic_cache_uses_pluggable_embedder_and_storage_protocols_dep_free_defaults
  rationale: portfolio_pattern_single_method_protocol_for_test_substitution_inmemory_storage_keeps_ci_hermetic_redis_optional
  alternatives_rejected: [hard_coded_openai_embedder, hard_coded_redis_storage, hash_only_no_protocol]
  reversibility: cheap
  related_issues: [2, 3, 5]
  superseded_by: null

- id: D-005
  date: 2026-05-15
  decision: cache_keys_include_model_id_separate_entries_per_model
  rationale: same_prompt_to_two_models_must_be_two_entries_otherwise_haiku_response_served_to_opus_caller
  alternatives_rejected: [model_agnostic_keys_global_pool, separate_cache_instance_per_model]
  reversibility: cheap
  related_issues: [2]
  superseded_by: null

- id: D-006
  date: 2026-05-15
  decision: default_similarity_threshold_0_95_high_on_purpose
  rationale: false_positives_user_visible_bugs_false_negatives_just_cache_misses_conservative_default
  alternatives_rejected: [default_0_85_higher_hit_rate_at_quality_cost, default_no_threshold_always_serve_nearest]
  reversibility: cheap
  related_issues: [2]
  superseded_by: null

- id: D-007
  date: 2026-05-15
  decision: false_positive_rate_measured_offline_via_helper_not_online_sampling
  rationale: online_sampling_silently_bleeds_savings_offline_helper_runs_by_operator_explicit_cost
  alternatives_rejected: [online_random_sampling_x_pct, no_false_positive_metric]
  reversibility: cheap
  related_issues: [2, 5]
  superseded_by: null

- id: D-008
  date: 2026-05-16
  decision: escalation_signal_is_one_method_protocol_same_shape_as_tool_reranker_embedder_backend
  rationale: consistent_with_portfolio_seams_lets_consumers_byo_signal_without_inheritance_or_complex_registration
  alternatives_rejected: [abstract_base_class_with_inheritance, callable_function_alias_loses_name_metadata, plugin_registration_via_decorator_too_much_overhead]
  reversibility: cheap
  related_issues: [3]
  superseded_by: null

- id: D-009
  date: 2026-05-16
  decision: router_returns_routerdecision_dataclass_with_signal_values_and_triggered_signal_not_just_model_id_string
  rationale: signal_values_are_telemetry_savings_dashboard_5_needs_them_for_cost_attribution_first_trip_wins_but_all_signals_still_measured
  alternatives_rejected: [return_just_model_id_string, return_tuple_brittle, return_optional_signal_only_if_tripped]
  reversibility: cheap
  related_issues: [3, 5]
  superseded_by: null
