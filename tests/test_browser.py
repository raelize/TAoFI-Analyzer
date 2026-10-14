"""The app in a real (headless) browser: what the callback tests can't see.

Opt-in: uv sync --group browser && uv run pytest -m browser
Uses the installed Chrome, or Playwright's Chromium (uv run playwright install chromium).
"""
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.conftest import EXAMPLE_ARGV, EXAMPLE_DATABASE, EXAMPLE_DIRECTORY, REPO

pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip("playwright.sync_api")

PLOT = "document.querySelector('#graph .js-plotly-plot')"
GRAPH_READY = f"{PLOT} && {PLOT}.data && {PLOT}.data.length > 0"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    """The analyzer (built-in server) with the example database and axes preselected."""
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "taofi-analyzer.py", EXAMPLE_DIRECTORY, "--port", str(port),
         "--database", EXAMPLE_DATABASE, "--x", "delay", "--y", "length"],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        try:
            urllib.request.urlopen(url)
            break
        except OSError:
            time.sleep(0.1)
    else:
        process.kill()
        pytest.fail("the analyzer did not start: " + process.communicate()[0].decode(errors="replace"))

    yield url

    process.terminate()
    output = process.communicate(timeout=10)[0].decode(errors="replace")
    assert "Traceback" not in output, output


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                pytest.skip(f"no Chrome/Chromium for playwright: {e}")
        yield browser
        browser.close()


@pytest.fixture
def page(server, browser):
    """A page with the graph of the example database; fails the test on browser errors."""
    page = browser.new_page(viewport={"width": 1600, "height": 1200})
    errors = []
    page.on("console", lambda m: (m.type == "error" or (m.type == "warning" and "AG Grid" in m.text)) and errors.append(m.text))
    page.on("response", lambda r: r.status >= 500 and errors.append(f"{r.status} {r.url}"))
    page.goto(server)
    page.wait_for_function(GRAPH_READY, timeout=20000)
    yield page
    page.close()
    assert not errors


def open_tab(page, name):
    page.get_by_text(name, exact=True).click()


def axis_title(page, axis):
    return page.evaluate(f"String(({PLOT}.layout['{axis}'].title || {{}}).text || '')")


def test_graph_follows_the_axis_dropdowns(page):
    assert axis_title(page, "yaxis").startswith("length")
    page.click("#y-dropdown")
    page.keyboard.type("id")
    page.keyboard.press("Enter")
    page.wait_for_function(f"String(({PLOT}.layout.yaxis.title || {{}}).text || '').startsWith('id')", timeout=20000)


def test_information_tab(page):
    # zoom x and y separately, right after each other
    page.evaluate(f"Plotly.relayout({PLOT}, {{'xaxis.range': [1100, 1500]}})")
    page.evaluate(f"Plotly.relayout({PLOT}, {{'yaxis.range[0]': 2000, 'yaxis.range[1]': 3000}})")
    page.wait_for_timeout(300)
    open_tab(page, "Information")
    page.wait_for_function("document.querySelector('#argv') && document.querySelector('#argv').innerText.trim() !== ''", timeout=10000)
    assert page.inner_text("#argv").strip() == EXAMPLE_ARGV
    assert page.inner_text("#points").splitlines() == ["delay: 1100 – 1500", "length: 2000 – 3000"]
    assert "database" in page.inner_text("#printstore")


def test_zoom_reset(page):
    page.evaluate(f"Plotly.relayout({PLOT}, {{'xaxis.range': [1100, 1500]}})")
    page.evaluate(f"Plotly.relayout({PLOT}, {{'xaxis.autorange': true, 'yaxis.autorange': true}})")
    page.wait_for_timeout(300)
    open_tab(page, "Information")
    page.wait_for_selector("#points", state="attached")
    page.wait_for_function("document.querySelector('#points').innerText.includes('Zoom into the graph')", timeout=10000)


