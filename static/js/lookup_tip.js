/* The first-time tip above the lookup box (#19).
 *
 * It is shown on every load and removed the moment the visitor touches the
 * box -- deliberately no localStorage. A "seen once" flag sounds tidier and is
 * wrong for who this is for: the tip exists for somebody meeting the site for
 * the first time, and the person most likely to have set that flag is the
 * owner testing, who would then never see it again and could not tell whether
 * it still worked.
 *
 * Hidden rather than deleted, and with `hidden` rather than a style, so the
 * markup stays readable and nothing has to know about the CSS.
 */
(function () {
    "use strict";

    var tip = document.getElementById("lookup-tip");
    var box = document.getElementById("word");
    if (!tip) { return; }

    function dismiss() {
        tip.hidden = true;
    }

    // Focus covers the tab key as well as a click, and `input` covers a
    // browser filling the field in without either.
    if (box) {
        box.addEventListener("focus", dismiss, { once: true });
        box.addEventListener("input", dismiss, { once: true });
    }

    var close = tip.querySelector(".lookup-tip__close");
    if (close) {
        close.addEventListener("click", dismiss);
    }
}());
