// The app pages: chart tooltips, the word lists (search, filters, quiz mode,
// moving words without a page reload), the workout log form and the finance forms.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// --- Tab / Shift+Tab cycles through the pages in the top menu (even from a form field)

document.addEventListener("keydown", (e) => {
  if (e.key !== "Tab" || e.ctrlKey || e.altKey || e.metaKey || e.defaultPrevented) return;
  const links = $$(".main-nav a");
  if (!links.length) return;
  const i = links.findIndex((a) => a.getAttribute("aria-current") === "page");
  const next = links[(i + (e.shiftKey ? -1 : 1) + links.length) % links.length];
  e.preventDefault();
  go(next.href);
});

// --- Chart tooltips: any element with data-tip-v (value) and data-tip-l (label)

const tip = $("#tip");

function showTip(el, x, y) {
  tip.querySelector("b").textContent = el.dataset.tipV;
  tip.querySelector("span").textContent = el.dataset.tipL;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(x + 14, innerWidth - r.width - 8) + "px";
  tip.style.top = Math.max(8, y - r.height - 12) + "px";
}

document.addEventListener("pointermove", (e) => {
  const el = e.target.closest?.("[data-tip-v]");
  if (el) showTip(el, e.clientX, e.clientY);
  else tip.hidden = true;
});
document.addEventListener("focusin", (e) => {
  const el = e.target.closest?.("[data-tip-v]");
  if (!el) return;
  const r = el.getBoundingClientRect();
  showTip(el, r.left + r.width / 2, r.top);
});
document.addEventListener("focusout", () => (tip.hidden = true));


// --- Drag to reorder: a list's rows (its direct children) move by the .drag-handle in them,
// with the mouse or touch, or with the up / down arrow keys while the handle has focus.
// done() runs once a row has landed somewhere new.

function sortable(list, done) {
  let dragged = null, from = -1;
  const rowOf = (el) => { while (el && el.parentElement !== list) el = el.parentElement; return el; };
  const index = (row) => [...list.children].indexOf(row);
  list.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest(".drag-handle");
    if (handle && list.contains(handle)) rowOf(handle).draggable = true;
  });
  list.addEventListener("pointerup", () => { for (const r of list.children) if (r !== dragged) r.draggable = false; });
  list.addEventListener("dragstart", (e) => {
    if (e.target.parentElement !== list) return;
    dragged = e.target;
    from = index(dragged);
    dragged.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", "");
  });
  list.addEventListener("dragover", (e) => {
    if (!dragged) return;
    e.preventDefault();
    const after = [...list.children].filter((r) => r !== dragged).find((r) => {
      const box = r.getBoundingClientRect();
      return e.clientY < box.top + box.height / 2;
    });
    if (after) list.insertBefore(dragged, after); else list.appendChild(dragged);
  });
  list.addEventListener("drop", (e) => e.preventDefault());
  list.addEventListener("dragend", () => {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    dragged.draggable = false;
    const moved = index(dragged) !== from;
    dragged = null;
    if (moved && done) done();
  });
  list.addEventListener("keydown", (e) => {
    const handle = e.target.closest?.(".drag-handle");
    if (!handle || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    const row = rowOf(handle);
    const other = e.key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
    if (!other) return;
    if (e.key === "ArrowUp") other.before(row); else other.after(row);
    handle.focus();
    if (done) done();
  });
}


// --- Everything that belongs to one page: runs on load and after each page swap ---------