def test_zoom_survives_redrawing(page):
    page.evaluate(f"Plotly.relayout({PLOT}, {{'xaxis.range': [1100, 1500]}})")
    page.wait_for_timeout(300)
    # redrawn with the same database and axes (like auto refresh)
    page.evaluate(f"window.redrawn = {PLOT}.data[0]")
    page.click("#update-button")
    page.wait_for_function(f"{PLOT}.data[0] !== window.redrawn", timeout=10000)
    assert page.evaluate(f"{PLOT}.layout.xaxis.range") == [1100, 1500]
    open_tab(page, "Information")
    page.wait_for_selector("#points", state="attached")
    page.wait_for_function("document.querySelector('#points').innerText.includes('delay: 1100 – 1500')", timeout=10000)


def legend_title(page):
    return page.evaluate(f"{PLOT}.layout.legend.title.text")


def test_enter_applies_the_text_fields(page):
    assert legend_title(page) == "Classification ( 6,843 ):"
    page.fill("#query-input", "delay > 1900")
    page.press("#query-input", "Enter")
    page.wait_for_function(f"{PLOT}.layout.legend.title.text !== 'Classification ( 6,843 ):'", timeout=10000)
    page.fill("#recolor-red", "^")
    page.fill("#recolor-red-label", "everything")
    page.press("#recolor-red-label", "Enter")
    page.wait_for_function(f"{PLOT}.data.some((trace) => trace.name.startsWith('everything'))", timeout=10000)
    # after a reload the fields are as they were, and applied
    page.reload()
    page.wait_for_function(f"{GRAPH_READY} && {PLOT}.data.some((trace) => trace.name.startsWith('everything'))", timeout=20000)
    assert legend_title(page) != "Classification ( 6,843 ):"


def test_data_tab(page):
    open_tab(page, "Data")
    page.wait_for_selector(".ag-row", timeout=20000)
    backgrounds = page.eval_on_selector_all(".ag-center-cols-container .ag-row", "rows => rows.map(r => getComputedStyle(r).backgroundColor)")
    assert any(b not in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)") for b in backgrounds)

    # number filter through the grid itself (the data is squeezed by default)
    page.evaluate("""async () => {
        const api = await dash_ag_grid.getApiAsync(document.querySelector('[id*="data-grid"]').id);
        api.setFilterModel({'delay >': {filterType: 'number', type: 'greaterThan', filter: 1900}});
    }""")
    page.wait_for_timeout(1500)
    # AG Grid escapes the > in the column id attribute
    cells = page.eval_on_selector_all('.ag-row [col-id="delay &gt;"]', "cells => cells.map(c => c.innerText)")
    values = [int(c) for c in cells if c.strip()]
    assert values and all(v > 1900 for v in values)


def test_selection_survives_tab_switches(page):
    open_tab(page, "Data")
    page.wait_for_selector(".ag-row", timeout=20000)
    open_tab(page, "Graph")
    page.wait_for_function(GRAPH_READY, timeout=20000)
    assert EXAMPLE_DATABASE in page.inner_text("#database-dropdown")


def test_timing_badge(page):
    page.wait_for_function("document.querySelector('#timing').innerText.startsWith('server (')", timeout=10000)
    page.wait_for_function("document.querySelector('#timing-label').innerText.trim() === 'graph:'", timeout=10000)
    open_tab(page, "Data")
    page.wait_for_selector(".ag-row", timeout=20000)
    # the data table and its first page of rows together
    page.wait_for_function("document.querySelector('#timing-label').innerText.trim() === 'data:'", timeout=10000)


def test_stale_page_reloads(page):
    page.wait_for_load_state("networkidle")
    page.evaluate("window.marker = 1")
    # unchanged callbacks: no reload
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_timeout(1500)
    assert page.evaluate("window.marker") == 1
    # the server's callbacks changed (e.g. updated and restarted): reload
    page.route("**/_dash-dependencies", lambda route: route.fulfill(response=route.fetch(), body=route.fetch().text() + " "))
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("window.marker === undefined", timeout=10000)
    page.unroute("**/_dash-dependencies")
