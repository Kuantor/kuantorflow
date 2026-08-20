/* Speech in the browser (#268), first used by the pronounce buttons (#283).
 *
 * The app's first audio. Everything here happens on the visitor's device:
 * no API key, no server round trip, nothing stored, nothing sent anywhere —
 * `speechSynthesis` reads with voices the operating system already ships.
 *
 * Two rules shape the whole file, and both come from iOS:
 *
 *   1. `speak()` is **synchronous from the user's gesture**. iOS grants the
 *      permission to make a sound to the click handler itself, and loses it
 *      the moment the handler awaits anything. So nothing — not the voice
 *      list, not a readiness check — is allowed to sit between the press and
 *      the call. A helper that resolved a promise first would be silent on
 *      iPhones and perfect everywhere else, which is the worst way to be
 *      wrong.
 *   2. **An unknown voice list is not the same as no voices.** `getVoices()`
 *      is commonly empty on the first call and fills in later; on iOS it can
 *      stay empty longer than anyone wants to wait. Speaking with the
 *      browser's default voice beats waiting for the right one and saying
 *      nothing, and hiding the buttons on an empty list would hide a feature
 *      that works.
 */
(function (window, document) {
    "use strict";

    var SUPPORTED = "speechSynthesis" in window
        && typeof window.SpeechSynthesisUtterance === "function";

    // How long to let the voice list settle before deciding whether English is
    // available. `voiceschanged` usually arrives in a few hundred ms; this is
    // the ceiling, not the expected wait, and nothing is blocked on it.
    var SETTLE_MS = 1200;

    var voices = [];
    var decided = false;
    var listeners = [];

    function refresh() {
        try {
            voices = window.speechSynthesis.getVoices() || [];
        } catch (err) {
            voices = [];
        }
        return voices;
    }

    function isEnglish(voice) {
        return !!voice && typeof voice.lang === "string"
            && voice.lang.toLowerCase().indexOf("en") === 0;
    }

    /* The voice to read English with, or null for "let the browser choose".
     *
     * Local voices first: some browsers synthesise their nicer voices on the
     * vendor's servers, which sends the word off the device. It is one public
     * dictionary headword and the exposure is small, but the device can
     * usually do the job itself, and preferring that keeps it here. Then
     * en-GB, then en-US, then any English at all. */
    function pickVoice() {
        var english = voices.filter(isEnglish);
        if (!english.length) return null;
        var order = [
            function (v) { return v.localService && /^en-gb/i.test(v.lang); },
            function (v) { return v.localService && /^en-us/i.test(v.lang); },
            function (v) { return v.localService; },
            function (v) { return /^en-gb/i.test(v.lang); },
            function (v) { return /^en-us/i.test(v.lang); }
        ];
        for (var i = 0; i < order.length; i++) {
            var found = english.filter(order[i])[0];
            if (found) return found;
        }
        return english[0];
    }

    /* Say `text`. Call this **directly from a click handler** — see rule 1. */
    /* How fast to speak, from the identity's `speech_rate` setting (#268).
     *
     * Read from the document **at the moment of speaking**, not cached at load:
     * the Settings popup rewrites the attribute when it saves, and re-reading
     * is what makes the next press already at the new speed without a reload.
     *
     * Stored as a percentage because the settings store's RANGES holds whole
     * numbers; dividing by 100 here is the "point of use" that arrangement
     * exists for. Anything missing or unparseable falls back to normal speed —
     * a page that never rendered the attribute must still be able to speak. */
    function rate() {
        var raw = document.body && document.body.getAttribute("data-speech-rate");
        var percent = parseInt(raw, 10);
        if (!percent || percent < 10 || percent > 400) return 1;
        return percent / 100;
    }

    function speak(text) {
        if (!SUPPORTED || !text) return false;
        try {
            // Pressing again should repeat the word, not queue a second
            // reading behind the first. Also clears the stuck "speaking"
            // state iOS can be left in after the screen locks.
            window.speechSynthesis.cancel();
            var utterance = new window.SpeechSynthesisUtterance(String(text));
            utterance.rate = rate();
            var voice = pickVoice();
            if (voice) {
                utterance.voice = voice;
                utterance.lang = voice.lang;
            } else {
                // No English voice known *yet*. Asking for the language still
                // steers a browser whose list has not arrived, and is better
                // than refusing to speak.
                utterance.lang = "en-GB";
            }
            window.speechSynthesis.speak(utterance);
            return true;
        } catch (err) {
            return false;                 // never let a missing voice throw
        }
    }

    /* Whether English speech looks available, once the list has settled.
     *
     * Called back with true or false, exactly once, and **false only when we
     * positively know**: a populated list with no English in it. An empty list
     * counts as available, because on iOS "empty" mostly means "not yet". */
    function whenDecided(callback) {
        if (typeof callback !== "function") return;
        if (!SUPPORTED) { callback(false); return; }
        if (decided) { callback(verdict()); return; }
        listeners.push(callback);
    }

    function verdict() {
        return !voices.length || voices.some(isEnglish);
    }

    function settle() {
        if (decided) return;
        decided = true;
        var answer = verdict();
        listeners.splice(0).forEach(function (cb) { cb(answer); });
    }

    if (SUPPORTED) {
        refresh();
        if (typeof window.speechSynthesis.addEventListener === "function") {
            window.speechSynthesis.addEventListener("voiceschanged", function () {
                refresh();
                settle();
            });
        } else {
            window.speechSynthesis.onvoiceschanged = function () {
                refresh();
                settle();
            };
        }
        // The event does not fire at all in browsers whose list was ready
        // immediately, so the timeout is the decision, not a fallback.
        window.setTimeout(function () { refresh(); settle(); }, SETTLE_MS);
    }

    window.kfSpeech = {
        supported: SUPPORTED,
        speak: speak,
        whenDecided: whenDecided,
        voices: function () { return voices.slice(); }   // for debugging
    };

    /* --- the buttons ------------------------------------------------------
     *
     * One delegated listener rather than a handler per card: a topic can hold
     * forty of these, and the click still has to reach `speak()` synchronously
     * (rule 1) — delegation costs nothing there, since the handler runs inside
     * the same gesture.
     *
     * Any element carrying `data-say` is a pronounce button, so a new surface
     * (the review popup, quiz results) needs markup and nothing else. */
    function ready() {
        var buttons = document.querySelectorAll("[data-say]");
        if (!buttons.length) return;

        if (!SUPPORTED) {
            hide(buttons);
            return;
        }
        buttons.forEach(function (el) { el.hidden = false; });
        whenDecided(function (ok) {
            if (!ok) hide(document.querySelectorAll("[data-say]"));
        });

        // **Capture phase**, and that is the whole trick. The deck binds its
        // flip to the card itself (#78), so a listener here on the way *up*
        // would run after the card had already turned over — stopping
        // propagation then is too late, and pressing the speaker would flip
        // the card as well. Capture runs from the document down, so this sees
        // the click first and can keep it from reaching anything else.
        //
        // Delegation rather than a listener per button: a topic holds forty of
        // these. It costs nothing on the gesture, since the handler still runs
        // inside it — which is what iOS requires (rule 1 at the top).
        document.addEventListener("click", function (event) {
            var button = event.target.closest ? event.target.closest("[data-say]") : null;
            if (!button) return;
            event.preventDefault();
            event.stopPropagation();
            speak(button.getAttribute("data-say"));
        }, true);
    }

    function hide(buttons) {
        Array.prototype.forEach.call(buttons, function (el) { el.hidden = true; });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", ready);
    } else {
        ready();
    }
})(window, document);
