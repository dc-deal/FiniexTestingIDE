"""
FiniexTestingIDE - SL/TP Validation Tests
Imports shared test classes from tests/shared/shared_sltp_validation.py

Uses sltp_validation_test.json scenario config:
- Scenario 0: LONG TP trigger (uptrend window, TP hit)
- Scenario 1: LONG SL trigger (downtrend window, SL hit)
- Scenario 2: SHORT TP trigger (downtrend window, TP hit)
- Scenario 3: SHORT SL trigger (uptrend window, SL hit)
- Scenario 4: Modify TP trigger (initial TP unreachable, modified TP triggers)
"""

from tests.shared.shared_sltp_validation import (
    TestLongTpTrigger,
    TestLongSlTrigger,
    TestShortTpTrigger,
    TestShortSlTrigger,
    TestModifyTpTrigger,
)
