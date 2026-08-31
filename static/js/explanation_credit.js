/* The credit stops being true the moment somebody rewrites the sentence (#390).
 *
 * Wiktionary's definitions are CC BY-SA, so a card that carries one carries a
 * credit naming where it came from. A learner is free to edit that text -- both
 * dialogs let them -- and the edited sentence is then theirs, not the
 * dictionary's. Leaving the credit on it would put other people's names on
 * their words, which is the one misattribution the licence actually cares
 * about.
 *
 * So `explanation_source` is cleared as soon as anybody types in an
 * explanation box, in either dialog and in any dialog added later. The server
 * does not trust this -- `update_flashcard()` clears the stored credit
 * whenever the text changes, and `_explanation_source()` validates what does
 * arrive -- but doing it here as well means the hidden field is never lying
 * about what is in the box beside it.
 *
 * **`isTrusted` is the whole trick.** #372's lookup fills that same box from
 * script and dispatches an `input` event so the textarea can resize, and a
 * handler that could not tell the two apart would wipe the credit of the very
 * lookup that earned it. A synthetic event is never trusted; a keystroke
 * always is.
 */
(function () {
    document.addEventListener("input", function (event) {
        var box = event.target;
        if (!event.isTrusted || !box || box.name !== "explanation_en") return;
        var credit = box.form && box.form.elements.explanation_source;
        if (credit) credit.value = "";
    });
})();
