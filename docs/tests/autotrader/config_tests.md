# AutoTrader Config Test Suite

## Purpose

Unit tests for what the AutoTrader pipeline resolves BEFORE a session starts — the settings that
decide whether real orders are sent and what they are expected to cost. Everything here runs
offline with a stubbed fetcher; nothing reaches a venue.

## What Is Tested

### `test_dry_run_resolution.py`

How `dry_run` is resolved across the profile, the broker entry and the user override. The near
miss this pins: a profile declared `dry_run: true`, the override said `false`, and the profile
field — declared, documented and parsed — was read by nothing. A session would have sent real
orders on a funded account with no crash and no warning (#304).

### `test_deployment_declaration.py`

Who may say that a bot's sessions form ONE deployment, and who may only take it away (#497).
Two halves:

- **The loader refuses an undeclared profile.** `deployment.continuous` is the one block with no
  default, because neither value is safe to inherit: forgotten on a deployed profile the history
  is unrecoverable, set wrongly on a one-off profile unrelated runs are welded together. Pinned
  with the refusal message, which has to name what to write.
- **`resolve_deployment()`, every combination.** The profile declares, `--one-off` and
  `--new-deployment` may only narrow. The case that matters most is the one that must NOT work:
  no flag can promote a profile declaring `false`, because an unattended restart re-executes a
  command nobody typed and a command-line deployment would break apart at exactly the restarts it
  exists to span.
- **Two deployments started in the same second get two keys.** The deployment history groups by
  the key alone, so its random half is what keeps two bots a supervisor brings up at once from
  sharing one history.

Plus a sweep over every tracked profile asserting the block is actually there — the loadability
suite would fail too, but as "this file does not parse", which reads like a typo.

### `test_carry_over_declaration.py` (#358)

`carry_over_from` is read from the profile alone: absent it is empty, declared it is read as
written, anything but a name is refused at load, and `app_config.json` has no field that could
declare it for every profile — the boot check reads the raw files and would not see one.

### `test_profile_loadability.py`

Every shipped AutoTrader profile loads. A profile that cannot be constructed is a session that
fails at startup, and the operator finds out at the moment they wanted to trade.

### `test_fee_tier_application.py`

`_apply_fee_tier()` — the bridge between the declared rate and the account's real one (#337).
It has TWO jobs, and the second runs even when the first is switched off:

- **APPLY** — with `auto_detect_fee_tier: true`, the venue's rates replace the declared ones for
  the session.
- **REPORT** — a divergence WARNS either way. Measured 2026-09-08, the declared rates were half
  the account's real tier and nothing said so; the warning is what turns that from invisible
  into a line in the session channel, which reaches the session summary.

| Test | Description |
|------|-------------|
| `test_with_the_switch_on_the_session_prices_with_the_venue_s_rates` | Opt-in → the fetched tier reaches `fee_structure` |
| `test_with_the_switch_off_the_declared_rates_stand` | Default → the config's rates are untouched |
| `test_a_venue_without_tiers_changes_nothing` | Fetcher returns `None` → no change, no warning |
| `test_a_divergence_warns_even_with_the_switch_off` | The report half does not depend on the apply half |
| `test_a_divergence_warns_with_the_switch_on_too` | Applying is not a reason to stay quiet |
| `test_agreement_is_quiet` | Rates that match produce no warning |

### `test_broker_config_boot_line.py` (#608)

The boot's broker-config line shows how long ago the session's symbol was fetched from the venue —
its own entry's `_last_fetched`. The file's age once stood there and said nothing: any symbol's
refresh rewrites the file, so it read 0.0 days beside an entry almost a week old.

| Test | Description |
|------|-------------|
| `test_the_line_names_the_symbol_and_its_age` | ETHUSD fetched 6.9 days ago → `[ETHUSD spec fetched 6.9d ago]` |
| `test_an_entry_without_a_stamp_shows_no_age` | No `_last_fetched` → no age, rather than a guessed one |

### `test_startup_guards.py` (#503, finding 205)

`setup_pipeline` is the one path every AutoTrader session is obliged to walk, and until 2026-09-10 no
test imported it — so every one of its abort conditions was unverified, including the three that
predate this file. A pre-run problem ABORTS an AutoTrader session precisely because a session that
starts wrong cannot be corrected later: it trades, or refuses to trade, for as long as nobody is
watching.

The tests drive real shipped profiles through the real loader into the real `setup_pipeline` and
assert what it REFUSES, which is reachable before the heavy construction.

| Test | What it verifies |
|---|---|
| `TestARestLadderThatNeverGivesUp` | A live session whose broker REST ladder has `attempt_budget` 0 — retry for ever — refuses to start and names the `market_config.json` setting: the session's start and end reads of the venue wait on that ladder, and an outage would hold them indefinitely. A mock session reaches no venue and is not stopped; the shipped Kraken entry declares a finite budget and passes |
| `test_a_profile_that_resolves_no_balances_refuses` | One of the older guards, covered for the first time |
| `test_a_resting_logic_without_a_cold_start_hook_refuses` · `test_and_the_same_profile_passes_the_cold_start_guard` | #493's guard, proven at its WIRING rather than at its rule — the rule has its own suite in `tests/autotrader/cold_start/`; what was never executed is that `setup_pipeline` asks it at all. A bot whose logic can leave an order resting must answer for finding one there after a 03:00 restart, and the boot is the only place that refusal is still cheap. The second test names the refusal that follows the guard today — the mock adapter carries market orders only, so the profile does not start in a mock session until the mock rests orders |

### `test_session_origin.py` (#551)

Where an AutoTrader session says it came from and which code it runs. The AutoTrader header site differs from
the simulation's in one way: the code identity is captured BEFORE the header and kept on the
session, because the startup guard asks it whether real orders would run from uncommitted code —
and that answer must not depend on a run directory having been created. The capture itself is
replaced; its git reads are pinned in `tests/framework/reporting/test_code_identity.py`. Two
tests run the REAL session through the REAL command line, for the two failures that arrive before
the session's loggers exist.

| Test | What it verifies |
|---|---|
| `test_a_session_constructed_in_code_is_direct` | A session nobody declared a channel for states `direct`, with `console` / `operator` / the test host and no override |
| `test_the_declared_channel_and_override_reach_the_header` | `channel` and `allow_dirty` given to `AutotraderMain` are what the header receives |
| `test_the_identity_is_captured_over_the_profile_before_the_header` | The capture runs over the profile's own strategy, before the loggers, and the same object reaches the header |
| `test_the_header_written_at_the_start_holds_origin_and_identity` | `create_autotrader_loggers` writes both blocks into the header at the start, so a session killed before its close still says who started it and which code it ran |
| `test_the_cli_declares_its_channel_and_allows_nothing_by_default` · `test_allow_dirty_is_carried_through` | The CLI declares `cli`; `--allow-dirty` reaches the session and is off unless typed |
| `test_an_accepted_composite_change_reaches_the_header` · `test_accept_composite_change_is_carried_through` | #626 — the key given with `--accept-composite-change` reaches the session from the CLI and the header's `origin.accept_composite_change`, and is null unless typed |
| `test_a_refused_run_directory_is_a_refusal_not_a_crash` | A run directory the operating system refuses ends the CLI with exit code 2 and the message that names the path and the cause, without a stack trace (#621) |
| `test_a_stored_level_refuses_the_start_and_leaves_the_book_alone` | A carry-over still holding a stop or a target ends the real session as `STARTUP FAILED` with exit 2, no stack trace, and the book byte for byte as it was |
| `test_an_untrusted_host_identity_is_a_refusal_not_a_crash` | A host identity file that cannot be trusted ends the CLI with exit code 2 and its own message, without a stack trace — the same shape as the other refusals |
| `test_an_untrusted_host_identity_file_refuses_before_any_capture` | The same through the real `AutotraderMain.run()`, with a broken identity file and isolation lifted for the host identity alone: exit code 2, the file named, no stack trace — and neither the capture nor the header is reached, so no git work is paid and no patch stored for a session that never starts |
| `test_a_capture_that_fails_ends_as_startup_failed_with_a_record` | A capture raising before the loggers exist ends as `STARTUP FAILED`, exit code 2, no stack trace on the console; the header is written with its origin and no code identity, and the global log holds the stack trace |

### `test_rendered_profile.py` (#547)

What an AutoTrader session records as the configuration it RAN with. The profile file is the
input to a cascade: the `app_config.autotrader` defaults lie under it, and every parameter it
leaves unset gets its schema default only when the factory builds the component. The rendered
document is the result, frozen before the header names it — so it is built without constructing
anything, and these tests hold it to what construction then produces.

| Test | What it verifies |
|---|---|
| `test_a_default_no_file_sets_is_in_the_document` | A decision-logic parameter the profile leaves out appears with its schema default |
| `test_the_app_config_layer_is_merged_into_the_blocks` | A block the profile sets in part and one it leaves out entirely both appear complete — the rest can only have come from `app_config`; the file location and the raw strategy are not blocks |
| `test_every_type_key_is_the_one_the_configuration_names` | `decision_logic_type` and each worker's `worker_type` are the keys the configuration names |
| `test_the_broker_entry_is_recorded_with_its_resolved_values` | The broker's `market_config.json` entry is in the document, `dry_run` and `config_mode` included |
| `test_the_operational_hash_can_be_recomputed_from_the_document` | The ledger's `profile_hash` comes out of the document's blocks: one projection (`to_plain`) for the hash and the record, so the two cannot drift apart |
| `test_the_same_profile_renders_the_same_document` | Rendering is deterministic and JSON-serialisable — the precondition for a content id |
| `test_the_rendered_strategy_matches_the_constructed_components` | Over EVERY shipped mock profile: what the renderer records equals what `create_logic` / `create_workers_from_config` build — both go through the factories' one helper, and this is what notices a drift |
| `test_a_parameter_that_moved_after_rendering_refuses_the_session` · `test_a_worker_the_record_does_not_name_refuses_the_session` | The startup check refuses, naming the parameter or the worker set — a record that misstates a real-money session is worse than none |

### `test_uncommitted_code_guard.py` (#551)

A session whose EFFECTIVE `dry_run` resolves to false refuses to start while any repository it
loads code from is dirty, unversioned or unreadable — unless `--allow-dirty` is typed, which the
session log states before the first order and the post-run validation reports as a Tier-1
warning. The parity backtest after a real-money session needs the code that ran, and a strategy can sit
untracked in its own repository, where this repository's commit says nothing about it.

Built on the SimpleNamespace + patched `MarketConfigManager` pattern of
`test_dry_run_resolution.py`: the resolved dry_run is the input, reached by two routes per answer
(broker default, profile field), so a guard reading the profile field instead would fail a cell.
Most code identities are hand-built; the tests that feed the guard a REAL `build_code_identity`
use temporary repositories — the framework repository redirected to one, the git and package
caches cleared before and after — never this working tree, whose state would decide the outcome.

| Test | What it verifies |
|---|---|
| `test_the_guard_decides_on_the_resolved_value` | The full matrix — dry_run by broker / by profile / real, × clean / dirty / unknown / not captured, × strict / `--allow-dirty`. Only real + uncommitted + strict refuses; only real + uncommitted + `--allow-dirty` records an override |
| `test_the_tier_1_warning_exists_only_where_the_override_was_used` | Every cell that starts, handed to `SessionPostRunValidator` as `_shutdown` does: the `uncommitted_code` warning appears in exactly the override cell. A flag typed on a clean tree or a dry run overrode nothing and reports nothing |
| `test_a_mock_session_starts_from_any_tree` | No code state and no profile field makes a mock session fire; the broker default is never even asked |
| `test_every_repository_is_named_with_its_state` · `test_it_says_real_orders_and_why_that_matters` · `test_both_ways_forward_are_offered` | The refusal lists each repository with its state, says real orders and why that matters, and offers both remedies — commit, or the exact command with the session's own profile and `--allow-dirty` |
| `test_a_clean_repository_is_not_asked_to_be_committed` | Only the repositories that block appear in the commit remedy |
| `test_an_unversioned_package_is_named_and_promised_no_patch` · `test_git_unavailable_is_named_as_unknown_not_as_clean` · `test_git_that_could_not_run_is_unknown_never_unversioned` · `test_an_identity_that_was_not_captured_refuses_too` · `test_an_identity_without_a_framework_state_names_no_question_mark` | The states that are not "dirty" each refuse with their own words and their own remedy — unknown is `state unknown (git unavailable or refused)` with the `safe.directory` hint, never "not under version control" and never a question mark — and none is promised a patch that cannot exist |
| `test_a_patch_that_was_not_kept_is_not_promised` · `test_a_patch_that_cannot_cover_a_nested_repository_is_not_promised` | The `--allow-dirty` line promises a patch only where `restorable` holds; elsewhere it names the code nothing can restore and why |
| `test_a_missing_profile_path_renders_a_placeholder` · `test_it_renders_inside_the_startup_failed_block` | The message fits the existing STARTUP FAILED block: every line indented inside it |
| `test_the_session_channel_names_the_patch_before_the_first_order` · `test_the_notice_is_not_a_second_warning` · `test_nothing_reaches_the_global_channel` · `test_a_patch_that_was_not_kept_says_so` · `test_a_strict_start_from_a_clean_tree_says_nothing` | The override's notice: one INFO line in the SESSION channel naming the patch (or saying it was not kept), on the console too, never on the global channel, and never at WARNING — the Tier-1 finding carries the verdict, a WARNING would report it twice |
| `test_the_finding_names_the_code_and_where_to_restore_it` · `test_it_reaches_the_report_as_a_tier_1_row` | The finding is a `setup` warning scoped to the run, names `--allow-dirty` and the patch, and arrives in the report as a Tier-1 row |
| `test_the_shutdown_hands_the_verdict_to_the_post_run_validation` | The CALL SITE: the verdict survives from startup to `_shutdown` and reaches the validator there. A unit test of the check alone cannot show the hand-over exists |
| `test_a_dirty_real_money_start_is_refused_there` · `test_a_clean_start_passes_on_to_the_next_check` | The guard runs inside `_validate_startup`, after the algo-clock and carry-over identity checks and before the swap-mode check |
| `test_a_dirty_framework_repository_refuses_real_orders` · `test_a_committed_tree_starts` | End to end over a REAL capture: a dirty temporary framework repository (a modified and an untracked file, patch stored) refuses with its own changes named and the patch promised; a committed algo package starts |
| `test_no_git_binary_renders_every_repository_as_unknown` | Every git call scripted to fail as a missing binary, the REAL capture run: the framework and a loose package are recorded as unknown, and the refusal renders both as unknown with the `safe.directory` hint |
| `test_a_checkout_git_refuses_is_unknown_and_named_for_safe_directory` | A real "dubious ownership" — an algo checkout chowned to another user (root only, skipped otherwise): unknown rather than unversioned, recorded under the checkout holding the `.git`, and named in the `safe.directory` command |
| `test_real_orders_are_refused_even_with_allow_dirty` · `test_a_dry_run_keeps_running_and_warns_in_the_session_channel` · `test_an_unchanged_tree_says_nothing` · `test_the_refusal_renders_inside_the_startup_failed_block` | A package edited between the capture and the guard: real orders refuse with the moved file named, `--allow-dirty` included; a dry run starts with one WARNING in the session channel; an unchanged package says nothing |

### `test_session_in_a_data_home.py` (#598)

A whole mock session with the code tree and the data home apart, the way a deploy runs one. The
strategy, its stores and its broker cache live in the home; the process runs from the tree, with
both on `PYTHONPATH`. A unit test of the home cannot show this, because the question is whether
every step of a boot looks a strategy up in the same place: a worker named relative to its strategy
file once failed the type check here, while the suite, whose working directory is the home, stayed
green. The fixture strategy under `tests/fixtures/data_home_session/my_algo/` names its worker
relative to its own file and imports a sibling by its package path.

## Why This Matters

These files cover the two questions asked before any money moves: *will this session send
real orders*, and *what does it think a trade costs*. Both were answered wrongly at some point in
this project's history, and neither failure was visible from the outside — one profile field was
read by nobody, one rate was half the truth for an unknown length of time.

## Running the Tests

```bash
pytest tests/autotrader/config/ -v --tb=short
```

VS Code: **"🧩 Pytest: AutoTrader Config (All)"** launch configuration.
