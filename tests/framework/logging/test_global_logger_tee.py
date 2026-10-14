"""
Global Logger Tee Tests (#175)

While an import runs, the global logger writes every line to a second file as well, and can
keep lines off the console while a live screen owns it. Both end with the block.
"""

import pytest

from python.framework.logging.bootstrap_logger import get_global_logger


def test_tee_writes_the_second_file_only_while_the_block_runs(tmp_path):
    logger = get_global_logger()
    tee_file = tmp_path / 'imports' / 'import_test.log'

    with logger.tee_to(tee_file):
        logger.warning('tee-test warning line')

    assert 'tee-test warning line' in tee_file.read_text(encoding='utf-8')

    logger.warning('tee-test after the block')
    assert 'after the block' not in tee_file.read_text(encoding='utf-8')


def test_muted_console_prints_nothing_and_is_unmuted_after_the_block(tmp_path, capsys):
    logger = get_global_logger()
    with logger.tee_to(tmp_path / 'import_test.log'):
        logger.set_console_muted(True)
        logger.warning('tee-test muted line')
        assert 'muted line' not in capsys.readouterr().out

    logger.warning('tee-test audible line')
    assert 'audible line' in capsys.readouterr().out
    assert 'muted line' in (tmp_path / 'import_test.log').read_text(encoding='utf-8')


def test_a_second_tee_inside_the_first_is_refused(tmp_path):
    logger = get_global_logger()
    outer = tmp_path / 'import_outer.log'
    with logger.tee_to(outer):
        logger.set_console_muted(True)
        with pytest.raises(RuntimeError, match='does not nest'):
            with logger.tee_to(tmp_path / 'import_inner.log'):
                pass
        # The outer block keeps its file and its muted console
        logger.warning('tee-test still in the outer file')
    assert 'still in the outer file' in outer.read_text(encoding='utf-8')