function initPage() {
  // Keep dates readable and consistent regardless of the browser's locale.
  for (const input of $$("[data-date-input]")) {
    if (/^\d{4}-\d{2}-\d{2}$/.test(input.value)) {
      const [year, month, day] = input.value.split("-");
      input.value = `${day}/${month}/${year}`;
    }
    const controls = document.createElement("span");
    controls.className = "date-controls";
    input.before(controls);
    controls.append(input);
    const picker = document.createElement("input");
    picker.type = "date";
    picker.tabIndex = -1;
    picker.setAttribute("aria-hidden", "true");
    picker.className = "date-picker";
    const initial = input.value.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (initial) picker.value = `${initial[3]}-${initial[2]}-${initial[1]}`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "date-picker-button";
    button.textContent = "\uf073";  // Cozette's calendar
    button.setAttribute("aria-label", "Choose date");
    button.addEventListener("click", () => picker.showPicker ? picker.showPicker() : picker.click());
    picker.addEventListener("change", () => {
      const [year, month, day] = picker.value.split("-");
      if (year) input.value = `${day}/${month}/${year}`;
    });
    controls.append(button, picker);
    input.form?.addEventListener("submit", () => {
      const match = input.value.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
      if (match) input.value = `${match[3]}-${match[2]}-${match[1]}`;
    });
  }

  // --- Page menus (the tabs next to each page's title): drag a tab to move it. The order is
  // remembered per page and menu in this browser, like the top menu's.

  for (const [i, group] of $$(".page-head .tabs .tab-group").entries()) {
    const key = `tabs:${location.pathname}:${i}`;
    // A tab is known by its first parameter (?view=plans -> "view=plans"), or its path
    const id = (a) => {
      const url = new URL(a.getAttribute("href"), location.href);
      const [first] = url.searchParams.entries();
      return first ? first.join("=") : url.pathname;
    };
    const tabs = () => $$(":scope > a", group);
    try {
      for (const k of JSON.parse(localStorage.getItem(key) || "[]")) {
        const a = tabs().find((x) => id(x) === k);
        if (a) group.appendChild(a);
      }
    } catch {}
    let dragged = null;
    group.addEventListener("dragstart", (e) => {
      dragged = e.target.closest?.("a");
      if (!dragged || dragged.parentElement !== group) return (dragged = null);
      e.dataTransfer.effectAllowed = "move";
      dragged.classList.add("dragging");
    });
    group.addEventListener("dragover", (e) => {
      if (!dragged) return;
      e.preventDefault();
      const after = tabs().filter((a) => a !== dragged).find((a) => {
        const r = a.getBoundingClientRect();
        return e.clientX < r.left + r.width / 2;
      });
      if (after) group.insertBefore(dragged, after); else group.appendChild(dragged);
    });
    group.addEventListener("drop", (e) => e.preventDefault());
    group.addEventListener("dragend", () => {
      dragged?.classList.remove("dragging");
      dragged = null;
      try { localStorage.setItem(key, JSON.stringify(tabs().map(id))); } catch {}
    });
  }

  // --- Pixel-icon pickers: the first time one opens, it gets every icon there is (see iconChoices)

  for (const picker of $$(".icon-picker")) {
    const field = $("input[type=hidden]", picker), grid = $(".icon-grid", picker), summary = $("summary", picker);
    picker.addEventListener("toggle", async () => {
      if (!picker.open || grid.childElementCount) return;
      grid.innerHTML = await iconChoices(picker.dataset.suggested.split(" ").filter(Boolean), field.value);
      const search = $("input[type=search]", grid);
      search.focus();
      search.addEventListener("input", () => {
        const q = search.value.trim().toLowerCase();
        for (const b of $$(".icon-choice", grid)) b.hidden = q && !b.dataset.icon.includes(q);
        for (const h of $$(".icon-group", grid)) {
          let el = h.nextElementSibling, any = false;
          for (; el && !el.classList.contains("icon-group"); el = el.nextElementSibling) any ||= !el.hidden;
          h.hidden = !any;
        }
      });
    });
    grid.addEventListener("click", (e) => {
      const b = e.target.closest(".icon-choice");
      if (!b) return;
      field.value = b.dataset.icon;
      $$(".icon-choice[aria-pressed=true]", grid).forEach((x) => x.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true");
      summary.innerHTML = b.dataset.icon ? b.innerHTML : '<span class="icon-none" aria-hidden="true">+</span>';
      picker.open = false;
    });
  }

  // --- Settings > Pages: "edit" turns a page's name into a box; the handle drags a row up or down

  for (const button of $$("[data-edit-name]")) {
    button.addEventListener("click", () => {
      const cell = $(".name-cell", button.closest("tr")), input = $("input", cell);
      $(".name-view", cell).hidden = true;
      button.hidden = true;
      input.hidden = false;
      input.focus();
      input.select();
    });
  }

  // --- Lists you reorder by the dash handle on each row's right (Settings > Pages, plan cards,
  // the plan editor, a day's exercises on the Log tab). The new order saves right away where
  // there's nothing else to save (plan cards, the Log); Settings and the editor save with the form.

  const saveOrder = {
    plan: (list) => new URLSearchParams([["id", list.dataset.plan], ...$$("tr", list).map((r) => ["row", r.dataset.id])]),
    day: (list) => new URLSearchParams([["date", list.dataset.day], ...$$(".exercise", list).map((x) => ["exercise", x.dataset.exercise])]),
  };
  const saveUrl = { plan: "/workouts/plan/order", day: "/workouts/day/order" };
  for (const list of $$("[data-sortable]")) {
    const kind = list.dataset.sortable;
    sortable(list, saveOrder[kind] && (async () => {
      try {
        const res = await fetch(saveUrl[kind], { method: "POST", body: saveOrder[kind](list) });
        if (!res.ok) throw new Error();
      } catch {
        alert("Couldn't save the new order. Reload the page to see what's saved.");
      }
    }));
  }

  // --- Strava: opening Stats starts a sync when the last one is a while ago; the page asks how the
  // scraper is doing every few seconds and shows the new runs (or Settings' new status) when it's done

  const stravaWatch = async (el, startCount) => {
    for (let i = 0; i < 400 && el.isConnected; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      try {
        const s = await (await fetch("/workouts/strava/status")).json();
        if (!s.running) {
          if ((startCount === null || s.count !== startCount) && el.isConnected) {
            fetched.clear();
            go(location.pathname + location.search, false);
          }
          return;
        }
      } catch { return; }
    }
  };
  const stravaSync = $("[data-strava-sync]");
  if (stravaSync) {
    (async () => {
      try {
        const res = await fetch("/workouts/strava/sync", {
          method: "POST", headers: { Accept: "application/json" }, body: new URLSearchParams({ when: "stale" }),
        });
        const result = await res.json();
        if (result.started || result.running) stravaWatch(stravaSync, result.count);
      } catch {}
    })();
  }
  const stravaStatus = $("[data-strava-status][data-running]");
  if (stravaStatus) stravaWatch(stravaStatus, null);

  // --- History > Runs: search by name, pick a period, sort with the menu or by clicking a
  // column's heading (again flips it); the count and distance at the top follow what's shown

  for (const box of $$("[data-run-history]")) {
    const tbody = $("tbody", box), rows = $$("tr", tbody), heads = $$("th[data-key]", box);
    const search = $("[data-run-search]", box), period = $("[data-run-period]", box), sortBy = $("[data-run-sort]", box);
    const count = $("[data-run-count]", box), none = $("[data-run-none]", box);
    const today = box.dataset.today, day = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
    let key = "date", dir = -1;
    const shown = (r) => {
      const p = period.value, d = r.dataset.date, q = search.value.trim().toLowerCase();
      const inPeriod = !p || (p === "30d" ? (day(today) - day(d)) / 864e5 <= 30 : p === "month" ? d.slice(0, 7) === today.slice(0, 7) : d.startsWith(p));
      return inPeriod && (!q || r.dataset.name.includes(q));
    };
    const update = () => {
      const text = key === "date" || key === "name";
      const sorted = rows.slice().sort((a, b) => {
        const x = a.dataset[key], y = b.dataset[key];
        return (text ? x.localeCompare(y) : x - y) * dir || b.dataset.date.localeCompare(a.dataset.date);
      });
      let n = 0, km = 0;
      for (const r of sorted) {
        r.hidden = !shown(r);
        if (!r.hidden) { n++; km += +r.dataset.km; }
        tbody.append(r);
      }
      count.textContent = `${n} run${n === 1 ? "" : "s"} · ${km.toFixed(1)} km`;
      none.hidden = n > 0;
      for (const th of heads) th.setAttribute("aria-sort", th.dataset.key === key ? (dir > 0 ? "ascending" : "descending") : "none");
      sortBy.value = `${key}:${dir}`;
    };
    for (const th of heads) {
      $("button", th).addEventListener("click", () => {
        if (th.dataset.key === key) dir = -dir;
        else { key = th.dataset.key; dir = key === "name" || key === "pace" ? 1 : -1; }  // A-Z, fastest, else biggest / newest first
        update();
      });
    }
    sortBy.addEventListener("change", () => {
      [key, dir] = sortBy.value.split(":");
      dir = +dir;
      update();
    });
    search.addEventListener("input", update);
    period.addEventListener("change", update);
    update();
  }

  // --- Stats > Distance per week: pointing anywhere over a week (or tabbing to its ring) shows
  // that week's distance, time and climb on top; it stays chosen until you point at another

  for (const chart of $$(".wk-chart")) {
    const weeks = JSON.parse(chart.dataset.weeks);
    const pts = $$(".wk-pt", chart), rule = $(".wk-rule", chart), plot = $(".wk-plot", chart);
    const pick = (pt) => {
      if (pt.classList.contains("on")) return;
      const w = weeks[+pt.dataset.i];
      $(".wk-span", chart).textContent = w.span;
      for (const b of $$("[data-k]", chart)) b.textContent = w[b.dataset.k];
      pts.forEach((p) => p.classList.toggle("on", p === pt));
      rule.style.left = pt.style.left;
      rule.style.top = pt.style.top;
    };
    plot.addEventListener("pointermove", (e) => {
      const box = plot.getBoundingClientRect();
      const i = Math.round(((e.clientX - box.left) / box.width) * (pts.length - 1));
      pick(pts[Math.max(0, Math.min(pts.length - 1, i))]);
    });
    for (const pt of pts) pt.addEventListener("focus", () => pick(pt));
  }

  // --- Finance > Settings: a category's Daily / Monthly ticks save as soon as they change

  for (const box of $$("[data-cat-flag]")) {
    box.addEventListener("change", async () => {
      try {
        const res = await fetch("/finance/category/flags", {
          method: "POST", body: new URLSearchParams({ category: box.dataset.category, flag: box.dataset.catFlag, on: box.checked ? "1" : "0" }),
        });
        if (!res.ok) throw new Error();
      } catch {
        box.checked = !box.checked;
      }
    });
  }

  // A button that asks first (Remove password)
  for (const button of $$("[data-confirm-click]")) {
    button.addEventListener("click", (e) => {
      if (!confirm(button.dataset.confirmClick)) e.preventDefault();
    });
  }

  // --- Workouts > Plans: a day in Coming up opens a popup to give that date its own plan

  const dayDialog = $("#day-plan-dialog");
  for (const card of $$(".day-card")) {
    card.addEventListener("click", () => {
      const form = $("form", dayDialog);
      form.elements.date.value = card.dataset.day;
      $("[data-day-label]", dayDialog).textContent = card.dataset.label;
      form.elements.plan.value = card.dataset.plan;
      dayDialog.showModal();
      form.elements.plan.focus();
    });
  }

  // A field marked autofocus gets the cursor after a page swap too
  $("main [autofocus]")?.focus();

  // Opened a locked page directly: the server sent Home with ?unlock=<that page>
  const wanted = new URLSearchParams(location.search).get("unlock");
  if (wanted !== null) {
    const url = new URL(location.href);
    url.searchParams.delete("unlock");
    url.searchParams.delete("error");
    history.replaceState(null, "", url);
    showUnlock(wanted);
  }

  // --- Settings: Home's widget switches save as soon as they're flipped

  for (const form of $$("form[data-autosave]")) {
    const status = $(".autosave-status", form);
    form.addEventListener("change", async () => {
      if (status) status.textContent = "Saving…";
      try {
        const res = await fetch(form.action, { method: "POST", body: new URLSearchParams(new FormData(form)) });
        if (!res.ok) throw new Error();
        if (status) status.textContent = "Saved";
      } catch {
        if (status) status.textContent = "Couldn't save; try again";
      }
    });
  }

  // --- Quotes: the ZenQuotes switch saves as soon as it's ticked

  $(".zen-toggle input")?.addEventListener("change", (e) => e.target.form.submit());

  // --- Chinese word lists ------------------------------------------------------------

  const words = $("#words");

  if (words) {
    const q = $("#q"), hsk = $("#f-hsk"), cat = $("#f-cat"), hide = $("#hide");
    const plain = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

    function visibleCards() {
      return $$(".word", words).filter((c) => !c.hidden);
    }

    function updateShown() {
      const n = visibleCards().length;
      $("#shown").textContent = `${n} word${n === 1 ? "" : "s"}`;
      $("#none").hidden = n > 0;
    }

    function filter() {
      const text = plain(q.value.trim());
      for (const card of $$(".word", words)) {
        card.hidden =
          (text && !plain(card.dataset.text).includes(text)) ||
          (hsk && hsk.value && card.dataset.hsk !== hsk.value) ||
          (cat && cat.value && card.dataset.cat !== cat.value);
      }
      updateShown();
    }
    q.addEventListener("input", filter);
    hsk?.addEventListener("change", filter);
    cat?.addEventListener("change", filter);

    // Quiz mode is remembered per browser
    try {
      hide.checked = localStorage.getItem("hideMeaning") === "1";
    } catch {}
    words.classList.toggle("quiz", hide.checked);
    hide.addEventListener("change", () => {
      words.classList.toggle("quiz", hide.checked);
      try {
        localStorage.setItem("hideMeaning", hide.checked ? "1" : "0");
      } catch {}
    });
    words.addEventListener("click", (e) => {
      const card = e.target.closest(".word");
      if (card && words.classList.contains("quiz") && !e.target.closest("button, a")) card.classList.toggle("shown");
    });

    async function move(ids, to) {
      const body = new URLSearchParams({ ids: ids.join(","), to });
      const res = await fetch("/hsk/move", { method: "POST", body, headers: { Accept: "application/json" } });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error);
      for (const [status, n] of Object.entries(data.counts)) {
        const el = $(`[data-count="${status}"]`);
        if (el) el.textContent = n;
      }
      for (const id of ids) {
        const card = $(`.word[data-id="${id}"]`, words);
        card.classList.add("leaving");
        setTimeout(() => {
          card.remove();
          updateShown();
        }, 200);
      }
    }

    // Learnt / Forgot buttons: move the word without reloading the page
    words.addEventListener("submit", (e) => {
      e.preventDefault();
      const form = e.target, button = e.submitter;
      $$("button", form).forEach((b) => (b.disabled = true));
      move([form.elements.namedItem("id").value], button.value).catch(() => form.submit());
    });

    $("#all-learnt")?.addEventListener("click", async () => {
      const ids = visibleCards().map((c) => c.dataset.id);
      if (!ids.length || !confirm(`Mark all ${ids.length} shown word${ids.length === 1 ? "" : "s"} as learnt?`)) return;
      await move(ids, "learnt");
    });
  }

  // --- Workout log ---------------------------------------------------------------------

  const exercise = $("#exercise");

  if (exercise) {
    // last: exercise -> the sets of its last session; done: exercise -> entries already on this day
    const { last, done } = JSON.parse($("#log-data").textContent);
    const setRows = $("#set-rows");
    const submit = $("#log-submit");
    const reps = (row) => $("[name=reps]", row);
    const weight = (row) => $("[name=weight]", row);
    const key = () => exercise.value.trim().toLowerCase();

    // Set numbers carry on from what's already logged for this exercise on this day
    const renumber = () => {
      const before = done[key()] || 0;
      [...setRows.children].forEach((row, i) => ($(".set-idx", row).textContent = before + i + 1));
      const filled = $$("[name=reps]", setRows).filter((i) => i.value.trim()).length;
      submit.textContent = filled > 1 ? `Log ${filled} sets` : "Log it";
    };
    const addSet = (r = "", w = "") => {
      const row = setRows.lastElementChild.cloneNode(true);
      reps(row).value = r;
      weight(row).value = w;
      setRows.append(row);
      renumber();
      return row;
    };
    // Picking a known exercise fills in every set of its last session, unless you've typed in the sets
    // (sets it filled in itself and you haven't touched are replaced when you pick another exercise)
    let autoFilled = false;
    const fillLast = () => {
      const sets = last[key()];
      renumber();
      if (!sets || (!autoFilled && $$("input", setRows).some((i) => i.value.trim()))) return;
      autoFilled = true;
      while (setRows.children.length > 1) setRows.lastElementChild.remove();
      // Weight stays empty when it was logged without reps or weight (bouldering, say)
      const value = (s) => [s.reps ?? "", s.reps || s.weight ? s.weight : ""];
      [reps(setRows.firstElementChild).value, weight(setRows.firstElementChild).value] = value(sets[0]);
      for (const s of sets.slice(1)) addSet(...value(s));
      renumber();
    };
    exercise.addEventListener("input", renumber);
    exercise.addEventListener("change", fillLast);

    // A new set copies the weight of the one above; its reps are yours to type
    $("#add-set").addEventListener("click", () => {
      autoFilled = false;
      reps(addSet("", weight(setRows.lastElementChild).value)).focus();
    });
    setRows.addEventListener("click", (e) => {
      const row = e.target.closest("[data-remove-set]")?.closest("li");
      if (!row) return;
      autoFilled = false;
      if (setRows.children.length > 1) row.remove();
      else reps(row).value = weight(row).value = "";
      renumber();
    });
    setRows.addEventListener("input", () => {
      autoFilled = false;
      renumber();
    });

    // Enter fills the sets in instead of logging: exercise -> reps -> weight -> next set's reps.
    // Ctrl+Enter (anywhere in the form) logs.
    exercise.form.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        exercise.form.requestSubmit();
        return;
      }
      const row = e.target.closest("#set-rows > li");
      if (e.target === exercise) {
        e.preventDefault();
        fillLast();
        reps(setRows.firstElementChild).focus();
      } else if (row && e.target.name === "reps") {
        e.preventDefault();
        weight(row).focus();
        weight(row).select();
      } else if (row) {
        e.preventDefault();
        reps(row.nextElementSibling || addSet("", weight(row).value)).focus();
      }
    });
    renumber();
    for (const form of $$("form.delete")) {
      form.addEventListener("submit", (e) => {
        if (!confirm("Delete this entry?")) e.preventDefault();
      });
    }
    // After logging, the form is empty again and the cursor is ready for the next entry
    if (new URLSearchParams(location.search).has("msg")) exercise.focus();
  }

  // --- Workout plans -------------------------------------------------------------------

  const planRows = $("#plan-rows");

  if (planRows) {
    const clear = (row) => $$("input", row).forEach((i) => (i.value = ""));
    $("#add-row").addEventListener("click", () => {
      const row = planRows.lastElementChild.cloneNode(true);
      clear(row);
      planRows.append(row);
      $("input", row).focus();
    });
    planRows.addEventListener("click", (e) => {
      const button = e.target.closest("button");
      const row = button?.closest("tr");
      if (!row) return;
      if (button.hasAttribute("data-remove")) {
        if (planRows.children.length > 1) row.remove();
        else clear(row);
      }
    });
  }

  for (const form of $$("form.delete-plan")) {
    form.addEventListener("submit", (e) => {
      const name = form.querySelector("button").dataset.name;
      if (!confirm(`Delete the plan "${name}"? It also comes off every day it's planned on.`)) e.preventDefault();
    });
  }

  // --- Finance -------------------------------------------------------------------------

  for (const form of $$("form[data-confirm]")) {
    form.addEventListener("submit", (e) => {
      if (!confirm(form.dataset.confirm)) e.preventDefault();
    });
  }

  const category = $("#category");

  if (category) {
    // The category buttons under the field fill it in; the one matching the field is lit
    const chips = $$("[data-category]");
    const mark = () =>
      chips.forEach((c) => c.setAttribute("aria-pressed", c.dataset.category.toLowerCase() === category.value.trim().toLowerCase()));
    for (const chip of chips) {
      chip.addEventListener("click", () => {
        category.value = chip.dataset.category;
        mark();
        $("#amount").focus();
      });
    }
    category.addEventListener("input", mark);
    mark();
  }

  // Budget editor: add and remove lines, and keep each column's total up to date
  for (const box of $$(".budget-edit")) {
    const body = $("tbody", box);
    const total = $("[data-total]", box);
    const update = () => {
      const sum = $$("input[type=number]", body).reduce((s, i) => s + (parseFloat(i.value) || 0), 0);
      total.textContent = "RM" + sum.toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    };
    body.addEventListener("input", update);
    $("[data-add-row]", box).addEventListener("click", () => {
      const row = body.lastElementChild.cloneNode(true);
      $$("input", row).forEach((i) => (i.value = ""));
      body.append(row);
      $("input", row).focus();
    });
    body.addEventListener("click", (e) => {
      const row = e.target.closest("[data-remove]")?.closest("tr");
      if (!row) return;
      if (body.children.length > 1) row.remove();
      else $$("input", row).forEach((i) => (i.value = ""));
      update();
    });
  }

  // Finance: opening the page fetches the latest prices, IBKR's statement and Wise's (the
  // server skips it if it did so in the last 5 minutes); then the Investments tab, which
  // has nothing to type into, is swapped for one with the new numbers
  const refreshStatus = $("[data-refresh-status]");
  if (refreshStatus) {
    (async () => {
      refreshStatus.textContent = "Fetching prices, IBKR and Wise…";
      try {
        const result = await (await fetch("/finance/refresh", { method: "POST" })).json();
        const live = $("[data-refresh]");
        if (result.fetched && live) {
          const res = await fetch(live.dataset.refresh, { headers: { Accept: "text/html" } });
          const fresh = document.createElement("div");
          fresh.innerHTML = res.ok ? await res.text() : "";
          const next = fresh.querySelector("[data-refresh]");
          if (next) live.replaceWith(next);
        }
        const trouble = /didn't work|Couldn't|went wrong/.test(result.said || "");
        refreshStatus.textContent = trouble ? result.said : `Up to date, fetched at ${result.at}`;
        refreshStatus.title = result.said || "";
      } catch {
        refreshStatus.textContent = "Couldn't fetch the latest: offline?";
      }
    })();
  }

  // --- Quotes and Thoughts: search what's shown -------------------------------------------

  // (#note-q too: the workout log's Notes field is also called #notes)
  const notes = $("#note-q") && $("#notes");

  if (notes) {
    const q = $("#note-q"), shown = $("#note-shown");
    const plain = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    q.addEventListener("input", () => {
      const text = plain(q.value.trim());
      let n = 0;
      for (const card of $$(".note-card", notes)) {
        card.hidden = text && !plain(card.dataset.text).includes(text);
        if (!card.hidden) n++;
      }
      shown.textContent = `${n} ${n === 1 ? shown.dataset.one : shown.dataset.many}`;
      $("#note-none").hidden = n > 0;
    });
  }
}

