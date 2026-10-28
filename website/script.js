// Chiku website: theme switch, sticky-nav shadow, scroll reveal, and the latest version.
(function () {
  var root = document.documentElement;

  function currentTheme() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  document.getElementById("theme").addEventListener("click", function () {
    var next = currentTheme() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("chiku-theme", next); } catch (e) {}
  });

  var nav = document.querySelector(".nav");
  function onScroll() { nav.classList.toggle("scrolled", window.scrollY > 8); }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // Fade sections in as they scroll into view.
  var targets = document.querySelectorAll(".section .card, .section h2, .checks li, .flow li, .duo figure");
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    targets.forEach(function (t) { t.classList.add("reveal"); io.observe(t); });
  }

  // Show the latest published version when GitHub answers (the page works without it).
  var line = document.getElementById("version-line");
  if (line && window.fetch) {
    fetch("https://api.github.com/repos/Intruder-tech/Chiku/releases/latest")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rel) {
        if (rel && rel.tag_name) {
          line.textContent = "Latest version " + rel.tag_name.replace(/^v/, "") +
            " · Windows 10/11 · no admin rights needed";
        }
      })
      .catch(function () {});
  }
})();
