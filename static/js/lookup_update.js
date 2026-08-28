/* Look up & update — one implementation, two callers (#191, #372).
 *
 * The edit dialog refills a saved card; the notes-upload review popup refills
 * one that has not been written yet. Same question, same answer, so the same
 * code: a second copy is how #359 happened, one private fix in the notes
 * parser while the dictionaries went without, and how #363's four surfaces
 * would have drifted.
 *
 * It **writes nothing**. It fills form fields and stops, so whatever route the
 * form posts to remains the only thing that touches the database — the edit
 * dialog's `edit_card()` with its ownership rule, or the popup's `add_card()`
 * with its duplicate rule.
 *
 * The two popups it drives live in base.html, so every page has them.
 */
(function () {
    "use strict";

    // Examples are one per line in a form and a list in the answer; every
    // other field is a string. One place that knows it.
    var LIST_FIELDS = ["examples_en", "examples_ukr", "examples_rus"];

    // Read to ask the question, never written back: the lookup was made *for*
    // this word, and `topic` is where the card lives rather than what it says.
    var NEVER_FILLED = ["word", "pos", "topic"];

    // Both popups hold a promise a lookup is waiting on, so every way out has
    // to answer it (#369). Escape used to hide the overlay directly, which
    // left the promise pending: the button stayed disabled on "Looking up…"
    // and the next press did nothing at all.
    var dismissPos = null;
    var dismissRewrite = null;

    function el(id) { return document.getElementById(id); }

    function incomingValue(entry, field) {
        var value = entry[field];
        if (value === null || value === undefined) return "";
        return LIST_FIELDS.indexOf(field) === -1
            ? String(value) : value.join("\n");
    }

    /* The fields this form will let a lookup fill.
     *
     * **What the learner can see, and nothing else** (#372). A proposal card
     * carries a language hidden in Settings as a hidden input; filling it
     * would put a translation on the card that its owner cannot see and did
     * not ask for. Skipping it costs nothing, because that input keeps the
     * value the parser gave it. The edit dialog renders no field at all for a
     * hidden language, so the same rule is simply never tested there.
     */
    function fillable(form) {
        return Array.prototype.map.call(form.elements, function (input) {
            return input;
        }).filter(function (input) {
            return input.name
                && NEVER_FILLED.indexOf(input.name) === -1
                && input.type !== "hidden";
        }).map(function (input) { return input.name; });
    }

    function fieldLabel(form, field) {
        var input = form.elements[field];
        var label = input && input.labels && input.labels[0];
        return label ? label.textContent.replace(/\s+/g, " ").trim() : field;
    }

    function conflictsFor(form, entry) {
        return fillable(form).filter(function (field) {
            var incoming = incomingValue(entry, field);
            var current = form.elements[field].value.trim();
            return incoming && current && incoming !== current;
        });
    }

    function applyEntry(form, entry, replace) {
        fillable(form).forEach(function (field) {
            var incoming = incomingValue(entry, field);
            // Nothing found for a field leaves it alone: an empty answer must
            // not empty a card.
            if (!incoming) return;
            var current = form.elements[field].value.trim();
            if (!current || replace.indexOf(field) !== -1) {
                form.elements[field].value = incoming;
                // #357's boxes size themselves to their content, and a value
                // set from script fires no input event of its own.
                form.elements[field].dispatchEvent(
                    new Event("input", { bubbles: true }));
            }
        });
    }

    function askWhichPos(entries) {
        return new Promise(function (resolve) {
            var overlay = el("lookup-pos-modal");
            // One entry is not "none of them": the plural was pluralised and
            // the rest of the sentence was not, which reads as broken English
            // to the people this app is for.
            el("pos-question").textContent = entries.length === 1
                ? "The lookup found one entry, and it does not match this "
                  + "card's part of speech."
                : "The lookup found " + entries.length + " entries, none of "
                  + "them matching this card's part of speech.";
            var options = el("pos-options");
            options.innerHTML = "";
            entries.forEach(function (entry) {
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className = "pos-option";
                btn.textContent = entry.pos || "no part of speech";
                btn.addEventListener("click", function () {
                    overlay.hidden = true;
                    dismissPos = null;
                    resolve(entry);
                });
                options.appendChild(btn);
            });
            function cancel() {
                overlay.hidden = true;
                dismissPos = null;
                resolve(null);
            }
            dismissPos = cancel;
            el("pos-cancel").onclick = cancel;
            el("pos-close").onclick = cancel;
            overlay.hidden = false;
        });
    }

    function confirmRewrites(form, entry, conflicts) {
        return new Promise(function (resolve) {
            var overlay = el("field-rewrite-confirm-popup");
            var rows = el("rewrite-fields");
            rows.innerHTML = "";
            conflicts.forEach(function (field) {
                var row = document.createElement("label");
                row.className = "rewrite-row";
                var box = document.createElement("input");
                box.type = "checkbox";
                box.checked = true;              // they pressed *update*
                box.dataset.field = field;
                var text = document.createElement("div");
                var name = document.createElement("div");
                name.className = "rewrite-field";
                name.textContent = fieldLabel(form, field);
                var was = document.createElement("div");
                was.className = "rewrite-was";
                was.textContent = form.elements[field].value.trim();
                var now = document.createElement("div");
                now.className = "rewrite-now";
                now.textContent = incomingValue(entry, field);
                text.appendChild(name);
                text.appendChild(was);
                text.appendChild(now);
                row.appendChild(box);
                row.appendChild(text);
                rows.appendChild(row);
            });
            function finish(fields) {
                overlay.hidden = true;
                dismissRewrite = null;
                resolve(fields);
            }
            dismissRewrite = function () { finish(null); };
            el("rewrite-apply").onclick = function () {
                finish(Array.prototype.slice.call(
                    rows.querySelectorAll("input:checked"))
                    .map(function (b) { return b.dataset.field; }));
            };
            el("rewrite-keep").onclick = function () { finish([]); };
            el("rewrite-close").onclick = dismissRewrite;
            overlay.hidden = false;
        });
    }

    /* Fill one form from the providers.
     *
     * `options.form` is the form to fill, `options.url` the lookup endpoint,
     * `options.button` the control to show progress on, and `options.onError`
     * is handed a sentence to put in front of the learner. Resolves when the
     * fields have been filled, or when the learner has said not to.
     */
    window.kfLookupUpdate = function (options) {
        var form = options.form;
        var button = options.button;
        var word = (form.elements.word.value || "").trim();
        if (!word) {
            options.onError("Type a word to look up.");
            return Promise.resolve();
        }
        var label = button.innerHTML;
        button.disabled = true;
        button.textContent = "Looking up…";
        return fetch(options.url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                word: word,
                pos: (form.elements.pos.value || "").trim(),
            }),
        }).then(function (resp) {
            return resp.json().then(function (data) {
                if (!resp.ok) throw new Error(data.error || "The lookup failed.");
                return data;
            });
        }).then(function (data) {
            // "Nothing to apply" is the question, not "how long is the list".
            // A match is what gets applied, and asking the list first meant a
            // usable answer could be refused for the shape of the field
            // beside it.
            if (!data.match && !data.entries.length) {
                throw new Error("Nothing came back for that word. "
                                + "Your card is unchanged.");
            }
            return data.match || askWhichPos(data.entries);
        }).then(function (entry) {
            if (!entry) return;                  // the picker was dismissed
            var conflicts = conflictsFor(form, entry);
            if (!conflicts.length) {
                applyEntry(form, entry, []);
                return;
            }
            return confirmRewrites(form, entry, conflicts)
                .then(function (replace) {
                    // null is "changed my mind" and touches nothing; an empty
                    // array is "fill the empty ones only".
                    if (replace !== null) applyEntry(form, entry, replace);
                });
        }).catch(function (err) {
            options.onError(err.message);
        }).then(function () {
            button.disabled = false;
            button.innerHTML = label;
        });
    };

    /* Let a page's Escape handler close the innermost popup, through the same
     * dismissal its buttons use. Returns whether it handled the key.
     */
    window.kfLookupEscape = function () {
        if (dismissPos) { dismissPos(); return true; }
        if (dismissRewrite) { dismissRewrite(); return true; }
        return false;
    };
})();
