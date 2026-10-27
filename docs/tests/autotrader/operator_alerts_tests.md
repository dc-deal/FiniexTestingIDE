# Operator Alerts Tests (#235)

An unattended session is only as good as what reaches the operator when it goes wrong — and as
what reaches the monitor when nothing can be sent at all. This suite holds the alert channel, its
dispatcher, the liveness ping and the texts to that: the tick loop never waits on a network, an
outage costs one message while a refusal closes the channel, a flapping symptom is one alert
episode rather than a storm, the ping vouches for the LOOP rather than the process — and for no
loop that never ran — a stalled loop reaches the operator even with the ping off, nothing on the
two threads ends them, and no token or ping URL ever appears in a message or a log line. No test
touches the network, and a test run never reads the operator's own credentials: the channel and
the monitor are stand-ins that keep what they were sent.

| Item | Value |
|---|---|
| Suite path | [tests/autotrader/operator_alerts/](../../../tests/autotrader/operator_alerts/) |
| Shared stand-ins | [operator_alert_doubles.py](../../../tests/autotrader/operator_alerts/operator_alert_doubles.py) — a scripted channel and a monitor · [tests/shared/operator_alert_recorder.py](../../../tests/shared/operator_alert_recorder.py) — for the loop stubs elsewhere |
| Profile | none of its own — the session tests replay the shortest mock profile and swap the channel builder |
| Pytest mark | `autotrader` (auto-applied via path) |
| Launch entries | `🧩 Pytest: Operator Alerts (All)` · `🤖 AutoTrader: Operator alerts — check` · `🤖 AutoTrader: Operator alerts — find the chat id` |
| Architecture doc | [unattended_operation.md](../../autotrader/unattended_operation.md) |

Also pinned in the suites whose code raises the alerts:
- the breaker as one episode — [safety_tests.md](safety_tests.md), `TestTheOperatorHearsOneEpisode`;
- the hard stop alerting once — on the spot path too — and its incomplete end, and the hard stop
  blocking entries without a soft threshold — `test_emergency_flatten.py`,
  `TestItBlocksEntriesWithoutASoftThreshold`;
- stale market data as one episode, its all-clear after the quiet period, and a first tick that
  never comes — [loop_cadence_tests.md](loop_cadence_tests.md),
  `test_the_all_clear_comes_once_the_data_stayed_fresh`,
  `test_a_first_tick_that_never_comes_is_said_once`, `test_the_first_tick_ends_that_wait`;
- the Field Study's refusal carrying the line the alert quotes —
  `tests/autotrader/field_study_machine/test_field_study_setup.py`, `TestTheRefusals`;
- the alert block reachable from a profile down to the Telegram ladder and endpoints —
  [config_cascade_tests.md](../framework/config_cascade_tests.md),
  `test_every_field_of_every_block_is_reachable_from_json`;
- the booking period handing each sealed period to its listener, once and after it is filed —
  `tests/framework/reporting/test_booking_period_recorder.py`, `TestEverySealIsHandedOn`;
- the credential guard naming the subfolder a real key belongs in —
  [test_credential_guard.py](../../../tests/framework/test_credential_guard.py),
  `test_the_fix_keeps_the_subfolder`.

## Suite Coverage

### `test_telegram_alert_channel.py` — one message, once, classified

