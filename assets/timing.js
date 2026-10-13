/* Measures, in the browser, how long the last content update took: from the
 * moment the callback request is sent until the response has been downloaded
 * and the page has rendered it. Shown next to the server-side timing badge. */
(function () {
    const LABELS = {
        "graph.figure": "graph",
        "data.children": "data",
        "query-output.children": "query",
        "tabs-content.children": "tab",
    };

    function labelFor(body) {
        try {
            const output = JSON.parse(body).output || "";
            if (output.endsWith(".getRowsResponse")) return "rows";
            return LABELS[output] || null;
        } catch (e) {
            return null;
        }
    }

    // wait until the main thread has processed the response and painted twice
    function afterRender(fn) {
        setTimeout(() => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(fn, 0))), 0);
    }

    function drain(response) {
        const reader = response.body && response.body.getReader();
        if (!reader) return Promise.resolve();
        return (function pump() {
            return reader.read().then((r) => (r.done ? null : pump()));
        })();
    }

    function show(text) {
        const element = document.getElementById("timing-client");
        if (element) element.textContent = text;
    }

    // start of the last data-table request: the first page of rows that follows
    // it is measured from there, so the client timer covers the whole Data tab
    let pendingDataStart = null;

    const originalFetch = window.fetch;
    window.fetch = function (input, init) {
        const url = typeof input === "string" ? input : input && input.url;
        const body = init && typeof init.body === "string" ? init.body : null;
        const label = url && url.indexOf("_dash-update-component") !== -1 && body ? labelFor(body) : null;
        if (!label) return originalFetch.apply(this, arguments);

        let started = performance.now();
        if (label === "data") pendingDataStart = started;
        if (label === "rows" && pendingDataStart !== null) {
            started = pendingDataStart;
            pendingDataStart = null;
        }
        show("client (...) / ");
        return originalFetch.apply(this, arguments).then((response) => {
            // read a copy of the body so we know when the download has finished
            drain(response.clone()).then(() =>
                afterRender(() => show("client (" + ((performance.now() - started) / 1000).toFixed(2) + " s) / "))
            );
            return response;
        });
    };
})();
