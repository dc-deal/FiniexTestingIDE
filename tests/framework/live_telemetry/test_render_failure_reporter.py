"""
FiniexTestingIDE - Render Failure Reporter Tests (#586)

A live screen redraws several times a second, so one failure repeats on every frame. The
reporter says each distinct failure once, a few per place, and never fails itself.
"""

from typing import List

from python.system.ui.render_failure_reporter import RenderFailureReporter


def _fail(message: str) -> Exception:
    """
    An exception that has been raised, so it carries a traceback.

    Args:
        message: Its message

    Returns:
        The exception
    """
    try:
        raise AttributeError(message)
    except AttributeError as error:
        return error


def test_a_failure_is_reported_once_with_its_traceback():
    sink: List[str] = []
    reporter = RenderFailureReporter(sink.append)

    for _ in range(5):
        reporter.report('Panel TRADE HISTORY', _fail('no attribute entry_lots'),
                        'The panel shows as failed; trading is unaffected.')

    assert len(sink) == 1
    assert sink[0].startswith(
        '❌ [DISPLAY] Panel TRADE HISTORY failed — AttributeError: no attribute entry_lots. '
        'The panel shows as failed; trading is unaffected.')
    assert 'Traceback' in sink[0]


def test_each_place_reports_only_its_share_and_says_so_last():
    sink: List[str] = []
    reporter = RenderFailureReporter(sink.append, max_per_area=3)

    for n in range(5):
        reporter.report('Panel ORDERS', _fail(f'defect {n}'), 'Shown as failed.')
    reporter.report('Panel SESSION', _fail('defect 0'), 'Shown as failed.')

    orders = [line for line in sink if 'Panel ORDERS' in line]
    assert len(orders) == 3
    assert 'Further failures of Panel ORDERS are not reported.' in orders[-1]
    assert 'Further failures' not in orders[0]
    assert len([line for line in sink if 'Panel SESSION' in line]) == 1


def test_without_a_sink_nothing_is_reported():
    RenderFailureReporter(None).report('Panel X', _fail('x'), 'Shown as failed.')


def test_a_sink_that_fails_does_not_raise(capsys):
    def broken_sink(_message: str) -> None:
        raise OSError('log file gone')

    RenderFailureReporter(broken_sink).report('Panel X', _fail('x'), 'Shown as failed.')

    assert 'could not be reported: log file gone' in capsys.readouterr().err
