import os

from analyzer.config import COLORS, AnalyzerConfig
from analyzer.graphs.common import COLOR_ORDER
from tests.conftest import REPO


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
    """In the data tab, light and dark (assets/raelize.css)."""
    with open(os.path.join(REPO, "assets", "raelize.css")) as f:
        css = f.read()
    for code in COLOR_ORDER:
        assert f".ag-theme-quartz .ag-row.row-{code} " in css
        assert f"[data-bs-theme=dark] .ag-row.row-{code} " in css
