# AutoTrader Config Test Suite

## Purpose

Unit tests for what the live pipeline resolves BEFORE a session starts — the settings that
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
- **`_resolve_deployment()`, every combination.** The profile declares, `--one-off` and
  `--new-deployment` may only narrow. The case that matters most is the one that must NOT work:
  no flag can promote a profile declaring `false`, because an unattended restart re-executes a
  command nobody typed and a command-line deployment would fragment at exactly the restarts it
  exists to span.

Plus a sweep over every tracked profile asserting the block is actually there — the loadability
suite would fail too, but as "this file does not parse", which reads like a typo.

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
  into a line in the session channel (§35).

| Test | Description |
|------|-------------|
| `test_with_the_switch_on_the_session_prices_with_the_venue_s_rates` | Opt-in → the fetched tier reaches `fee_structure` |
| `test_with_the_switch_off_the_declared_rates_stand` | Default → the config's rates are untouched |
| `test_a_venue_without_tiers_changes_nothing` | Fetcher returns `None` → no change, no warning |
| `test_a_divergence_warns_even_with_the_switch_off` | The report half does not depend on the apply half |
| `test_a_divergence_warns_with_the_switch_on_too` | Applying is not a reason to stay quiet |
| `test_agreement_is_quiet` | Rates that match produce no warning |

### `test_startup_guards.py` (#503, finding 205)

`setup_pipeline` is the one path every live session is obliged to walk, and until 2026-09-10 no
test imported it — so every one of its abort conditions was unverified, including the three that
predate this file. §35 puts pre-run problems in the ABORT class precisely because a session that
starts wrong cannot be corrected later: it trades, or refuses to trade, for as long as nobody is
watching.

The tests drive real shipped profiles through the real loader into the real `setup_pipeline` and
assert what it REFUSES, which is reachable before the heavy construction.

| Test | What it verifies |
|---|---|
| `test_a_live_session_refuses_to_start` | A profile-wide `venue_held_protection` opt-in against a venue that cannot carry one aborts the session. Left to the submit path it would reject every protected entry one at a time, and each would read as an isolated incident rather than one wrong line in the profile |
| `test_the_refusal_names_both_sides` | The message names the profile switch AND the broker, and says the per-order route is refused the same way |
| `test_a_mock_rehearsal_is_not_stopped` | The refusal is LIVE-only. A mock session builds a `MockBrokerAdapter` whatever its `broker_type` says, so it can never carry a protective order — refusing there would make an opted-in profile unrehearsable, the same mistake the simulation avoids by accepting the flag and changing nothing. It warns once and ignores the switch for the run |
| `test_the_switch_is_off_by_default` | A profile that never mentions it starts against any venue. Opting in changes what the bot does with real money, so a session must never acquire the behaviour by accident |
| `test_a_profile_that_resolves_no_balances_refuses` | One of the older guards, covered for the first time |
| `test_a_resting_logic_without_a_cold_start_hook_refuses` · `test_and_the_same_profile_starts_with_it` | #493's guard, proven at its WIRING rather than at its rule — the rule has its own suite in `tests/autotrader/cold_start/`; what was never executed is that `setup_pipeline` asks it at all. A bot whose logic can leave an order resting must answer for finding one there after a 03:00 restart, and the boot is the only place that refusal is still cheap |

### `test_session_origin.py` (#551)

Where a live session says it came from and which code it runs. The live header site differs from
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
| `test_an_untrusted_host_identity_is_a_refusal_not_a_crash` | A host identity file that cannot be trusted ends the CLI with exit code 2 and its own message, without a stack trace — the same shape as the other refusals |
| `test_an_untrusted_host_identity_file_refuses_before_any_capture` | The same through the real `AutotraderMain.run()`, with a broken identity file and isolation lifted for the host identity alone: exit code 2, the file named, no stack trace — and neither the capture nor the header is reached, so no git work is paid and no patch stored for a session that never starts |
| `test_a_capture_that_fails_ends_as_startup_failed_with_a_record` | A capture raising before the loggers exist ends as `STARTUP FAILED`, exit code 2, no stack trace on the console; the header is written with its origin and no code identity, and the global log holds the stack trace |

### `test_uncommitted_code_guard.py` (#551)

A session whose EFFECTIVE `dry_run` resolves to false refuses to start while any repository it
loads code from is dirty, unversioned or unreadable — unless `--allow-dirty` is typed, which the
session log states before the first order and the post-run validation reports as a Tier-1
warning. The parity backtest after a live run needs the code that ran, and a strategy can sit
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
