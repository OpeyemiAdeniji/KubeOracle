// KubeOracle site interactions: theme, menu, install tabs, copy, animated terminal, reveals, counters.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Theme toggle (stored per visitor)
  $("#theme")?.addEventListener("click", () => {
    const root = document.documentElement;
    const isLight = root.dataset.theme === "light" ||
      (!root.dataset.theme && window.matchMedia("(prefers-color-scheme: light)").matches);
    root.dataset.theme = isLight ? "dark" : "light";
    try { localStorage.setItem("ko-theme", root.dataset.theme); } catch (e) {}
  });

  // Mobile menu
  const menuBtn = $("#menu"), menu = $("#mobile-menu");
  menuBtn?.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    menuBtn.setAttribute("aria-expanded", String(open));
  });
  $$("#mobile-menu a").forEach(a => a.addEventListener("click", () => { menu.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); }));

  // Marquee: duplicate the items so the -50% loop is seamless (copy hidden from screen readers)
  const track = $(".marquee-track");
  if (track) {
    const clone = document.createElement("span");
    clone.setAttribute("aria-hidden", "true");
    clone.style.display = "contents";
    clone.innerHTML = track.innerHTML;
    track.appendChild(clone);
  }

  // Install tabs
  $$("[data-tabs] [role=tab]").forEach(tab => tab.addEventListener("click", () => {
    $$("[data-tabs] [role=tab]").forEach(t => t.setAttribute("aria-selected", String(t === tab)));
    $("#cmd-text").textContent = tab.dataset.cmd;
  }));

  // Copy buttons
  $$("[data-copy-target]").forEach(btn => btn.addEventListener("click", async () => {
    const text = document.getElementById(btn.dataset.copyTarget).textContent;
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied"; btn.classList.add("done");
    } catch (e) {
      btn.textContent = "Select & copy";
    }
    setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("done"); }, 1800);
  }));

  // Animated terminal: a real-looking KubeOracle diagnosis
  const term = $("#term");
  const script = [
    { type: "cmd", text: "kubectl oracle diagnose -n shop" },
    { type: "out", html: '<span class="t-dim">Reading cluster state… 412 objects · 3 failing containers · logs collected</span>', delay: 500 },
    { type: "out", html: '<span class="t-dim">Running 60 checks · correlating 9 changes in the last 6h</span>', delay: 450 },
    { type: "out", html: "", delay: 250 },
    { type: "out", html: '<span class="t-mag">╭─ Diagnosis ─────────────────────────────────────────────────────╮</span>', delay: 300 },
    { type: "out", html: '  <span class="t-bold">1. Deployment/shop/checkout: container is crash looping</span>  <span class="t-dim">[85%]</span>', delay: 250 },
    { type: "out", html: "     Exit code 1. The app expects env var <span class=\"t-bold\">DATABASE_URL</span>, which is not set.", delay: 200 },
    { type: "out", html: '     Started after: <span class="t-mag">rollout revision 2 · checkout:1.4 → 1.5 · env removed DATABASE_URL</span>', delay: 200 },
    { type: "out", html: '     <span class="t-ok">→ Roll back first, then restore the variable.</span>', delay: 200 },
    { type: "out", html: '     <span class="t-cmd">$ kubectl rollout undo deployment/checkout -n shop --to-revision=1</span>', delay: 200 },
    { type: "out", html: "", delay: 150 },
    { type: "out", html: '  <span class="t-bold">2. Service/shop/web: no ready backends</span>  <span class="t-dim">[85%]</span>', delay: 250 },
    { type: "out", html: '     Selector <span class="t-bold">app=wbe</span> matches no pods. Pods nearby are labelled <span class="t-bold">app=web</span>.', delay: 200 },
    { type: "out", html: '<span class="t-mag">╰─────────────────────────────────────────────────────────────────╯</span>', delay: 250 },
    { type: "out", html: '<span class="t-crit">CRITICAL</span> 3   <span class="t-high">HIGH</span> 4   <span class="t-dim">· 12 configuration risks (--risks to list)</span>', delay: 300 },
  ];

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const render = lines => { term.innerHTML = lines.join("\n"); };

  async function play() {
    if (!term) return;
    const lines = [];
    for (const step of script) {
      if (step.type === "cmd") {
        const base = '<span class="t-p">$</span> ';
        for (let i = 0; i <= step.text.length; i++) {
          render([...lines, base + step.text.slice(0, i) + '<span class="cursor"></span>']);
          await sleep(reduced ? 0 : 38 + Math.random() * 40);
        }
        lines.push(base + step.text);
        await sleep(reduced ? 0 : 450);
      } else {
        lines.push(step.html);
        render([...lines, '<span class="cursor"></span>']);
        await sleep(reduced ? 0 : step.delay || 200);
      }
    }
    render([...lines, '<span class="t-p">$</span> <span class="cursor"></span>']);
  }

  if (term) {
    if (reduced || !("IntersectionObserver" in window)) {
      play();
    } else {
      const io = new IntersectionObserver(entries => {
        if (entries.some(e => e.isIntersecting)) { io.disconnect(); play(); }
      }, { threshold: 0.25 });
      io.observe(term);
    }
  }

  // Reveal on scroll
  if ("IntersectionObserver" in window && !reduced) {
    const io = new IntersectionObserver(entries => entries.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { threshold: 0, rootMargin: "0px 0px 160px 0px" });  // start just before it scrolls into view
    $$(".reveal").forEach(el => io.observe(el));
  } else {
    $$(".reveal").forEach(el => el.classList.add("in"));
  }

  // Count-up numbers
  const counters = $$("[data-count]");
  const countUp = el => {
    const target = Number(el.dataset.count);
    if (reduced) { el.textContent = target; return; }
    const start = performance.now(), dur = 1100;
    const tick = now => {
      const p = Math.min((now - start) / dur, 1);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(entries => entries.forEach(e => {
      if (e.isIntersecting) { countUp(e.target); io.unobserve(e.target); }
    }), { threshold: 0.5 });
    counters.forEach(el => io.observe(el));
  } else {
    counters.forEach(el => { el.textContent = el.dataset.count; });
  }
})();
