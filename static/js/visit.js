/* The visit check-in (#598): tells the server this browser is still here,
 * and for how long the page was actually in front.
 *
 * While the tab is visible it posts `/visit/ping` about once a minute with
 * the seconds it was visible since the last check-in. The first check-in waits
 * until the page has been in front for five seconds in all, so a glance -- or
 * a robot that runs scripts and leaves -- is not a visit. When the tab is
 * hidden or the page is left, it sends what it has with `sendBeacon` (which
 * survives the page going away) and stops until it is shown again.
 *
 * The server keeps one row per visit (`utils.record_visit`); #599 emails a
 * signed-in learner's visit when it ends and #600 counts visitors each day.
 * Loaded by base.html and deck.html with `data-url` naming the route.
 */
(function () {
    "use strict";

    var script = document.currentScript;
    var URL = (script && script.dataset && script.dataset.url) || "/visit/ping";
    var FIRST_MS = 5000;     // in front this long, in all, before the first check-in
    var EVERY_MS = 60000;    // then about once a minute while visible

    var started = false;     // has this page checked in yet?
    var pageSent = false;    // the first check-in of a page counts the page
    var visibleSince = null; // when the current visible stretch began
    var carriedMs = 0;       // visible time not yet reported
    var timer = null;

    function now() { return Date.now(); }

    // The visible seconds since the last check-in, and start counting afresh.
    function collect() {
        if (visibleSince !== null) {
            carriedMs += now() - visibleSince;
            visibleSince = now();
        }
        var seconds = Math.round(carriedMs / 1000);
        carriedMs = 0;
        return seconds;
    }

    function send(asBeacon) {
        var body = JSON.stringify({ active: collect(), page: !pageSent });
        pageSent = true;
        started = true;
        try {
            if (asBeacon && navigator.sendBeacon) {
                navigator.sendBeacon(URL, new Blob([body], { type: "application/json" }));
                return;
            }
            fetch(URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: body,
                credentials: "same-origin",
                keepalive: true
            }).catch(function () {});
        } catch (e) {
            // A check-in is never worth an error on the page.
        }
    }

    function schedule() {
        clearTimeout(timer);
        var shown = visibleSince === null ? 0 : now() - visibleSince;
        var wait = started ? EVERY_MS : Math.max(0, FIRST_MS - carriedMs - shown);
        timer = setTimeout(tick, wait);
    }

    function tick() {
        if (document.visibilityState !== "visible") return;
        send(false);
        schedule();
    }

    function onShow() {
        if (visibleSince === null) visibleSince = now();
        schedule();
    }

    function onHide() {
        clearTimeout(timer);
        if (visibleSince !== null) {
            carriedMs += now() - visibleSince;
            visibleSince = null;
        }
        // Before the first check-in this page is not a visit yet, so a short
        // look sends nothing; after it, what was seen is sent once.
        if (started && carriedMs >= 1000) send(true);
    }

    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible") onShow();
        else onHide();
    });
    window.addEventListener("pagehide", onHide);

    if (document.visibilityState === "visible") onShow();
})();
