# Session History (AI-readable, append-only)

Schema: see .skills/portfolio-memory/SKILL.md

---
session: 2026-05-14T09:52:00Z
duration_min: 65
issue: 1
focus: anthropic_prompt_caching_wrapper
delta:
  files_changed: 9
  tests_added: 18
  coverage_pct: 96
  benchmarks: {}   # no live-API benchmark this session; math is unit-tested against published multipliers
context_for_next_session:
  - real_api_integration_test_pending_followup_issue_to_be_filed
  - savings_dashboard_blocked_on_layer_2_3_landing_first
  - pyproject_uses_hatchling_matching_llm_eval_harness_pattern
  - duck_typed_anthropic_client_means_no_sdk_install_needed_for_tests
decisions_made: [D-002, D-003]
followups: []
---

---
session: 2026-05-15T15:47Z
duration_min: 70
issue: 2
focus: semantic_response_cache_with_pluggable_embedder_and_storage
delta:
  files_added: 1
  files_changed: 4
  tests_added: 35
  test_pass_rate: "53/53"
context_for_next_session:
  - semantic_cache_layer_shipped_embedder_protocol_storage_protocol
  - inmemory_storage_default_redisstorage_behind_redis_extra_lazy_imported
  - hashembedder_dep_free_fallback_for_hermetic_ci_real_embedders_byo_via_protocol
  - threshold_default_0_95_d_006_conservative_fp_better_than_fn
  - false_positive_measurement_offline_helper_d_007_not_online_sampling
  - 1000_row_hit_rate_benchmark_intentionally_deferred_to_issue_5_savings_dashboard
  - fakeredis_in_dev_extra_so_redisstorage_tests_run_without_a_redis_container
decisions_made: [D-004, D-005, D-006, D-007]
followups: []
---

---
session: 2026-05-16T04:11Z
duration_min: 35
issue: 3
focus: uncertainty_routed_cheap_to_strong_model_fallback
delta:
  files_added: 3
  files_changed: 2
  tests_added: 24
  test_pass_rate: "77/77"
context_for_next_session:
  - router_module_lives_at_cost_optimizer_router_ts_with_protocol_escalationsignal_d_008
  - two_signals_ship_entropysignal_shannon_over_first_token_logprobs_judgeconfidencesignal_calls_eval_harness_judge
  - routerdecision_dataclass_with_model_id_triggered_signal_signal_values_cheap_response_d_009
  - first_trip_wins_but_all_signals_still_measured_for_telemetry
  - scripts_tune_threshold_py_sweeps_entropy_thresholds_writes_json_and_optional_matplotlib_plot
  - dry_mode_default_uses_stub_adapter_and_5_row_canned_dataset_real_api_mode_explicitly_documented_as_not_implemented_in_this_pr
  - no_fabricated_benchmarks_quality_at_80_20_verification_requires_operator_run
  - 18_router_tests_plus_6_tune_threshold_tests_24_total_new_77_77_overall
  - cross_repo_seam_judgeconfidencesignal_takes_eval_harness_judge_object_duck_typed
  - readme_model_routing_3_section_added_under_quickstart_architecture_md_pending_followup
decisions_made: [D-008, D-009]
followups: []
---

---
session: 2026-05-16T20:30Z
duration_min: 55
issue: 4
focus: anthropic_batch_api_wrapper_with_idempotency_and_cost_comparison
delta:
  files_added: 2  # cost_optimizer/batch.py, tests/test_batch.py
  files_changed: 2  # __init__.py, README.md
  tests_added: 28
  test_pass_rate: "105/105"
context_for_next_session:
  - batch_module_at_cost_optimizer_batch_inmemory_backend_plus_anthropic_backend_duck_typed_per_d_002
  - lifecycle_submit_poll_results_status_pending_in_progress_ended_succeeded_failed_canceled
  - idempotency_caller_key_plus_content_hash_d_010_same_payload_same_key_returns_existing_job_different_payload_same_key_raises
  - content_hash_is_request_count_custom_ids_prompts_model_max_tokens_system_order_sensitive
  - cost_compare_uses_documented_batch_discount_factor_0_5_caller_supplies_prices_no_defaults_shipped_per_d_003
  - cost_compare_skips_failed_rows_supports_multi_model_via_model_of_kwarg
  - anthropic_backend_takes_pre_constructed_client_d_002_duck_typed_messages_batches_surface
  - 28_new_tests_lifecycle_idempotency_validation_cost_math_anthropic_via_fake_client
  - issue_4_acceptance_submit_poll_result_lifecycle_done_idempotency_keys_done_cost_report_done
  - real_api_smoke_against_anthropic_batch_intentionally_out_of_ci_scope_no_api_key_budget
decisions_made: [D-010]
followups: []
---

---
session: 2026-05-17T23:30Z
duration_min: 70
issue: 5
focus: savings_dashboard_five_strategy_bench_plus_streamlit
delta:
  files_added: 6  # scripts/bench_savings.py, dashboard/__init__.py, dashboard/app.py, tests/test_bench_savings.py, docs/savings.json, docs/savings.md (+ docs/savings_workload.json)
  files_changed: 2  # README.md, pyproject.toml
  tests_added: 18
  test_pass_rate: "122/122 + 1 skipped"
  benchmarks:
    workload_rows: 500
    workload_mix: { redundant: 300, easy: 150, hard: 50 }
    baseline_usd: 0.0577
    prompt_cache_saved_pct: 0.840
    semantic_cache_saved_pct: 0.562
    semantic_cache_hit_rate: 0.56
    router_saved_pct: -1.548  # negative by design — buys quality
    router_quality_lift: 0.035  # 0.886 → 0.921
    batch_saved_pct: 0.500
