"""
Composite constants — the values of the composite key's floor that sit in a rendered document (#615).

The floor is always defining, and no definition may loosen it. Two of its values are no document
path at all — the run's axes and the resolved account currency reach the key from the pipeline —
and the two below are, so a definition file must never name them.
"""

# The decision logic and the account model, as paths in a rendered document.
FLOOR_DOCUMENT_PATHS = ('strategy_config.decision_logic_type', 'market_entry.trading_model')
