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

- id: D-010
  date: 2026-05-16
  decision: batch_idempotency_is_caller_key_plus_content_hash_conflict_raises_not_overwrites
  rationale: same_payload_same_key_returns_existing_job_id_for_retries_different_payload_same_key_must_raise_so_a_caller_accidentally_reusing_a_key_for_a_different_workload_fails_loud_instead_of_silently_double_charging_content_hash_is_request_count_custom_ids_prompts_model_max_tokens_system
  alternatives_rejected: [server_generated_idempotency_keys_couples_to_anthropic_specific_endpoint, key_only_no_content_hash_silent_overwrite_risk, content_hash_only_no_caller_key_cant_be_supplied_before_payload_is_known]
  reversibility: cheap
  related_issues: [4]
  superseded_by: null

- id: D-011
  date: 2026-05-17
  decision: savings_dashboard_is_streamlit_behind_dashboard_optional_extra_reads_bench_json
  rationale: portfolio_pattern_optional_extras_mirrors_redis_d_004_core_package_stays_dep_free_dashboard_does_no_recomputation_so_table_and_dashboard_never_drift_file_on_disk_is_source_of_truth
  alternatives_rejected: [next_js_dashboard_doubles_build_matrix_for_python_lib, static_html_report_loses_interactivity_per_acceptance_criteria, dashboard_recomputes_savings_from_workload_inline_would_drift_from_committed_bench_artifacts]
  reversibility: cheap
  related_issues: [5]
  superseded_by: null

- id: D-012
  date: 2026-05-17
  decision: bench_workload_is_hermetic_synthetic_with_documented_60_30_10_split_not_hf_dataset_slice
  rationale: ci_proves_plumbing_and_math_d_007_posture_extended_real_api_path_unimplemented_same_as_tune_threshold_operator_runs_against_real_data_and_commits_docs_savings_real_md_workload_committed_at_docs_savings_workload_json_so_numbers_re_derivable
  alternatives_rejected: [hf_dataset_slice_at_bench_time_breaks_hermetic_ci_and_introduces_network_dep, fabricated_savings_numbers_in_readme_violates_handoff_section_10_no_fabricated_benchmarks, real_api_mode_in_this_pr_requires_anthropic_key_and_budget_not_appropriate_for_ci]
  reversibility: cheap
  related_issues: [5]
  superseded_by: null
