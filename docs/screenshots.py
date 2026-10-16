"""The screenshots of the README (docs/screenshots/*.png).

A made-up characterization of an unrolled loop (no real target): the firmware
increments a counter 10000 times (unrolled: one instruction after another) and
prints it; a skipped increment gives a lower counter.

  uv sync --group browser && uv run python docs/screenshots.py
"""

import math
import os
import random
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(REPO, "docs", "screenshots")
DATABASE = "g_CharacterizeUnrolled.py_20261015_093012.sqlite"
ARGV = "--target /dev/ttyUSB0 --delay 1000 2000 --length 1000 5000 --experiments 12000"
EXPERIMENTS = 12000
LOOP = 10000

PLOT = "document.querySelector('#graph .js-plotly-plot')"
GRAPH_READY = f"{PLOT} && {PLOT}.data && {PLOT}.data.length > 0"

# a reset: the boot ROM's output at the wrong baud rate (a few variants)
BOOT_ROM = [random.Random(i).randbytes(30) for i in range(4)]

CRASHES = [0x40100000 + random.Random(i).randrange(0x200, 0x9C80, 3) for i in range(6)]

EXCEPTIONS = [
    (0, "IllegalInstruction"),
    (2, "InstructionFetchError"),
    (3, "LoadStoreError"),
    (28, "LoadProhibited"),
    (29, "StoreProhibited"),
]


def response(rng, delay, length):
    """The color and the response of an experiment: the glitch gets stronger with its length."""
    # the target resets above this length (a bit more or less over time: the delay)
    threshold = 3300 + 150 * math.sin(delay / 80)
    strength = length - threshold
    # an increment is skipped more often at some moments of a clock cycle (25 ns)
    in_phase = 7 <= delay % 25 <= 13

    if strength < -300:
        kind = "G"
    elif strength > 400:
        kind = "Y"
    else:
        fault = 0.30 * math.exp(-((strength / 130) ** 2)) * (1.6 if in_phase else 0.5)
        crash = 0.25 * math.exp(-(((strength - 150) / 120) ** 2))
        reset = 1 / (1 + math.exp(-(strength - 150) / 70))
        roll = rng.random()
        if roll < fault:
            kind = "R"
        elif roll < fault + crash:
            kind = "M"
        elif roll < fault + crash + reset:
            kind = "Y"
        else:
            kind = "G"

    if kind == "G":
        return "G", b"cnt=0x%08x\r\n" % LOOP
    if kind == "R":
        skipped = rng.choices([1, 2, 3, rng.randrange(4, 600)], weights=[70, 18, 6, 6])[0]
        return "R", b"cnt=0x%08x\r\n" % (LOOP - skipped)
    if kind == "M":
        # (mostly at a few places of the loop)
        cause, name = rng.choices(EXCEPTIONS, weights=[30, 25, 5, 30, 10])[0]
        epc = rng.choice(CRASHES) if rng.random() < 0.8 else 0x40100000 + rng.randrange(0x200, 0x9C80, 3)
        excvaddr = rng.choice([0, epc & ~3, 0xCD])
        text = (
            f"Fatal exception ({cause}): {name}\r\n"
            f"epc1=0x{epc:08x}, epc2=0x00000000, epc3=0x00000000, excvaddr=0x{excvaddr:08x}, depc=0x00000000\r\n"
        )
        return "M", text.encode()
    # a reset: nothing, or the boot ROM's output at the wrong baud rate
    return "Y", rng.choices([b"", b"\xff", *BOOT_ROM], weights=[55, 30, 6, 4, 3, 2])[0]


def create_database(path):
    rng = random.Random(10000)
    rows = []
    for i in range(EXPERIMENTS):
        delay = rng.randrange(1000, 2001)
        length = rng.randrange(1000, 5001)
        rows.append((i, delay, length, *response(rng, delay, length)))
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE experiments(id integer, delay integer, length integer, color text, response blob)")
    con.executemany("INSERT INTO experiments VALUES (?, ?, ?, ?, ?)", rows)
    con.execute("CREATE TABLE metadata (stime_seconds integer, argv blob)")
    con.execute("INSERT INTO metadata VALUES (?, ?)", (1792049412, ARGV))
    con.commit()
    con.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def screenshots(url):
    os.makedirs(OUTPUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)

        def shot(name, full_page=False):
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(OUTPUT, f"{name}.png"), full_page=full_page)

        def open_tab(name):
            page.locator("#tabs").get_by_text(name, exact=True).click()

        page.goto(url)
        page.wait_for_function(GRAPH_READY, timeout=30000)
        shot("graph", full_page=True)

        open_tab("Data")
        page.wait_for_selector(".ag-row", timeout=30000)
        shot("data")

        # zoomed into the faults: the information tab shows the range
        open_tab("Graph")
        page.evaluate(f"Plotly.relayout({PLOT}, {{'xaxis.range': [1200, 1600], 'yaxis.range': [2900, 3700]}})")
        page.wait_for_timeout(1000)
        open_tab("Information")
        page.wait_for_function("!document.querySelector('#points').innerText.includes('Zoom into')", timeout=30000)
        page.wait_for_function("document.querySelector('#argv').innerText.includes('--target')", timeout=30000)
        shot("information")

        open_tab("Database")
        page.fill("#select-input", "response, count(*) AS amount, min(delay), max(delay), min(length), max(length)")
        page.fill("#where-input", "color = 'R' GROUP BY response ORDER BY amount DESC")
        page.click("#query-button")
        page.wait_for_selector("#query-output table, #query-output .ag-row", timeout=30000)
        shot("database")

        open_tab("Guide")
        shot("guide")
        browser.close()


def main():
    # (a fixed directory: it's in the screenshots)
    directory = os.path.join(tempfile.gettempdir(), "taofi", "databases")
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory)
    with tempfile.TemporaryFile() as output:
        create_database(os.path.join(directory, DATABASE))
        port = free_port()
        command = [sys.executable, "taofi-analyzer.py", directory, "--port", str(port), "--database", DATABASE]
        process = subprocess.Popen(
            [*command, "--x", "delay", "--y", "length"], cwd=REPO, stdout=output, stderr=subprocess.STDOUT
        )
        url = f"http://127.0.0.1:{port}/"
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(url)
                    break
                except OSError:
                    time.sleep(0.1)
            screenshots(url)
        finally:
            process.terminate()
            process.wait(timeout=10)
            shutil.rmtree(os.path.dirname(directory))
    print(f"screenshots in {OUTPUT}")


if __name__ == "__main__":
    main()
