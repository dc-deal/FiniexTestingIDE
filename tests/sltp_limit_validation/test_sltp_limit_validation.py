"""
FiniexTestingIDE - SL/TP & Limit Order Validation Tests
Imports shared test classes from tests/shared/shared_sltp_limit_validation.py

Uses sltp_limit_validation_test.json scenario config:
- Scenario 0: LONG TP trigger (uptrend window, TP hit)
- Scenario 1: LONG SL trigger (downtrend window, SL hit)
- Scenario 2: SHORT TP trigger (downtrend window, TP hit)
- Scenario 3: SHORT SL trigger (uptrend window, SL hit)
- Scenario 4: Modify TP trigger (initial TP unreachable, modified TP triggers)
- Scenario 5: LONG limit fill (price drops to limit level)
- Scenario 6: SHORT limit fill (price rises to limit level)
- Scenario 7: Limit fill then SL trigger (limit fills, then SL hits)
- Scenario 8: Modify limit price fill (limit price changed before fill)
"""

from tests.shared.shared_sltp_limit_validation import (
    TestLongTpTrigger,
    TestLongSlTrigger,
    TestShortTpTrigger,
    TestShortSlTrigger,
    TestModifyTpTrigger,
    TestLongLimitFill,
    TestShortLimitFill,
    TestLimitFillThenSl,
    TestModifyLimitPriceFill,
)
