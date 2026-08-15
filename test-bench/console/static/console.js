/* BHS simulation console.
   Talks to the local console server: GET /api/state to read, POST to change.
   No framework, no build: the bench should start with `docker compose up`. */

(function () {
  "use strict";

  const SLIDERS = ["arrival_rate", "dwell_minutes", "stuck_bags"];
  const NUMERIC = ["day_speed_min"];        // segmented controls holding numbers
  const POLL_MS = 2000;

  let state = null;
  let holding = false;   // true while a slider is being dragged
  let pending = null;    // debounce timer

  const $ = (id) => document.getElementById(id);

  // Shared secret, only used when the bench is exposed (see
  // docker-compose.vps.yml). Passed once as ?token=... then kept for the tab,
  // so the address bar can be cleaned up afterwards.
  const TOKEN = (function () {
    const fromUrl = new URLSearchParams(location.search).get("token");
    if (fromUrl) sessionStorage.setItem("bhs-console-token", fromUrl);
    return sessionStorage.getItem("bhs-console-token") || "";
  })();

  const UNITS = {
    arrival_rate: (v) => `${v} bags/h`,
    dwell_minutes: (v) => `${Number(v).toFixed(1)} min`,
    stuck_bags: (v) => `${v}`,
  };

  // ---------------------------------------------------------------- server

  async function send(path, body) {
    const headers = { "Content-Type": "application/json" };
    if (TOKEN) headers["X-Console-Token"] = TOKEN;

    try {
      const res = await fetch(path, {
        method: "POST",
        headers,
        body: JSON.stringify(body || {}),
      });
      if (res.status === 403) {
        note("Read-only: this bench expects a console token. Open it with ?token=…");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      render(await res.json());
      note("Applied — dashboard follows within 5 s");
    } catch (err) {
      note(`Console server unreachable: ${err.message}`);
    }
  }

  async function poll() {
    if (holding) return;
    try {
      const res = await fetch("/api/state");
      if (res.ok) render(await res.json());
    } catch (err) {
      note("Console server unreachable.");
    }
  }

  function patch(changes) {
    clearTimeout(pending);
    pending = setTimeout(() => send("/api/state", changes), 120);
  }

  function note(message) {
    $("status-line").textContent = message;
  }

  // ---------------------------------------------------------------- colour

  function tone(value, warn, crit) {
    if (value >= crit) return "red";
    if (value >= warn) return "orange";
    return "green";
  }

  const BANNER_TEXT = {
    OK: "DONNÉES À JOUR",
    IDLE: "SYSTÈME AU REPOS : AUCUN BAGAGE ATTENDU",
    STALE: "DATA STALE : DONNÉES FIGÉES DEPUIS PLUS DE 5 MIN",
  };

  function bannerTone(banner) {
    if (banner === "STALE") return "red";
    if (banner === "IDLE") return "blue";
    return "green";
  }

  // ------------------------------------------------------------ scenarios

  // The worst colour a scenario produces, used for its left-edge marker.
  function severity(item, thresholds) {
    const tones = [
      tone(item.bags, thresholds.bags[0], thresholds.bags[1]),
      tone(item.dwell, thresholds.dwell[0], thresholds.dwell[1]),
      bannerTone(item.banner),
      item.phantoms_per_hour > 0 ? "red" : "green",
    ];
    if (tones.includes("red")) return "red";
    if (tones.includes("orange")) return "orange";
    if (tones.includes("blue")) return "blue";
    return "green";
  }

  function buildScenarios(items, thresholds) {
    const host = $("scenarios");
    host.innerHTML = "";

    items.forEach((item) => {
      const card = document.createElement("button");
      card.className = "scenario";
      card.type = "button";
      card.setAttribute("aria-pressed", "false");
      card.dataset.name = item.name;

      const chips = [
        { text: `${item.bags}`, tone: tone(item.bags, thresholds.bags[0], thresholds.bags[1]) },
        { text: `${item.dwell}m`, tone: tone(item.dwell, thresholds.dwell[0], thresholds.dwell[1]) },
        { text: item.banner, tone: bannerTone(item.banner) },
      ];
      if (item.phantoms_per_hour > 0) {
        chips.push({ text: `+${item.phantoms_per_hour}/h`, tone: "red" });
      }

      card.innerHTML =
        `<span class="scenario-flag" data-tone="${severity(item, thresholds)}"></span>` +
        `<span class="scenario-body">` +
          `<span class="scenario-title">${item.title}</span>` +
          `<span class="scenario-note">${item.note}</span>` +
        `</span>` +
        `<span class="scenario-chips">` +
        chips.map((c) => `<span class="chip" data-tone="${c.tone}">${c.text}</span>`).join("") +
        `</span>`;

      card.addEventListener("click", () => send("/api/scenario", { name: item.name }));
      host.appendChild(card);
    });
  }

  // ------------------------------------------------------------ stuck bags

  function paintStuck(dwells, thresholds) {
    const host = $("stuck-cells");
    const [red, ghost] = thresholds.stuck;

    if (!dwells.length) {
      host.innerHTML = `<span class="empty">No bag over 15 min</span>`;
      return;
    }
    host.innerHTML = dwells
      .map((dwell) => {
        const t = dwell >= ghost ? "grey" : dwell >= red ? "red" : "green";
        return `<span data-tone="${t}" title="${dwell} min in system">${dwell}</span>`;
      })
      .join("");
  }

  // -------------------------------------------------------------- day bars

  function buildDayChart() {
    const host = $("daychart");
    if (host.childElementCount === 24) return;
    host.innerHTML = "";
    for (let hour = 0; hour < 24; hour += 1) host.appendChild(document.createElement("span"));
  }

  function paintDayChart(throughput, simHour) {
    const bars = $("daychart").children;
    const peak = Math.max(1, ...throughput);
    const currentHour = Math.floor(simHour) % 24;

    for (let i = 0; i < bars.length; i += 1) {
      const height = Math.max(2, Math.round((throughput[i] / peak) * 100));
      bars[i].style.height = `${height}%`;
      // Buckets run oldest first and end on the current hour.
      const hour = (currentHour - (bars.length - 1 - i) + 48) % 24;
      bars[i].dataset.now = String(i === bars.length - 1);
      bars[i].title = `${String(hour).padStart(2, "0")}:00 — ${throughput[i]} bags/h`;
      if (hour % 6 === 0) {
        bars[i].dataset.tick = `${String(hour).padStart(2, "0")}h`;
      } else {
        delete bars[i].dataset.tick;
      }
    }
  }

  // ----------------------------------------------------- segmented controls

  function paintSegmented(settings) {
    document.querySelectorAll(".segmented").forEach((group) => {
      const key = group.dataset.control;
      const current = String(settings[key]);
      group.querySelectorAll("button").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.value === current));
      });
    });
  }

  // One delegated listener rather than one per button.
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".segmented button");
    if (!button) return;
    const key = button.closest(".segmented").dataset.control;
    const raw = button.dataset.value;
    patch({ [key]: NUMERIC.includes(key) ? Number(raw) : raw });
  });

  // ---------------------------------------------------------------- render

  function render(payload) {
    state = payload;
    const s = payload.settings;
    const d = payload.derived;
    const th = payload.thresholds;

    if ($("scenarios").childElementCount !== payload.scenarios.length) {
      buildScenarios(payload.scenarios, th);
    }
    Array.from($("scenarios").children).forEach((card) => {
      card.setAttribute("aria-pressed", String(card.dataset.name === s.scenario));
    });

    // Sliders: never fight the user while they drag.
    if (!holding) {
      SLIDERS.forEach((name) => {
        $(name).value = s[name];
        $(`out-${name}`).textContent = UNITS[name](s[name]);
      });
    }

    paintSegmented(s);

    $("banner-preview").dataset.state = d.expected_banner;
    $("banner-text").textContent = BANNER_TEXT[d.expected_banner];

    $("v-bags").textContent = d.bags_in_system;
    $("ro-bags").dataset.tone = tone(d.bags_in_system, th.bags[0], th.bags[1]);

    $("v-dwell").innerHTML = `${d.avg_dwell_minutes}<small>min</small>`;
    $("ro-dwell").dataset.tone = tone(d.avg_dwell_minutes, th.dwell[0], th.dwell[1]);

    $("v-phantoms").innerHTML = `${d.phantoms_per_hour}<small>/h</small>`;
    $("ro-phantoms").dataset.tone = d.phantoms_per_hour > 0 ? "red" : "green";

    $("v-gap").textContent = d.api_sql_gap;
    $("ro-gap").dataset.tone = tone(d.api_sql_gap, th.health[0], th.health[1]);

    paintStuck(payload.stuck_dwells, th);

    $("formula-line").textContent =
      `buffer ${payload.batch_size} events → ` +
      `${d.lost_scans_per_hour} scans lost/h → ` +
      `${d.phantoms_per_hour} phantoms/h`;

    // Day cycle
    const playing = s.day_cycle === "on";
    $("clock").hidden = !playing;
    if (playing) {
      const hours = Math.floor(s.sim_hour);
      const minutes = Math.floor((s.sim_hour - hours) * 60);
      $("clock-value").textContent =
        `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
    }

    $("time-scale").textContent = `×${Math.round(d.time_scale)}`;
    paintDayChart(payload.throughput, s.sim_hour);

    const g = payload.grafana;
    $("dashboard-link").href = g.public_url
      ? g.public_url + g.path
      : `${location.protocol}//${location.hostname}:${g.port}${g.path}`;
  }

  // ------------------------------------------------------------- listeners

  SLIDERS.forEach((name) => {
    const input = $(name);
    input.addEventListener("pointerdown", () => { holding = true; });
    input.addEventListener("input", () => {
      $(`out-${name}`).textContent = UNITS[name](input.value);
      patch({ [name]: Number(input.value) });
    });
    const release = () => { holding = false; };
    input.addEventListener("pointerup", release);
    input.addEventListener("blur", release);
    // Keyboard users never fire pointerup.
    input.addEventListener("keyup", release);
  });

  $("purge").addEventListener("click", () => {
    send("/api/purge-phantoms", {});
    note("Purge requested — cleared within 5 s");
  });

  // ------------------------------------------------------------------ boot

  buildDayChart();
  poll();
  setInterval(poll, POLL_MS);
})();
