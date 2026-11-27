"""Probe: what do the drawdown periods of #529 cost per equity sample?

The question arose in #529, which owns the probe. Every tick of both pipelines samples the account's
equity (`PortfolioManager.sample_equity`), and #529 adds the five deepest drawdown periods to that
sample: two comparisons on a sample that neither opens, deepens nor closes a period, a clock read
on one that does, and a ring update at a recovery. The issue asks for the cost to be measured on its
own, as an interleaved A/B, never as one absolute number.

It drives one portfolio's `sample_equity(value)` over synthetic curves — rising, falling, and a
seeded random walk that opens and closes periods all the time — and reports microseconds per
sample. Run with `--trees A,B`, it alternates two code stands round by round, each in a subprocess
whose PYTHONPATH is that tree, so both read the same machine at the same moments and the noise
falls on both; the order flips every round. The median and the spread of each stand are printed,
and the difference of the medians. The figure moves with the machine and the code, so it is run
again after either changes.

Touches nothing on disk apart from the global log header every start of the framework writes.

Recorded results:
  2026-11-23, development container, base b9be0223 against the #529-A build, 10 rounds of 200,000
  samples, interleaved; no test suite ran, but the load average stood at 7.5 to 8.2 on 16 cores.
  Taken as the issue's measurement: the interleaving lets both stands share the load, and
  whole-backtest throughput on a quiet machine is the release benchmark's.
    rising   base 0.251 µs, build 0.294 µs (+0.044)   — no period ever opens
    falling  base 0.271 µs, build 0.533 µs (+0.263)   — every sample deepens the open period
    noisy    base 0.251 µs, build 0.293 µs (+0.042)   — periods open and close all the time
  A backtest tick costs about 380 µs (probe_portfolio_cache_cost.py), so the ordinary case adds
  about 0.01 % and the worst one 0.07 %. Before the instants were kept as datetimes until a period
  closes, the falling curve cost 1.64 µs a sample in a single run on the build.

Usage:
    python python/experiments/cost_probes/probe_account_drawdown_period_cost.py --trees <base>,<build>
        [--rounds N] [--samples N]
    python python/experiments/cost_probes/probe_account_drawdown_period_cost.py --measure [--samples N]
"""

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.portfolio_manager import PortfolioManager

_MT5_CONFIG = 'configs/brokers/mt5/mt5_broker_config.json'
_BALANCE = 10_000.0
_T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)


class _NullLogger:
    """A logger that drops everything — the measured path logs nothing worth timing."""

    def verbose(self, *args, **kwargs) -> None:
        pass

    def debug(self, *args, **kwargs) -> None:
        pass

    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def error(self, *args, **kwargs) -> None:
        pass


def _curves(samples: int) -> Dict[str, List[float]]:
    """
    The synthetic equity curves the probe samples.

    Args:
        samples: How many values each curve holds

    Returns:
        Each curve by name
    """
    walk = random.Random(529)
    value, noisy = _BALANCE, []
    for _ in range(samples):
        value += walk.gauss(0.0, 5.0)
        noisy.append(value)
    return {
        'rising': [_BALANCE + i * 0.01 for i in range(samples)],
        'falling': [_BALANCE - i * 0.01 for i in range(samples)],
        'noisy': noisy,
    }


def _measure(samples: int) -> Dict[str, float]:
    """
    Time `sample_equity` over each curve in this process, on the code its PYTHONPATH imports.

    Args:
        samples: How many samples per curve

    Returns:
        Microseconds per sample, by curve
    """
    broker_config = BrokerConfigFactory.build_broker_config(_MT5_CONFIG)
    results = {}
    for name, curve in _curves(samples).items():
        instant = [_T0]

        def clock() -> datetime:
            """The run's clock, one second further per sample."""
            return instant[0]
        portfolio = PortfolioManager(
            logger=_NullLogger(), initial_balance=_BALANCE, account_currency='USD',
            broker_config=broker_config, leverage=500, margin_call_level=50.0,
            stop_out_level=20.0, clock_fn=clock)
        step = timedelta(seconds=1)
        start = time.perf_counter()
        for value in curve:
            instant[0] += step
            portfolio.sample_equity(value)
        results[name] = (time.perf_counter() - start) / samples * 1e6
    return results


def _alternate(trees: List[str], rounds: int, samples: int) -> None:
    """
    Run the measurement in a subprocess per tree, round by round, the order flipping each round,
    and print each tree's median and spread per curve.

    Args:
        trees: The two code stands, base first
        rounds: How many rounds
        samples: How many samples per curve
    """
    script = Path(__file__).resolve()
    runs: Dict[str, List[Dict[str, float]]] = {tree: [] for tree in trees}
    for round_no in range(rounds):
        order = trees if round_no % 2 == 0 else list(reversed(trees))
        for tree in order:
            env = {**os.environ, 'PYTHONPATH': tree}
            out = subprocess.run(
                [sys.executable, str(script), '--measure', '--samples', str(samples)],
                cwd=tree, env=env, capture_output=True, text=True, check=True)
            runs[tree].append(json.loads(out.stdout.strip().splitlines()[-1]))
    medians = {}
    for tree in trees:
        print(f'{tree}')
        medians[tree] = {}
        for curve in runs[tree][0]:
            values = [run[curve] for run in runs[tree]]
            medians[tree][curve] = statistics.median(values)
            print(f'  {curve:8} median {medians[tree][curve]:.3f} µs/sample '
                  f'(min {min(values):.3f}, max {max(values):.3f}, {len(values)} rounds)')
    base, build = trees
    print('difference of the medians, build − base:')
    for curve in medians[base]:
        print(f'  {curve:8} {medians[build][curve] - medians[base][curve]:+.3f} µs/sample')


def main() -> None:
    """Parse the arguments and run the probe."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--trees', help='two code stands, comma-separated: base,build')
    parser.add_argument('--rounds', type=int, default=10)
    parser.add_argument('--samples', type=int, default=200_000)
    parser.add_argument('--measure', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.measure:
        print(json.dumps(_measure(args.samples)))
        return
    trees = [tree.strip() for tree in (args.trees or '').split(',') if tree.strip()]
    if len(trees) != 2:
        parser.error('--trees needs two code stands: base,build')
    _alternate(trees, args.rounds, args.samples)


if __name__ == '__main__':
    main()
