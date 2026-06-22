"""Grid expander tests (#390) — Cartesian product + determinism."""

from python.framework.optimization.grid_expander import expand_grid


def test_cartesian_product_size():
    """The product has the product of the per-parameter level counts."""
    grid = {
        'decision_logic_config.sl_pips': [100, 150, 200],
        'decision_logic_config.tp_pips': [200, 300],
    }
    combos = expand_grid(grid)
    assert len(combos) == 6
    assert all(set(c.keys()) == set(grid.keys()) for c in combos)


def test_every_combination_is_unique():
    """No two combinations are identical."""
    grid = {'a.x': [1, 2], 'b.y': [3, 4], 'c.z': [5, 6]}
    combos = expand_grid(grid)
    assert len(combos) == 8
    seen = {tuple(sorted(c.items())) for c in combos}
    assert len(seen) == 8


def test_deterministic_order():
    """The same grid expands to the same ordered combinations every time."""
    grid = {'b.y': [3, 4], 'a.x': [1, 2]}
    assert expand_grid(grid) == expand_grid(grid)


def test_keys_sorted_for_determinism():
    """Parameter paths are visited in sorted order regardless of grid insertion order."""
    combos = expand_grid({'z.p': [1], 'a.q': [2]})
    # sorted paths → a.q before z.p; first (and only) combo carries both
    assert list(combos[0].keys()) == ['a.q', 'z.p']


def test_single_parameter():
    """A one-parameter grid yields one combination per value."""
    combos = expand_grid({'decision_logic_config.min_confidence': [0.3, 0.4, 0.5]})
    assert [c['decision_logic_config.min_confidence'] for c in combos] == [0.3, 0.4, 0.5]


def test_empty_grid_yields_base():
    """An empty grid yields a single empty combination (the base config)."""
    assert expand_grid({}) == [{}]
