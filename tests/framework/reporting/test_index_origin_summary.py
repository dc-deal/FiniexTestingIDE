"""
Whose Data the Index Status Shows (#647).

The bar and tick index status print whose data an archive holds: for a symbol's bars the origin
classes and where our own collection takes over, for its tick files the files and ticks per origin.
The figures come from the index managers; these tests pin the lines.
"""

from datetime import datetime, timezone

from python.framework.reporting.console.index_origin_summary import (
    print_bar_origin,
    print_tick_origin,
)
from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.types.data_origin_types import OriginFileCount, SymbolDataOrigin


class TestTheBarOrigin:

    def test_the_classes_and_the_seam_are_printed(self, capsys):
        print_bar_origin(SymbolDataOrigin(
            origin_classes=[OriginClass.PRODUCTION, OriginClass.EXTERNAL],
            collected_since=datetime(2026, 1, 24, 14, 20, 20, tzinfo=timezone.utc)), indent='   ')

        out = capsys.readouterr().out
        assert '   Origin:     production, external' in out
        assert 'Collected since: 2026-01-24 14:20:20 UTC' in out

    def test_unstamped_bars_say_so_and_show_no_seam(self, capsys):
        print_bar_origin(SymbolDataOrigin(origin_classes=[], collected_since=None), indent='')

        out = capsys.readouterr().out
        assert 'Origin:     not stamped' in out
        assert 'Collected since' not in out


class TestTheTickOrigin:

    def test_one_line_per_origin_class(self, capsys):
        print_tick_origin([OriginFileCount('production', 238, 9_286_311),
                           OriginFileCount('external', 188, 60_013_484)])

        lines = capsys.readouterr().out.splitlines()
        assert lines == ['Origin:      production   238 file(s) · 9,286,311 ticks',
                         'Origin:      external     188 file(s) · 60,013,484 ticks']