| Test | What it verifies |
|---|---|
| `test_one_post_to_send_message_with_plain_text` | `sendMessage` with chat, text and no `parse_mode` — MarkdownV2 would fail on an unescaped symbol |
| `test_a_long_message_is_cut_to_the_limit_and_says_so` | 4096 characters, the cut marked |
| `test_verify_proves_the_token_and_the_chat` | the boot check asks `getMe` AND `sendChatAction` — the token alone passes a wrong or blocked chat |
| `test_a_chat_the_bot_may_not_write_into_refuses` | a bot blocked by its user refuses the start |
| `test_the_description_names_no_secret` | the log name carries the endpoint, never the token |
| `test_the_chats_that_wrote_to_the_bot_are_listed_once_each` | the setup aid behind `chat-id` |
| `test_a_refusal_is_terminal_an_outage_is_not` | 400/401/403 stop, 5xx retries |
| `test_a_200_without_a_bot_api_answer_is_not_delivered` | a wrong `base_url` answering a page is a refusal, not a delivery |
| `test_ok_false_beside_a_200_is_a_refusal` | a refused call inside a 200 is a refusal |
| `test_429_carries_the_wait_telegram_asked_for` | `retry_after` travels to the dispatcher |
| `test_a_transport_failure_names_its_kind_and_drops_the_original` | the `requests` error, which quotes the token-carrying URL, is not chained |

### `test_operator_alert_dispatcher.py` — the queue and the thread

| Test | What it verifies |
|---|---|
| `test_send_returns_at_once_while_the_channel_hangs` | the tick loop never waits |
| `test_a_full_queue_drops_the_oldest_and_says_so_at_once` | a bounded queue, the drop said when it starts — before the run is graded — once per streak |
| `test_a_critical_alert_outranks_the_rest_when_the_queue_is_full` | the oldest alert below CRITICAL goes first |
| `test_send_never_raises` | called from the abort path, a raise would skip the shutdown |
| `test_messages_keep_the_configured_gap` | `min_send_interval_s` between messages |
| `test_a_request_to_slow_down_is_honoured` | the wait Telegram asked for, not the ladder's |
| `test_an_outage_costs_one_message_and_the_next_is_tried` | an exhausted budget drops one message; the give-up said once |
| `test_a_refusal_closes_the_channel_for_the_session` | no retry of a refusal; later alerts abandoned and counted |
| `test_the_thread_survives_a_failure_nobody_classified` | only `stop()` ends the thread — a later emergency alert still goes out |
| `test_an_alert_that_cannot_be_written_costs_only_itself` | a text that cannot be rendered is dropped and said; the channel stays open |
| `test_what_could_not_go_out_is_said_at_the_end` | a bounded flush, and what it left behind named |
| `test_every_message_names_its_speaker_and_when_it_was_raised` | mark and title, detail, speaker and UTC time |
| `test_every_delivered_message_leaves_one_line_in_the_session_log` | what reached the operator is in the run's log; a message lost to an outage is not |

### `test_alert_episode.py` — a flapping symptom is one episode

| Test | What it verifies |
|---|---|
| `test_the_first_edge_opens_and_later_ones_count_as_recurrences` | one opening alert, recurrences counted |
| `test_the_all_clear_waits_for_the_quiet_period_and_comes_once` | the all-clear after `all_clear_after_s`, once |
| `test_a_recurrence_restarts_the_quiet_period` | coming back before the all-clear keeps the episode open |
| `test_a_closed_episode_opens_afresh` | the next occurrence is a new episode |
| `test_the_all_clear_says_how_long_it_held_and_whether_it_flapped` | the all-clear's detail line |

### `test_liveness_pinger.py` — the ping follows the loop