initPage();

// --- Moving between pages without a page load ------------------------------------------
// The top bar and background stay put; only <main> is swapped for the next page's, so the
// window never goes blank in between. Anything that isn't a plain page falls back to a normal load.

// The app's own pages (Home, /hsk, /finance, ...), which swap in place; Datasette's raw pages (/finance-db/...) load whole
const isPage = (url) => url.origin === location.origin && /^\/(hsk|workouts|finance|quotes|thoughts|work|settings)?(\/|$)/i.test(url.pathname);
const fetched = new Map();

// --- The password popup: typing the password once opens every locked page -------------------

function showUnlock(next) {
  const dialog = $("#unlock-dialog");
  if (!dialog) return false;
  const form = $("form", dialog);
  form.elements.next.value = next || "";
  form.elements.password.value = "";
  $(".unlock-error", dialog).hidden = true;
  if (!dialog.open) dialog.showModal();
  form.elements.password.focus();
  return true;
}

document.addEventListener("submit", async (e) => {
  const form = e.target;
  if (!form.closest?.("#unlock-dialog")) return;
  e.preventDefault();
  const error = $(".unlock-error", form);
  try {
    const res = await fetch(form.action, {
      method: "POST", headers: { Accept: "application/json" }, body: new URLSearchParams(new FormData(form)),
    });
    const result = await res.json().catch(() => ({ ok: false, error: "Couldn't unlock: try again." }));
    if (!result.ok) throw new Error(result.error);
    form.closest("dialog").close();
    fetched.clear();  // pages fetched while they were locked
    // Go where you were heading, or show this page again, now unlocked
    const here = location.pathname + location.search, next = result.next && result.next !== here ? result.next : null;
    go(next || here, Boolean(next));
  } catch (err) {
    error.textContent = err.message || "Couldn't unlock: try again.";
    error.hidden = false;
    form.elements.password.value = "";
    form.elements.password.focus();
  }
});

