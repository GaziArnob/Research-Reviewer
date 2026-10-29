/* Progressive enhancement only: every page works without this file, except live status and Q&A. */
(function () {
  "use strict";
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";

  // Live status for papers that are still being processed.
  var pending = document.querySelectorAll("[data-status-id]");
  var ids = [];
  pending.forEach(function (el) {
    var s = el.getAttribute("data-status");
    if (s === "queued" || s === "running") ids.push(el.getAttribute("data-status-id"));
  });
  ids = Array.from(new Set(ids));
  function poll() {
    fetch("/app/api/status?ids=" + ids.join(","), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        var done = false;
        rows.forEach(function (row) {
          document.querySelectorAll('[data-status-id="' + row.id + '"]').forEach(function (el) {
            var label = el.querySelector("[data-progress]");
            if (label) label.textContent = row.progress || row.status;
            if (row.status !== el.getAttribute("data-status")) done = true;
          });
        });
        if (done) { window.location.reload(); } else { setTimeout(poll, 3000); }
      })
      .catch(function () { setTimeout(poll, 6000); });
  }
  if (ids.length) setTimeout(poll, 1500);

  // Filter chips on the findings page.
  var chips = document.querySelectorAll("[data-filter]");
  if (chips.length) {
    var active = {};
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        var group = chip.getAttribute("data-group"), value = chip.getAttribute("data-filter");
        var on = chip.getAttribute("aria-pressed") === "true";
        document.querySelectorAll('[data-group="' + group + '"]').forEach(function (c) { c.setAttribute("aria-pressed", "false"); });
        if (on) { delete active[group]; } else { chip.setAttribute("aria-pressed", "true"); active[group] = value; }
        document.querySelectorAll(".finding").forEach(function (f) {
          var show = Object.keys(active).every(function (g) { return f.getAttribute("data-" + g) === active[g]; });
          f.hidden = !show;
        });
        var counter = document.getElementById("shown-count");
        if (counter) counter.textContent = document.querySelectorAll(".finding:not([hidden])").length;
      });
    });
  }

  // Ask the paper.
  var askForm = document.getElementById("ask-form");
  if (askForm) {
    askForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var q = askForm.querySelector("textarea").value.trim();
      if (!q) return;
      var btn = askForm.querySelector("button"), out = document.getElementById("ask-result");
      btn.disabled = true;
      out.innerHTML = '<p class="muted"><span class="spinner"></span> Searching the paper and answering. A local model can take about half a minute.</p>';
      fetch(askForm.getAttribute("action"), {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify({ q: q })
      }).then(function (r) { return r.text(); })
        .then(function (html) { out.innerHTML = html; btn.disabled = false; askForm.querySelector("textarea").value = ""; })
        .catch(function () { out.innerHTML = '<div class="alert a-bad"><div>Something went wrong. Try again.</div></div>'; btn.disabled = false; });
    });
  }

  // Drag and drop for the upload page.
  var zone = document.getElementById("dropzone"), input = document.getElementById("files");
  if (zone && input) {
    ["dragenter", "dragover"].forEach(function (e) { zone.addEventListener(e, function (ev) { ev.preventDefault(); zone.classList.add("is-over"); }); });
    ["dragleave", "drop"].forEach(function (e) { zone.addEventListener(e, function (ev) { ev.preventDefault(); zone.classList.remove("is-over"); }); });
    zone.addEventListener("drop", function (ev) { input.files = ev.dataTransfer.files; show(); });
    input.addEventListener("change", show);
    function show() {
      var list = document.getElementById("file-list");
      list.textContent = Array.from(input.files).map(function (f) { return f.name + " (" + Math.round(f.size / 1024) + " KB)"; }).join(", ");
    }
  }

  // Useful / not useful votes on findings.
  document.querySelectorAll(".fb").forEach(function (box) {
    var reason = box.querySelector(".fb-reason");
    function send(verdict, why, done) {
      fetch("/app/papers/" + box.getAttribute("data-sub") + "/feedback", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ finding_id: box.getAttribute("data-finding"), verdict: verdict, reason: why || null })
      }).then(function (r) { if (r.ok && done) done(); });
    }
    box.querySelectorAll("button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var verdict = parseInt(btn.getAttribute("data-verdict"), 10);
        var on = btn.getAttribute("aria-pressed") === "true";
        send(on ? 0 : verdict, "", function () {
          box.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", "false"); });
          if (!on) btn.setAttribute("aria-pressed", "true");
          if (reason) { reason.value = ""; reason.hidden = on || verdict !== -1; }
        });
      });
    });
    if (reason) reason.addEventListener("change", function () { send(-1, reason.value); });
  });

  // Confirm destructive actions.
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (ev) { if (!window.confirm(f.getAttribute("data-confirm"))) ev.preventDefault(); });
  });
})();

// Motion: reveal on scroll, product window tilt, cursor glow on cards. Decoration only; every page works without it.
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var targets = document.querySelectorAll(".reveal, .window, .steps");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { threshold: 0.15, rootMargin: "0px 0px -6% 0px" });
    targets.forEach(function (el) { io.observe(el); });
  } else {
    targets.forEach(function (el) { el.classList.add("in"); });
  }

  // The product window starts tilted back and flattens as the page scrolls.
  var win = document.getElementById("product");
  if (win && !reduce) {
    var ticking = false;
    var update = function () {
      var r = win.getBoundingClientRect(), vh = window.innerHeight || 800;
      var p = Math.min(1, Math.max(0, 1 - r.top / (vh * 0.85)));  // 0 when it enters, 1 when it is well inside the screen
      win.style.setProperty("--tilt", (14 * (1 - p)).toFixed(2) + "deg");
      win.style.setProperty("--lift", (-24 * p).toFixed(1) + "px");
      ticking = false;
    };
    window.addEventListener("scroll", function () { if (!ticking) { ticking = true; window.requestAnimationFrame(update); } }, { passive: true });
    update();
  }

  // A soft light follows the cursor over feature cards.
  document.querySelectorAll(".feature, .card.glow").forEach(function (card) {
    card.addEventListener("pointermove", function (ev) {
      var r = card.getBoundingClientRect();
      card.style.setProperty("--mx", (ev.clientX - r.left) + "px");
      card.style.setProperty("--my", (ev.clientY - r.top) + "px");
    });
  });
})();
