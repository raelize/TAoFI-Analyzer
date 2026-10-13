from analyzer.config import COLORS, ROW_COLORS, AnalyzerConfig
from analyzer.graphs.common import COLOR_ORDER


def test_from_store_ignores_unknown_keys():
    # e.g. the store of a page from an older version
    config = AnalyzerConfig.from_store({"serverip": "1.2.3.4", "database": "a.sqlite", "jitter": 2})
    assert config.database == "a.sqlite"
    assert config.jitter == 2


def test_from_store_without_store():
    assert AnalyzerConfig.from_store(None) == AnalyzerConfig()


def test_default_colors_are_not_shared():
    first, second = AnalyzerConfig(), AnalyzerConfig()
    first.colors["red"][0] = "."
    assert second.colors["red"] == [None, None]
    assert list(first.colors) == COLORS


def test_every_color_code_has_a_row_color():
    assert set(ROW_COLORS) == set(COLOR_ORDER)
