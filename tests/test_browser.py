"""The app in a real (headless) browser: what the callback tests can't see.

Opt-in: uv sync --group browser && uv run pytest -m browser
Uses the installed Chrome, or Playwright's Chromium (uv run playwright install chromium).
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.conftest import EXAMPLE_ARGV, EXAMPLE_DATABASE, EXAMPLE_DIRECTORY, REPO, free_port

pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip("playwright.sync_api")

PLOT = "document.querySelector('#graph .js-plotly-plot')"
GRAPH_READY = f"{PLOT} && {PLOT}.data && {PLOT}.data.length > 0"


@pytest.fixture(scope="module")
def server():
    """The analyzer (built-in server) with the example database and axes preselected."""
    yield from analyzer(EXAMPLE_DIRECTORY, EXAMPLE_DATABASE)


def analyzer(directory, database):
    """Run the analyzer for ``directory`` (with ``database`` and the axes preselected); yields its URL."""
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "taofi-analyzer.py", directory, "--port", str(port),
         "--database", database, "--x", "delay", "--y", "length"],
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
    # after a reload the fields are as they were applied, and applied
    page.fill("#query-input", "typed, not applied")
    page.reload()
    page.wait_for_function(f"{GRAPH_READY} && {PLOT}.data.some((trace) => trace.name.startsWith('everything'))", timeout=20000)
    assert legend_title(page) != "Classification ( 6,843 ):"
    assert page.input_value("#query-input") == "delay > 1900"
    assert page.input_value("#recolor-red") == "^"
    # switching tabs doesn't apply what was typed
    page.fill("#query-input", "delay > 1950")
    open_tab(page, "Data")
    page.wait_for_selector(".ag-row", timeout=20000)
    open_tab(page, "Graph")
    page.wait_for_function(GRAPH_READY, timeout=20000)
    page.wait_for_timeout(1000)
    assert page.input_value("#query-input") == "delay > 1900"
    title = legend_title(page)
    page.press("#query-input", "End")
    page.fill("#query-input", "delay > 1950")
    page.press("#query-input", "Enter")
    page.wait_for_function(f"{PLOT}.layout.legend.title.text !== {title!r}", timeout=10000)


def test_clicking_a_point(page):
    # the pixel of the second point of the first trace
    x, y = page.evaluate(f"""(() => {{
        const layout = {PLOT}._fullLayout, trace = {PLOT}._fullData[0];
        return [layout.xaxis.d2p(trace.x[1]) + layout.margin.l, layout.yaxis.d2p(trace.y[1]) + layout.margin.t];
    }})()""")
    box = page.locator("#graph .js-plotly-plot").bounding_box()
    # scroll the point into view (the mouse can only click what is shown)
    page.evaluate(f"window.scrollBy(0, {box['y'] + y - 600})")
    box = page.locator("#graph .js-plotly-plot").bounding_box()
    page.mouse.click(box["x"] + x, box["y"] + y)
    page.wait_for_function("document.querySelector('#experiment').innerText.includes('the same for')", timeout=10000)
    assert "delay" in page.inner_text("#experiment")


def grid_rows(page):
    return page.evaluate("""(() => {
        const grid = document.querySelector('[id*="data-grid"]');
        return dash_ag_grid.getApi(grid.id).getDisplayedRowCount();
    })()""")


def test_selecting_in_the_graph(page):
    # drag a box (select mode) from delay 1100, length 4000 to delay 1500, length 3000
    page.evaluate(f"Plotly.relayout({PLOT}, {{dragmode: 'select'}})")
    x0, y0, x1, y1 = page.evaluate(f"""(() => {{
        const layout = {PLOT}._fullLayout, x = layout.xaxis, y = layout.yaxis;
        return [x._offset + x.d2p(1100), y._offset + y.d2p(4000), x._offset + x.d2p(1500), y._offset + y.d2p(3000)];
    }})()""")
    box = page.locator("#graph .js-plotly-plot").bounding_box()
    page.mouse.move(box["x"] + x0, box["y"] + y0)
    page.mouse.down()
    page.mouse.move(box["x"] + x1, box["y"] + y1, steps=10)
    page.mouse.up()
    page.wait_for_timeout(500)
    open_tab(page, "Data")
    page.wait_for_selector(".ag-row", timeout=20000)
    page.wait_for_function("document.querySelector('#data-selection').innerText.includes('selected in the graph')", timeout=10000)
    assert "(delay 1" in page.inner_text("#data-selection")
    page.wait_for_timeout(500)
    selected = grid_rows(page)
    page.click("#data-show-all")
    page.wait_for_function("!document.querySelector('#data-selection').innerText", timeout=10000)
    page.wait_for_function(f"(() => {{ const grid = document.querySelector('[id*=\"data-grid\"]'); return dash_ag_grid.getApi(grid.id).getDisplayedRowCount() > {selected}; }})()", timeout=10000)


def test_data_tab(page):
    open_tab(page, "Data")
    page.wait_for_selector(".ag-row", timeout=20000)
    backgrounds = page.eval_on_selector_all(".ag-center-cols-container .ag-row", "rows => rows.map(r => getComputedStyle(r).backgroundColor)")
    assert any(b not in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)") for b in backgrounds)

    # number filter through the grid itself (the data is squeezed by default)
    page.evaluate("""async () => {
        const api = await dash_ag_grid.getApiAsync(document.querySelector('[id*="data-grid"]').id);
        api.setFilterModel({'min(delay)': {filterType: 'number', type: 'greaterThan', filter: 1900}});
    }""")
    page.wait_for_timeout(1500)
    cells = page.eval_on_selector_all('.ag-row [col-id="min(delay)"]', "cells => cells.map(c => c.innerText)")
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


def test_data_tab_auto_refresh(browser, tmp_path):
    """A database that is being written to: the data tab shows the new experiments, on the same page."""
    shutil.copy(os.path.join(EXAMPLE_DIRECTORY, EXAMPLE_DATABASE), tmp_path / "live.sqlite")
    url = analyzer(str(tmp_path), "live.sqlite")
    page = browser.new_page(viewport={"width": 1600, "height": 1200})
    errors = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    try:
        page.goto(next(url))
        page.fill("#refresh-seconds", "1")
        page.click("#switch-autorefresh")
        open_tab(page, "Data")
        page.wait_for_selector(".ag-row", timeout=20000)
        rows = """(() => {
            const grid = document.querySelector('[id*="data-grid"]');
            return dash_ag_grid.getApi(grid.id).getDisplayedRowCount();
        })()"""
        before = page.evaluate(rows)
        con = sqlite3.connect(tmp_path / "live.sqlite")
        con.executemany("INSERT INTO experiments VALUES (?, 1, 1, 'R', ?)", [(100000 + i, b"new %d" % i) for i in range(5)])
        con.commit()
        con.close()
        # squeezed: 5 new responses
        page.wait_for_function(f"{rows} === {before + 5}", timeout=10000)
        assert not errors
    finally:
        page.close()
        next(url, None)


def test_no_outside_requests(server, browser):
    """Everything comes from the analyzer itself (e.g. no fonts from the internet: works offline)."""
    page = browser.new_page()
    outside = []
    page.on("request", lambda r: not r.url.startswith(server) and not r.url.startswith("data:") and outside.append(r.url))
    page.goto(server)
    page.wait_for_function(GRAPH_READY, timeout=20000)
    # the theme's font (used by the heading)
    page.wait_for_function("[...document.fonts].some((f) => f.family.includes('News Cycle') && f.status === 'loaded')", timeout=10000)
    page.close()
    assert not outside


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