context_for_next_session:
  - savings_dashboard_5_shipped_ends_priority_med_queue_for_this_repo
  - bench_savings_py_runs_500_row_synthetic_workload_60_30_10_split_hermetic_deterministic
  - workload_committed_at_docs_savings_workload_json_so_numbers_re_derivable
  - streamlit_dashboard_is_optional_extra_dashboard_pattern_mirrors_redis_d_004
  - router_shows_negative_dollar_savings_by_design_d_011_honest_writeup_in_readme_pairs_with_cache_layer
  - real_api_savings_mode_unimplemented_same_posture_as_tune_threshold_d_007
  - five_strategies_bench_baseline_prompt_cache_semantic_cache_router_batch_all_use_real_pricing_table
  - cumulative_savings_per_row_computed_independently_from_strategy_summary_two_derivations_reconciled_in_tests
  - quality_maintained_check_tolerates_0_01_drift_router_only_strategy_that_changes_quality_in_this_workload
  - readme_table_replaces_benchmarks_pending_placeholder_with_real_measured_numbers
  - bench_inputs_only_no_output_tokens_in_savings_axis_documented_inline
  - all_remaining_priority_low_open_issues_on_this_repo_only_no_more_priority_med
decisions_made: [D-011, D-012]
followups: []
---

---
session: 2026-05-18T05:00Z
duration_min: 25
issue: 7
focus: live_api_integration_test_for_prompt_cache_wrapper
delta:
  files_changed: 4
  tests_added: 3
context_for_next_session:
  - tests_integration_test_live_cache_py_gated_on_anthropic_api_key_module_level_skip
  - budget_guardrail_live_cache_budget_usd_default_010
  - integration_workflow_dispatch_only_never_push_pr
  - main_pytest_invocation_still_cost_free_122_passed_1_skipped
  - no_new_d_entry_gating_pattern_not_a_tradeoff
decisions_made: []
followups: []
---

---
session: 2026-05-18T15:37Z
duration_min: 30
issue: 13
focus: architecture_doc_covers_all_five_shipped_layers
delta:
  files_changed: 2  # README.md, docs/architecture.md
  files_added: 0
  tests_added: 0   # pure docs
  test_pass_rate: "124/124 + 1 skipped"
context_for_next_session:
  - docs_architecture_md_rewritten_six_sections_one_per_shipped_layer_plus_integrated_top_diagram_mermaid_labels_with_parens_are_quoted_double_quotes_to_avoid_parser_choke
  - readme_architecture_section_no_longer_stub_one_line_summary_points_at_doc
  - quality_bar_section_1_architecture_diagram_requirement_now_met_was_previously_partial
  - no_new_d_entry_pure_docs_references_d_002_through_d_012
  - live_api_section_6_documents_workflow_dispatch_gating_pattern_for_reuse_across_portfolio
decisions_made: []
followups: []
---

---
session: 2026-05-18T19:50Z
duration_min: 30
issue: 15
focus: snapshot_test_locks_docs_savings_json_md_and_readme_table_to_bench_output
delta:
  files_added: 1   # tests/test_savings_snapshot.py
  files_changed: 1  # README.md (drop stale 122-test number, replace with non-specific)
  tests_added: 8   # 3 fixed + 5 parametrized strategy rows + 1 row-count guard
  test_pass_rate: "130/130 + 1 skipped"
context_for_next_session:
  - snapshot_test_three_planes_run_bench_payload_eq_savings_json_format_markdown_eq_savings_md_readme_table_cell_eq_savings_json
  - readme_strategy_name_match_is_substring_keyword_so_cosmetic_renames_allowed_numeric_cells_locked
  - readme_percent_tolerance_5e_3_one_decimal_place_rounding_dollars_5e_5_four_decimals_quality_5e_4_three_decimals
  - failure_messages_on_every_assertion_print_regen_command_python_scripts_bench_savings_dry_out_docs_savings
  - readme_122_test_count_replaced_with_non_specific_to_avoid_future_bitrot_same_treatment_as_llm_eval_harness_session_2026_05_18T19_30
  - tampered_savings_json_total_usd_999_verified_fires_first_test_with_regen_hint_visible
  - no_new_d_entry_snapshot_pattern_directly_enforces_d_012_synthetic_workload_no_fabricated_numbers
decisions_made: []
followups: []
---

---
session: 2026-05-19T05:15Z
duration_min: 30
issue: 17
focus: drop_future_layers_framing_plus_extend_snapshot_test
delta:
  files_changed: 2   # README.md, tests/test_savings_snapshot.py
  files_added: 0
  tests_added: 3
  test_pass_rate: "133/133"
context_for_next_session:
  - readme_what_this_is_fourth_paragraph_rewritten_to_six_bullet_present_tense_layer_picture
  - demo_section_replaces_bare_pending_with_two_command_hermetic_demo_plus_followup_issue_18
  - snapshot_test_extended_to_lock_future_layers_string_absence_plus_demo_section_invariant
  - tamper_verified_reinjecting_future_layers_fires_snapshot
  - sister_to_eight_other_portfolio_readme_drift_prs_landed_yesterday_or_today_pattern_complete
  - issue_18_filed_for_captured_demo_asset_priority_low
decisions_made: []
followups: ["#18"]
---
