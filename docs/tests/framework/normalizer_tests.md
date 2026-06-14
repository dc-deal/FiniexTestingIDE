# Normalizer Tests

`tests/framework/test_normalizer.py` — the central normalization apparatus
(`python/framework/utils/trading_math/normalizer.py`). Runs under the synthetic `framework/_root` suite.

**Total Tests:** 17

## TestRescale (7 Tests) — `rescale(value, lower, upper)` (MinMax / %B, unclamped)

| Test | Description |
|------|-------------|
| `test_value_at_lower_is_zero` | value == lower → 0.0 |
| `test_value_at_upper_is_one` | value == upper → 1.0 |
| `test_value_at_midpoint` | midpoint → 0.5 |
| `test_overshoot_above_upper_exceeds_one` | above upper → > 1.0 (overshoot preserved) |
| `test_overshoot_below_lower_is_negative` | below lower → < 0.0 |
| `test_degenerate_range_returns_midpoint` | upper == lower → 0.5 |
| `test_inverted_range_returns_midpoint` | upper < lower → 0.5 |

## TestClamp (5 Tests) — `clamp(x, low, high)`

| Test | Description |
|------|-------------|
| `test_within_range_unchanged` | value inside [low, high] returned as-is |
| `test_below_low_clamped` | below low → low |
| `test_above_high_clamped` | above high → high |
| `test_at_bounds` | exact bounds returned as-is |
| `test_custom_range` | non-default [low, high] honored |

## TestNormalize (5 Tests) — `normalize(value, scale)`

| Test | Description |
|------|-------------|
| `test_normal_ratio` | value / scale |
| `test_preserves_sign` | negative value → negative ratio |
| `test_zero_scale_returns_zero` | scale == 0 → 0.0 (safe) |
| `test_negative_scale_returns_zero` | scale < 0 → 0.0 (safe) |
| `test_zero_value_is_zero` | value == 0 → 0.0 |
