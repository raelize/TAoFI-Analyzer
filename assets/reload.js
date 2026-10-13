/* Reloads the page when the server's callbacks changed (e.g. the analyzer was
 * updated and restarted while this tab stayed open). A page with the old
 * callbacks only gets errors from the new server ("Callback function not
 * found for output ..."), so reloading is the only way to recover.
 *
 * The callbacks are compared via Dash's own /_dash-dependencies, which is the
 * same for every gunicorn worker and only changes when the code does. */
(function () {
    const CHECK_INTERVAL = 60 * 1000;

    function prefix() {
        try {
            const config = JSON.parse(document.getElementById("_dash-config").textContent);
            return config.requests_pathname_prefix || "/";
        } catch (e) {
            return "/";
        }
    }

    let loaded = null; // the callbacks this page was loaded with
    let checking = false;

    function dependencies() {
        return fetch(prefix() + "_dash-dependencies", { cache: "no-store" }).then((response) =>
            response.ok ? response.text() : null
        );
    }

    function check() {
        if (loaded === null || checking) return;
        checking = true;
        dependencies()
            .then((current) => {
                // null: server (re)starting, try again later
                if (current !== null && current !== loaded) window.location.reload();
            })
            .catch(() => {})
            .finally(() => {
                checking = false;
            });
    }

    window.addEventListener("load", () => {
        dependencies()
            .then((text) => {
                loaded = text;
            })
            .catch(() => {});
    });

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") check();
    });
    window.addEventListener("focus", check);
    setInterval(() => {
        if (document.visibilityState === "visible") check();
    }, CHECK_INTERVAL);

    // a failing callback request is the typical symptom of a stale page
    const originalFetch = window.fetch;
    window.fetch = function (input, init) {
        return originalFetch.apply(this, arguments).then((response) => {
            const url = typeof input === "string" ? input : input && input.url;
            if (response.status >= 500 && url && url.indexOf("_dash-update-component") !== -1) check();
            return response;
        });
    };
})();