// --- Every pixel icon: Pixicons (11x11 bitmaps, drawn as SVG) and HackerNoon's (a font) -------

let iconData = null;
const loadIcons = () => (iconData ??= Promise.all([
  "/static/fonts/pixicons/icons.json", "/static/fonts/pixicons/extra.json", "/static/fonts/hackernoon/icons.json",
].map((u) => fetch(u).then((r) => r.json()))).then(([pixicons, extra, hackernoon]) => ({ pixicons: { ...pixicons, ...extra }, hackernoon })));

// A Pixicon's 121 bits (11 rows, top left first) as an SVG path, one rectangle per run of set pixels
function pixiconSvg(hex) {
  const bits = BigInt("0x" + hex).toString(2).padStart(hex.length * 4, "0");
  let d = "";
  for (let y = 0; y < 11; y++) {
    for (let x = 0; x < 11; ) {
      if (bits[y * 11 + x] !== "1") { x++; continue; }
      const s = x;
      while (x < 11 && bits[y * 11 + x] === "1") x++;
      d += `M${s} ${y}h${x - s}v1h-${x - s}z`;
    }
  }
  return `<svg class="px pxi" viewBox="0 0 11 11" shape-rendering="crispEdges" aria-hidden="true"><path d="${d}" fill="currentColor"/></svg>`;
}