| Test | What it verifies |
|---|---|
| `test_no_ping_before_the_loop_has_passed` | nothing to vouch for yet |
| `test_pings_while_the_loop_passes` | the ordinary ping |
| `test_a_stalled_loop_silences_the_ping_and_says_so_once` | no ping, the fail URL once, one alert, one error line |
| `test_the_ping_resumes_with_the_loop` | the stall ends, and the operator hears it |
| `test_without_a_ping_url_it_only_watches_the_loop` | Telegram on, the ping off: the stall and its end are alerted; nothing pinged, no stamp file |
| `test_the_watch_ends_with_its_thread` | once the session stops it, a shutdown longer than the threshold is no stall |
| `test_an_outage_is_said_once_per_streak` | a warning per streak, not per ping |
| `test_a_refused_url_stops_the_pings_for_the_session` | a 4xx on the ping URL stops the pings and alerts |
| `test_a_refused_fail_url_does_not_silence_the_ping` | a 4xx on the fail URL stops only the fail pings |
| `test_a_url_the_http_stack_cannot_request_is_refused` | an error that is not a transport failure — a URL that fails to parse on the request — is a refusal, not the thread's end |
| `test_the_thread_survives_a_failure_nobody_classified` | only `stop()` ends the thread: the failure said once, the next beat pings |
| `test_no_log_line_names_a_url` | the ping URL is a secret |
| `test_an_emergency_end_pings_the_fail_url` · `test_any_other_end_of_a_loop_that_passed_sends_one_last_ping` | the last word to the monitor |
| `test_a_loop_that_never_passed_gets_no_last_ping` | a refused boot vouches for nothing — its silence is the alarm |
| `test_the_stamp_file_is_rewritten_whole` | a UTC instant, renamed into place |
| `test_a_stamp_file_that_cannot_be_written_is_said_once_and_the_ping_goes_on` | one warning per streak; the ping is not held up |

### `test_operator_alert_messages.py` — what the operator reads

