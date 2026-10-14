"""
Alert Episode — a symptom that flaps is one message when it starts and one when it ends (#235).

The all-clear waits for the quiet period, and a recurrence before it keeps the episode open, so
the operator's last message stays true while the symptom comes and goes.
"""

from datetime import datetime, timedelta, timezone

from python.framework.autotrader.operator_alerts.alert_episode import (
    AlertEpisode,
    all_clear_detail,
)

T0 = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_the_first_edge_opens_and_later_ones_count_as_recurrences():
    episode = AlertEpisode(all_clear_after_s=300.0)
    assert episode.opens(_at(0)) is True
    episode.clears(_at(10))
    assert episode.opens(_at(20)) is False
    assert episode.get_recurrences() == 1
    assert episode.is_open()


def test_the_all_clear_waits_for_the_quiet_period_and_comes_once():
    episode = AlertEpisode(all_clear_after_s=300.0)
    episode.opens(_at(0))
    episode.clears(_at(10))
    assert episode.all_clear_due(_at(309)) is False
    assert episode.all_clear_due(_at(310)) is True
    assert episode.all_clear_due(_at(400)) is False
    assert not episode.is_open()


def test_a_recurrence_restarts_the_quiet_period():
    episode = AlertEpisode(all_clear_after_s=300.0)
    episode.opens(_at(0))
    episode.clears(_at(10))
    episode.opens(_at(200))                       # back before the all-clear
    episode.clears(_at(250))
    assert episode.all_clear_due(_at(320)) is False
    assert episode.all_clear_due(_at(550)) is True


def test_a_closed_episode_opens_afresh():
    episode = AlertEpisode(all_clear_after_s=0.0)
    episode.opens(_at(0))
    episode.clears(_at(1))
    assert episode.all_clear_due(_at(1)) is True
    assert episode.opens(_at(2)) is True
    assert episode.get_recurrences() == 0


def test_the_all_clear_says_how_long_it_held_and_whether_it_flapped():
    episode = AlertEpisode(all_clear_after_s=300.0)
    episode.opens(_at(0))
    for second in (10, 30, 50):
        episode.clears(_at(second))
        episode.opens(_at(second + 5))
    assert all_clear_detail(episode, 300.0) == (
        'clear for 300 s · it came back 3 time(s) before that')
