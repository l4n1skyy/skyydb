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
    button.textContent = "\uf073";
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
    const muscles = JSON.parse($("#exercise-muscles").textContent);
    // Picking an exercise you've used before fills in its muscle group
    planRows.addEventListener("change", (e) => {
      if (e.target.name !== "exercise") return;
      const muscle = e.target.closest("tr").querySelector("[name=muscle]");
      const known = muscles[e.target.value.trim().toLowerCase()];
      if (known && !muscle.value) muscle.value = known;
    });
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
      } else if (button.dataset.move === "-1" && row.previousElementSibling) {
        row.previousElementSibling.before(row);
      } else if (button.dataset.move === "1" && row.nextElementSibling) {
        row.nextElementSibling.after(row);
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

// A click on the dimmed page around the popup closes it
document.addEventListener("click", (e) => {
  const dialog = e.target.id === "unlock-dialog" ? e.target : null;
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
      window.applyNavOrder?.();
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
