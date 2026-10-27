"""
The scenario log prefix is one plain name (#621): every backtest log's file name begins with it, and
the run store refuses a name part that is empty, a dot or a path. A value it would refuse is refused
when the configuration loads, naming the key, rather than at the start of the first run.
"""

import pytest
from pydantic import ValidationError

from python.framework.types.config_types.file_logging_config_types import (
    ScenarioFileLoggingConfig,
)


@pytest.mark.parametrize('prefix', ['scenario', 'my-run_1', '.x'])
def test_a_plain_name_is_accepted(prefix):
    assert ScenarioFileLoggingConfig(file_name_prefix=prefix).file_name_prefix == prefix


@pytest.mark.parametrize('prefix', ['', '.', '..', 'a/b', 'a\\b'])
def test_a_name_the_run_store_would_refuse_is_refused_at_load(prefix):
    with pytest.raises(ValidationError, match='file_name_prefix'):
        ScenarioFileLoggingConfig(file_name_prefix=prefix)
