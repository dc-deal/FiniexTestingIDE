# Order Features Tests

`tests/framework/test_order_features.py` — what a decision logic declares against what its venue's
API holds (#625). Runs under the synthetic `framework/_root` suite. Pure: stand-ins for the
executor and the broker, no data and no run.

## Why it exists

A venue's API holds a stop or a target for a position or it does not, and the framework does not
rebuild what it lacks. A level nothing holds reads like protection nobody provides, so the answer
comes before the run. The check sits in three places that must give one answer in one sentence,
and whatever gets past them is refused at order time.

What this file does not cover: a logic's own exits, which
[Own Exit Levels Tests](own_exit_levels_tests.md) pin, and the benchmark certificate's refusal,
which its own suite holds.

## What Is Tested

| Class | What it pins |
|-------|--------------|
| `TestWhatAnAdapterStates` | MT5 holds both levels, Kraken and the mock none; a capability answers per feature; the runtime guard knows every member of `OrderFeature` |
| `TestALogicHasToAnswer` | a logic that does not override is refused; one keeping its own exits declares none; the probe declares what its sequences send, none in its own-exit mode; the field study declares its stop only with the protective phase |
| `TestTheSentence` | it names the logic, the venue and the way out; only a backtest is told of the exception |
| `TestTheStartRefuses` | a declared feature the venue lacks; a pipeline that does not carry it; an exception lets a backtest through, but only for the feature it names |
| `TestAnUndeclaredLevelIsRefusedAtOrderTime` | an order and all three modifies are refused with `order_feature_not_declared`; removing a level or leaving it alone passes; no cooldown follows |
| `TestTheBatchRefusesInItsMainProcess` | the scenario is excluded, the others run; the exception lets it through; an exception nothing needs, or that the venue holds anyway, is refused; one scenario needing it is enough for the set |
| `TestTheSetDeclaresTheException` | it reaches every scenario; an unknown name and a misspelt key are refused |
| `TestItIsRecorded` | the run header serves the values; the ledger row says it over all scenarios, and is empty without one; the column is declared with how it combines |
| `TestASessionCannotDeclareIt` | the key in an AutoTrader profile is refused |

## Related

- How a bot uses it: [Quickstart — Own Exits](../../user_guides/quickstart_guide.md).
- [Own Exit Levels Tests](own_exit_levels_tests.md) · [Config Cascade Tests](config_cascade_tests.md).