| Test | What it verifies |
|---|---|
| `test_every_level_has_a_mark` · `test_every_grade_has_a_level` | the two declared maps cover their enums |
| `test_a_clean_end_quotes_the_grade_and_the_reports_figures` | the grade from the result, the figures from the run summary |
| `test_a_failed_end_is_critical_and_says_why` | a hard stop's reason is the executor's session-end reason |
| `test_an_open_holding_is_shown_beside_the_realised_figure` | realised and valued kept apart (#492) |
| `test_an_operator_stop_reads_as_one` | a Ctrl+C is not a failure |
| `test_missing_figures_are_said_rather_than_shown_as_zero` | no summary, no invented zeros |
| `test_it_says_whether_real_orders_go_out` | mock, dry run or LIVE |
| `test_a_duration_reads_the_way_a_person_says_it` | `2d 4h 12m` |
| `TestTheDaysClosedUnheard` | the trading days one late check sealed after the first — a machine that slept, a clock that jumped — are one message, titled with their span (or the one day), counting the days and their trades and closing with the equity line |
| `TestTheDayClose` | the sealed booking period rendered one figure per line — its own figures, an open holding beside the realised one, an unobserved value said rather than shown as zero (both ends, or which one), a day that never fell reading `0.00`, and a day the session started in saying it covers only the rest |

### `test_day_closed_message.py` — the day's close, as the operator hears it

| Test | What it verifies |
|---|---|
| `test_a_closed_trading_day_is_one_message_named_by_its_day` | one message per trading-day boundary, titled with the market's trading day |
| `test_the_session_end_is_not_a_day_close` | the session's last period is covered by its closing message, not announced twice |
| `test_switched_off_it_stays_silent` | `daily_digest: false` |
| `test_a_message_that_cannot_be_written_does_not_end_the_session` | a failure is said in the session log; the trading session goes on |

### `test_signal_staleness_alerter.py` — a stale signal input reaches the operator

| Test | What it verifies |
|---|---|
| `test_a_stale_input_is_said_once_and_its_recovery_after_the_quiet_period` | one alert per edge, not per pass; the all-clear only once the input stayed fresh for the quiet period |
| `test_an_input_that_flaps_inside_the_quiet_period_stays_one_episode` | stale and fresh three times inside the quiet period: one alert and one all-clear |
| `test_each_input_is_its_own_episode` | two signal workers, two alerts |
| `test_nothing_is_said_before_the_clock_is_set` | the edge waits for the canonical clock |

### `test_operator_alerts_setup.py` — what a profile switches on, and what is refused

| Test | What it verifies |
|---|---|
| `test_a_profile_that_switches_nothing_on_gets_no_channel` · `test_the_handle_before_startup_is_silent_too` | off by default, every call a no-op |
| `test_an_accepted_channel_carries_the_alerts` | the boot check, then delivery |
| `test_a_refused_token_refuses_the_start` | a configuration that cannot work stops the start |
| `test_an_unreachable_messenger_does_not_stop_the_start` | an outage at boot is logged, not refused |
| `test_telegram_alone_still_hears_a_stalled_loop` | with the ping off, the stall watch runs and pings nothing |
| `test_a_test_run_never_reads_the_workspace_copy` | under config isolation only the tracked copy — the placeholder — answers |
| `test_a_missing_file_names_where_it_belongs` · `test_the_committed_placeholder_is_refused` · `test_an_empty_field_is_named_and_no_value_is_quoted` · `test_the_active_endpoint_decides_which_file` | the Telegram credential, read through the shared cascade in a scratch directory that stands in for the workspace |
| `test_each_bot_reads_its_own_ping_url` · `test_a_named_file_wins_over_the_bot_default` · `test_without_a_bot_id_or_a_file_the_ping_is_refused` · `test_a_liveness_file_without_a_ping_url_is_refused` | one monitor check per bot |
| `test_a_ping_url_nothing_could_request_is_refused_at_boot` | a double dot in the host passes the HTTP parser and fails only on the request — refused at the start, the URL not quoted |
| `test_a_refused_credential_leaves_no_thread_running` | every credential is read before a thread starts |
| `TestTheConfigurationRefusesWhatCannotWork` | a queue of 0, negative intervals, an `abort` give-up rule, a ladder that never gives up and a stamp path naming no file are refused at load |

### `test_operator_alerts_check.py` — the command run before a session is left alone

| Test | What it verifies |
|---|---|
| `test_the_check_sends_one_test_message` | a channel is proven before it is switched on; a ping never set up is said in one line |
| `test_a_refused_channel_fails_the_check` | a refusal fails the check (exit 1) |
| `test_the_chats_that_wrote_to_the_bot_are_listed` | the chat id, without a browser URL that carries the token |
| `test_without_a_profile_the_check_names_the_installation` | the speaker is the installation's `host` id, never a container hostname |
| `test_a_ping_is_per_bot_so_the_check_asks_for_a_profile` | without a profile there is no bot, so no ping URL |

### `test_operator_alerts_loading.py` — a mock session pages nobody by accident

| Test | What it verifies |
|---|---|
| `test_a_mock_session_switches_both_off` | switched on in the defaults, a mock replay still sends and pings nothing |
| `test_a_mock_profile_that_says_so_keeps_its_answer` | a profile that sets `enabled` keeps it |

### `test_operator_alerts_session.py` — the wiring, end to end

A 100-tick mock replay with the channel builder swapped for one wired to the stand-ins.

| Test | What it verifies |
|---|---|
| `test_a_session_says_it_started_and_how_it_ended` | two messages; the loop stamped the very progress the pinger reads; an ordinary last ping |
| `test_an_abort_is_one_message_and_a_fail_ping` | STARTUP FAILED once, no second message at the end, the fail URL at once |
| `test_a_crash_while_shutting_down_is_said_and_fails_the_ping` | a crash in the report phase is alerted, pings the fail URL, and still raises |
| `test_a_refused_start_is_one_message_quoting_why_and_no_ping` | a cold start refusing an unattended restart: STARTUP REFUSED with its reason, graded by what it logged, and no ping for a loop that never ran |
| `test_a_refused_start_whose_shutdown_crashes_shuts_down_once` | the refusal runs the one guarded shutdown — a crash in it is SESSION CRASHED, never a second STARTUP message |

## Not covered here

- **Real delivery** — a real bot, a real chat, a real monitor. That is the `check` command, run
  on the machine that will run the session.
- **A real kill** — a process killed from outside sends nothing by definition; what the monitor
  then sees is shown by hand (stop pinging, see the alarm), not by a test.
