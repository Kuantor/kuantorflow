/* Folding the sections of "Browse flashcards" (#288).
 *
 * The panel is the tallest thing on the front page — eighteen tiles in the
 * curriculum section (#203) and everything anybody ever invented in `Other` —
 * so everything below it sits that far down. A section folds away by clicking
 * its heading, and stays folded.
 *
 * Three decisions are worth knowing before changing anything here:
 *
 *   1. **`<details>` does the folding, not this file.** The markup is a real
 *      disclosure widget, so it folds with JavaScript disabled, answers the
 *      keyboard, and tells a screen reader what it is — none of which a click
 *      handler on an `<h3>` gets for free. What is left for this file is the
 *      only part the browser does not do: remembering.
 *   2. **The state is per device, in `localStorage`.** A fold is presentation,
 *      not a preference about the deck. `settings_store.DEFAULTS` is the wrong
 *      home for it twice: settings are read-only for anonymous visitors
 *      (#102), who can browse and would be stuck with whatever the shared
 *      default said, and every settings write logs a `SETTINGS` line to
 *      `cards.log` (#161) — folding a heading is not an action worth a log
 *      line. The card deck's flip-animation switch is the same call (#78).
 *   3. **What is stored is what is CLOSED**, never what is open. A section
 *      nobody has touched — including one created after the last visit — is
 *      absent from the list and therefore open, which is the default the
 *      ticket asks for. Storing the open ones instead would make every new
 *      section arrive folded, hiding cards the learner just added.
 *
 * `apply()` is exported because the panel has two renderers: this page, and
 * `refreshBrowseTopics()` in base.html, which rebuilds it in place after
 * Mykola saves a card from the chat (#53). The rebuild throws the `<details>`
 * elements away and makes new ones, so without a second call every section
 * would spring open behind a chat save. Same markup, same restore, one place.
 */
(function (window, document) {
    "use strict";

    var STORAGE_KEY = "kf_browse_folds_v1";

    // Marks a `<details>` whose toggle is already being listened to, so a
    // rebuild that reuses an element cannot end up saving twice per click.
    var BOUND = "kfFoldBound";

    function hasStorage() {
        try {
            var probe = "__kf_folds_probe__";
            window.localStorage.setItem(probe, "1");
            window.localStorage.removeItem(probe);
            return true;
        } catch (e) {
            return false;
        }
    }

    /* The names of the sections that should be closed.
     *
     * Anything unreadable is treated as "nothing is folded" rather than as an
     * error: the worst case is a panel that opens as it always did, and a
     * front page must not fail to render because a stored preference went bad.
     */
    function closedNames() {
        if (!hasStorage()) return [];
        try {
            var raw = window.localStorage.getItem(STORAGE_KEY);
            var names = raw ? JSON.parse(raw) : null;
            if (!names || !names.length || typeof names.length !== "number") return [];
            return names.filter(function (n) { return typeof n === "string"; });
        } catch (e) {
            return [];
        }
    }

    function remember(names) {
        if (!hasStorage()) return;
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(names));
        } catch (e) {
            // A full or locked store costs the memory, not the fold.
        }
    }

    /* Rewrite the stored list from what is on the page right now.
     *
     * Read back off the DOM rather than edited in place, because the DOM is
     * the truth: the learner may have opened one section and closed another
     * before this runs, and a stored list patched one name at a time drifts
     * from what they can see.
     *
     * Sections **not currently on the page keep their entry.** Turning on
     * `individual_cards` (#127) can empty a section out of the panel entirely,
     * and it comes back when they turn it off again — forgetting the fold in
     * between would be this file quietly making a decision on their behalf.
     */
    function save(root) {
        var folds = (root || document).querySelectorAll("details.topic-fold");
        var onPage = {};
        var closed = [];
        Array.prototype.forEach.call(folds, function (fold) {
            var name = fold.getAttribute("data-section");
            if (!name) return;
            onPage[name] = true;
            if (!fold.open) closed.push(name);
        });
        closedNames().forEach(function (name) {
            if (!onPage[name] && closed.indexOf(name) === -1) closed.push(name);
        });
        remember(closed);
    }

    /* Fold the sections under `root` to match what was stored, and keep them
     * that way. Safe to call on a page that has none — every other page. */
    function apply(root) {
        var scope = root || document;
        var folds = scope.querySelectorAll("details.topic-fold");
        if (!folds.length) return;
        var closed = closedNames();
        Array.prototype.forEach.call(folds, function (fold) {
            var name = fold.getAttribute("data-section");
            fold.open = !(name && closed.indexOf(name) !== -1);
            if (fold[BOUND]) return;
            fold[BOUND] = true;
            // `toggle` fires for the keyboard and for a programmatic change
            // alike, which is why the listener is here rather than on the
            // summary's click: the browser owns the interaction, this file
            // only writes down the result.
            fold.addEventListener("toggle", function () { save(scope); });
        });
    }

    window.kfBrowseFolds = {
        apply: apply,
        closed: closedNames        // for tests and for debugging
    };

    // The panel is usually already parsed when this runs — index.html loads it
    // immediately after the block, so a section that should be closed never
    // gets painted open. The listener is the belt to that braces, for a load
    // order that puts this first.
    apply(document);
    document.addEventListener("DOMContentLoaded", function () { apply(document); });
})(window, document);
