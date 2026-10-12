# Account Leverage Tests

`tests/framework/test_account_leverage.py` — what `DecisionTradingApi.get_leverage()` answers
(#586). Runs under the synthetic `framework/_root` suite.

## Why this file exists at all

`get_leverage(symbol)` called a method that exists nowhere as soon as an algo passed a symbol, so
any strategy asking for a symbol's leverage raised `AttributeError`. No configuration and no
adapter declares a leverage per symbol: it is account-wide at every venue this framework speaks
to. The method therefore takes no symbol and answers the figure the margin calculation divides
by — the broker configuration's `broker_info.leverage`.

| Test | Description |
|------|-------------|
| `test_the_account_leverage_is_the_broker_configuration_s` [mt5, kraken_spot] | Through a real simulation executor over each shipped broker configuration, the answer equals the `broker_info.leverage` that configuration declares — read from the file, so the test holds the source rather than a copy of its value |