async function iconChoices(suggested, current) {
  const { pixicons, hackernoon } = await loadIcons();
  const draw = (name) => name.startsWith("pi:")
    ? (pixicons[name.slice(3)] ? pixiconSvg(pixicons[name.slice(3)][1]) : "")
    : (hackernoon[name] ? `<i class="px" aria-hidden="true">&#x${hackernoon[name]};</i>` : "");
  const button = (name) => `<button type="button" class="icon-choice" data-icon="${name}" title="${name.replace(/^pi:/, "")}" aria-pressed="${name === current}">${draw(name)}</button>`;
  const group = (title, names) => names.length ? `<p class="icon-group">${title} <span class="muted">${names.length}</span></p>` + names.map(button).join("") : "";
  return `<input type="search" placeholder="Search ${Object.keys(pixicons).length + Object.keys(hackernoon).length} icons" aria-label="Search icons">`
    + `<button type="button" class="icon-choice icon-clear" data-icon="" aria-pressed="${!current}">none</button>`
    + group("Suggested", suggested)
    + group("Pixicons", Object.keys(pixicons).sort().map((n) => "pi:" + n))
    + group("HackerNoon", Object.keys(hackernoon).sort());
}

// An open icon picker closes when you click anywhere else
document.addEventListener("click", (e) => {
  for (const open of $$(".icon-picker[open]")) if (!open.contains(e.target)) open.open = false;
});

