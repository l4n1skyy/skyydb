// skyydb for Obsidian: logs the "#expenses" lines in your daily notes as expenses in skyydb.
//
//   2.5 - Food - nasi lemak #expenses
//   3.5 - Household #expenses
//
// "Log #expenses from daily notes" (command palette, or the wallet in the left ribbon) goes
// through every daily note, sends those lines to skyydb (POST /finance/expenses/import) with
// the note's own date, then takes the saved lines out of the notes. A line skyydb can't take
// (say, a category it doesn't have) stays, with "⚠ failed: why" after it; fix it and run again.
// Desktop only: it talks to skyydb on this computer.
const { Notice, Plugin, PluginSettingTab, Setting, normalizePath } = require("obsidian");

const DEFAULTS = { address: "http://127.0.0.1:8001", folder: "journals/daily" };
const TAG = /(^|\s)#expenses(?=$|[\s.,;:!?)])/i;
const FAILED = /\s*⚠ failed:.*$/;  // what a failed line gets; dropped again before the next try
const LIST = /^\s*(?:[-*+]|\d+[.)])\s+(?:\[.\]\s+)?/;  // "- ", "1. ", "- [ ] " in front
const PARTS = /\s+[-–—]\s+/;  // "amount - category - note": dashes with spaces round them

// A daily note's name -> its date: "21-11-2026" or "2026-11-21" -> "2026-11-21"
function noteDate(name) {
  let m = /^(\d{2})-(\d{2})-(\d{4})$/.exec(name);
  if (m) return `${m[3]}-${m[2]}-${m[1]}`;
  m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(name);
  return m ? name : null;
}

// One line -> null (no #expenses), or {base, amount, category, notes} or {base, error}.
// base is the line without an old "⚠ failed" note, which is what a failed line is rebuilt from.
function parseLine(line) {
  if (!TAG.test(line)) return null;
  const base = line.replace(FAILED, "");
  const body = base.replace(TAG, "$1").replace(LIST, "").trim();
  const [amount, category, ...notes] = body.split(PARTS).map((s) => s.trim());
  if (!amount || !category) return { base, error: "write it as: amount - category - note #expenses" };
  return { base, amount, category, notes: notes.join(" - ") };
}

// POST JSON to skyydb with Node's own http, so no browser headers go with it
function post(address, path, body) {
  return new Promise((resolve, reject) => {
    let url;
    try {
      url = new URL(path, address);
    } catch {
      return reject(new Error(`"${address}" isn't an address. Fix it in the plugin's settings.`));
    }
    const data = Buffer.from(JSON.stringify(body));
    const http = require(url.protocol === "https:" ? "https" : "http");
    const req = http.request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": data.length },
      timeout: 20000,
    }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString();
        if (res.statusCode === 403) return reject(new Error("Finance is locked in skyydb. Unlock it there, then run this again."));
        if (res.statusCode === 404) return reject(new Error("this skyydb doesn't take expenses from Obsidian yet. Update it and run: skyydb restart"));
        let reply;
        try {
          reply = JSON.parse(text);
        } catch {
          return reject(new Error(`skyydb answered ${res.statusCode}, not with expenses. Is ${address} skyydb?`));
        }
        if (res.statusCode !== 200 || !reply.ok) return reject(new Error(reply.error || `skyydb answered ${res.statusCode}`));
        resolve(reply);
      });
    });
    req.on("timeout", () => req.destroy(new Error("skyydb took too long to answer")));
    req.on("error", (e) => reject(["ECONNREFUSED", "ENOTFOUND"].includes(e.code)
      ? new Error(`can't reach skyydb at ${address}. Is it running? (skyydb status)`) : e));
    req.end(data);
  });
}

const money = (n) => `RM${n.toFixed(2)}`;

class SkyydbPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULTS, await this.loadData());
    this.addRibbonIcon("wallet", "Log #expenses from daily notes", () => this.run());
    this.addCommand({ id: "log-expenses", name: "Log #expenses from daily notes", callback: () => this.run() });
    this.addSettingTab(new SkyydbSettings(this.app, this));
  }

  async run() {
    if (this.running) return new Notice("skyydb: already logging, one moment.");
    this.running = true;
    try {
      new Notice(await this.logExpenses(), 8000);
    } catch (e) {
      console.error("skyydb", e);
      new Notice(`skyydb: ${e.message}`, 12000);
    } finally {
      this.running = false;
    }
  }

  // Returns what to tell you. Nothing in a note changes unless skyydb answered.
  async logExpenses() {
    const folder = normalizePath(this.settings.folder);
    const files = this.app.vault.getMarkdownFiles().filter((f) => f.path.startsWith(folder + "/"));
    const found = [];
    for (const file of files) {
      const text = await this.app.vault.cachedRead(file);
      if (!/#expenses/i.test(text)) continue;
      const date = noteDate(file.basename);
      for (const line of text.split(/\r?\n/)) {
        const p = parseLine(line);
        if (!p) continue;
        if (!p.error && !date) p.error = "can't tell the date from this note's name";
        found.push({ file, line, ...p });
      }
    }
    if (!found.length) return `skyydb: no #expenses lines in ${folder}.`;

    const sending = found.filter((f) => !f.error);
    if (sending.length) {
      const { results } = await post(this.settings.address, "/finance/expenses/import", {
        expenses: sending.map((f) => ({ date: noteDate(f.file.basename), amount: f.amount, category: f.category, notes: f.notes })),
      });
      sending.forEach((f, i) => {
        const r = results[i] || { ok: false, error: "skyydb didn't answer for this one" };
        if (r.ok) f.saved = r;
        else f.error = r.error;
      });
    }

    // Saved lines go; failed ones say why. Lines are found again by their text, so a note
    // edited in the meantime keeps whatever changed
    const byFile = new Map();
    for (const f of found) byFile.set(f.file, [...(byFile.get(f.file) || []), f]);
    for (const [file, items] of byFile) {
      await this.app.vault.process(file, (text) => {
        const eol = text.includes("\r\n") ? "\r\n" : "\n";
        const lines = text.split(/\r?\n/);
        for (const f of items) {
          const i = lines.indexOf(f.line);
          if (i < 0) continue;
          if (f.saved) lines.splice(i, 1);
          else lines[i] = `${f.base.trimEnd()} ⚠ failed: ${f.error}`;
        }
        return lines.join(eol);
      });
    }

    const saved = found.filter((f) => f.saved), failed = found.filter((f) => !f.saved);
    const total = saved.reduce((sum, f) => sum + f.saved.amount, 0);
    const parts = [];
    if (saved.length) parts.push(`logged ${saved.length} expense${saved.length === 1 ? "" : "s"} (${money(total)})`);
    if (failed.length) {
      const where = [...new Set(failed.map((f) => f.file.basename))].join(", ");
      parts.push(`${failed.length} line${failed.length === 1 ? "" : "s"} failed, marked in ${where}`);
    }
    return `skyydb: ${parts.join("; ")}.`;
  }
}

class SkyydbSettings extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    const save = (key) => async (value) => {
      this.plugin.settings[key] = value.trim() || DEFAULTS[key];
      await this.plugin.saveData(this.plugin.settings);
    };
    new Setting(containerEl).setName("skyydb address").setDesc(`Where skyydb runs (${DEFAULTS.address}).`)
      .addText((t) => t.setPlaceholder(DEFAULTS.address).setValue(this.plugin.settings.address).onChange(save("address")));
    new Setting(containerEl).setName("Daily notes folder")
      .setDesc("Every note in it is looked through; its name (21-11-2026 or 2026-11-21) gives the expenses' date.")
      .addText((t) => t.setPlaceholder(DEFAULTS.folder).setValue(this.plugin.settings.folder).onChange(save("folder")));
  }
}

module.exports = SkyydbPlugin;
Object.assign(module.exports, { noteDate, parseLine });  // for tests
