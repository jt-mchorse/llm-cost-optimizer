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