// A click on the dimmed page around a popup closes it
document.addEventListener("click", (e) => {
  const dialog = e.target.matches?.("dialog.unlock-dialog") ? e.target : null;
  if (!dialog) return;
  const r = dialog.getBoundingClientRect();
  if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
});

function fetchPage(url) {
  const hit = fetched.get(url.href);
  if (hit && Date.now() - hit.at < 5000) return hit.promise;
  const promise = fetch(url, { headers: { Accept: "text/html" } }).then(async (res) => {
    if (!res.ok || !(res.headers.get("content-type") || "").includes("html")) throw new Error("not a page");
    return { text: await res.text(), url: res.url };
  });
  fetched.set(url.href, { at: Date.now(), promise });
  promise.catch(() => fetched.delete(url.href));
  return promise;
}

let navigation = 0;

async function go(href, push = true) {
  const url = new URL(href, location.href), id = ++navigation, main = $("main");
  // ?unlock=... (a locked card, the Unlock button) opens the password popup over this page
  if (url.searchParams.has("unlock") && showUnlock(url.searchParams.get("unlock"))) return;
  const slow = setTimeout(() => main.classList.add("loading"), 150);  // only if it takes a moment
  try {
    const { text, url: final } = await fetchPage(url);
    if (id !== navigation) return;
    // A locked page: the server answered with Home + ?unlock=; stay here and ask for the password
    const landed = new URL(final);
    if (landed.searchParams.has("unlock") && showUnlock(landed.searchParams.get("unlock"))) return;
    const next = new DOMParser().parseFromString(text, "text/html");
    const same = (sel, attr) => next.querySelector(sel)?.getAttribute(attr) === document.querySelector(sel)?.getAttribute(attr);
    if (!next.querySelector("main") || !same('link[rel="stylesheet"]', "href") || !same("script[src]", "src")) throw new Error("full load");
    document.title = next.title;
    main.replaceChildren(...next.querySelector("main").childNodes);
    for (const old of $$("script:not([type])", main)) {  // scripts set in by hand don't run; remake them
      const fresh = document.createElement("script");
      fresh.textContent = old.textContent;
      old.replaceWith(fresh);
    }
    // The menu and the top right follow the new page: names, hidden pages and locks can change
    const nextNav = next.querySelector(".main-nav"), nextRight = next.querySelector(".top-right");
    if (nextNav) {
      $(".main-nav").replaceChildren(...[...nextNav.children].map((a) => document.importNode(a, true)));
    }
    if (nextRight) $(".top-right")?.replaceWith(document.importNode(nextRight, true));
    if (push) history.pushState(null, "", final);
    lastPath = location.pathname + location.search;
    scrollTo(0, 0);
    initPage();
  } catch {
    if (id === navigation) location.href = url.href;
  } finally {
    clearTimeout(slow);
    main.classList.remove("loading");
  }
}

document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest?.("a[href]");
  if (!a || a.target || a.hasAttribute("download")) return;
  const url = new URL(a.href, location.href);
  if (!isPage(url)) return;
  if (url.hash && url.pathname === location.pathname && url.search === location.search) return;  // jump within the page
  e.preventDefault();
  go(url.href);
});

// Start fetching a menu page as soon as the pointer is over its link
document.addEventListener("pointerover", (e) => {
  const a = e.target.closest?.(".main-nav a");
  if (a && isPage(new URL(a.href))) fetchPage(new URL(a.href)).catch(() => {});
});

let lastPath = location.pathname + location.search;
addEventListener("popstate", () => {
  const now = location.pathname + location.search;
  if (now !== lastPath) { lastPath = now; go(location.href, false); }
});
