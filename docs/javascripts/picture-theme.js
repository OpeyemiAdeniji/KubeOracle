// Diagrams use <picture> with a dark <source> so they match GitHub's theme. On the docs site the reader can
// switch light/dark themselves, so make the dark source follow the site's palette instead of the OS setting.
(function () {
  function sync() {
    var dark = document.body.getAttribute("data-md-color-scheme") === "slate";
    document.querySelectorAll("picture source[srcset*='-dark.']").forEach(function (s) {
      s.media = dark ? "all" : "not all";
    });
  }
  new MutationObserver(sync).observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });
  if (typeof document$ !== "undefined") document$.subscribe(sync);  // Material's instant navigation
  sync();
})();
