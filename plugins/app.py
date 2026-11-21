"""The day-to-day pages: home, HSK (Chinese) study lists, the workout log, finance, quotes,
thoughts, work, and Settings.

Datasette's own pages (/chinese, /workouts, /finance and their tables) stay available for
browsing and editing raw rows (listed on Settings); these pages are the interface for everything else.
Pages can be renamed, hidden and locked with a password on Settings (settings.json).
Colours are Catppuccin Mocha on my own background (theme()); the font is the desktop's monospace.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from itertools import zip_longest
from html import escape
from math import ceil, cos, floor, log10, radians, sin
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode

from datasette import hookimpl
from datasette.utils.asgi import Request, Response
from markupsafe import Markup

ROOT = Path(__file__).resolve().parent.parent
LISTS = {"to-revise": "to revise", "to-learn": "to learn", "learnt": "learnt"}
STATUS_ORDER = ("to learn", "to revise", "learnt")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_background = set()

# Which features are installed: the words in the `features` file next to this folder's
# README (written by `skyydb install`); without the file, all of them.
ALL_FEATURES = ("chinese", "workouts", "finance", "work", "elearn", "quotes", "thoughts")


def installed_features():
    try:
        words = (ROOT / "features").read_text().split()
    except OSError:
        return ALL_FEATURES
    words = ["thoughts" if w == "knowledge" else w for w in words]  # Thoughts used to be called Knowledge
    return tuple(f for f in ALL_FEATURES if f in words)


FEATURES = installed_features()


# --- Spaced repetition sync ---------------------------------------------------

async def sync(datasette):
    sql = (ROOT / "schema" / "sync.sql").read_text()
    return await datasette.get_database("chinese").execute_write_fn(lambda conn: conn.execute(sql).rowcount)


async def _sync_every_few_minutes(datasette):
    while True:
        await asyncio.sleep(300)
        try:
            await sync(datasette)
        except Exception as e:  # keep the loop alive; the next page load syncs too
            print(f"app: sync failed: {e}")


@hookimpl
def startup(datasette):
    # Raw data lives at /<database>-db (/finance-db/expenses), so the pages can have the plain
    # addresses (/finance) without the two clashing
    for name, db in datasette.databases.items():
        if not name.startswith("_"):
            db.route = f"{name}-db"

    async def inner():
        if "chinese" not in FEATURES:
            return
        await sync(datasette)
        loop = asyncio.get_running_loop()
        _background.add(loop.create_task(_sync_every_few_minutes(datasette)))

    return inner


# --- Theme ----------------------------------------------------------------------

# Catppuccin Mocha (https://catppuccin.com/palette)
MOCHA = {
    "red": "#f38ba8", "peach": "#fab387", "yellow": "#f9e2af", "green": "#a6e3a1", "teal": "#94e2d5",
    "blue": "#89b4fa", "lavender": "#b4befe", "mauve": "#cba6f7",
    "text": "#cdd6f4", "subtext0": "#a6adc8", "overlay2": "#9399b2", "overlay0": "#6c7086",
    "surface2": "#585b70", "surface1": "#45475a", "surface0": "#313244",
    "base": "#1e1e2e", "mantle": "#181825", "crust": "#11111b",
}


def theme():
    m = MOCHA
    return {
        "mode": "dark",
        "vars": {
            "bg": m["base"],  # the page
            "surface": m["mantle"],  # panels, a step darker than the page
            "surface-2": m["surface0"],  # tiles and buttons on them
            "border": m["surface0"],
            "grid": m["base"],  # chart gridlines, one step off the panel
            "axis": m["surface2"],
            "text": m["text"],
            "muted": m["overlay2"],
            "accent": m["mauve"],
            "on-accent": m["crust"],
            "danger": m["red"],
            "good": m["green"],  # a price or return going up (always with its + sign)
            # Chart colours: of the Mocha colours, blue, peach and teal stay the easiest to
            # tell apart, colour blindness included (checked with a palette validator)
            "series-1": m["blue"],
            "series-2": m["peach"],
            "series-3": m["teal"],
            # The savings chart's own three, with Mocha's purple: as easy to tell apart
            "savings-1": m["mauve"],
            "savings-2": m["peach"],
            "savings-3": m["teal"],
            "data": m["blue"],  # one-series charts (the share price)
            "spending": m["mauve"],  # the Stats tab's charts of what you spent
            "context": m["overlay0"],  # grey marks beside the data ("everything else")
            # The Budget pie, in this order round the ring (the 4th touches the 3rd and the 1st):
            # every pair that touches stays apart for every reader, colour blindness included
            # (palette validator, adjacent pairs with the wrap: CVD ΔE 11.3, normal vision 15.2)
            "pie-1": m["peach"],
            "pie-2": m["blue"],
            "pie-3": m["teal"],
            "pie-4": m["mauve"],
            "streak": m["peach"],  # the workout streak's flame and band
            "run": m["peach"],  # running (Strava's orange, in Mocha)
        },
    }


def _ui_font():
    try:
        out = subprocess.run(["fc-match", "-f", "%{family[0]}", "monospace"], capture_output=True, text=True, timeout=3)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


UI_FONT = _ui_font()


def _asset_version():
    return int(max((ROOT / "static" / f).stat().st_mtime for f in ("app.css", "app.js")))


_LOGO = {}


def _logo_uri():
    """The top-bar logo as a data: URI, rebuilt only when the file changes. Inlined in the page, it is
    there from the first paint, so it no longer flashes away while the browser refetches it on every page."""
    f = ROOT / "static" / "logo-small.png"
    stamp = f.stat().st_mtime
    if _LOGO.get("stamp") != stamp:
        _LOGO.update(stamp=stamp, uri="data:image/png;base64," + base64.b64encode(f.read_bytes()).decode())
    return _LOGO["uri"]


# --- Settings: page names, hidden and locked pages, Home's widgets -----------------------

# Kept in settings.json next to the README, out of git (it holds the password's hash)
SETTINGS_FILE = ROOT / "settings.json"
# Every page: its default name, its address, and the addresses that belong to it. Locking a
# page locks all of them, its databases' raw Datasette pages too (at /<database>-db, see startup).
PAGES = {
    "home": dict(name="Home", url="/", paths=(), dbs=()),
    "chinese": dict(name="HSK", url="/hsk", paths=("/hsk",), dbs=("chinese",)),
    "workouts": dict(name="Workouts", url="/workouts", paths=("/workouts",), dbs=("workouts",)),
    "finance": dict(name="Finance", url="/finance", paths=("/finance",), dbs=("finance",)),
    "quotes": dict(name="Quotes", url="/quotes", paths=("/quotes",), dbs=("quotes",)),
    "thoughts": dict(name="Thoughts", url="/thoughts", paths=("/thoughts",), dbs=("thoughts",)),
    "work": dict(name="Work", url="/work", paths=("/work",), dbs=("work", "elearn")),
}
# Home's widgets, in the order they appear, and the page each one belongs to
WIDGETS = {
    "date": dict(name="Today's date", page=None),
    "quote": dict(name="Quote of the day", page="quotes"),
    "chinese": dict(name="Today's words", page="chinese"),
    "workouts": dict(name="Streaks and today's plan", page="workouts"),
    "finance": dict(name="Today's budget", page="finance"),
    "work": dict(name="Urgent and high-priority tasks", page="work"),
}
DEFAULT_SETTINGS = {"names": {}, "icons": {}, "order": [], "hidden": [], "locked": [], "widgets_off": [], "password": "", "unlock_minutes": 10}

# Pixel icons, by name: the HackerNoon Pixel Icon Library font (static/fonts/hackernoon, MIT),
# and Pixicons (static/fonts/pixicons, MIT; 11x11 bitmaps, drawn here as SVG), named "pi:<name>".
# (The small icons of the app itself, like the lock and the cog, come from Cozette's font.)
PX = {name: chr(int(code, 16)) for name, code in
      json.loads((ROOT / "static" / "fonts" / "hackernoon" / "icons.json").read_text()).items()}
PIXICONS = {"pi:" + name: bits for source in ("icons.json", "extra.json")
            for name, bits in json.loads((ROOT / "static" / "fonts" / "pixicons" / source).read_text()).items()}
# The ones offered for pages and workout plans (any name works, "-solid" HackerNoon ones too)
FITNESS_ICONS = ("pi:sneaker", "pi:shoe", "pi:barbell", "pi:dumbbell", "pi:rock", "pi:leg", "pi:flex", "pi:foot",
                 "pi:bicycle", "pi:stationary-bike", "pi:mountains", "pi:stopwatch", "pi:medal", "pi:trophy", "pi:heart",
                 "pi:fire", "pi:lightning")
PAGE_ICONS = ("pi:cloud-lightning", "home", "translate", "book", "book-bookmark", "notebook", "bolt", "heart", "fire",
              "trophy", "finance", "wallet", "coins", "coin", "dollar", "bank", "chart-line", "trending", "quote-left",
              "comment-quote", "lightbulb", "briefcase", "graduation-cap", "calendar-alt", "clock", "check-circle", "star",
              "moon", "sun", "seedlings", "music", "gaming", "crown", "flag", "pen", "robot", "globe", "laptop",
              "headphones", "shop", "sparkles", "pi:barbell", "pi:sneaker", "pi:flex")
PLAN_ICONS = FITNESS_ICONS + (
    "bolt-solid", "fire-solid", "heart-solid", "trophy-solid", "star-solid", "crown-solid", "flag-solid",
    "sun-solid", "moon-solid", "seedlings-solid", "music-solid", "user-solid", "users-solid", "globe-solid",
    "bolt", "fire", "heart", "trophy", "star", "crown", "flag", "flag-checkered", "sun", "moon", "seedlings",
    "sparkles", "music", "headphones", "clock", "check-circle", "octagon-check", "user", "users", "plane",
    "location-pin", "gaming", "hockey-mask", "shapes", "lightbulb", "glasses", "life-hacking", "medical-house")
PAGE_ICONS = tuple(i for i in PAGE_ICONS if i in PX or i in PIXICONS)
PLAN_ICONS = tuple(i for i in PLAN_ICONS if i in PX or i in PIXICONS)


def pixicon_svg(name, fill=True):
    """A Pixicon as SVG: each row's runs of set pixels as rectangles, in the text's colour"""
    bits = bin(int(PIXICONS[name][1 if fill else 0], 16))[2:].zfill(124)
    runs = []
    for y in range(11):
        row, x = bits[y * 11:(y + 1) * 11], 0
        while x < 11:
            if row[x] == "1":
                start = x
                while x < 11 and row[x] == "1":
                    x += 1
                runs.append(f"M{start} {y}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return f'<path d="{"".join(runs)}" fill="currentColor"/>'


def icon_ok(name):
    return name in PX or name in PIXICONS


def px(name, cls=""):
    """A pixel icon by name (nothing when there's no such icon)"""
    if name in PIXICONS:
        return Markup(f'<svg class="px pxi{" " + cls if cls else ""}" viewBox="0 0 11 11" shape-rendering="crispEdges" '
                      f'aria-hidden="true">{pixicon_svg(name)}</svg>')
    if name not in PX:
        return Markup("")
    return Markup(f'<i class="px{" " + cls if cls else ""}" aria-hidden="true">{escape(PX[name])}</i>')


OPEN_COOKIE, OPEN_NAMESPACE = "skyydb_open", "skyydb-open"
_settings = {}


def settings():
    """settings.json (with the defaults for anything missing), read again only when the file changes"""
    try:
        stamp = SETTINGS_FILE.stat().st_mtime_ns
    except OSError:
        stamp = None
    if "data" not in _settings or _settings.get("stamp") != stamp:
        data = json.loads(json.dumps(DEFAULT_SETTINGS))
        if stamp is not None:
            try:
                data.update(json.loads(SETTINGS_FILE.read_text()))
            except (OSError, ValueError):
                pass
        _settings.update(stamp=stamp, data=data)
    return json.loads(json.dumps(_settings["data"]))  # a copy, so changing it changes nothing until saved


def save_settings(data):
    tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(SETTINGS_FILE)
    _settings.clear()


def ordered_pages(s=None):
    """PAGES in your menu order (Settings > Pages, or dragging the top menu); new pages go last"""
    order = [k for k in (s or settings())["order"] if k in PAGES]
    return [(k, PAGES[k]) for k in order + [k for k in PAGES if k not in order]]


def page_installed(key):
    return key == "home" or key in FEATURES


def page_names(s=None):
    names = (s or settings())["names"]
    return {k: (names.get(k) or "").strip() or p["name"] for k, p in PAGES.items()}


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def password_ok(password):
    stored = settings()["password"]
    try:
        _, salt, _ = stored.split("$")
        return bool(password) and hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), stored)
    except ValueError:
        return False


def unlocked_until(datasette, cookies):
    """Typing the password once unlocks every locked page in this browser until this time (0 = until
    the browser closes); None = they're locked. The cookie is signed with Datasette's secret, which
    is new each start: a restart locks everything again."""
    try:
        until = datasette.unsign(cookies.get(OPEN_COOKIE, ""), OPEN_NAMESPACE).get("until")
    except Exception:
        return None
    return until if isinstance(until, (int, float)) and (until == 0 or until > time.time()) else None


def locked_pages(datasette, cookies, s=None):
    """The pages that are locked right now: locked on Settings (with a password set), unless unlocked here"""
    s = s or settings()
    if not s["password"] or unlocked_until(datasette, cookies) is not None:
        return set()
    return {p for p in s["locked"] if p in PAGES and p != "home"}


def open_until(s=None):
    minutes = (s or settings())["unlock_minutes"]
    return time.time() + minutes * 60 if minutes else 0


def set_open_cookie(datasette, response, until):
    """Unlock everything until `until` (see unlocked_until), or lock it all again (None)"""
    if until is not None:
        response.set_cookie(OPEN_COOKIE, datasette.sign({"until": until}, OPEN_NAMESPACE), httponly=True, samesite="lax")
    else:
        response.set_cookie(OPEN_COOKIE, "", max_age=0, httponly=True, samesite="lax")


def page_of(path):
    """Which page an address belongs to: /finance/... and /finance-db/... (its raw rows) are Finance's"""
    path = path.lower()  # the pages answer in any case (/HSK)
    for key, p in PAGES.items():
        if any(path == x or path.startswith(x + "/") for x in p["paths"]):
            return key
        if any(path == f"/{db}-db" or path.startswith((f"/{db}-db/", f"/{db}-db.")) for db in p["dbs"]):
            return key
    return None


def safe_next(target):
    """Only addresses on this app, so a link can't send you elsewhere after unlocking"""
    target = target or ""
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else ""


def widget_shown(key, s=None):
    """A Home widget shows when it's switched on and its page is installed and not hidden"""
    s = s or settings()
    page = WIDGETS[key]["page"]
    return key not in s["widgets_off"] and (page is None or (page in FEATURES and page not in s["hidden"]))


@hookimpl
def asgi_wrapper(datasette):
    """A locked page, everything under it and its raw Datasette pages send you to Home with the
    password popup open (or, for anything that isn't a page, answer "locked") until unlocked."""
    def wrap(app):
        async def guard(scope, receive, send):
            path = scope.get("path", "").lower() if scope["type"] == "http" else ""
            page = page_of(path) if path else None
            if path == "/settings" or path.startswith("/settings/"):
                page = "settings"
            if page:
                request = Request(scope, receive)
                s = settings()
                shut = (page == "settings" and s["password"] and unlocked_until(datasette, request.cookies) is None) \
                    or page in locked_pages(datasette, request.cookies, s)
                if shut:
                    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                        query = scope.get("query_string", b"").decode()
                        response = back("/", unlock=scope["path"] + (f"?{query}" if query else ""))
                    else:
                        response = Response.text("That page is locked. Unlock it in the app first.", status=403)
                    await response.asgi_send(send)
                    return
            await app(scope, receive, send)
        return guard
    return wrap


async def render(datasette, request, template, **context):
    s = settings()
    names, locked = page_names(s), locked_pages(datasette, request.cookies, s)
    lock_on = bool(s["password"])  # Settings, and any locked page, are behind the password
    until = unlocked_until(datasette, request.cookies)
    nav = [dict(key=k, name=names[k], url=p["url"], locked=k in locked, icon=s["icons"].get(k, ""))
           for k, p in ordered_pages(s) if page_installed(k) and k not in s["hidden"]]
    context.update(features=FEATURES, theme=theme(), ui_font=UI_FONT, v=_asset_version(), logo_uri=_logo_uri(), msg=request.args.get("msg"),
                   error=request.args.get("error"), names=names, nav=nav, locked=locked, lock_on=lock_on, px=px,
                   any_open=lock_on and until is not None,
                   here=request.path + (f"?{request.query_string}" if request.query_string else ""))
    response = Response.html(await datasette.render_template(template, context, request=request))
    # Using the app keeps it unlocked: the minutes start again
    if lock_on and until:
        set_open_cookie(datasette, response, open_until(s))
    return response


def back(path, **params):
    params = {k: v for k, v in params.items() if v not in (None, "")}
    return Response.redirect(path + ("?" + urlencode(params) if params else ""))


async def rows(db, sql, params=None):
    return [dict(r) for r in (await db.execute(sql, params or {})).rows]


async def one(db, sql, params=None):
    found = await rows(db, sql, params)
    return found[0] if found else {}


# --- Formatting -------------------------------------------------------------------

def nice_date(iso, with_year=False):
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    out = f"{d:%a} {d.day} {d:%b}"
    return f"{out} {d.year}" if with_year or d.year != date.today().year else out


def short_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b}"


def days_ago(iso):
    if not iso:
        return ""
    n = (date.today() - date.fromisoformat(iso)).days
    return {0: "today", 1: "yesterday", -1: "tomorrow"}.get(n, f"{n} days ago" if n > 0 else f"in {-n} days")


def plain(v):
    """A number as a form value: 60, 62.5 (no thousands separators)."""
    return "" if v is None else f"{v:g}"


def num(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:g}" if v != int(v) else f"{int(v):,}"
    return f"{v:,}"


def money(v):
    """RM1,234.50, or -RM12.00 below zero"""
    if v is None:
        return "-"
    return f"{'-' if v < 0 else ''}RM{abs(v):,.2f}"


def amount(v, currency="USD"):
    """A sum in a share's currency: $1,234.50, RM12.00, or 12.00 SGD"""
    if v is None:
        return "-"
    if currency == "MYR":
        return money(v)
    sign, symbol = "-" if v < 0 else "", {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency)
    return f"{sign}{symbol}{abs(v):,.2f}" if symbol else f"{sign}{abs(v):,.2f} {currency}"


def month_name(ym, short=False):
    """'2026-08' -> 'August 2026', or short: Aug '26"""
    d = date.fromisoformat(ym + "-01")
    return f"{d:%b} '{d:%y}" if short else f"{d:%B %Y}"


@hookimpl
def prepare_jinja2_environment(env):
    env.filters["nice_date"] = nice_date
    env.filters["days_ago"] = days_ago
    env.globals["px"] = px  # pixel icons, in every template (macros too)
    env.filters["pace"] = pace
    env.filters["duration"] = duration
    env.filters["ordinal_suffix"] = lambda n: "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    env.filters["name_color"] = lambda name: UNI_COLOURS[int(hashlib.md5(str(name).encode()).hexdigest(), 16) % len(UNI_COLOURS)] if name else "var(--muted)"
    env.filters["course_color"] = lambda cid: UNI_COLOURS[(int(cid or 0) - 1) % len(UNI_COLOURS)] if cid else "var(--muted)"
    env.filters["short_due"] = short_due
    env.filters["num"] = num
    env.filters["plain"] = plain
    env.filters["money"] = money
    env.filters["amount"] = amount
    env.filters["short_date"] = short_date
    env.filters["month_name"] = month_name


# --- Charts (plain HTML/SVG, coloured by CSS variables) ---------------------------

def nice_ticks(top, count=4):
    if top <= 0:
        return [0, 1]
    raw = top / count
    mag = 10 ** floor(log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    if step < 1:
        step = 1
    ticks = [0]
    while ticks[-1] < top:
        ticks.append(ticks[-1] + step)
    return ticks


def _tip(value, label):
    return f'data-tip-v="{escape(str(value))}" data-tip-l="{escape(label)}" tabindex="0"'


def legend(names):
    items = "".join(
        f'<span class="key"><i style="background:var(--series-{i + 1})"></i>{escape(n)}</span>'
        for i, n in enumerate(names)
    )
    return Markup(f'<div class="legend">{items}</div>')


def stacked_bars(items, names, fmt=num):
    """Horizontal bars, one per item, split into the named parts. items: [(label, [v, ...])]"""
    top = max((sum(v) for _, v in items), default=0) or 1
    out = []
    for label, values in items:
        total = sum(values)
        segs = []
        last = max((i for i, v in enumerate(values) if v), default=-1)
        for i, v in enumerate(values):
            if not v:
                continue
            cls = "seg end" if i == last else "seg"
            segs.append(f'<span class="{cls}" style="flex:{v};background:var(--series-{i + 1})" '
                        f'{_tip(fmt(v), f"{label} · {names[i]}")}></span>')
        width = 100 * total / top
        out.append(
            f'<div class="hbar"><span class="hbar-label" title="{escape(label)}">{escape(label)}</span>'
            f'<span class="hbar-area"><span class="hbar-fill" style="width:calc({width:.2f}% * 0.85)">{"".join(segs)}</span>'
            f'<span class="hbar-total">{fmt(total)}</span></span></div>'
        )
    head = str(legend(names)) if len(names) > 1 else ""
    return Markup(head + '<div class="hbars">' + "".join(out) + "</div>")


def pie(parts, fmt=num, keep=3):
    """A pie of a whole split into parts [(label, value)], largest first. The three biggest get a
    part each and the rest are one "Other", four colours (--pie-1..4) that stay apart for every
    reader where slices touch, checked with the palette validator. Each slice has a tooltip; the
    legend beside it gives every name, amount and share, so nothing rests on colour alone."""
    parts = sorted(((l, v) for l, v in parts if v > 0), key=lambda p: -p[1])
    total = sum(v for _, v in parts)
    if not total:
        return Markup("")
    shown = parts if len(parts) <= keep + 1 else parts[:keep] + [("Other", sum(v for _, v in parts[keep:]))]
    others = ", ".join(l for l, _ in parts[keep:]) if len(parts) > keep + 1 else ""
    slices, keys, angle = [], [], -90.0
    for i, (label, value) in enumerate(shown):
        colour = f"var(--pie-{min(i, 3) + 1})"
        share = value / total
        tip = _tip(f"{fmt(value)} · {share:.0%}", f"{label}{' (' + others + ')' if label == 'Other' and others else ''}")
        if share >= 0.9999:
            slices.append(f'<circle cx="100" cy="100" r="92" fill="{colour}" {tip}/>')
        else:
            end = angle + 360 * share
            (x1, y1), (x2, y2) = [(100 + 92 * cos(radians(a)), 100 + 92 * sin(radians(a))) for a in (angle, end)]
            slices.append(f'<path d="M100,100 L{x1:.2f},{y1:.2f} A92,92 0 {1 if share > 0.5 else 0} 1 {x2:.2f},{y2:.2f} Z" '
                          f'fill="{colour}" {tip}/>')
            angle = end
        keys.append(f'<li><i style="background:{colour}"></i><span class="pie-name">{escape(label)}</span>'
                    f'<span class="pie-val">{fmt(value)}</span><span class="pie-share">{share:.0%}</span></li>')
    return Markup(f'<div class="pie"><svg viewBox="0 0 200 200" role="img" aria-label="Spending by category">{"".join(slices)}</svg>'
                  f'<ul class="pie-legend">{"".join(keys)}</ul></div>')


def columns(points, names, unit="", fmt=num, target=None, target_label=""):
    """Vertical columns over time, stacked when there are several series. points: [(label, [v, ...])]
    target draws a dashed line across at that value (a budget, say)."""
    top = max(max((sum(v) for _, v in points), default=0), target or 0)
    ticks = nice_ticks(top)
    scale = ticks[-1] or 1
    grid = "".join(
        f'<span class="tick" style="bottom:{100 * t / scale:.2f}%"><b>{num(t)}</b></span>' for t in ticks
    )
    every = max(1, round(len(points) / 7))
    labelled = set(range(len(points) - 1, -1, -every))  # count back from the latest
    cols = []
    for idx, (label, values) in enumerate(points):
        segs = []
        last = max((i for i, v in enumerate(values) if v), default=-1)
        for i, v in enumerate(values):
            if not v:
                continue
            cls = "seg end" if i == last else "seg"
            tip = _tip(f"{fmt(v)}{unit}", f"{label} · {names[i]}") if len(names) > 1 else ""
            segs.append(f'<span class="{cls}" style="flex:{v};background:var(--series-{i + 1})" {tip}></span>')
        height = 100 * sum(values) / scale
        shown = escape(label) if idx in labelled else ""
        whole = _tip(f"{fmt(sum(values))}{unit}", label) if len(names) == 1 else ""
        cols.append(f'<div class="col" {whole}><span class="col-fill" style="height:{height:.2f}%">{"".join(segs)}</span>'
                    f'<span class="col-label">{shown}</span></div>')
    head = legend(names) if len(names) > 1 else ""
    target_line = (f'<div class="target" style="bottom:{100 * target / scale:.2f}%"><b>{escape(target_label)} {fmt(target)}{unit}</b></div>'
                   if target else "")
    return Markup(f'{head}<div class="columns"><div class="grid">{grid}</div><div class="cols">{"".join(cols)}</div>{target_line}</div>')


def line(points, unit=""):
    """One series over time, as a line with a dot per workout. points: [(iso date, value)]"""
    if not points:
        return Markup('<p class="empty">Nothing logged yet.</p>')
    ticks = nice_ticks(max(v for _, v in points))
    scale = ticks[-1] or 1
    n = len(points)

    def x(i):  # percent across, with half a step of room at each end
        return 100 * (i + 0.5) / n

    def y(v):
        return 100 * v / scale

    grid = "".join(f'<span class="tick" style="bottom:{y(t):.2f}%"><b>{num(t)}</b></span>' for t in ticks)
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i) * 10:.1f},{1000 - y(v) * 10:.1f}" for i, (_, v) in enumerate(points))
    every = max(1, round(n / 6))
    labels = "".join(f'<span class="xl" style="left:{x(i):.2f}%">{escape(short_date(points[i][0]))}</span>'
                     for i in range(n - 1, -1, -every))
    hits = "".join(
        f'<span class="hitcol" style="left:{100 * i / n:.2f}%;width:{100 / n:.2f}%" '
        f'{_tip(f"{num(v)}{unit}", nice_date(d, True))}><i class="pt" style="bottom:{y(v):.2f}%"></i></span>'
        for i, (d, v) in enumerate(points)
    )
    return Markup(
        f'<div class="linechart"><div class="grid">{grid}</div>'
        f'<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">'
        f'<path d="{path}" vector-effect="non-scaling-stroke"/></svg>{hits}{labels}</div>'
    )


def combo(labels, tips, bars=(), lines=(), refs=(), fmt=num, faded_label="", estimate_label="estimate", label_all=False):
    """Columns and lines over time on one axis, with reference lines across (a budget, an average).

    labels: the x labels. tips: (value, label) per x for the tooltip, which lists every series
    there, so the caller writes it. bars: [{"name", "color", "values", "styles"}] stacked
    bottom-up; a style is "solid", "estimate" (faded, with a dashed outline) or "faded" (only
    partly recorded).
    lines: [{"name", "color", "values" (None = no point), "recorded" ([bool]; a point that
    wasn't recorded gets no dot and a dashed line into it), "dashed" (True for a trend)}].
    refs: [{"name", "value", "style": "dashed" or "dotted"}].
    When there are too many x labels to show them all, every other one is shown counting back
    from the last. label_all shows every one (the days of a month); narrow screens then hide
    every other one but keep the first and the last.
    """
    n = len(labels)
    totals = [sum(b["values"][i] for b in bars) for i in range(n)]
    top = max([0, *totals, *(v for ln in lines for v in ln["values"] if v is not None), *(r["value"] for r in refs)])
    ticks = nice_ticks(top)
    scale = ticks[-1] or 1

    def x(i):
        return 100 * (i + 0.5) / n

    def y(v):
        return 100 * v / scale

    grid = "".join(f'<span class="tick" style="bottom:{y(t):.2f}%"><b>{num(t)}</b></span>' for t in ticks)
    cols = []
    for i in range(n):
        shown = [b for b in bars if b["values"][i]]
        segs = "".join(
            f'<span class="seg {b.get("styles", ["solid"] * n)[i]}{" end" if b is shown[-1] else ""}" '
            f'style="flex:{b["values"][i]};--c:{b["color"]}"></span>' for b in shown)
        cols.append(f'<div class="col"><span class="col-fill" style="height:{y(totals[i]):.2f}%">{segs}</span></div>')
    paths, dots = [], []
    for ln in lines:
        recorded = ln.get("recorded") or [True] * n
        pts = [None if v is None else (x(i) * 10, 1000 - y(v) * 10) for i, v in enumerate(ln["values"])]
        solid, dashed = [], []
        for i in range(1, n):
            if pts[i - 1] and pts[i]:
                seg = f"M{pts[i - 1][0]:.1f},{pts[i - 1][1]:.1f}L{pts[i][0]:.1f},{pts[i][1]:.1f}"
                (dashed if ln.get("dashed") or not (recorded[i - 1] and recorded[i]) else solid).append(seg)
        for d, cls in ((solid, "solid"), (dashed, "dash")):
            if d:  # a ring of panel colour under each line keeps it readable where it crosses a column
                paths.append(f'<path class="halo" d="{"".join(d)}" vector-effect="non-scaling-stroke"/>'
                             f'<path class="{cls}" d="{"".join(d)}" style="stroke:{ln["color"]}" vector-effect="non-scaling-stroke"/>')
        if not ln.get("dashed"):
            dots += [f'<i class="pt" style="left:{x(i):.2f}%;bottom:{y(v):.2f}%;background:{ln["color"]}"></i>'
                     for i, v in enumerate(ln["values"]) if v is not None and recorded[i]]
    # The reference lines' values go in the legend, so no label sits on top of the columns
    marks = [f'<div class="ref {r["style"]}" style="bottom:{y(r["value"]):.2f}%"></div>' for r in refs]
    hits = "".join(f'<span class="hitcol" style="left:{100 * i / n:.2f}%;width:{100 / n:.2f}%" {_tip(*tips[i])}></span>'
                   for i in range(n))
    if label_all:
        shown_x = [(i, i % 2 == 1 and i != n - 1 or n % 2 == 0 and i == n - 2) for i in range(n)]
    else:
        every = max(1, round(n / 14))
        shown_x = [(i, (n - 1 - i) // every % 2 == 1) for i in range(n - 1, -1, -every)]
    xls = "".join(f'<span class="xl{" alt" if alt else ""}" style="left:{x(i):.2f}%">{escape(labels[i])}</span>'
                  for i, alt in shown_x)
    keys = [f'<span class="key"><i class="estimate" style="--c:{b["color"]}"></i>{escape(b["name"])}</span>'
            if all(s == "estimate" for s in b.get("styles", ["solid"])) else
            f'<span class="key"><i style="background:{b["color"]}"></i>{escape(b["name"])}</span>' for b in bars]
    if any("estimate" in b.get("styles", ()) for b in bars) and not any(all(s == "estimate" for s in b.get("styles", ["solid"])) for b in bars):
        keys.append(f'<span class="key"><i class="estimate" style="--c:var(--muted)"></i>{escape(estimate_label)}</span>')
    if faded_label and any(s == "faded" and v for b in bars for s, v in zip(b.get("styles", ()), b["values"])):
        keys.append(f'<span class="key"><i class="faded" style="--c:{bars[0]["color"]}"></i>{escape(faded_label)}</span>')
    keys += [f'<span class="key"><i class="line-key{" dash" if ln.get("dashed") else ""}" style="--c:{ln["color"]}"></i>'
             f'{escape(ln["name"])}</span>' for ln in lines]
    keys += [f'<span class="key"><i class="line-key {r["style"]}"></i>{escape(r["name"])} {fmt(r["value"])}</span>' for r in refs]
    legend_html = f'<div class="legend">{"".join(keys)}</div>' if len(keys) > 1 else ""
    svg = (f'<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">{"".join(paths)}</svg>'
           if paths else "")
    return Markup(f'{legend_html}<div class="combo"><div class="grid">{grid}</div><div class="cols">{"".join(cols)}</div>'
                  f'{svg}{"".join(dots)}{"".join(marks)}{hits}{xls}</div>')


def bullets(items, fmt=num, untargeted="not in budget"):
    """Ranked horizontal bars on a track, each against its own target: items are (label,
    value, target or None, share of the total). Past its target a bar turns red. The numbers
    sit in columns: the value, its target, and what's left of it (green) or over it (red).
    A bar without a target says `untargeted`, or its share when that's None."""
    # A little room at the end, so the longest bar stops short of the end of its track
    scale = 1.04 * max([0, *(v for _, v, _, _ in items), *(t or 0 for _, _, t, _ in items)]) or 1
    targets = any(t is not None for _, _, t, _ in items)

    def pct(v):
        return f"{100 * v / scale:.2f}%"

    out = ['<span></span><span></span><span class="bullet-head">Spent</span><span class="bullet-head">Budget</span>'
           '<span class="bullet-head">Left</span>'] if targets else []
    for label, v, target, share in items:
        over = v - target if target is not None and v > target else 0
        if target is None:
            detail = untargeted or ""
            second, third = escape(untargeted) if untargeted else f"{share:.0%}", ""
        elif over:
            detail = f"{fmt(over)} over a budget of {fmt(target)}"
            second, third = fmt(target), f'<b class="over-text">-{fmt(over)}</b>'
        else:
            detail = f"{fmt(target - v)} left of {fmt(target)}"
            second, third = fmt(target), f'<span class="up">{fmt(target - v)}</span>'
        bar = f'<span class="bullet-fill" style="width:{pct(v - over)}"></span>'
        if over:
            bar += f'<span class="bullet-over" style="left:{pct(target)};width:calc({pct(over)} - 2px)"></span>'
        if target is not None:
            bar += f'<i class="bullet-target" style="left:{pct(target)}"></i>'
        out.append(f'<div class="bullet" {_tip(fmt(v), chr(10).join(filter(None, [label, detail, f"{share:.0%} of the spending"])))}>'
                   f'<span class="hbar-label" title="{escape(label)}">{escape(label)}</span>'
                   f'<span class="bullet-track">{bar}</span><span class="bullet-val">{fmt(v)}</span>'
                   f'<span class="bullet-val muted">{second}</span><span class="bullet-val">{third}</span></div>')
    return Markup(f'<div class="bullets">{"".join(out)}</div>')


def weeks_back(n):
    monday = date.today() - timedelta(days=date.today().weekday())
    return [(monday - timedelta(weeks=k)).isoformat() for k in range(n - 1, -1, -1)]


# --- Home -----------------------------------------------------------------------

async def home(request, datasette):
    s = settings()
    # The widgets switched on in Settings (and whose page isn't hidden); a locked page's widget
    # is covered, and stand-ins replace its data (stand_in)
    show = {w: widget_shown(w, s) for w in WIDGETS}
    locked = locked_pages(datasette, request.cookies, s)
    db = lambda name: datasette.get_database(name) if name in FEATURES else None
    zh, wo, fi, un = db("chinese"), db("workouts"), db("finance"), db("work")
    ctx = dict(page="home", show=show, shuffle=int(request.args.get("shuffle", "0")) if request.args.get("shuffle", "").isdigit() else 0)
    if show["chinese"] and zh:
        await sync(datasette)
        ctx["picks"] = await todays_words(zh, ctx["shuffle"])
    if show["workouts"] and wo:
        lifted, ran = await workout_days(wo)
        ctx["streak"] = streaks(set(lifted) | set(ran))
        today_iso = date.today().isoformat()
        plan = await plan_for_day(wo, today_iso)
        if plan:
            ctx["today_plan"] = dict(name=plan["name"], n=len(plan["exercises"]), rest=False)
        elif await is_rest_day(wo, today_iso):
            ctx["today_plan"] = dict(name="Rest day", n=0, rest=True)
    if show["finance"] and fi:
        ctx["fin"] = await day_summary(fi, date.today().isoformat(), await budget_lines(fi))
    if show["work"] and un:
        uni_open = await rows(un, "SELECT * FROM task_list")
        ctx["uni"] = dict(semester=await semester_week(un), open=len(uni_open),
                          next=sorted((t for t in uni_open if t["priority"] <= 2), key=lambda t: (t["priority"], t["due"] is None, t["due"] or ""))[:8],
                          overdue=sum(t["days_left"] is not None and t["days_left"] < 0 for t in uni_open),
                          week=sum(t["days_left"] is not None and 0 <= t["days_left"] <= 7 for t in uni_open),
                          top=sum(t["priority"] <= 2 for t in uni_open))
    if show["quote"]:
        ctx["quote"] = await quote_of_the_day(datasette)
    # A locked widget gets stand-ins shaped like its data, so its card keeps its size and nothing real is sent
    stand_in(ctx, locked)
    ctx["today"] = dict(weekday=date.today().strftime("%A"), day=date.today().strftime("%d"), month=date.today().strftime("%m"), rest=date.today().strftime("%Y · %A"))
    return await render(datasette, request, "app_home.html", **ctx)


def stand_in(ctx, locked):
    """Swap a locked widget's data for stand-ins of the same shape: as many words, the same plan
    lines, a budget bar or not, the quote with every letter an x. The card is laid out as usual
    (so it's as big as it really is) but stays hidden under the lock."""
    blank = lambda text: re.sub(r"\w", "x", text or "")
    if "chinese" in locked and "picks" in ctx:
        ctx["picks"] = [dict(id=0, character="字", pinyin="zì", english="word", type="", category="", hsk=None,
                             status=w["status"]) for w in ctx["picks"]]
    if "workouts" in locked and "streak" in ctx:
        ctx["streak"] = {"days": 0, "weeks": 0}
        if ctx.get("today_plan"):
            p = ctx["today_plan"]
            ctx["today_plan"] = dict(name=blank(p["name"]), n=p["n"], rest=p["rest"])
    if "finance" in locked and "fin" in ctx:
        budget = ctx["fin"]["daily_total"].get("budget")
        ctx["fin"] = {"daily_total": dict(budget=1 if budget else 0, spent=0, left=1, pct=0, over=False)}
    if "work" in locked and "uni" in ctx:
        ctx["uni"] = dict(semester=None, open=0, next=[], overdue=0, week=0, top=0)
    if "quotes" in locked and ctx.get("quote"):
        q = ctx["quote"]
        ctx["quote"] = dict(api=False, text=blank(q.get("text")), author=blank(", ".join(filter(None, [q.get("author"), q.get("source")]))))


async def workout_days(wo):
    """({date: [exercises logged]}, {date: [Strava runs]}): every day you trained, a run counting as
    a workout too. Weeks are Monday to Sunday, so a streak runs on across month ends."""
    lifted, ran = {}, {}
    for r in await rows(wo, "SELECT DISTINCT date, exercise FROM sets ORDER BY date, exercise"):
        lifted.setdefault(date.fromisoformat(r["date"]), []).append(r["exercise"])
    for r in await rows(wo, "SELECT date, name, km FROM strava_runs ORDER BY date, id"):
        ran.setdefault(date.fromisoformat(r["date"]), []).append(r)
    return lifted, ran


def streaks(days):
    """Days and weeks in a row with a workout. Today (or this week) without one yet doesn't
    break the streak; it counts from yesterday (or last week) until it's over."""
    today = date.today()
    d = today if today in days else today - timedelta(days=1)
    day_streak = 0
    while d in days:
        day_streak += 1
        d -= timedelta(days=1)
    weeks = {d - timedelta(days=d.weekday()) for d in days}
    wk = today - timedelta(days=today.weekday())
    if wk not in weeks:
        wk -= timedelta(weeks=1)
    week_streak = 0
    while wk in weeks:
        week_streak += 1
        wk -= timedelta(weeks=1)
    return {"days": day_streak, "weeks": week_streak}


async def daily_settings(zh):
    """(new words, revise words, HSK levels) for Today's words, from the settings table"""
    found = {r["key"]: r["value"] for r in await rows(zh, "SELECT key, value FROM settings WHERE key LIKE 'daily_%'")}

    def count(key, default):
        value = found.get(key, "")
        return int(value) if value.isdigit() else default

    levels = [int(x) for x in found.get("daily_hsk", "").split(",") if x.strip().isdigit()]
    return count("daily_learn", 2), count("daily_revise", 1), levels


async def todays_words(zh, shuffle=0):
    """Today's words: settings.daily_revise from To revise, the rest from To learn.

    The new words can be limited to some HSK levels (settings.daily_hsk). If fewer words are
    due for revision than asked for, To learn fills the gap. The draw is fixed for the day
    (and the shuffle number), so Home shows the same words until you study them: a word that
    leaves its list is replaced, the others stay put.
    """
    day = date.today().isoformat()
    n_learn, n_revise, levels = await daily_settings(zh)

    def draw(words, n):
        return sorted(words, key=lambda w: hashlib.sha1(f"{day}:{shuffle}:{w['id']}".encode()).digest())[:n]

    fields = "id, character, pinyin, english, notes, type, category, hsk, status, last_studied"
    revise = draw(await rows(zh, f"SELECT {fields} FROM words WHERE status = 'to revise'"), n_revise)
    where = f" AND hsk IN ({','.join(str(l) for l in levels)})" if levels else ""
    learn = draw(await rows(zh, f"SELECT {fields} FROM words WHERE status = 'to learn'{where}"),
                 n_learn + n_revise - len(revise))
    return learn + revise


# --- Chinese ----------------------------------------------------------------------

async def list_counts(zh):
    found = {r["status"]: r["n"] for r in await rows(zh, "SELECT status, count(*) AS n FROM words GROUP BY status")}
    return {s: found.get(s, 0) for s in STATUS_ORDER}


async def chinese(request, datasette):
    moved = await sync(datasette)
    zh = datasette.get_database("chinese")
    counts = await list_counts(zh)
    tab = request.args.get("list")
    if tab not in (*LISTS, "add", "stats", "settings"):
        tab = "to-revise" if counts["to revise"] else "to-learn"
    interval = int((await one(zh, "SELECT value FROM settings WHERE key = 'revise_after_days'"))["value"])
    n_learn, n_revise, levels = await daily_settings(zh)
    ctx = dict(page="chinese", tab=tab, counts=counts, moved=moved, interval=interval,
               daily=dict(learn=n_learn, revise=n_revise, levels=levels),
               hsk_levels=[r["hsk"] for r in await rows(zh, "SELECT DISTINCT hsk FROM words WHERE hsk IS NOT NULL ORDER BY hsk")],
               types=[r["name"] for r in await rows(zh, "SELECT name FROM types ORDER BY name")],
               categories=[r["name"] for r in await rows(zh, "SELECT name FROM categories ORDER BY name")])
    if tab in LISTS:
        order = {"to-revise": "last_studied, id", "to-learn": "hsk IS NULL, hsk, id", "learnt": "revise_on, id"}[tab]
        ctx["words"] = await rows(zh, f"""
            SELECT id, character, pinyin, english, notes, type, category, hsk, last_studied,
                   date(last_studied, '+' || :n || ' days') AS revise_on
            FROM words WHERE status = :status ORDER BY {order}""", {"status": LISTS[tab], "n": interval})
    elif tab == "stats":
        ctx.update(await chinese_stats(zh))
    return await render(datasette, request, "app_chinese.html", **ctx)


async def chinese_stats(zh):
    def split(rs, key):
        table = {}
        for r in rs:
            table.setdefault(r[key], [0, 0, 0])[STATUS_ORDER.index(r["status"])] = r["n"]
        return table

    hsk = split(await rows(zh, "SELECT hsk, status, count(*) AS n FROM words GROUP BY hsk, status"), "hsk")
    hsk_items = [(f"HSK {k}" if k else "No level", v) for k, v in sorted(hsk.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))]
    cat = split(await rows(zh, "SELECT category, status, count(*) AS n FROM words GROUP BY category, status"), "category")
    cat_items = sorted(((k or "No category", v) for k, v in cat.items()), key=lambda kv: -sum(kv[1]))
    weeks = weeks_back(12)
    log = await rows(zh, """
        SELECT date(date, '-6 days', 'weekday 1') AS week,
               sum(from_status = 'to learn') AS learnt, sum(from_status = 'to revise') AS revised
        FROM study_log WHERE to_status = 'learnt' AND date >= :start GROUP BY week""", {"start": weeks[0]})
    by_week = {r["week"]: (r["learnt"], r["revised"]) for r in log}
    weekly = [(short_date(wk), list(by_week.get(wk, (0, 0)))) for wk in weeks]
    return dict(
        hsk_chart=stacked_bars(hsk_items, STATUS_ORDER), hsk_items=hsk_items,
        cat_chart=stacked_bars(cat_items, STATUS_ORDER), cat_items=cat_items,
        weekly_chart=columns(weekly, ("newly learnt", "revised")), weekly=weekly,
        status_names=STATUS_ORDER,
    )


async def chinese_move(request, datasette):
    form = await request.post_vars()
    to = form.get("to")
    ids = [int(i) for i in re.findall(r"\d+", form.get("ids") or form.get("id") or "")]
    if to not in STATUS_ORDER or not ids:
        return Response.json({"ok": False, "error": "bad request"}, status=400)
    zh = datasette.get_database("chinese")
    marks = ",".join("?" * len(ids))
    await zh.execute_write(
        f"UPDATE words SET status = ?, last_studied = date('now', 'localtime') WHERE id IN ({marks})", [to, *ids])
    if "application/json" in request.headers.get("accept", ""):
        return Response.json({"ok": True, "counts": await list_counts(zh)})
    if form.get("from") == "home":
        return back("/", shuffle=form.get("shuffle") if form.get("shuffle") != "0" else None)
    return back("/hsk", list=form.get("list"))


async def chinese_add(request, datasette):
    form = await request.post_vars()
    fields = {k: (form.get(k) or "").strip() for k in ("character", "pinyin", "english", "notes", "type", "category", "hsk")}
    if not fields["character"]:
        return back("/hsk", list="add", error="A word needs a character.")
    zh = datasette.get_database("chinese")
    try:
        await zh.execute_write("""
            INSERT INTO words (character, pinyin, english, notes, type, category, hsk)
            VALUES (:character, :pinyin, :english, :notes, :type, :category, NULLIF(:hsk, ''))""", fields)
    except Exception as e:
        reason = "it's already in the database" if "UNIQUE" in str(e) else str(e)
        return back("/hsk", list="add", error=f"Couldn't add {fields['character']}: {reason}.")
    return back("/hsk", list="add", msg=f"Added {fields['character']} to To learn.")


async def chinese_settings(request, datasette):
    form = await request.post_vars()
    zh = datasette.get_database("chinese")
    numbers = {"days": 1, "daily_learn": 0, "daily_revise": 0}
    for key, least in numbers.items():
        if not form.get(key, "").isdigit() or int(form[key]) < least:
            return back("/hsk", list="settings", error="Those settings must be whole numbers.")
    if int(form["daily_learn"]) + int(form["daily_revise"]) < 1:
        return back("/hsk", list="settings", error="Today's words needs at least one word.")
    levels = sorted(int(k[4:]) for k in form if k.startswith("hsk_") and k[4:].isdigit())
    values = {"revise_after_days": str(int(form["days"])), "daily_learn": str(int(form["daily_learn"])),
              "daily_revise": str(int(form["daily_revise"])), "daily_hsk": ",".join(map(str, levels))}

    def save(conn):
        for key, value in values.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", [key, value])
    await zh.execute_write_fn(save)
    await sync(datasette)
    return back("/hsk", list="settings", msg="Saved. Home's Today's words follows the new settings.")


# --- Workouts -------------------------------------------------------------------

def valid_date(value):
    try:
        return value if DATE_RE.match(value or "") and date.fromisoformat(value) else None
    except ValueError:
        return None


async def workouts(request, datasette):
    wo = datasette.get_database("workouts")
    view = request.args.get("view")
    if view not in ("log", "plans", "history", "stats", "settings"):
        view = "log"
    day = valid_date(request.args.get("date")) or date.today().isoformat()
    ctx = dict(page="workouts", view=view, day=day, today=date.today().isoformat())
    if view == "log":
        # The day's exercises in the order you dragged them into (day_order), the rest after, in the
        # order they were first logged
        sets = await rows(wo, """
            SELECT s.* FROM (
                SELECT id, exercise, reps, weight, notes,
                       ROW_NUMBER() OVER (PARTITION BY exercise ORDER BY id) AS set_no,
                       min(id) OVER (PARTITION BY exercise) AS first_id
                FROM sets WHERE date = :d) s
            LEFT JOIN day_order o ON o.date = :d AND o.exercise = s.exercise
            ORDER BY o.position IS NULL, o.position, s.first_id, s.id""", {"d": day})
        groups = []
        for s in sets:
            if not groups or groups[-1]["exercise"] != s["exercise"]:
                groups.append({"exercise": s["exercise"], "sets": []})
            groups[-1]["sets"].append(s)
        plan_names = {r["name"].lower() for r in await rows(wo, "SELECT name FROM plans")}
        for g in groups:
            # Entries without reps ("Bouldering") are done, but aren't counted as sets
            g["counted"] = sum(s["reps"] is not None for s in g["sets"])
            g["reps"] = sum(s["reps"] or 0 for s in g["sets"])
            g["volume"] = sum((s["reps"] or 0) * s["weight"] for s in g["sets"])
            # A whole plan logged as one entry ("Bouldering", "Leg Day") is just done: it gets no set
            # row. Any other exercise lists its sets, even a single one.
            g["whole_plan"] = (len(g["sets"]) == 1 and not g["sets"][0]["reps"]
                               and g["exercise"].lower() in plan_names)
        last_per_exercise = await rows(wo, """
            SELECT s.exercise, s.reps, s.weight FROM sets s
            JOIN (SELECT exercise, max(id) AS id FROM sets GROUP BY exercise) m ON m.id = s.id
            ORDER BY s.id DESC""")
        # The form starts with one empty set row; only a failed log (or a link) fills it in.
        # A failed log sends its rows back as "8,8,6" / "60,60,62.5".
        prefill = {k: request.args.get(k, "") for k in ("exercise", "notes")}
        prefill["sets"] = [{"reps": r, "weight": w} for r, w in zip_longest(
            request.args.get("reps", "").split(","), request.args.get("weight", "").split(","), fillvalue="")]
        done = {g["exercise"].lower(): len(g["sets"]) for g in groups}
        # Picking an exercise in the form fills in every set of its last session before this day
        # (or its last entry, when it was only done on this day or later)
        last_sets = {}
        for s in await rows(wo, """
                SELECT s.exercise, s.reps, s.weight FROM sets s
                JOIN (SELECT exercise, max(date) AS date FROM sets WHERE date < :d GROUP BY exercise) m
                  ON m.exercise = s.exercise AND m.date = s.date
                ORDER BY s.id""", {"d": day}):
            last_sets.setdefault(s["exercise"].lower(), []).append({"reps": s["reps"], "weight": s["weight"]})
        for e in last_per_exercise:
            last_sets.setdefault(e["exercise"].lower(), [{"reps": e["reps"], "weight": e["weight"]}])
        plan = await plan_for_day(wo, day)
        if plan:
            last_weight = {e["exercise"].lower(): e["weight"] for e in last_per_exercise}
            for e in plan["exercises"]:
                e["done"] = done.get(e["exercise"].lower(), 0)
                first = re.match(r"\d+", e["reps"] or "")
                e["log_reps"] = int(first.group()) if first else ""
                e["log_weight"] = e["weight"] if e["weight"] is not None else last_weight.get(e["exercise"].lower(), 0)
        ctx.update(
            plan=plan, plans=await all_plans(wo), rest_day=plan is None and await is_rest_day(wo, day),
            groups=groups, prefill=prefill, last_per_exercise=last_per_exercise,
            log_data={"last": last_sets, "done": done},
            total_sets=sum(g["counted"] for g in groups), total_volume=sum(g["volume"] for g in groups),
            prev_day=(date.fromisoformat(day) - timedelta(days=1)).isoformat(),
            next_day=(date.fromisoformat(day) + timedelta(days=1)).isoformat() if day < date.today().isoformat() else None,
        )
    elif view == "plans":
        ctx.update(await plans_page(wo, request.args.get("plan")))
    elif view == "history":
        ctx["history"] = await rows(wo, "SELECT * FROM workouts LIMIT 100")
        ctx["runs"] = await rows(wo, "SELECT * FROM strava_runs")  # newest first
    elif view == "settings":
        ctx["strava"] = await strava_panel(wo)
    else:
        ctx.update(await workout_stats(wo, request.args.get("month")))
        ctx["running"] = await running_stats(wo)
        ctx["strava_on"] = bool((await workout_settings(wo)).get("strava_synced"))
    return await render(datasette, request, "app_workouts.html", **ctx)


# --- Strava: every activity you've logged there --------------------------------------------
#
# Read by a scraper that signs in to strava.com as you (scrapers/strava in this folder,
# strava.py; its browser keeps you signed in). Workouts > Settings takes your profile address,
# opens the sign-in window and starts syncs; the scraper writes the activities into workouts.db
# (strava_activities) and how it went into its settings table (strava_synced, strava_said, strava_error).

STRAVA_PROJECT = ROOT / "scrapers" / "strava"
STRAVA_PYTHON = Path.home() / ".local/share/venvs/strava/bin/python"
STRAVA_GAP = timedelta(hours=1)  # opening Stats syncs again only after this long
RUN_SPORTS = ("Run", "TrailRun", "VirtualRun")
_strava = {}  # "proc": the scraper while it runs, "what": "login" or "sync"


async def workout_settings(wo):
    return {r["key"]: r["value"] for r in await rows(wo, "SELECT key, value FROM settings")}


async def save_workout_settings(wo, **values):
    await wo.execute_write_fn(lambda conn: conn.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [(k, "" if v is None else str(v)) for k, v in values.items()]))


def strava_athlete_id(text):
    """'https://www.strava.com/athletes/12345678' (or just the number) -> '12345678'"""
    found = re.search(r"athletes/(\d+)", text or "") or re.fullmatch(r"\s*(\d+)\s*", text or "")
    return found.group(1) if found else ""


def strava_running():
    proc = _strava.get("proc")
    return bool(proc) and proc.poll() is None


def strava_start(*args):
    """Run strava.py in the background (login shows a browser window; sync doesn't)"""
    if strava_running():
        return "Strava is already busy."
    if not STRAVA_PYTHON.exists():
        return f"The Strava scraper isn't set up ({STRAVA_PYTHON} is missing); see {STRAVA_PROJECT}/README.md."
    _strava["proc"] = subprocess.Popen([str(STRAVA_PYTHON), "-I", str(STRAVA_PROJECT / "strava.py"), *args], cwd=STRAVA_PROJECT,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    _strava["what"] = args[0]
    return ""


async def strava_save(request, datasette):
    """Your Strava profile's address"""
    if request.method != "POST":
        return back("/workouts", view="settings")
    form = await request.post_vars()
    athlete = strava_athlete_id(form.get("profile", ""))
    if form.get("profile", "").strip() and not athlete:
        return back("/workouts", view="settings", error="That doesn't look like a Strava profile (strava.com/athletes/<number>).")
    await save_workout_settings(datasette.get_database("workouts"), strava_profile=athlete)
    return back("/workouts", view="settings", msg="Saved your Strava profile.")


async def strava_login(request, datasette):
    """Open Strava's sign-in in a browser window; once you're in, it reads everything"""
    if request.method != "POST":
        return back("/workouts", view="settings")
    problem = strava_start("login")
    return back("/workouts", view="settings", **({"error": problem} if problem else
                {"msg": "A Strava window is open: sign in there. It closes by itself and reads your activities."}))


async def strava_sync_route(request, datasette):
    """Start a sync: a button, `skyydb strava`, or opening Stats (then only when the last one is a while ago)"""
    if request.method != "POST":
        return Response.text("POST only", status=405)
    form = await request.post_vars()
    wo = datasette.get_database("workouts")
    s = await workout_settings(wo)
    wants_json = "application/json" in request.headers.get("accept", "")
    started, problem = False, ""
    if form.get("when") == "stale" and s.get("strava_synced") and \
            datetime.now() - datetime.strptime(s["strava_synced"], "%Y-%m-%d %H:%M") < STRAVA_GAP:
        pass
    elif form.get("when") == "stale" and not s.get("strava_synced"):
        pass  # never signed in yet: nothing to do by itself
    else:
        problem = strava_start("sync", *(["--all"] if form.get("everything") else []))
        started = not problem
    if wants_json:
        return Response.json({"started": started, "running": strava_running(), "problem": problem,
                              "count": (await one(wo, "SELECT count(*) AS n FROM strava_activities"))["n"]})
    return back("/workouts", view="settings", **({"error": problem} if problem else
                {"msg": "Reading your Strava activities… this page shows how it went when you come back."}))


async def strava_status(request, datasette):
    """Is the scraper still going, and how did the last run go (the page asks every few seconds)"""
    wo = datasette.get_database("workouts")
    s = await workout_settings(wo)
    return Response.json({"running": strava_running(), "what": _strava.get("what", ""), "synced": s.get("strava_synced", ""),
                          "said": s.get("strava_said", ""), "error": s.get("strava_error", ""),
                          "count": (await one(wo, "SELECT count(*) AS n FROM strava_activities"))["n"]})


async def strava_panel(wo):
    """Workouts > Settings: Strava as it stands"""
    s = await workout_settings(wo)
    counts = await one(wo, f"""SELECT count(*) AS n, count(*) FILTER (WHERE sport IN ({",".join("?" * len(RUN_SPORTS))})) AS runs,
                                      min(date(start)) AS first FROM strava_activities""", list(RUN_SPORTS))
    return dict(profile=s.get("strava_profile", ""), athlete=s.get("strava_athlete", ""), synced=s.get("strava_synced", ""),
                said=s.get("strava_said", ""), error=s.get("strava_error", ""), running=strava_running(),
                what=_strava.get("what", ""), ready=STRAVA_PYTHON.exists(), **counts)


def pace(seconds_per_km):
    if not seconds_per_km:
        return "-"
    m, s = divmod(round(seconds_per_km), 60)
    return f"{m}:{s:02d}"


def duration(seconds):
    h, rest = divmod(int(seconds or 0), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def running_stats(wo):
    """The Stats tab's running section, from your Strava runs (None when there are none)"""
    runs = await rows(wo, "SELECT * FROM strava_runs")
    if not runs:
        return None
    today = date.today()
    month, year = today.isoformat()[:7], str(today.year)
    km = lambda rs: round(sum(r["km"] for r in rs), 1)
    this_month = [r for r in runs if r["date"].startswith(month)]
    this_year = [r for r in runs if r["date"].startswith(year)]
    recent = [r for r in runs if r["date"] >= (today - timedelta(days=30)).isoformat() and r["pace"]]
    longest = max(runs, key=lambda r: r["km"])
    # Fastest pace: over runs of 3 km or more, so a dash doesn't count
    fast = min((r for r in runs if r["km"] >= 3 and r["pace"]), key=lambda r: r["pace"], default=None)
    recent_pace = sum(r["moving_time"] for r in recent) / sum(r["km"] for r in recent) if recent and sum(r["km"] for r in recent) else None
    return dict(
        month_km=km(this_month), month_runs=len(this_month), year_km=km(this_year), year_runs=len(this_year),
        total_km=km(runs), total_runs=len(runs), longest=longest, fastest=fast, recent_pace=recent_pace,
        weekly_chart=run_weeks_chart(runs))


def week_span(monday):
    """'9 – 15 Nov', or '26 Oct – 1 Nov' across a month end"""
    sunday = monday + timedelta(days=6)
    return f"{monday.day} – {sunday.day} {sunday:%b}" if monday.month == sunday.month else f"{monday.day} {monday:%b} – {sunday.day} {sunday:%b}"


def hours_minutes(seconds):
    h, m = divmod(round((seconds or 0) / 60), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def run_weeks_chart(runs, n=12):
    """Distance per week over the last n weeks, after Strava's: the chosen week's distance, time and
    climb on top, then a line with a soft fill and a ring per week; pointing at (or tabbing to) a
    week shows it on top. Starts on this week."""
    weeks = [date.fromisoformat(wk) for wk in weeks_back(n)]
    per = {wk: dict(km=0.0, secs=0, climb=0.0) for wk in weeks}
    for r in runs:
        d = date.fromisoformat(r["date"])
        wk = d - timedelta(days=d.weekday())
        if wk in per:
            per[wk]["km"] += r["km"] or 0
            per[wk]["secs"] += r["moving_time"] or 0
            per[wk]["climb"] += r["elevation"] or 0
    top = nice_ticks(max(w["km"] for w in per.values()))[-1] or 1
    xs = [100 * i / (n - 1) for i in range(n)]
    ys = [100 - 100 * per[wk]["km"] / top for wk in weeks]
    info = [dict(span=week_span(wk), km=f"{per[wk]['km']:.2f} km", time=hours_minutes(per[wk]["secs"]),
                 climb=f"{round(per[wk]['climb'])} m") for wk in weeks]
    line_pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys))
    grid = "".join(f'<line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="100"/>' for x in xs)
    svg = (f'<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">'
           f'<defs><linearGradient id="run-fill" x1="0" y1="0" x2="0" y2="1">'
           f'<stop offset="0" style="stop-color:var(--run);stop-opacity:0.45"/>'
           f'<stop offset="1" style="stop-color:var(--run);stop-opacity:0.02"/></linearGradient></defs>'
           f'<g class="wk-grid">{grid}<line x1="0" y1="0" x2="100" y2="0"/></g>'
           f'<line class="wk-base" x1="0" y1="100" x2="100" y2="100"/>'
           f'<path class="wk-area" d="M0,100 L{line_pts.replace(" ", " L")} L100,100 Z"/>'
           f'<polyline class="wk-line" points="{line_pts}"/></svg>')
    last = n - 1
    points = "".join(
        f'<button type="button" class="wk-pt{" on" if i == last else ""}" data-i="{i}" style="left:{x:.2f}%;top:{y:.2f}%" '
        f'aria-label="{escape(info[i]["span"])}: {escape(info[i]["km"])}"></button>'
        for i, (x, y) in enumerate(zip(xs, ys)))
    # A month's name under the week its 1st falls in
    months = "".join(
        f'<span class="wk-month" style="left:{x:.2f}%">{(wk + timedelta(days=6)):%b}</span>'
        for x, wk in zip(xs, weeks) if (wk + timedelta(days=6)).day <= 7)
    now = info[last]
    head = (f'<div class="wk-span">{escape(now["span"])}</div><div class="wk-stats">'
            + "".join(f'<div><span>{label}</span><b data-k="{key}">{escape(now[key])}</b></div>'
                      for label, key in (("Distance", "km"), ("Time", "time"), ("Elevation", "climb")))
            + "</div>")
    return Markup(
        f'<div class="wk-chart" data-weeks="{escape(json.dumps(info))}">{head}'
        f'<div class="wk-label">Last {n} weeks</div>'
        f'<div class="wk-plot"><span class="wk-top">{num(top)} km</span>{svg}'
        f'<i class="wk-rule" style="left:{xs[last]:.2f}%;top:{ys[last]:.2f}%"></i>{points}{months}</div></div>')


# --- Workout plans ------------------------------------------------------------------

async def all_plans(wo):
    plans = await rows(wo, "SELECT id, name, notes, icon FROM plans ORDER BY name COLLATE NOCASE")
    by_plan = {}
    for e in await rows(wo, "SELECT * FROM plan_exercises ORDER BY plan_id, position, id"):
        by_plan.setdefault(e["plan_id"], []).append(e)
    for p in plans:
        p["exercises"] = by_plan.get(p["id"], [])
    return plans


async def schedule(wo):
    return {r["day"]: r["plan_id"] for r in await rows(wo, "SELECT day, plan_id FROM plan_schedule")}


def scheduled(day, sched):
    """(plan id or None, "one-off" or "weekly") for a date: a date entry wins over its weekday."""
    if day in sched:
        return sched[day], "one-off"
    return sched.get(WEEKDAYS[date.fromisoformat(day).weekday()]), "weekly"


async def plan_for_day(wo, day):
    plan_id, _ = scheduled(day, await schedule(wo))
    return next((p for p in await all_plans(wo) if p["id"] == plan_id), None) if plan_id else None


async def is_rest_day(wo, day):
    """True when the schedule says rest (rather than nothing being planned at all)."""
    sched = await schedule(wo)
    return bool(sched) and scheduled(day, sched)[0] is None


async def plans_page(wo, editing):
    plans = await all_plans(wo)
    sched = await schedule(wo)
    names = {p["id"]: p["name"] for p in plans}
    today = date.today()
    upcoming = []
    for k in range(7):
        d = (today + timedelta(days=k)).isoformat()
        plan_id, source = scheduled(d, sched)
        upcoming.append({"date": d, "plan": names.get(plan_id), "plan_id": plan_id, "source": source})
    one_offs = [{"date": d, "plan": names.get(pid)} for d, pid in sorted(sched.items())
                if DATE_RE.match(d) and d >= today.isoformat()]
    edit = None
    if editing == "new":
        edit = {"id": "", "name": "", "notes": "", "icon": "", "exercises": []}
    elif editing and editing.isdigit():
        edit = next((p for p in plans if p["id"] == int(editing)), None)
    return dict(
        plans=plans, upcoming=upcoming, one_offs=one_offs, edit=edit,
        weekly=[(key, WEEKDAY_NAMES[i], sched.get(key)) for i, key in enumerate(WEEKDAYS)],
        exercise_names=[r["name"] for r in await rows(wo, "SELECT name FROM exercises ORDER BY name COLLATE NOCASE")],
        plan_icons=PLAN_ICONS, icons={p["id"]: p["icon"] for p in plans},
    )


async def plan_save(request, datasette):
    form = parse_qs((await request.post_body()).decode(), keep_blank_values=True)
    get = lambda key: (form.get(key) or [""])[0].strip()
    plan_id, name = get("id"), get("name")
    icon = get("icon") if icon_ok(get("icon")) else ""
    if not name:
        return back("/workouts", view="plans", plan=plan_id or "new", error="A plan needs a name.")
    columns = [form.get(k, []) for k in ("exercise", "sets", "reps", "weight", "ex_notes")]
    exercises = []
    for exercise, sets, reps, weight, notes in zip(*columns):
        if not exercise.strip():
            continue
        try:
            sets_n = int(sets) if sets.strip() else None
            weight_n = float(weight) if weight.strip() else None
            if (sets_n is not None and sets_n < 1) or (weight_n is not None and weight_n < 0):
                raise ValueError
        except ValueError:
            return back("/workouts", view="plans", plan=plan_id or "new",
                        error=f"{exercise.strip()}: sets must be a whole number above 0 and weight 0 or more.")
        exercises.append((exercise.strip(), sets_n, reps.strip(), weight_n, notes.strip()))

    def write(conn):
        if plan_id:
            conn.execute("UPDATE plans SET name = ?, notes = ?, icon = ? WHERE id = ?", [name, get("notes"), icon, int(plan_id)])
            pid = int(plan_id)
        else:
            pid = conn.execute("INSERT INTO plans (name, notes, icon) VALUES (?, ?, ?)", [name, get("notes"), icon]).lastrowid
        conn.execute("DELETE FROM plan_exercises WHERE plan_id = ?", [pid])
        conn.executemany(
            "INSERT INTO plan_exercises (plan_id, position, exercise, sets, reps, weight, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(pid, i + 1, *e) for i, e in enumerate(exercises)])

    try:
        await datasette.get_database("workouts").execute_write_fn(write)
    except Exception as e:
        reason = "there's already a plan with that name" if "UNIQUE" in str(e) else str(e)
        return back("/workouts", view="plans", plan=plan_id or "new", error=f"Couldn't save {name}: {reason}.")
    return back("/workouts", view="plans",
                msg=f"Saved {name} ({len(exercises)} exercise{'s' * (len(exercises) != 1)})." if exercises else f"Saved {name}.")


async def plan_delete(request, datasette):
    form = await request.post_vars()
    wo = datasette.get_database("workouts")
    plan = await one(wo, "SELECT id, name FROM plans WHERE id = :id", {"id": form.get("id")})
    if plan:
        await wo.execute_write("DELETE FROM plans WHERE id = ?", [plan["id"]])
    return back("/workouts", view="plans", msg=f"Deleted {plan['name']}." if plan else None)


async def plan_order(request, datasette):
    """A plan card's exercises dragged into a new order (their ids, top to bottom); JSON for app.js"""
    if request.method != "POST":
        return back("/workouts", view="plans")
    form = parse_qs((await request.post_body()).decode())
    plan_id = (form.get("id") or [""])[0]
    ids = [int(i) for i in form.get("row", []) if i.isdigit()]
    if not plan_id.isdigit() or not ids:
        return Response.json({"ok": False}, status=400)

    def write(conn):
        conn.executemany("UPDATE plan_exercises SET position = ? WHERE id = ? AND plan_id = ?",
                         [(i + 1, row_id, int(plan_id)) for i, row_id in enumerate(ids)])

    await datasette.get_database("workouts").execute_write_fn(write)
    return Response.json({"ok": True})


async def day_order_save(request, datasette):
    """The Log tab's exercises for one day dragged into a new order (names, top to bottom); JSON"""
    if request.method != "POST":
        return back("/workouts", view="log")
    form = parse_qs((await request.post_body()).decode())
    day = valid_date((form.get("date") or [""])[0])
    names = form.get("exercise", [])
    if not day or not names:
        return Response.json({"ok": False}, status=400)

    def write(conn):
        conn.execute("DELETE FROM day_order WHERE date = ?", [day])
        conn.executemany("INSERT OR REPLACE INTO day_order (date, exercise, position) VALUES (?, ?, ?)",
                         [(day, name, i + 1) for i, name in enumerate(names)])

    await datasette.get_database("workouts").execute_write_fn(write)
    return Response.json({"ok": True})


async def schedule_week(request, datasette):
    form = await request.post_vars()

    def write(conn):
        for key in WEEKDAYS:
            value = form.get(key, "")
            if value.isdigit():
                conn.execute("INSERT OR REPLACE INTO plan_schedule (day, plan_id) VALUES (?, ?)", [key, int(value)])
            else:
                conn.execute("DELETE FROM plan_schedule WHERE day = ?", [key])

    await datasette.get_database("workouts").execute_write_fn(write)
    return back("/workouts", view="plans", msg="Saved your weekly schedule.")


async def schedule_date(request, datasette):
    form = await request.post_vars()
    day, value = valid_date(form.get("date")), form.get("plan", "")
    if not day:
        return back("/workouts", view="plans", error="Pick a date.")
    wo = datasette.get_database("workouts")
    if value == "weekly":
        await wo.execute_write("DELETE FROM plan_schedule WHERE day = ?", [day])
        msg = f"{nice_date(day)} follows your weekly schedule again."
    else:
        plan_id = int(value) if value.isdigit() else None
        await wo.execute_write("INSERT OR REPLACE INTO plan_schedule (day, plan_id) VALUES (?, ?)", [day, plan_id])
        name = (await one(wo, "SELECT name FROM plans WHERE id = :id", {"id": plan_id})).get("name") if plan_id else None
        msg = f"{nice_date(day)}: {name or 'rest day'}."
    return back("/workouts", view="plans", msg=msg)


async def workout_stats(wo, month):
    # Favourite training days: the days you logged a workout on each weekday, all time (runs left
    # out), as a heat strip: level 1-4 of the busiest weekday's count, 0 = never
    lifted, _ = await workout_days(wo)
    per_day = [0] * 7
    for d in lifted:
        per_day[d.weekday()] += 1
    most = max(per_day)
    weekdays = [dict(letter=name[0], name=name, n=n, level=ceil(4 * n / most) if n else 0)
                for name, n in zip(WEEKDAY_NAMES, per_day)]
    return dict(weekdays=weekdays, calendar=await workout_calendar(wo, month))


async def workout_calendar(wo, month):
    """One month, Monday first, for the Stats tab: the days you worked out (a Strava run counts, with
    a shoe), and beside each week whether it had a workout and whether it's part of your current
    week streak (which runs on across month ends)."""
    today = date.today()
    month = month if month and MONTH_RE.match(month) and month <= today.isoformat()[:7] else today.isoformat()[:7]
    lifted, ran = await workout_days(wo)  # date -> exercises logged / Strava runs
    done = set(lifted) | set(ran)
    plans = [p for p in await all_plans(wo) if p["icon"]]
    sched = await schedule(wo)

    def day_plans(d):
        """The plans a day's workout was (those with an icon): logged under the plan's own name, or
        sharing exercises with it, best match first, the day's scheduled plan winning a tie"""
        did = {e.lower() for e in lifted.get(d, [])}
        planned, _ = scheduled(d.isoformat(), sched)
        scored = []
        for p in plans:
            # (a whole plan logged at once can also be a single entry under the plan's name)
            score = len(did & {e["exercise"].lower() for e in p["exercises"]}) + 2 * (p["name"].lower() in did)
            if score:
                scored.append((-score, p["id"] != planned, p["name"].lower(), p))
        return [p for *_, p in sorted(scored)]

    def what(d):
        runs = [f"run {num(r['km'])} km" for r in ran.get(d, [])]
        return ", ".join(lifted.get(d, []) + runs)
    trained_weeks = {d - timedelta(days=d.weekday()) for d in done}
    # The current week streak: weeks in a row with a workout, up to this week (or last week, while
    # this one has none yet), as in streaks()
    wk = today - timedelta(days=today.weekday())
    if wk not in trained_weeks:
        wk -= timedelta(weeks=1)
    streak_weeks = set()
    while wk in trained_weeks:
        streak_weeks.add(wk)
        wk -= timedelta(weeks=1)
    first = date.fromisoformat(month + "-01")
    last = date(first.year + first.month // 12, first.month % 12 + 1, 1) - timedelta(days=1)
    rows_ = []
    monday = first - timedelta(days=first.weekday())
    while monday <= last:
        days = []
        for k in range(7):
            d = monday + timedelta(days=k)
            if d.month != first.month:
                days.append(None)
                continue
            matched = day_plans(d) if d in lifted else []
            # Up to two icons: the plan(s) it was, and a shoe for a run; a dot when there's more
            icons = [p["icon"] for p in matched] + (["pi:sneaker"] if d in ran else [])
            if d in ran and len(icons) > 2:
                icons = icons[:1] + ["pi:sneaker"] + icons[1:-1]
            days.append(dict(
                day=d.day, iso=d.isoformat(), done=d in done, what=what(d), today=d == today, future=d > today,
                icons=icons[:2], more=len(icons) > 2,
                plan=", ".join(p["name"] for p in matched),
                # Workouts on days in a row are joined (within the week's row)
                link_prev=d in done and k > 0 and (d - timedelta(days=1)).month == d.month and d - timedelta(days=1) in done,
                link_next=d in done and k < 6 and (d + timedelta(days=1)).month == d.month and d + timedelta(days=1) in done))
        rows_.append(dict(days=days, trained=monday in trained_weeks, streak=monday in streak_weeks, future=monday > today))
        monday += timedelta(weeks=1)
    # The streak shows as one band down the week column; the flame sits on its latest week
    for i, r in enumerate(rows_):
        r["band_start"] = r["streak"] and (i == 0 or not rows_[i - 1]["streak"])
        r["band_end"] = r["streak"] and (i == len(rows_) - 1 or not rows_[i + 1]["streak"])
    latest = max((i for i, r in enumerate(rows_) if r["streak"]), default=None)
    if latest is not None:
        rows_[latest]["flame"] = True
    earliest = min(done, default=today)
    return dict(
        title=f"{first:%B %Y}", rows=rows_, streak=len(streak_weeks),
        workouts=sum(first <= d <= last for d in done),
        prev=add_months(month, -1) if earliest.isoformat()[:7] < month else None,
        next=add_months(month, 1) if month < today.isoformat()[:7] else None,
    )


def describe_sets(entries):
    """[(8, 60), (8, 60), (6, 60)] -> '8, 8, 6 × 60 kg'; with different weights, each set's own."""
    kg = lambda w: f"{plain(w)} kg" if w else "bodyweight"
    if len({w for _, w in entries}) == 1:
        return ", ".join(str(r) for r, _ in entries) + f" × {kg(entries[0][1])}"
    return ", ".join(f"{r} × {kg(w)}" for r, w in entries)


async def workouts_log(request, datasette):
    """Logs one exercise: every set row of the form at once (reps + weight each)."""
    form = parse_qs((await request.post_body()).decode(), keep_blank_values=True)
    get = lambda key: (form.get(key) or [""])[0].strip()
    day = valid_date(get("date")) or date.today().isoformat()
    exercise, notes = get("exercise"), get("notes")
    pairs = [(r.strip(), w.strip()) for r, w in zip(form.get("reps", []), form.get("weight", []))]
    try:
        # Only the exercise is needed; empty reps = done without counting ("Bouldering"), empty weight = bodyweight
        parsed = [(int(r) if r else None, float(w) if w else 0.0) for r, w in pairs]
        if not exercise or any((r is not None and r < 1) or w < 0 for r, w in parsed):
            raise ValueError
    except ValueError:
        return back("/workouts", date=day, exercise=exercise, notes=notes,
                    reps=",".join(r for r, _ in pairs), weight=",".join(w for _, w in pairs),
                    error="It needs an exercise. Reps (whole numbers above 0) and weights (0 or more) are optional.")
    # Rows with reps are the sets; a row left without reps (a spare one from "+ Add set") is ignored.
    # No reps anywhere = one entry that marks it done, with the first weight given.
    entries = [(r, w) for r, w in parsed if r is not None] or [(None, next((w for _, w in parsed if w), 0.0))]
    wo = datasette.get_database("workouts")
    # The notes go on the first set, so they show once
    await wo.execute_write_fn(lambda conn: conn.executemany(
        "INSERT INTO sets (date, exercise, reps, weight, notes) VALUES (?, ?, ?, ?, ?)",
        [(day, exercise, r, w, notes if i == 0 else "") for i, (r, w) in enumerate(entries)]))
    s = await one(wo, """SELECT exercise, (SELECT count(*) FROM sets t WHERE t.date = s.date AND t.exercise = s.exercise) AS n
                         FROM sets s ORDER BY id DESC LIMIT 1""")
    reps_n, weight_n = entries[0]
    if reps_n is None:
        msg = f"Logged {s['exercise']}" + (f" ({plain(weight_n)} kg)." if weight_n else ".")
    elif len(entries) == 1:
        msg = f"Logged {s['exercise']} set {s['n']}: {describe_sets(entries)}."
    else:
        msg = f"Logged {s['exercise']} sets {s['n'] - len(entries) + 1}–{s['n']}: {describe_sets(entries)}."
    return back("/workouts", date=day, msg=msg)


async def workouts_log_plan(request, datasette):
    """Logs every exercise in a plan on one day, skipping the ones already logged there."""
    form = await request.post_vars()
    day = valid_date(form.get("date")) or date.today().isoformat()
    wo = datasette.get_database("workouts")
    plan = next((p for p in await all_plans(wo) if str(p["id"]) == form.get("plan")), None)
    if not plan:
        return back("/workouts", date=day, error="Pick a plan to log.")
    already = {r["exercise"].lower() for r in await rows(wo, "SELECT DISTINCT exercise FROM sets WHERE date = :d", {"d": day})}
    last_weight = {r["exercise"].lower(): r["weight"] for r in await rows(wo, """
        SELECT s.exercise, s.weight FROM sets s
        JOIN (SELECT exercise, max(id) AS id FROM sets GROUP BY exercise) m ON m.id = s.id""")}
    # A plan that's just a name ("Bouldering") goes in as one entry under that name
    items = plan["exercises"] or [{"exercise": plan["name"], "sets": None, "reps": "", "weight": None}]
    entries, logged, skipped = [], [], []
    for e in items:
        name = e["exercise"]
        if name.lower() in already:
            skipped.append(name)
            continue
        first = re.match(r"\d+", e["reps"] or "")
        reps = (int(first.group()) or None) if first else None
        weight = e["weight"] if e["weight"] is not None else last_weight.get(name.lower(), 0)
        # Planned sets need reps to count as sets; without reps the exercise is marked done once
        entries += [(day, name, reps, weight)] * ((e["sets"] or 1) if reps else 1)
        logged.append(name)
    if entries:
        await wo.execute_write_fn(lambda conn: conn.executemany(
            "INSERT INTO sets (date, exercise, reps, weight) VALUES (?, ?, ?, ?)", entries))
    counted = sum(r[2] is not None for r in entries)
    if not logged:
        msg = f"Everything in {plan['name']} is already logged on {nice_date(day)}."
    elif not plan["exercises"]:
        msg = f"Logged {plan['name']}."
    else:
        msg = (f"Logged {len(logged)} exercise{'s' if len(logged) != 1 else ''} from {plan['name']}"
               + (f" ({counted} set{'s' if counted != 1 else ''})" if counted else "") + ".")
    if skipped and logged:
        msg += f" Skipped what was already logged: {', '.join(skipped)}."
    return back("/workouts", date=day, msg=msg)


async def workouts_delete(request, datasette):
    form = await request.post_vars()
    wo = datasette.get_database("workouts")
    s = await one(wo, "SELECT id, date, exercise FROM sets WHERE id = :id", {"id": form.get("id")})
    if s:
        await wo.execute_write("DELETE FROM sets WHERE id = ?", [s["id"]])
    return back("/workouts", date=s.get("date"), msg=f"Deleted a {s['exercise']} entry." if s else None)


# --- Finance ----------------------------------------------------------------------

FINANCE_TABLES = {"expenses": "expense", "balances": "balance"}  # what the pages can delete


def add_months(ym, n):
    y, m = divmod(int(ym[:4]) * 12 + int(ym[5:7]) - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def month_range(ym):
    """First day of the month and first day of the next, for date >= start AND date < end"""
    return ym + "-01", add_months(ym, 1) + "-01"


def parse_money(text):
    """'5.9', 'RM5.90' or '1,200' -> 5.9 or 1200.0 (ValueError for anything else)"""
    value = float((text or "").strip().upper().removeprefix("RM").replace(",", "").strip())
    if not -1e9 < value < 1e9:  # also rejects nan and inf
        raise ValueError
    return round(value, 2)


async def budget_lines(fi):
    lines = {"daily": [], "monthly": []}
    for r in await rows(fi, "SELECT period, category, amount FROM budget ORDER BY position, id"):
        lines[r["period"]].append(r)
    return lines


async def spending(fi, start, end, counts=None):
    """What was spent per category from start up to (not including) end, biggest first. counts
    "monthly" or "daily": only the categories ticked for it on Finance > Settings."""
    only = f"AND e.category IN (SELECT name FROM categories WHERE {counts})" if counts in ("daily", "monthly") else ""
    return await rows(fi, f"""
        SELECT e.category, round(sum(e.amount), 2) AS spent, count(*) AS expenses FROM expenses e
        WHERE e.date >= :start AND e.date < :end {only} GROUP BY e.category ORDER BY spent DESC""", {"start": start, "end": end})


def meter(label, spent, budget):
    """Spent against a budget (None = no budget): what's left and how full the bar is"""
    left = None if budget is None else round(budget - spent, 2)
    pct = min(100, 100 * spent / budget) if budget else (100 if spent else 0)
    return {"category": label, "spent": spent, "budget": budget, "left": left, "pct": round(pct, 1),
            "over": left is not None and left < 0}


def against_budget(lines, spent, extras=True):
    """Each budget line with what was spent on it, then (with extras) the categories spent
    on that the budget doesn't have"""
    by_category = {r["category"].lower(): r["spent"] for r in spent}
    out = [meter(line["category"], by_category.pop(line["category"].lower(), 0), line["amount"]) for line in lines]
    if extras:
        out += [meter(r["category"], r["spent"], None) for r in spent if r["category"].lower() in by_category]
    return out


async def month_summary(fi, month, lines):
    start, end = month_range(month)
    total = await one(fi, """SELECT round(coalesce(sum(amount), 0), 2) AS spent, count(*) AS n FROM expenses
                             WHERE date >= :start AND date < :end
                               AND category IN (SELECT name FROM categories WHERE monthly)""", {"start": start, "end": end})
    budget = round(sum(line["amount"] for line in lines["monthly"]), 2) if lines["monthly"] else None
    summary = meter(month, total["spent"], budget)
    today = date.today()
    days_left = (date.fromisoformat(end) - today).days if month == today.isoformat()[:7] else None
    summary.update(month=month, expenses=total["n"], days_left=days_left,
                   per_day=round(summary["left"] / days_left, 2) if days_left and (summary["left"] or 0) > 0 else None)
    return summary


async def day_summary(fi, day, lines):
    """A day's spending, and the daily budget: its categories only"""
    spent = await spending(fi, day, (date.fromisoformat(day) + timedelta(days=1)).isoformat())
    daily = against_budget(lines["daily"], spent, extras=False)
    budget = round(sum(line["amount"] for line in lines["daily"]), 2) if lines["daily"] else None
    return dict(daily=daily, daily_total=meter(day, round(sum(d["spent"] for d in daily), 2), budget),
                day_spent=round(sum(r["spent"] for r in spent), 2))


async def category_names(fi):
    """Every category, the most used first"""
    return [r["name"] for r in await rows(fi, """
        SELECT c.name FROM categories c LEFT JOIN expenses e ON e.category = c.name
        GROUP BY c.name ORDER BY count(e.id) DESC, c.name COLLATE NOCASE""")]


async def finance(request, datasette):
    fi = datasette.get_database("finance")
    view = request.args.get("view")
    if view not in ("log", "budget", "savings", "investments", "stats", "settings"):
        view = "log"
    today = date.today().isoformat()
    lines = await budget_lines(fi)
    ctx = dict(page="finance", view=view, today=today, lines=lines, categories=await category_names(fi))
    if view == "log":
        day = valid_date(request.args.get("date")) or today
        ctx.update(
            day=day, month=await month_summary(fi, day[:7], lines), **await day_summary(fi, day, lines),
            expenses=await rows(fi, "SELECT id, category, amount, notes FROM expenses WHERE date = :d ORDER BY id", {"d": day}),
            # The form starts empty; only a failed add (or a link) fills it in
            prefill={k: request.args.get(k, "") for k in ("category", "amount", "notes")},
            prev_day=(date.fromisoformat(day) - timedelta(days=1)).isoformat(),
            next_day=(date.fromisoformat(day) + timedelta(days=1)).isoformat() if day < date.today().isoformat() else None,
        )
    elif view == "budget":
        month = request.args.get("month") or ""
        ctx.update(await finance_month(fi, month if MONTH_RE.match(month) else today[:7], lines))
    elif view == "savings":
        ctx.update(await finance_savings(fi, request.args.get("year") or ""))
    elif view == "investments":
        ctx.update(await finance_investments(fi, request.args.get("ticker"), request.args.get("range")))
    elif view == "settings":
        found = {r["key"]: r["value"] for r in await rows(fi, "SELECT key, value FROM settings WHERE key LIKE 'ibkr_%' OR key LIKE 'wise_%'")}
        ctx.update(category_rows=await rows(fi, """
            SELECT c.name, c.daily, c.monthly,
                   (SELECT count(*) FROM expenses e WHERE e.category = c.name) AS expenses
            FROM categories c ORDER BY 4 DESC, c.name COLLATE NOCASE"""),
                   ibkr=dict(token=bool(found.get("ibkr_flex_token")), query=found.get("ibkr_flex_query") or ""),
                   wise=dict(token=bool(found.get("wise_api_token"))))
    else:
        ctx.update(await finance_stats(fi, lines, request.args.get("month") or ""))
    return await render(datasette, request, "app_finance.html", **ctx)


async def category_save(request, datasette):
    """Settings: add a category, rename one (to an existing name = merge them) or delete an unused one"""
    form = {k: v[0].strip() for k, v in parse_qs((await request.post_body()).decode(), keep_blank_values=True).items()}
    action, name, new = form.get("action", ""), form.get("name", ""), form.get("new", "")
    if form.get("delete"):
        action, name = "delete", form["delete"]
    renames = []
    if action == "rename":  # the edit table: every row that changed
        renames = [(form[k], form.get("new_" + k[4:], "")) for k in form if k.startswith("old_")]
        renames = [(o, n) for o, n in renames if n and n != o]
    fi = datasette.get_database("finance")
    result = {}

    def write(conn, name=name, new=new):
        if action == "add":
            if not new:
                result["error"] = "Give the category a name."
            elif conn.execute("SELECT 1 FROM categories WHERE name = ?", [new]).fetchone():
                result["error"] = f"{new} already exists."
            else:
                conn.execute("INSERT INTO categories (name) VALUES (?)", [new])
                result["msg"] = f"Added {new}."
        elif action == "rename":
            for name, new in renames:
                # a new name keeps the old one's daily / monthly ticks
                conn.execute("INSERT OR IGNORE INTO categories (name, daily, monthly) SELECT ?, daily, monthly FROM categories WHERE name = ?", [new, name])
                # a budget line the target already has is kept, the old one dropped
                conn.execute("DELETE FROM budget WHERE category = ? AND period IN (SELECT period FROM budget WHERE category = ?)", [name, new])
                conn.execute("UPDATE budget SET category = ? WHERE category = ?", [new, name])
                conn.execute("UPDATE expenses SET category = ? WHERE category = ?", [new, name])
                if name.lower() != new.lower():
                    conn.execute("DELETE FROM categories WHERE name = ?", [name])
                else:  # only the capitals changed
                    conn.execute("UPDATE categories SET name = ? WHERE name = ?", [new, name])
                result["msg"] = f"{name} is now {new}."
            if not renames:
                result["msg"] = "Nothing changed."
            elif len(renames) > 1:
                result["msg"] = f"Renamed {len(renames)} categories."
        elif action == "delete":
            if conn.execute("SELECT 1 FROM expenses WHERE category = ?", [name]).fetchone():
                result["error"] = f"{name} has expenses: rename it into another category instead."
            else:
                conn.execute("DELETE FROM budget WHERE category = ?", [name])
                conn.execute("DELETE FROM categories WHERE name = ?", [name])
                result["msg"] = f"Deleted {name}."

    try:
        await fi.execute_write_fn(write)
    except Exception as e:
        result = {"error": f"Couldn't save: {e}"}
    return back("/finance", view="settings", **result)


async def finance_month(fi, month, lines):
    start, end = month_range(month)
    first = (await one(fi, "SELECT min(date) AS d FROM expenses")).get("d")
    record = next((r for r in await month_records(fi) if r["month"] == month), None)
    return dict(
        month=month, summary=await month_summary(fi, month, lines), record=record,
        by_category=against_budget(lines["monthly"], await spending(fi, start, end, "monthly")),
        pie_chart=pie([(r["category"], r["spent"]) for r in await spending(fi, start, end, "monthly")], money),
        prev_month=add_months(month, -1) if first and first[:7] < month else None,
        next_month=add_months(month, 1) if month < date.today().isoformat()[:7] else None,
    )


TRACKED_SHARE = 0.5  # a month counts in averages once expenses were logged on this share of its days


async def month_records(fi):
    """Every month from the first expense to this one: what was spent, on how many days
    something was logged, and whether the month counts in averages. It counts when you
    logged on at least half its days, unless you chose otherwise (month_settings); this
    month never does, as it isn't over. status: complete, partial (logged a few days),
    none (nothing logged) or current."""
    first = (await one(fi, "SELECT min(date) AS d FROM expenses")).get("d")
    if not first:
        return []
    found = {r["month"]: r for r in await rows(fi, "SELECT month, spent, expenses, logged_days FROM months")}
    chosen = {r["month"]: bool(r["counted"]) for r in await rows(fi, "SELECT month, counted FROM month_settings")}
    today = date.today()
    this_month, month, out = today.isoformat()[:7], first[:7], []
    while month <= this_month:
        start, end = (date.fromisoformat(d) for d in month_range(month))
        length = (end - start).days
        days = today.day if month == this_month else length
        r = found.get(month, {})
        logged = r.get("logged_days", 0)
        auto = logged >= TRACKED_SHARE * days
        status = "current" if month == this_month else "complete" if auto else "partial" if logged else "none"
        out.append(dict(month=month, spent=r.get("spent", 0), expenses=r.get("expenses", 0), logged_days=logged,
                        days=days, length=length, auto=auto, status=status, chosen=chosen.get(month),
                        counted=month != this_month and chosen.get(month, auto)))
        month = add_months(month, 1)
    return out


def projection(record):
    """What this month comes to at the pace so far, once there's enough of it to go on"""
    if record["status"] != "current" or not record["auto"] or record["days"] < 5:
        return None
    return round(record["spent"] / record["days"] * record["length"], 2)


def month_note(r):
    if r["status"] == "future":
        return "still to come"
    if r["status"] == "none":
        return "nothing logged"
    note = f"logged on {r['logged_days']} of {r['days']} days"
    return note + (" so far" if r["status"] == "current" else "")


def year_records(records, year):
    """January to December of a year: the months from month_records, and blank ones for
    the months before your first expense (nothing logged) and after this one (to come)"""
    by_month, this_month = {r["month"]: r for r in records}, date.today().isoformat()[:7]
    out = []
    for m in range(1, 13):
        month = f"{year}-{m:02d}"
        out.append(by_month.get(month) or dict(
            month=month, spent=0, expenses=0, logged_days=0, days=0, length=0, auto=False, chosen=None, counted=False,
            status="future" if month > this_month else "none"))
    return out


def month_chart(records, budget, average):
    """Spent per month against the budget and the average month. A month you only partly
    logged is faded, one with nothing logged has no column, and this month shows where
    it's heading."""
    spent, styles, pace, tips = [], [], [], []
    for r in records:
        ahead = projection(r)
        faded = r["status"] in ("partial", "none") and not r["counted"]
        spent.append(r["spent"])
        styles.append("faded" if faded else "solid")
        pace.append(max(0, ahead - r["spent"]) if ahead else 0)
        lines = [month_name(r["month"]) + (" so far" if r["status"] == "current" else ""), month_note(r)]
        if ahead:
            lines.append(f"on pace for {money(ahead)}")
        if r["status"] not in ("current", "future"):
            lines.append("counts in averages" if r["counted"] else "left out of averages")
        tips.append(("-" if r["status"] in ("none", "future") else money(r["spent"]), "\n".join(lines)))
    bars = [{"name": "Spent", "color": "var(--spending)", "values": spent, "styles": styles}]
    if any(pace):
        bars.append({"name": "On pace for", "color": "var(--spending)", "values": pace, "styles": ["estimate"] * len(pace)})
    refs = [{"name": "budget", "value": budget, "style": "dashed"}] if budget else []
    if average:
        refs.append({"name": "average", "value": average, "style": "dotted"})
    return combo([month_name(r["month"], short=True) for r in records], tips, bars=bars, refs=refs, fmt=money,
                 faded_label="partly logged")


async def finance_stats(fi, lines, pick):
    records = await month_records(fi)
    by_month = {r["month"]: r for r in records}
    options = [r["month"] for r in reversed(records) if r["expenses"]]
    this_month = date.today().isoformat()[:7]
    if pick != "all" and pick not in options:
        pick = this_month if this_month in options else (options[0] if options else "all")
    budget = round(sum(line["amount"] for line in lines["monthly"]), 2) if lines["monthly"] else None
    counted = [r for r in records if r["counted"]]
    average_month = round(sum(r["spent"] for r in counted) / len(counted), 2) if counted else None
    year = (this_month if pick == "all" else pick)[:4]
    shown = year_records(records, year)
    start, end = ("0000-01-01", "9999-12-31") if pick == "all" else month_range(pick)
    by_category = await spending(fi, start, end, "monthly")
    spent = round(sum(r["spent"] for r in by_category), 2)
    ctx = dict(
        pick=pick, month_options=options, budget=budget, spent=spent, records=shown, counted=counted, year=year,
        average_month=average_month, monthly_chart=month_chart(shown, budget, average_month),
        by_category=by_category,
        biggest=await rows(fi, """SELECT id, date, category, amount, notes FROM expenses
                                  WHERE date >= :start AND date < :end AND category IN (SELECT name FROM categories WHERE monthly)
                                  ORDER BY amount DESC, date LIMIT 5""",
                           {"start": start, "end": end}),
    )
    if pick == "all":
        days = sum(r["days"] for r in counted)
        ctx.update(average_day=round(sum(r["spent"] for r in counted) / days, 2) if days else None,
                   category_chart=bullets([(r["category"], r["spent"], None, r["spent"] / spent) for r in by_category],
                                          money, untargeted=None))
        return ctx

    record = by_month[pick]
    compared = sorted(against_budget(lines["monthly"], by_category), key=lambda c: -c["spent"])
    ctx["category_chart"] = bullets([(c["category"], c["spent"], c["budget"], c["spent"] / spent if spent else 0)
                                     for c in compared], money)
    # Spent per day adds up only the categories ticked "daily" on Finance > Settings (everything
    # when none is).
    # The chart shows every day of the month, the ones still to come too; averages per day
    # (the chart's line and the Average day tile) add up the same categories and count every
    # day of the month so far, the days without spending too.
    first = date.fromisoformat(start)
    every_day = [(first + timedelta(days=k)).isoformat() for k in range((date.fromisoformat(end) - first).days)]
    days = [d for d in every_day if d <= date.today().isoformat()]
    picked = await daily_chart_categories(fi, lines)
    wanted = {c.lower() for c in picked}
    per_day = {d: {} for d in every_day}
    for r in await rows(fi, """SELECT date, category, round(sum(amount), 2) AS spent FROM expenses
                               WHERE date >= :start AND date < :end GROUP BY date, category""",
                        {"start": start, "end": end}):
        if r["date"] in per_day and (not wanted or r["category"].lower() in wanted):
            per_day[r["date"]][r["category"]] = r["spent"]
    totals = [round(sum(cats.values()), 2) for cats in per_day.values()]
    shown_total = sum(totals)
    average = round(shown_total / len(days), 2) if days and shown_total else None
    tips = [("-", nice_date(d, True)) if d not in days else
            (money(total), "\n".join([nice_date(d, True), *(f"{c}: {money(v)}" for c, v in sorted(cats.items(), key=lambda kv: -kv[1]))]))
            for (d, cats), total in zip(per_day.items(), totals)]
    refs = []
    # The daily budget line adds up the budget of the picked categories that have one
    in_budget = [line for line in lines["daily"] if not wanted or line["category"].lower() in wanted]
    if in_budget:
        names = [line["category"] for line in in_budget]
        refs.append({"name": "daily budget" if {n.lower() for n in names} == wanted else f"daily budget ({', '.join(names)})",
                     "value": round(sum(line["amount"] for line in in_budget), 2), "style": "dashed"})
    if average:
        refs.append({"name": "average", "value": average, "style": "dotted"})
    ctx.update(
        record=record, ahead=projection(record), compared=compared, day_count=len(days),
        average_day=average, spent_days=sum(1 for d, total in zip(every_day, totals) if d in days and total),
        left=round(budget - spent, 2) if budget is not None else None,
        daily=[(d, total, cats) for (d, cats), total in zip(per_day.items(), totals) if d in days],
        daily_picked=picked,
        daily_chart=combo([str(date.fromisoformat(d).day) for d in every_day], tips, fmt=money, refs=refs, label_all=True, bars=[{
            "name": ", ".join(picked) or "Spent", "color": "var(--spending)", "values": totals}]),
    )
    return ctx


async def daily_chart_categories(fi, lines):
    """The categories Spent per day adds up: the ones ticked "daily" on Finance > Settings
    (an empty list means every category)"""
    return [r["name"] for r in await rows(fi, "SELECT name FROM categories WHERE daily ORDER BY name COLLATE NOCASE")]


async def category_flags_save(request, datasette):
    """Finance > Settings: tick or untick whether a category counts in daily or monthly spending"""
    if request.method != "POST":
        return back("/finance", view="settings")
    form = await request.post_vars()
    flag, on = form.get("flag"), 1 if form.get("on") in ("1", "true", "on") else 0
    if flag not in ("daily", "monthly"):
        return Response.json({"ok": False}, status=400)
    await datasette.get_database("finance").execute_write(f"UPDATE categories SET {flag} = ? WHERE name = ?", [on, form.get("category", "")])
    return Response.json({"ok": True})


async def finance_savings(fi, year=""):
    """The Savings tab. The chart shows the last 24 months, or one year's January to December"""
    latest = await rows(fi, "SELECT * FROM latest_balances")
    entries = await rows(fi, "SELECT id, date, account, balance FROM balances ORDER BY date, id")
    shares = await load_shares(fi)
    today = date.today()
    now = valuation(shares, today.isoformat())
    accounts = [b["account"] for b in latest]
    # Three chart colours (--savings-1..3): the accounts, then the investments; with more
    # accounts than fit, the last account colour holds the rest of them together
    room = 3 - bool(shares["trades"])
    folded = len(accounts) > room
    names = (accounts[:room - 1] + ["Other accounts"] if folded else accounts) + (["Investments"] if shares["trades"] else [])
    for i, b in enumerate(latest):
        b["series"] = min(i, room - 1) + 1
    first = min([e["date"] for e in entries[:1]] + [t["date"] for t in shares["trades"][:1]], default=None)
    this_month = today.isoformat()[:7]
    months = []
    if first:
        # Each account's balance at the end of every month: its newest entry so far. A month
        # without an entry keeps the last balance (it's carried over, not zero) and is marked
        # as not recorded, so the chart can show it as an estimate. The investments are
        # worked out from the trades and that day's prices, so they're never carried over.
        month, current, i = first[:7], {}, 0
        while month <= this_month:
            end, seen = month_range(month)[1], set()
            while i < len(entries) and entries[i]["date"] < end:
                current[entries[i]["account"].lower()] = entries[i]["balance"]
                seen.add(entries[i]["account"].lower())
                i += 1
            values = [current.get(a.lower()) for a in accounts]
            recorded = [a.lower() in seen for a in accounts]
            if folded:
                rest = [v for v in values[room - 1:] if v is not None]
                values = [*values[:room - 1], sum(rest) if rest else None]
                recorded = [*recorded[:room - 1], any(recorded[room - 1:])]
            if shares["trades"]:
                day = min(date.fromisoformat(end) - timedelta(days=1), today).isoformat()
                worth = valuation(shares, day)
                values.append(worth["worth"] if worth["holding"] and worth["complete"] else None)
                recorded.append(True)
            months.append(dict(month=month, values=values, recorded=recorded, any=any(recorded),
                               total=round(sum(v for v in values if v is not None), 2)))
            month = add_months(month, 1)
    years = sorted({m["month"][:4] for m in months})
    this_year = str(date.today().year)
    year = "" if year == "24m" else year if year in years else this_year if this_year in years else ""
    if year:  # January to December; months before the first entry or still to come stay empty
        have = {m["month"]: m for m in months}
        shown = [have.get(f"{year}-{k:02d}") or dict(month=f"{year}-{k:02d}", values=[None] * len(names),
                                                     recorded=[False] * len(names), any=False, total=0, empty=True)
                 for k in range(1, 13)]
    else:
        shown = months[-24:]
    tips = []
    for m in shown:
        parts = [f"{name}: {money(v)}" + ("" if rec else " (carried over)")
                 for name, v, rec in zip(names, m["values"], m["recorded"]) if v is not None]
        note = ["nothing yet"] if m.get("empty") else [] if m["any"] else ["nothing recorded this month"]
        tips.append(("-" if m.get("empty") else money(m["total"]), "\n".join([f"end of {month_name(m['month'])}", *parts, *note])))
    bars = [{"name": name, "color": f"var(--savings-{k + 1})",
             "values": [max(m["values"][k] or 0, 0) for m in shown],
             "styles": ["solid" if m["recorded"][k] else "estimate" for m in shown]} for k, name in enumerate(names)]
    chart = combo([month_name(m["month"], short=True) for m in shown], tips, bars=bars, fmt=money,
                  estimate_label="carried over, not recorded") if shown else ""
    recorded_months = [m for m in months if m["any"]]
    change = (round(recorded_months[-1]["total"] - recorded_months[-2]["total"], 2), recorded_months[-2]["month"]) \
        if len(recorded_months) >= 2 else None
    in_accounts = round(sum(b["balance"] for b in latest), 2) if latest else 0
    return dict(
        latest=latest, change=change, names=names, months=[m for m in shown if not m.get("empty")], balance_chart=chart,
        years=years, year=year,
        total=round(in_accounts + (now["worth"] if now["complete"] else 0), 2) if latest or now["holding"] else None,
        # Month-end balances: early in a month you're most likely closing the one before
        checkin_date=(today.replace(day=1) - timedelta(days=1) if today.day <= 7 else today).isoformat(),
        accounts=[r["name"] for r in await rows(fi, "SELECT name FROM accounts ORDER BY name COLLATE NOCASE")],
        now=now,
    )


# --- Fetching from the internet ---------------------------------------------------

def fetch(request, timeout=60):
    """urlopen(...).read(), trying twice more when the server can't be reached (a network
    blip, like a name lookup failing for a moment). Blocking."""
    for wait in (2, 5, None):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError):
            if wait is None:
                raise
            time.sleep(wait)


def offline_reason(e):
    """An error as the page says it: a network failure in plain words"""
    if isinstance(e, (urllib.error.URLError, TimeoutError)) and not isinstance(e, urllib.error.HTTPError):
        return "couldn't reach it (no internet for a moment?); it tries again when you next open Finance"
    return str(e)


# --- Shares -------------------------------------------------------------------------

PRICE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
INTRADAY = {}  # ticker -> 15-minute prices of the last few trading days, kept in memory only
RANGES = {"1d": "1 day", "5d": "5 days", "1m": "1 month", "6m": "6 months", "1y": "1 year", "all": "Since you bought"}


def _yahoo(symbol, latest=True, **query):
    """One Yahoo Finance chart request: (meta, [(unix time, close)]), with the latest trade
    added when `latest` and it's newer. Blocking: runs in a thread."""
    url = PRICE_URL.format(symbol=quote(symbol), query=urlencode(query))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    result = json.loads(fetch(request, timeout=20))["chart"]["result"][0]
    closes = result["indicators"]["quote"][0].get("close") or []
    points = [(ts, c) for ts, c in zip(result.get("timestamp") or [], closes) if c]
    meta = result["meta"]
    if latest and meta.get("regularMarketPrice") and meta.get("regularMarketTime"):
        if not points or meta["regularMarketTime"] > points[-1][0]:
            points.append((meta["regularMarketTime"], meta["regularMarketPrice"]))
    if meta.get("currency") == "GBp":  # London prices in pence
        points, meta["currency"] = [(ts, c / 100) for ts, c in points], "GBP"
    return meta, points


def fetch_closes(symbol, start):
    """Daily closing prices since `start` (a date), the latest price as today's:
    ([(iso date, price)], currency). The day is the exchange's own."""
    meta, points = _yahoo(symbol, period1=int(datetime.combine(start, datetime.min.time()).timestamp()),
                          period2=int(datetime.now().timestamp()), interval="1d")
    offset = meta.get("gmtoffset") or 0
    closes = {datetime.fromtimestamp(ts + offset, timezone.utc).date().isoformat(): c for ts, c in points}
    return sorted(closes.items()), (meta.get("currency") or "USD").upper()


def fetch_intraday(symbol):
    """15-minute prices of the last 5 trading days: [(iso minute in this computer's time,
    the exchange's trading day, price)]. The trading day keeps a session together that
    crosses midnight here (London's runs 4pm to half past midnight in Malaysia)."""
    meta, points = _yahoo(symbol, latest=False, range="5d", interval="15m")
    offset = meta.get("gmtoffset") or 0
    return [(datetime.fromtimestamp(ts).isoformat(timespec="minutes"),
             datetime.fromtimestamp(ts + offset, timezone.utc).date().isoformat(), c) for ts, c in points]


async def update_prices(datasette):
    """Every ticker's daily prices (from a year before your first trade or conversion, for the
    market's trend) with its currency's rate to ringgit, and its 15-minute prices. Returns
    what happened, for the page."""
    fi = datasette.get_database("finance")
    tickers = await rows(fi, """SELECT ticker, coalesce(nullif(trim(symbol), ''), ticker) AS symbol FROM tickers
                                WHERE ticker IN (SELECT ticker FROM trades)""")
    first = (await one(fi, "SELECT min(d) AS d FROM (SELECT min(date) AS d FROM trades UNION ALL SELECT min(date) FROM conversions)")).get("d")
    if not tickers or not first:
        return "No shares yet, so there are no prices to get."
    start = min(date.fromisoformat(first), date.today()) - timedelta(days=400)
    found, currencies, failed = [], {}, []
    for tk in tickers:
        try:
            closes, currencies[tk["ticker"]] = await asyncio.to_thread(fetch_closes, tk["symbol"], start)
            found += [(tk["ticker"], d, c) for d, c in closes]
            INTRADAY[tk["ticker"]] = await asyncio.to_thread(fetch_intraday, tk["symbol"])
        except Exception:  # offline, or Yahoo doesn't know the symbol
            failed.append(f"{tk['ticker']} (looked up as {tk['symbol']})")
    wanted = set(currencies.values()) | {r["currency"] for r in await rows(fi, "SELECT DISTINCT currency FROM conversions")}
    for currency in sorted(wanted - {"MYR"}):
        try:
            closes, _ = await asyncio.to_thread(fetch_closes, f"{currency}MYR=X", start)
            found += [(f"{currency}/MYR", d, c) for d, c in closes]
        except Exception:
            failed.append(f"the {currency} to ringgit rate")

    def write(conn):
        conn.executemany("INSERT OR REPLACE INTO prices (ticker, date, close) VALUES (?, ?, ?)", found)
        conn.executemany("UPDATE tickers SET currency = ? WHERE ticker = ?", [(c, tk) for tk, c in currencies.items()])
        if found:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('prices_updated', ?)",
                         [datetime.now().isoformat(timespec="minutes")])

    await fi.execute_write_fn(write)
    return (f"Couldn't get prices for {', '.join(failed)}. " if failed else "") + \
        ("Prices updated." if found else "Nothing updated.")


async def fee_categories(fi):
    """The expense categories whose expenses are fees for your shares (like the fee for
    changing ringgit into dollars). Without a choice: "Currency Exchange", if you have it."""
    chosen = (await one(fi, "SELECT value FROM settings WHERE key = 'fee_categories'")).get("value")
    if chosen is not None:
        return json.loads(chosen)
    found = await one(fi, "SELECT name FROM categories WHERE name = 'Currency Exchange'")
    return [found["name"]] if found else []


async def load_shares(fi):
    """Everything the share sums need, read once: trades, conversions, fees logged as
    expenses, tickers and the price history"""
    history = {}
    for r in await rows(fi, "SELECT ticker, date, close FROM prices ORDER BY ticker, date"):
        dates, closes = history.setdefault(r["ticker"], ([], []))
        dates.append(r["date"])
        closes.append(r["close"])
    fees = await fee_categories(fi)
    marks = ",".join("?" * len(fees))
    return dict(
        trades=await rows(fi, "SELECT * FROM trades ORDER BY date, id"),
        conversions=await rows(fi, "SELECT * FROM conversions ORDER BY date, id"),
        fee_categories=fees,
        fee_expenses=[dict(r) for r in (await fi.execute(
            f"SELECT id, date, category, amount, notes FROM expenses WHERE category IN ({marks}) ORDER BY date, id", fees)).rows]
        if fees else [],
        tickers={r["ticker"]: r for r in await rows(fi, "SELECT * FROM tickers ORDER BY ticker")},
        history=history,
        updated=(await one(fi, "SELECT value FROM settings WHERE key = 'prices_updated'")).get("value"),
    )


def price_on(history, name, day):
    """The latest closing price on or before `day`: (price, date), or (None, None)"""
    dates, closes = history.get(name, ((), ()))
    i = bisect_right(dates, day) - 1
    return (closes[i], dates[i]) if i >= 0 else (None, None)


def valuation(shares, day):
    """Your shares at the end of `day`, as exactly as the records allow.

    Ringgit in: every conversion (what left your account, its fee included), and any
    purchase the converted money didn't cover, at that day's exchange rate. Fees logged as
    expenses only count when no conversions are recorded at all: otherwise they're the same
    fees, already inside the conversions. Converted money is a pool per currency that
    remembers what it cost in ringgit, so a purchase costs what its dollars cost you.
    Sales go back into the pool.

    Per ticker: shares, cost in its currency and in ringgit (average cost, commission
    included), price that day, worth, gain. Costs: the commission in ringgit, the
    conversion fees, and the exchange rate: what conversions cost beyond their fee
    compared with the market rate that day (it can come out below zero when the rate you
    got beat the day's closing rate).
    """
    history, tickers = shares["history"], shares["tickers"]

    def rate(currency, on):
        return 1.0 if currency == "MYR" else price_on(history, f"{currency}/MYR", on)[0]

    positions, cash, pool, flows, complete, covered = {}, {}, {}, [], True, True
    costs = dict(commission=0.0, conversion_fees=0.0, fee_expenses=0.0, spread=0.0, spread_known=bool(shares["conversions"]))
    fee_expenses = [] if shares["conversions"] else shares["fee_expenses"]
    events = sorted([(c["date"], 0, c["id"], c) for c in shares["conversions"] if c["date"] <= day]
                    + [(e["date"], 1, e["id"], e) for e in fee_expenses if e["date"] <= day]
                    + [(t["date"], 2, t["id"], t) for t in shares["trades"] if t["date"] <= day], key=lambda e: e[:3])
    for when, kind, _, e in events:
        if kind == 0:  # ringgit changed into another currency
            flows.append((when, e["myr"]))
            costs["conversion_fees"] += e["fee"]
            market = rate(e["currency"], when)
            if market:
                costs["spread"] += e["myr"] - e["fee"] - e["amount"] * market
            else:
                costs["spread_known"] = False
            cash[e["currency"]] = cash.get(e["currency"], 0) + e["amount"]
            pool[e["currency"]] = pool.get(e["currency"], 0) + e["myr"]
            continue
        if kind == 1:  # a fee logged as an expense (only without conversions)
            flows.append((when, e["amount"]))
            costs["fee_expenses"] += e["amount"]
            continue
        currency = (tickers.get(e["ticker"]) or {}).get("currency", "USD")
        p = positions.setdefault(e["ticker"], dict(ticker=e["ticker"], currency=currency, shares=0.0, cost=0.0,
                                                   cost_myr=0.0, realized=0.0, commission=0.0, bought=0.0))
        p["commission"] += e["commission"]
        p["last_price"], p["last_date"] = e["price"], when
        market = rate(currency, when)
        held = cash.get(currency, 0)
        if e["side"] == "buy":
            spent = e["shares"] * e["price"] + e["commission"]
            from_pool = min(spent, held)
            ringgit = pool.get(currency, 0) * from_pool / held if held else 0
            uncovered = spent - from_pool
            if uncovered > 0.005:  # more than rounding: bought with money the conversions don't show
                covered = False
                if market:
                    ringgit += uncovered * market
                    flows.append((when, uncovered * market))
                else:
                    complete = False
            cash[currency] = held - from_pool
            pool[currency] = pool.get(currency, 0) - (pool.get(currency, 0) * from_pool / held if held else 0)
            p["shares"] += e["shares"]
            p["cost"] += spent
            p["cost_myr"] += ringgit
            p["bought"] += spent
            costs["commission"] += e["commission"] * ringgit / spent
        else:  # sold at average cost: the money goes back into the pool at that day's rate
            sold = min(e["shares"], p["shares"])
            share = sold / p["shares"] if p["shares"] else 0
            proceeds = sold * e["price"] - e["commission"]
            p["realized"] += proceeds - p["cost"] * share
            p["cost"] -= p["cost"] * share
            p["cost_myr"] -= p["cost_myr"] * share
            p["shares"] -= sold
            cash[currency] = held + proceeds
            pool[currency] = pool.get(currency, 0) + proceeds * (market or 0)
            costs["commission"] += e["commission"] * (market or 0)
    worth, worth_by_currency = 0.0, {}
    for p in positions.values():
        price, price_date = price_on(history, p["ticker"], day)
        if price is None or price_date < p["last_date"]:  # no newer price yet: the last trade's
            price, price_date = p["last_price"], p["last_date"]
        fx = rate(p["currency"], day)
        p.update(price=price, price_date=price_date, value=p["shares"] * price, rate=fx,
                 average=p["cost"] / p["shares"] if p["shares"] > 1e-9 else None,
                 symbol=(tickers.get(p["ticker"]) or {}).get("symbol"))
        p["gain"] = p["value"] - p["cost"]
        p["gain_pct"] = p["gain"] / p["cost"] if p["cost"] > 1e-9 else None
        p["value_myr"] = p["value"] * fx if fx else None
        p["gain_myr"] = p["value_myr"] - p["cost_myr"] if fx else None
        worth_by_currency[p["currency"]] = worth_by_currency.get(p["currency"], 0) + p["value"]
        if fx:
            worth += p["value_myr"]
        else:
            complete = False
    cash = {c: v for c, v in cash.items() if v > 0.005}
    for currency, amount in cash.items():
        worth_by_currency[currency] = worth_by_currency.get(currency, 0) + amount
        fx = rate(currency, day)
        if fx:
            worth += amount * fx
        else:
            complete = False
    put_in = sum(a for _, a in flows)
    costs["total"] = costs["commission"] + costs["conversion_fees"] + costs["fee_expenses"] \
        + (costs["spread"] if costs["spread_known"] else 0)
    with_conversions = any(e[1] == 0 for e in events)
    return dict(
        positions=[p for p in positions.values() if p["shares"] > 1e-9 or p["realized"]],
        holding=any(p["shares"] > 1e-9 for p in positions.values()) or bool(cash),
        cash=cash, worth_by_currency=worth_by_currency, flows=flows, costs=costs,
        put_in=round(put_in, 2), worth=round(worth, 2), gain=round(worth - put_in, 2),
        gain_pct=(worth - put_in) / put_in if put_in else None, complete=complete,
        put_in_from="conversions" if covered and with_conversions else "both" if with_conversions else "rates",
        rates={c: price_on(history, f"{c}/MYR", day) for c in set(worth_by_currency) - {"MYR"}},
    )


def exit_costs(shares, now, quotes):
    """What selling everything today and bringing it back as ringgit would cost, in ringgit:
    IBKR's commission per ticker (as much as its last trade's) and Wise's fee for changing
    the rest into ringgit, from its latest quote (`quotes`: {currency: {"amount", "fee"}},
    receiving IBKR's wire included). Withdrawing from IBKR is free once a month."""
    last = {t["ticker"]: t["commission"] for t in shares["trades"]}
    rates = {c: fx for c, (fx, _) in now["rates"].items()}
    commission = {}
    for p in now["positions"]:
        if p["shares"] > 1e-9:
            commission[p["currency"]] = commission.get(p["currency"], 0) + last.get(p["ticker"], 0)
    left = {c: v - commission.get(c, 0) for c, v in now["worth_by_currency"].items() if c != "MYR"}
    wise = {c: quotes[c]["fee"] for c in left if c in quotes}

    def in_myr(fees):
        return sum(v * (rates.get(c) or 0) for c, v in fees.items())

    return dict(commission=commission, commission_myr=in_myr(commission), left=left, wise=wise, wise_myr=in_myr(wise),
                known=all(c in quotes for c in left) and all(rates.get(c) for c in left),
                total=in_myr(commission) + in_myr(wise))


def yearly_return(flows, worth, today):
    """The money-weighted return a year (XIRR): the yearly rate at which every ringgit you
    put in, from the day it went in, grows to what it's all worth today. None when it
    can't be told (no money in, or not a full day yet)."""
    if not flows or worth <= 0:
        return None
    first = date.fromisoformat(min(d for d, _ in flows))
    if (today - first).days < 1:
        return None
    items = [((date.fromisoformat(d) - first).days / 365.25, -a) for d, a in flows] + [((today - first).days / 365.25, worth)]

    def value(r):
        return sum(cf / (1 + r) ** years for years, cf in items)

    lo, hi = -0.9999, 100.0
    if value(lo) * value(hi) > 0:
        return None
    for _ in range(200):  # bisection: slow and certain
        mid = (lo + hi) / 2
        if value(lo) * value(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def nice_step(span, count=4):
    raw = span / count if span > 0 else 1
    mag = 10 ** floor(log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


def price_chart(points, currency, marks=(), refs=(), name="price"):
    """A price over time on its own scale (a price chart doesn't start at zero).
    points: [(x label, tooltip label, price)], evenly spaced (trading time only);
    marks: [(index, price, tooltip)] for your trades; refs: [{"name", "value", "style"}]."""
    if not points:
        return Markup('<p class="empty">No prices yet.</p>')
    values = [v for *_, v in points] + [m[1] for m in marks] + [r["value"] for r in refs]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08 or hi * 0.01
    step = nice_step(hi - lo + 2 * pad)
    lo, hi = floor((lo - pad) / step) * step, -floor(-(hi + pad) / step) * step
    n = len(points)

    def x(i):
        return 100 * (i + 0.5) / n

    def y(v):
        return 100 * (v - lo) / (hi - lo)

    ticks, t = [], lo
    while t <= hi + step / 2:
        ticks.append(t)
        t += step
    grid = "".join(f'<span class="tick" style="bottom:{y(t):.2f}%"><b>{t:,.{0 if step >= 1 else 2}f}</b></span>' for t in ticks)
    line_d = " ".join(f"{'M' if i == 0 else 'L'}{x(i) * 10:.1f},{1000 - y(v) * 10:.1f}" for i, (*_, v) in enumerate(points))
    area_d = f"{line_d} L{x(n - 1) * 10:.1f},1000 L{x(0) * 10:.1f},1000 Z"
    hits = "".join(f'<span class="hitcol" style="left:{100 * i / n:.2f}%;width:{100 / n:.2f}%" {_tip(amount(v, currency), tip)}></span>'
                   for i, (_, tip, v) in enumerate(points))
    dots = "".join(f'<i class="pt mark" style="left:{x(i):.2f}%;bottom:{y(v):.2f}%" {_tip(amount(v, currency), tip)}></i>'
                   for i, v, tip in marks)
    lines = "".join(f'<div class="ref {r["style"]}" style="bottom:{y(r["value"]):.2f}%"></div>' for r in refs)
    every = max(1, round(n / 6))
    xls = "".join(f'<span class="xl" style="left:{x(i):.2f}%">{escape(points[i][0])}</span>' for i in range(n - 1, -1, -every))
    keys = [f'<span class="key"><i class="line-key" style="--c:var(--data)"></i>{escape(name)}</span>']
    if marks:
        keys.append('<span class="key"><i class="mark-key"></i>your trades</span>')
    keys += [f'<span class="key"><i class="line-key {r["style"]}"></i>{escape(r["name"])} {amount(r["value"], currency)}</span>'
             for r in refs]
    return Markup(
        f'<div class="legend">{"".join(keys)}</div><div class="pricechart"><div class="grid">{grid}</div>'
        f'<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true"><path class="area" d="{area_d}"/>'
        f'<path class="line" d="{line_d}" vector-effect="non-scaling-stroke"/></svg>{lines}{hits}{dots}{xls}</div>')


# --- IBKR: trades, deposits and the statement, from a Flex Query -----------------

FLEX_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
# Yahoo Finance's ending for a listing exchange, for new tickers' prices
YAHOO_SUFFIX = {"LSE": ".L", "LSEETF": ".L", "LSEIOB1": ".L", "IBIS": ".DE", "IBIS2": ".DE", "XETRA": ".DE",
                "AEB": ".AS", "SBF": ".PA", "EBS": ".SW", "SEHK": ".HK", "SGX": ".SI", "BM": ".KL", "ASX": ".AX"}


class FlexError(Exception):
    pass


def _flex_get(url, **params):
    request = urllib.request.Request(f"{url}?{urlencode({**params, 'v': 3})}", headers={"User-Agent": "databases-app/1.0"})
    return ET.fromstring(fetch(request))


def flex_statement(token, query):
    """Ask IBKR for a Flex Query's statement and wait until it's ready: the XML. Blocking,
    so it runs in a thread. Raises FlexError with IBKR's message when it says no."""
    sent = _flex_get(FLEX_URL, t=token, q=query)
    if sent.findtext("Status") != "Success":
        raise FlexError(f"{sent.findtext('ErrorMessage')} (IBKR error {sent.findtext('ErrorCode')})")
    reference, url = sent.findtext("ReferenceCode"), sent.findtext("Url")
    for wait in (3, 5, 5, 10, 10, 20, 30):
        time.sleep(wait)
        got = _flex_get(url, t=token, q=reference)
        if got.tag == "FlexQueryResponse":
            return got
        if got.findtext("ErrorCode") not in ("1019", "1001"):  # in progress, or try again shortly
            raise FlexError(f"{got.findtext('ErrorMessage')} (IBKR error {got.findtext('ErrorCode')})")
    raise FlexError("IBKR took too long to make the statement; try again in a few minutes")


def flex_date(text):
    """'20260707', '20260707;221520' or '2026-07-07' -> '2026-07-07'. Other date formats
    (07/07/2026 could be either way round) raise: the query should use yyyyMMdd."""
    if not text:
        return None
    found = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", text)
    if not found:
        raise FlexError(f"IBKR sent the date {text!r}: set the Flex Query's date format to yyyyMMdd")
    return "-".join(found.groups())


def parse_flex(root):
    """What the app uses from an Activity Flex statement: trades, deposits and withdrawals,
    and the day it runs to"""
    statement = root.find(".//FlexStatement")
    rows = [r.attrib for r in root.iter("Trade")]
    # A statement can list a trade per execution, per order and in summaries: take the finest
    for level in ("EXECUTION", "ORDER"):
        if any(r.get("levelOfDetail") == level for r in rows):
            rows = [r for r in rows if r.get("levelOfDetail") == level]
            break
    trades = []
    for r in rows:
        if r.get("assetCategory", "STK") != "STK" or not r.get("symbol"):
            continue
        quantity = float(r.get("quantity") or 0)
        trades.append(dict(
            id=r.get("tradeID") or r.get("transactionID") or "", date=flex_date(r.get("tradeDate") or r.get("dateTime")),
            ticker=r["symbol"].upper(), side="sell" if quantity < 0 or r.get("buySell", "").upper().startswith("SELL") else "buy",
            shares=abs(quantity), price=float(r.get("tradePrice") or 0), commission=abs(float(r.get("ibCommission") or 0)),
            currency=(r.get("currency") or "USD").upper(), exchange=r.get("listingExchange") or r.get("exchange") or ""))
    deposits = []
    for r in (c.attrib for c in root.iter("CashTransaction")):
        if "deposit" in r.get("type", "").lower() or "withdrawal" in r.get("type", "").lower():
            if r.get("levelOfDetail") in (None, "", "DETAIL") and r.get("currency") != "BASE_SUMMARY":
                deposits.append(dict(id=r.get("transactionID") or f"{r.get('dateTime')}:{r.get('amount')}",
                                     date=flex_date(r.get("dateTime") or r.get("settleDate") or r.get("reportDate")),
                                     currency=(r.get("currency") or "USD").upper(), amount=float(r.get("amount") or 0),
                                     description=r.get("description", "")))
    return dict(trades=[x for x in trades if x["date"]], deposits=[x for x in deposits if x["date"]],
                date=flex_date(statement.get("toDate")) if statement is not None else None)


async def ibkr_sync(datasette):
    """Fetch the IBKR Flex statement and bring its trades and deposits into the database.
    A trade the app already has (same ticker, side, shares and price within a day) isn't
    added again, but takes IBKR's date and commission. Then Wise's statements fill in what
    new deposits cost. Returns what happened."""
    fi = datasette.get_database("finance")
    found = {r["key"]: r["value"] for r in await rows(fi, "SELECT key, value FROM settings WHERE key LIKE 'ibkr_%'")}
    if not found.get("ibkr_flex_token") or not found.get("ibkr_flex_query"):
        return ""
    try:
        data = parse_flex(await asyncio.to_thread(flex_statement, found["ibkr_flex_token"], found["ibkr_flex_query"]))
    except (FlexError, OSError, ET.ParseError) as e:
        await fi.execute_write("INSERT OR REPLACE INTO settings (key, value) VALUES ('ibkr_error', ?)", [offline_reason(e)])
        return f"IBKR didn't work: {offline_reason(e)}."
    have = await rows(fi, "SELECT id, date, ticker, side, shares, price, commission FROM trades")
    used, new_trades, fixed = set(), [], []
    for tr in sorted(data["trades"], key=lambda x: x["date"]):
        match = next((h for h in have if h["id"] not in used and h["ticker"] == tr["ticker"] and h["side"] == tr["side"]
                      and abs(h["shares"] - tr["shares"]) < 1e-6 and abs(h["price"] - tr["price"]) < 0.005
                      and abs((date.fromisoformat(h["date"]) - date.fromisoformat(tr["date"])).days) <= 1), None)
        if match:
            used.add(match["id"])
            if match["date"] != tr["date"] or abs(match["commission"] - tr["commission"]) >= 0.005:
                fixed.append((tr["date"], tr["commission"], match["id"]))
        else:
            new_trades.append(tr)

    def write(conn):
        conn.executemany("UPDATE trades SET date = ?, commission = ? WHERE id = ?", fixed)
        for tr in new_trades:
            conn.execute("INSERT INTO trades (date, ticker, side, shares, price, commission, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [tr["date"], tr["ticker"], tr["side"], tr["shares"], tr["price"], tr["commission"], "from IBKR"])
            conn.execute("UPDATE tickers SET currency = ?, symbol = CASE WHEN symbol = ticker THEN ? ELSE symbol END WHERE ticker = ?",
                         [tr["currency"], tr["ticker"] + YAHOO_SUFFIX.get(tr["exchange"].upper(), ""), tr["ticker"]])
        conn.executemany("INSERT OR REPLACE INTO deposits (id, date, currency, amount, description) VALUES (?, ?, ?, ?, ?)",
                         [(d["id"], d["date"], d["currency"], d["amount"], d["description"]) for d in data["deposits"]])
        conn.execute("DELETE FROM settings WHERE key = 'ibkr_error'")

    await fi.execute_write_fn(write)
    msg = f"IBKR (statement up to {nice_date(data['date'], True)}): " if data["date"] else "IBKR: "
    msg += f"{len(new_trades)} new trade{'s' * (len(new_trades) != 1)}" if new_trades else "no new trades"
    if fixed:
        msg += f", {len(fixed)} trade{'s' * (len(fixed) != 1)} corrected"
    if new_trades and not await one(fi, "SELECT 1 AS x FROM prices WHERE ticker IN (%s)" % ",".join("?" * len(new_trades)),
                                    [tr["ticker"] for tr in new_trades]):
        msg += ". " + (await update_prices(datasette)).rstrip(".")
    wise = await wise_sync(datasette)  # the ringgit for any new deposits
    return msg + "." + (f" {wise}" if wise else "")


async def pending_deposits(fi):
    """IBKR deposits that no conversion accounts for: a conversion of the same amount in
    the same currency, from a week before the deposit to a day after"""
    deposits = await rows(fi, "SELECT * FROM deposits WHERE amount > 0 ORDER BY date, id")
    conversions = await rows(fi, "SELECT id, date, currency, amount FROM conversions ORDER BY date, id")
    used, waiting = set(), []
    for d in deposits:
        when = date.fromisoformat(d["date"])
        match = next((c for c in conversions if c["id"] not in used and c["currency"] == d["currency"]
                      and abs(c["amount"] - d["amount"]) < 0.01
                      and -7 <= (date.fromisoformat(c["date"]) - when).days <= 1), None)
        if match:
            used.add(match["id"])
        else:
            waiting.append(d)
    return waiting


async def ibkr_settings_save(request, datasette):
    form = await request.post_vars()
    token, query = (form.get("token") or "").strip(), (form.get("query") or "").strip()
    wise_token = (form.get("wise_token") or "").strip()
    fi = datasette.get_database("finance")
    if query and not query.isdigit():
        return back("/finance", view="settings", error="The Query ID is the number next to the query in IBKR's Flex Queries list.")

    def write(conn):
        if token:  # an empty field keeps the saved token
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ibkr_flex_token', ?)", [token])
        if query:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ibkr_flex_query', ?)", [query])
        if wise_token:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wise_api_token', ?)", [wise_token])

    await fi.execute_write_fn(write)
    _refresh.pop("at", None)  # fetch again when the page opens, however recent the last time was
    return back("/finance", view="settings", msg="Saved. Finance fetches from IBKR and Wise when you open it.")


# --- Wise: what the money that arrived at IBKR cost in ringgit, from Wise's statements --

WISE_URL = "https://api.wise.com"
WISE_WINDOW = timedelta(days=460)  # Wise makes a statement for at most 469 days at a time


class WiseError(Exception):
    pass


def _wise_get(token, path):
    request = urllib.request.Request(WISE_URL + path, headers={"Authorization": f"Bearer {token}", "User-Agent": "databases-app/1.0"})
    try:
        return json.loads(fetch(request))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise WiseError("Wise didn't accept the API token: make a new one in Wise, Settings → API tokens") from None
        raise WiseError(f"Wise said {e.code} {e.reason} to {path.split('?')[0]}") from None


def wise_statements(token, currencies, since):
    """Wise's statements for your balances in `currencies`, from `since` (a date) to now:
    {currency: (balance at the start, [transactions, oldest first])}. Blocking, so it runs
    in a thread. Needs a token that can read statements (Malaysian accounts can)."""
    profile = next((p["id"] for p in _wise_get(token, "/v2/profiles") if p.get("type", "").upper() == "PERSONAL"), None)
    if not profile:
        raise WiseError("Wise has no personal profile for this token")
    balances = {b["currency"]: b["id"] for b in _wise_get(token, f"/v4/profiles/{profile}/balances?types=STANDARD")}
    end, found = datetime.now(timezone.utc), {}
    for currency in currencies:
        if currency not in balances:
            continue
        start, opening, transactions = datetime.combine(since, datetime.min.time(), timezone.utc), None, {}
        while start < end:
            stop = min(start + WISE_WINDOW, end)
            statement = _wise_get(token, f"/v1/profiles/{profile}/balance-statements/{balances[currency]}/statement.json?" + urlencode(dict(
                currency=currency, type="COMPACT", intervalStart=start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                intervalEnd=stop.strftime("%Y-%m-%dT%H:%M:%S.000Z"))))
            if opening is None:
                opening = statement["startOfStatementBalance"]["value"]
            transactions.update({(t["referenceNumber"], t["type"], t["date"]): t for t in statement["transactions"]})
            start = stop
        found[currency] = (opening or 0.0, sorted(transactions.values(), key=lambda t: t["date"]))
    return found


def wise_payments(statements, rate):
    """Money sent from Wise in another currency than ringgit, with what it cost:
    [{id, date, currency, amount (what arrived), myr, fee, to}], amounts in ringgit.

    Sent straight from ringgit: the ringgit that left (fee included) and the fee, as the
    statement has them. Sent from a balance in that currency: the balance is a pool that
    remembers what its money cost in ringgit (the conversions into it, with their fees),
    and the payment costs its share, its own fee included. Money that was in the balance
    before the statements start, or came in some other way, counts at the market rate
    (`rate(currency, day)`) on the day it's spent."""
    def day(t):
        return datetime.fromisoformat(t["date"].replace("Z", "+00:00")).astimezone().date().isoformat()

    myr_fees = {t["referenceNumber"]: t["totalFees"]["value"] for t in statements.get("MYR", (0, []))[1]}
    payments = []
    for currency, (opening, transactions) in statements.items():
        pool = dict(amount=0.0, myr=0.0, fee=0.0, other=opening)  # other: money of unknown cost
        for t in transactions:
            value, fees, details, ex = t["amount"]["value"], t["totalFees"]["value"], t.get("details") or {}, t.get("exchangeDetails")
            sent = t["type"] == "DEBIT" and details.get("type") == "TRANSFER"
            to = (details.get("recipient") or {}).get("name", "")
            if currency == "MYR":
                if sent and ex and ex["toAmount"]["currency"] != "MYR":
                    payments.append(dict(id=t["referenceNumber"], date=day(t), currency=ex["toAmount"]["currency"],
                                         amount=ex["toAmount"]["value"], myr=-value, fee=fees, to=to))
                continue
            if t["type"] == "CREDIT":
                if ex and ex["fromAmount"]["currency"] == "MYR":
                    pool["amount"] += value
                    pool["myr"] += ex["fromAmount"]["value"]
                    pool["fee"] += myr_fees.get(t["referenceNumber"], 0)
                else:
                    pool["other"] += value
                continue
            held = pool["amount"] + pool["other"]
            share = min(1.0, -value / held) if held > 0 else 0.0
            market = rate(currency, day(t))
            myr, fee = pool["myr"] * share + pool["other"] * share * (market or 0), pool["fee"] * share
            complete = share > 0 and (market or not pool["other"])
            pool = {k: v * (1 - share) for k, v in pool.items()}
            if sent and not ex and complete:
                fee += fees * (myr - fee) / -value  # the transfer's own fee, at what its money cost
                payments.append(dict(id=t["referenceNumber"], date=day(t), currency=currency, amount=round(-value - fees, 2),
                                     myr=round(myr, 2), fee=round(fee, 2), to=to))
    return sorted(payments, key=lambda p: (p["date"], p["id"]))


def wise_exit_quote(currency, amount):
    """Wise's fee for changing `amount` of `currency` into ringgit for a bank in Malaysia,
    the money coming in by bank transfer (how it leaves IBKR), the fee for receiving it
    included: (fee, rate). The quote Wise's own calculator uses, so no token. Blocking."""
    body = json.dumps(dict(sourceCurrency=currency, targetCurrency="MYR", sourceAmount=round(amount, 2))).encode()
    request = urllib.request.Request(f"{WISE_URL}/v3/quotes", data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "databases-app/1.0"})
    quote = json.loads(fetch(request, timeout=30))
    option = next(o for o in quote["paymentOptions"]
                  if o["payIn"] == "BANK_TRANSFER" and o["payOut"] == "BANK_TRANSFER" and not o.get("disabled"))
    return option["fee"]["total"], quote["rate"]


async def update_exit_quote(datasette):
    """Ask Wise what changing everything back into ringgit would cost now (settings.wise_exit_quote)"""
    fi = datasette.get_database("finance")
    shares = await load_shares(fi)
    left = exit_costs(shares, valuation(shares, date.today().isoformat()), {})["left"]
    quotes = {}
    try:
        for currency, amount in left.items():
            if amount > 0:
                fee, rate = await asyncio.to_thread(wise_exit_quote, currency, amount)
                quotes[currency] = dict(amount=round(amount, 2), fee=fee, rate=rate)
    except (OSError, ValueError, KeyError, StopIteration) as e:
        return f"Couldn't get Wise's price for changing back to ringgit: {e}."
    await fi.execute_write("INSERT OR REPLACE INTO settings (key, value) VALUES ('wise_exit_quote', ?)",
                           [json.dumps(dict(at=datetime.now().isoformat(timespec="minutes"), quotes=quotes))])
    return ""


def _sent_for(payment, deposit):
    """Whether a Wise payment is the money behind an IBKR deposit: same currency and amount,
    sent up to a week before"""
    return (payment["currency"] == deposit["currency"] and abs(payment["amount"] - deposit["amount"]) < 0.01
            and -1 <= (date.fromisoformat(deposit["date"]) - date.fromisoformat(payment["date"])).days <= 7)


def _same_money(conversion, payment):
    """Whether a conversion is a Wise payment: same currency and amount, within a week"""
    return (conversion["currency"] == payment["currency"] and abs(conversion["amount"] - payment["amount"]) < 0.01
            and abs((date.fromisoformat(conversion["date"]) - date.fromisoformat(payment["date"])).days) <= 7)


async def wise_sync(datasette):
    """Fill in the ringgit of IBKR deposits that came from Wise, from Wise's statements,
    and make conversions of earlier deposits what Wise says (date, ringgit, fee). Returns
    what happened."""
    fi = datasette.get_database("finance")
    token = (await one(fi, "SELECT value FROM settings WHERE key = 'wise_api_token'")).get("value")
    deposits = await rows(fi, "SELECT * FROM deposits WHERE amount > 0 ORDER BY date, id")
    if not token or not deposits:
        return ""
    since = date.fromisoformat(deposits[0]["date"]) - timedelta(days=30)
    currencies = ["MYR"] + sorted({d["currency"] for d in deposits} - {"MYR"})
    try:
        statements = await asyncio.to_thread(wise_statements, token, currencies, since)
    except (WiseError, OSError, ValueError, KeyError) as e:
        await fi.execute_write("INSERT OR REPLACE INTO settings (key, value) VALUES ('wise_error', ?)", [offline_reason(e)])
        return f"Wise didn't work: {offline_reason(e)}."
    history = (await load_shares(fi))["history"]
    payments = wise_payments(statements, lambda currency, on: price_on(history, f"{currency}/MYR", on)[0])
    used, to_ibkr = set(), []
    for d in deposits:
        p = next((p for p in payments if p["id"] not in used and _sent_for(p, d)), None)
        if p:
            used.add(p["id"])
            to_ibkr.append(p)
    # Conversions the app has for these payments, made exactly what Wise says
    conversions, have, fixed = await rows(fi, "SELECT * FROM conversions ORDER BY date, id"), set(), []
    for p in to_ibkr:
        c = next((c for c in conversions if c["id"] not in have and _same_money(c, p)), None)
        if c:
            have.add(c["id"])
            if c["date"] != p["date"] or abs(c["myr"] - p["myr"]) >= 0.005 or abs(c["fee"] - p["fee"]) >= 0.005:
                fixed.append((p["date"], p["myr"], p["fee"], c["id"]))
    waiting, new = await pending_deposits(fi), []
    for d in waiting:
        p = next((p for p in to_ibkr if p["id"] not in {x["id"] for _, x in new} and _sent_for(p, d)), None)
        if p:
            new.append((d, p))

    def write(conn):
        conn.executemany("UPDATE conversions SET date = ?, myr = ?, fee = ? WHERE id = ?", fixed)
        for d, p in new:
            conn.execute("INSERT INTO conversions (date, myr, currency, amount, fee, notes) VALUES (?, ?, ?, ?, ?, ?)",
                         [p["date"], p["myr"], d["currency"], d["amount"], p["fee"], f"from Wise ({p['id']})"])
        conn.execute("DELETE FROM settings WHERE key = 'wise_error'")

    await fi.execute_write_fn(write)
    said = [f"{amount(d['amount'], d['currency'])} cost {money(p['myr'])}" for d, p in new]
    if fixed:
        said.append(f"{len(fixed)} conversion{'s' * (len(fixed) != 1)} corrected")
    return "Wise: " + (", ".join(said) if said else "nothing new") + "."


# --- Fetching it all when the Finance page opens -----------------------------------

REFRESH_GAP = timedelta(minutes=5)  # opening Finance again sooner uses what was just fetched
_refresh = {}  # "task": the fetch going on, "at": when the last one started


async def fetch_everything(datasette):
    """Prices from Yahoo Finance, IBKR's statement (and with it Wise's) and Wise's price for
    changing back to ringgit, side by side: what happened. Nothing runs on a timer; the
    Finance page asks for this when it opens."""
    fi = datasette.get_database("finance")
    jobs = [ibkr_sync(datasette)]
    if (await one(fi, "SELECT value FROM settings WHERE key = 'prices_auto'")).get("value") != "off":
        jobs += [update_prices(datasette), update_exit_quote(datasette)]
    said = await asyncio.gather(*jobs, return_exceptions=True)
    return " ".join(str(s) if not isinstance(s, Exception) else f"Something went wrong: {s}." for s in said if s)


async def finance_refresh(request, datasette):
    """The Finance page calls this when it opens: fetch everything, unless that started
    less than 5 minutes ago, and answer once it's done. A fetch keeps going if the page
    is closed before it finishes."""
    if request.method != "POST":
        return Response.json({"ok": False, "error": "POST only"}, status=405)
    task = _refresh.get("task")
    if not task or task.done():
        if _refresh.get("at") and datetime.now() - _refresh["at"] < REFRESH_GAP:
            return Response.json({"fetched": False, "at": _refresh["at"].strftime("%H:%M")})
        _refresh["at"] = datetime.now()
        task = _refresh["task"] = asyncio.create_task(fetch_everything(datasette))
    try:
        said = await asyncio.shield(task)
    except Exception as e:
        said = f"Something went wrong: {e}."
    if re.search(r"didn't work|Couldn't|went wrong", said) and _refresh.get("task") is task:
        _refresh.pop("at", None)  # so opening Finance again tries again straight away
    print(f"app: {said}")
    return Response.json({"fetched": True, "said": said, "at": _refresh["at"].strftime("%H:%M")})


async def finance_investments(fi, pick, rng):
    """The Investments tab: what you have, what went in, the costs, the returns, the price"""
    shares = await load_shares(fi)
    today = date.today()
    now = valuation(shares, today.isoformat())
    held = [p["ticker"] for p in now["positions"] if p["shares"] > 1e-9] or list(shares["tickers"])
    ticker = pick if pick in shares["tickers"] else (held[0] if held else None)
    rng = rng if rng in RANGES else "all"
    found = {r["key"]: r["value"] for r in await rows(fi, "SELECT key, value FROM settings WHERE key LIKE 'ibkr_%' OR key LIKE 'wise_%'")}
    ibkr = dict(token=bool(found.get("ibkr_flex_token")), query=found.get("ibkr_flex_query") or "",
                error=found.get("ibkr_error"), waiting=await pending_deposits(fi))
    wise = dict(token=bool(found.get("wise_api_token")), error=found.get("wise_error"))
    exit_quote = json.loads(found.get("wise_exit_quote") or "{}")
    ctx = dict(now=now, shares=shares, ticker=ticker, range=rng, ranges=RANGES, ibkr=ibkr, wise=wise, held=held,
               yearly=yearly_return(now["flows"], now["worth"], today) if now["complete"] else None,
               since=min((d for d, _ in now["flows"]), default=None))
    # In the shares' own currency, without exchange rates or conversion fees: how the
    # shares themselves did
    cost = sum(p["cost"] for p in now["positions"])
    ctx["market_gain_pct"] = sum(p["value"] - p["cost"] for p in now["positions"]) / cost if cost else None
    ctx["exit"] = exit_costs(shares, now, exit_quote.get("quotes", {}))
    if not ticker:
        return ctx
    info = shares["tickers"][ticker]
    currency = info.get("currency", "USD")
    dates, closes = shares["history"].get(ticker, ([], []))
    # How the market did: the past twelve months, and today against the last close
    year_ago = price_on(shares["history"], ticker, (today - timedelta(days=365)).isoformat())[0]
    latest = closes[-1] if closes else None
    ctx["market_year_pct"] = latest / year_ago - 1 if latest and year_ago else None
    ctx["day_change"] = latest / closes[-2] - 1 if len(closes) >= 2 else None
    ctx["latest"], ctx["latest_date"], ctx["currency"] = latest, dates[-1] if dates else None, currency
    position = next((p for p in now["positions"] if p["ticker"] == ticker), None)
    trades = [t for t in shares["trades"] if t["ticker"] == ticker]
    refs = [{"name": "your average cost", "value": position["average"], "style": "dashed"}] if position and position["average"] else []
    if rng in ("1d", "5d"):
        intraday = INTRADAY.get(ticker) or []
        if rng == "1d" and intraday:
            intraday = [pt for pt in intraday if pt[1] == intraday[-1][1]]  # the last trading day
        points = [(when[11:] if rng == "1d" else short_date(day), f"{nice_date(when[:10])} {when[11:]}", v)
                  for when, day, v in intraday]
        stamps = [day for _, day, _ in intraday]
    else:
        start = {"1m": today - timedelta(days=31), "6m": today - timedelta(days=183), "1y": today - timedelta(days=366)}.get(
            rng, date.fromisoformat(trades[0]["date"]) - timedelta(days=7) if trades else today - timedelta(days=366)).isoformat()
        picked = [(d, c) for d, c in zip(dates, closes) if d >= start]
        points = [(short_date(d), nice_date(d, True), c) for d, c in picked]
        stamps = [d for d, _ in picked]
    marks = []
    for tr in trades:
        i = bisect_right(stamps, tr["date"]) - 1
        if stamps and i >= 0 and stamps[i] == tr["date"]:
            marks.append((i, tr["price"], f"{'Bought' if tr['side'] == 'buy' else 'Sold'} {tr['shares']:g} on {nice_date(tr['date'], True)}"))
    ctx["price_chart"] = price_chart(points, currency, marks, refs, name=ticker)
    return ctx


async def expense_add(request, datasette):
    form = await request.post_vars()
    day = valid_date(form.get("date")) or date.today().isoformat()
    category, amount, notes = ((form.get(k) or "").strip() for k in ("category", "amount", "notes"))
    try:
        value = parse_money(amount)
        if not category or value <= 0:
            raise ValueError
    except ValueError:
        return back("/finance", date=day, category=category, amount=amount, notes=notes,
                    error="An expense needs an amount above 0 and a category.")
    fi = datasette.get_database("finance")
    await fi.execute_write("INSERT INTO expenses (date, category, amount, notes) VALUES (?, ?, ?, ?)",
                           [day, category, value, notes])
    category = (await one(fi, "SELECT name FROM categories WHERE name = :c", {"c": category})).get("name", category)
    return back("/finance", date=day,
                msg=f"Added {money(value)} for {category}" + (f" ({notes})." if notes else "."))


async def balances_save(request, datasette):
    """Month-end balances: one per account. An account whose balance didn't change is still
    saved, so the month counts as recorded, unless it already has an entry that month."""
    form = parse_qs((await request.post_body()).decode(), keep_blank_values=True)
    day = valid_date((form.get("date") or [""])[0]) or date.today().isoformat()
    fi = datasette.get_database("finance")
    latest = {r["account"].lower(): r for r in await rows(fi, "SELECT * FROM latest_balances")}
    new = []
    for account, balance in zip(form.get("account", []), form.get("balance", [])):
        account, balance = account.strip(), balance.strip()
        if not account and not balance:
            continue
        try:
            value = parse_money(balance)
            if not account:
                raise ValueError
        except ValueError:
            return back("/finance", view="savings", error=f"{account or 'A new account'} needs a name and a balance.")
        last = latest.get(account.lower())
        if last and last["balance"] == value and last["as_of"][:7] == day[:7]:
            continue
        new.append((day, account, value))
    if new:
        await fi.execute_write_fn(lambda conn: conn.executemany(
            "INSERT INTO balances (date, account, balance) VALUES (?, ?, ?)", new))
    total = (await one(fi, "SELECT round(sum(balance), 2) AS t FROM latest_balances")).get("t")
    return back("/finance", view="savings",
                msg=f"Saved {len(new)} balance{'s' * (len(new) != 1)} for {nice_date(day)}. In your accounts: {money(total)}."
                if new else "Nothing changed, nothing saved.")


async def month_setting(request, datasette):
    form = await request.post_vars()
    month, choice = form.get("month") or "", form.get("counted")
    if not MONTH_RE.match(month) or choice not in ("0", "1", "auto"):
        return Response.json({"ok": False, "error": "bad request"}, status=400)
    fi = datasette.get_database("finance")
    if choice == "auto":
        await fi.execute_write("DELETE FROM month_settings WHERE month = ?", [month])
    else:
        await fi.execute_write("INSERT OR REPLACE INTO month_settings (month, counted) VALUES (?, ?)", [month, int(choice)])
    msg = {"0": "is left out of averages", "1": "counts in averages", "auto": "counts in averages if you logged at least half its days"}
    back_to = form.get("view") if form.get("view") in ("budget", "stats") else "budget"
    return back("/finance", view=back_to, month=month, msg=f"{month_name(month)} {msg[choice]}.")


async def investments_live(request, datasette):
    """The Investments tab without the rest of the page, which the page swaps in once the
    fetch it started on opening is done"""
    ctx = await finance_investments(datasette.get_database("finance"), request.args.get("ticker"), request.args.get("range"))
    html = await datasette.render_template("app_finance_live.html", ctx, request=request)
    return Response.html(html, headers={"cache-control": "no-store"})


async def finance_delete(request, datasette):
    form = await request.post_vars()
    table = form.get("table")
    if table not in FINANCE_TABLES:
        return Response.json({"ok": False, "error": "bad request"}, status=400)
    fi = datasette.get_database("finance")
    found = await one(fi, f"SELECT id, date FROM {table} WHERE id = :id", {"id": form.get("id")})
    if found:
        await fi.execute_write(f"DELETE FROM {table} WHERE id = ?", [found["id"]])
    view = form.get("view") if form.get("view") in ("budget", "savings", "investments", "stats") else None
    return back("/finance", view=view, month=form.get("month") if view else None,
                date=found.get("date") if not view else None,
                msg=f"Deleted the {FINANCE_TABLES[table]}." if found else None)


async def budget_save(request, datasette):
    form = parse_qs((await request.post_body()).decode(), keep_blank_values=True)
    month = (form.get("month") or [""])[0]
    lines, seen = [], set()
    for period, prefix in (("daily", "d"), ("monthly", "m")):
        for category, amount in zip(form.get(f"{prefix}_category", []), form.get(f"{prefix}_amount", [])):
            category, amount = category.strip(), amount.strip()
            if not category and not amount:
                continue
            try:
                value = parse_money(amount) if amount else 0
                if not category or value < 0:
                    raise ValueError
            except ValueError:
                return back("/finance", view="budget", month=month,
                            error=f"Every {period} budget line needs a category and an amount of 0 or more.")
            if (period, category.lower()) in seen:
                return back("/finance", view="budget", month=month, error=f"{category} is in the {period} budget twice.")
            seen.add((period, category.lower()))
            lines.append((period, len(lines), category, value))

    def write(conn):
        conn.execute("DELETE FROM budget")
        conn.executemany("INSERT INTO budget (period, position, category, amount) VALUES (?, ?, ?, ?)", lines)

    await datasette.get_database("finance").execute_write_fn(write)
    total = {p: sum(v for period, _, _, v in lines if period == p) for p in ("daily", "monthly")}
    return back("/finance", view="budget", month=month,
                msg=f"Saved your budget: {money(total['daily'])} a day and {money(total['monthly'])} a month.")


# --- Quotes and thoughts: short notes with tags -------------------------------------

# Two databases built the same way (schema/quotes.sql, schema/thoughts.sql): the notes,
# the tags, and a table linking them. One page each, from the same code and template.
NOTEBOOKS = {
    "quotes": dict(
        name="quotes", title="Quotes", one="quote", many="quotes", table="quotes", links="quote_tags", key="quote_id",
        one_tag=True,  # a single, general tag per quote
        fields=[dict(name="text", label="Quote", kind="textarea", rows=4, required=True),
                dict(name="author", label="Who said it", placeholder="a person or a character"),
                dict(name="source", label="From", placeholder="a book, a film, a show"),
                dict(name="notes", label="Notes", kind="textarea", rows=2)]),
    "thoughts": dict(
        name="thoughts", title="Thoughts", one="thought", many="thoughts", table="thoughts", links="thought_tags", key="thought_id",
        one_tag=False,
        fields=[dict(name="title", label="Title", placeholder="optional"),
                dict(name="text", label="Thought", kind="textarea", rows=5, required=True),
                dict(name="source", label="Source", placeholder="optional: a link, a book, a person")]),
}


def clean_tag(text):
    """' #Self  Control ' -> 'self control': tags are lower case, single-spaced"""
    return " ".join(text.strip().lstrip("#").split()).lower()


async def tag_counts(db, book):
    return await rows(db, f"""SELECT t.name, count(l.{book['key']}) AS n FROM tags t
                              LEFT JOIN {book['links']} l ON l.tag = t.name GROUP BY t.name ORDER BY t.name""")


async def entries_with_tags(db, book):
    """Every note, newest first, each with its tags"""
    found = await rows(db, f"SELECT * FROM {book['table']} ORDER BY added DESC, id DESC")
    tags = {}
    for r in await rows(db, f"SELECT {book['key']} AS id, tag FROM {book['links']} ORDER BY tag"):
        tags.setdefault(r["id"], []).append(r["tag"])
    for e in found:
        e["tags"] = tags.get(e["id"], [])
    return found


async def notebook(request, datasette):
    book = NOTEBOOKS[request.url_vars["book"].lower()]
    db = datasette.get_database(book["name"])
    entries, tags = await entries_with_tags(db, book), await tag_counts(db, book)
    view = {"settings": "tags"}.get(request.args.get("view"), request.args.get("view"))  # the Settings tab holds the tags
    view = view if view in ("add", "tags") else "all"
    ctx = dict(page=book["name"], book=book, view=view, tags=tags, total=len(entries))
    if book["name"] == "quotes":
        ctx["zen"] = await zenquotes_percent(datasette)
    editing = next((e for e in entries if str(e["id"]) == request.args.get("edit", "")), None)
    if editing:
        ctx.update(view="edit", entry=editing)
    elif view == "add":
        ctx["entry"] = dict(tags=[t for t in [clean_tag(request.args.get("tag", ""))] if t])
    else:
        pick = clean_tag(request.args.get("tag", ""))
        ctx.update(tag=pick, entries=[e for e in entries if not pick or pick in e["tags"]])
    return await render(datasette, request, "app_notes.html", **ctx)


async def notebook_save(request, datasette):
    """Add a note, or change one (id given), with its tags: the ticked ones and any new ones.
    Where a note has one tag (quotes), a new tag typed in wins over the one picked."""
    book = NOTEBOOKS[request.url_vars["book"].lower()]
    form = parse_qs((await request.post_body()).decode(), keep_blank_values=True)
    values = {f["name"]: (form.get(f["name"]) or [""])[0].strip() for f in book["fields"]}
    picked = [*form.get("tag", []), *(form.get("new_tags") or [""])[0].split(",")]
    tags = list(dict.fromkeys(t for t in map(clean_tag, picked) if t))
    if book["one_tag"]:
        tags = tags[-1:]
    entry_id = (form.get("id") or [""])[0]
    if not values["text"]:
        return back(f"/{book['name']}", view="add", error=f"A {book['one']} needs its text.")
    columns = list(values)

    def write(conn):
        if entry_id:
            if not conn.execute(f"UPDATE {book['table']} SET {', '.join(f'{c} = :{c}' for c in columns)} WHERE id = :id",
                                {**values, "id": entry_id}).rowcount:
                return None
            saved = int(entry_id)
        else:
            saved = conn.execute(f"INSERT INTO {book['table']} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})",
                                 values).lastrowid
        conn.execute(f"DELETE FROM {book['links']} WHERE {book['key']} = ?", [saved])
        for tag in tags:  # a tag that exists keeps its spelling
            known = conn.execute("SELECT name FROM tags WHERE name = ?", [tag]).fetchone()
            conn.execute(f"INSERT OR IGNORE INTO {book['links']} ({book['key']}, tag) VALUES (?, ?)", [saved, known[0] if known else tag])
        return saved

    if not await datasette.get_database(book["name"]).execute_write_fn(write):
        return back(f"/{book['name']}", error=f"That {book['one']} isn't there any more.")
    what = "Saved the" if entry_id else "Added a"
    return back(f"/{book['name']}", msg=f"{what} {book['one']}" + (f", tagged {', '.join(tags)}." if tags else "."))


async def notebook_delete(request, datasette):
    book = NOTEBOOKS[request.url_vars["book"].lower()]
    form = await request.post_vars()
    await datasette.get_database(book["name"]).execute_write(f"DELETE FROM {book['table']} WHERE id = ?", [form.get("id")])
    return back(f"/{book['name']}", msg=f"Deleted the {book['one']}.")


async def notebook_tags(request, datasette):
    """Make a tag, rename one (into an existing tag: the two become one) or delete one"""
    book = NOTEBOOKS[request.url_vars["book"].lower()]
    form = await request.post_vars()
    action, old, new = form.get("action"), form.get("tag") or "", clean_tag(form.get("to") or "")
    db = datasette.get_database(book["name"])
    if action in ("create", "rename") and not new:
        return back(f"/{book['name']}", view="settings", error="A tag needs a name.")

    def write(conn):
        if action == "create":
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", [new])
        elif action == "delete":
            conn.execute("DELETE FROM tags WHERE name = ?", [old])
        elif new.lower() == old.lower() or not conn.execute("SELECT 1 FROM tags WHERE name = ?", [new]).fetchone():
            conn.execute("UPDATE tags SET name = ? WHERE name = ?", [new, old])
        else:  # merge into the tag that's already called that
            conn.execute(f"""INSERT OR IGNORE INTO {book['links']} ({book['key']}, tag)
                             SELECT {book['key']}, (SELECT name FROM tags WHERE name = ?) FROM {book['links']} WHERE tag = ?""",
                         [new, old])
            conn.execute("DELETE FROM tags WHERE name = ?", [old])

    await db.execute_write_fn(write)
    msg = {"create": f"Made the tag {new}.", "delete": f"Deleted the tag {old}; its {book['many']} stay.",
           "rename": f"Renamed {old} to {new}."}.get(action, "")
    return back(f"/{book['name']}", view="settings", msg=msg)


# The quote at the top of Home mixes your own quotes with ZenQuotes' free "quote of the day"
# (https://zenquotes.io): each day is one or the other, the same all day. The switch is on
# the Quotes page (setting `zenquotes` in quotes.db). If ZenQuotes can't be reached, or
# you've no quotes of your own, the other source covers.
QUOTE_API = "https://zenquotes.io/api/today"
_qotd = {"day": None, "quote": None, "tried": 0.0}


def _fetch_quote():
    request = urllib.request.Request(QUOTE_API, headers={"User-Agent": "skyydb"})
    with urllib.request.urlopen(request, timeout=4) as response:
        first = json.load(response)[0]
    if not first.get("q") or first.get("a") == "Zen Quotes":  # ZenQuotes' "too many requests" notice
        raise ValueError("no quote")
    return dict(text=first["q"], author=first.get("a", ""), source="", api=True)


async def zenquotes_percent(datasette):
    row = await one(datasette.get_database("quotes"), "SELECT value FROM settings WHERE key = 'zenquotes_percent'")
    value = row.get("value", "50")
    return min(100, max(0, int(value))) if value.isdigit() else 50


async def zenquote_today():
    day = date.today().isoformat()
    if _qotd["day"] == day and _qotd["quote"]:
        return _qotd["quote"]
    if time.time() - _qotd["tried"] > 600:  # after a failure, wait before asking again
        _qotd["tried"] = time.time()
        try:
            _qotd.update(day=day, quote=await asyncio.to_thread(_fetch_quote))
            return _qotd["quote"]
        except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError):
            pass
    return None


async def quote_of_the_day(datasette):
    """For the top of Home: one quote, the same all day"""
    day = date.today().isoformat()
    own = await rows(datasette.get_database("quotes"), "SELECT id, text, author, source FROM quotes")
    mine = min(own, key=lambda q: hashlib.sha1(f"{day}:quotes:{q['id']}".encode()).digest(), default=None)
    percent = await zenquotes_percent(datasette)
    if percent <= 0:
        return mine
    # Which source today: ZenQuotes on `percent` days out of 100 (a fixed pick per day), or
    # ZenQuotes alone when you have none of your own
    roll = int.from_bytes(hashlib.sha1(f"{day}:source".encode()).digest()[:2], "big") % 100
    if roll < percent or not mine:
        return await zenquote_today() or mine
    return mine


STOP_WORDS = set("a an the and or but if of to in on at by for with from as is are was were be been it its this that these those "
                 "i you he she we they me my your our their his her not no do does did have has had so than then there what who "
                 "which when where how all any can will just about into out up more most one".split())


def _words(text):
    return [w for w in re.findall(r"[a-z']+", text.lower().replace("’", "'")) if w not in STOP_WORDS and len(w) > 2]


async def guess_tag(db, text, author):
    """Pick the tag your own quotes would give this one: the words it shares with the quotes
    you've already tagged (naive Bayes), with a push to what you tagged the same author."""
    import math
    tagged = await rows(db, """SELECT q.text, q.author, l.tag FROM quotes q JOIN quote_tags l ON l.quote_id = q.id""")
    if not tagged:
        return None
    seen, totals, per_tag, by_author = {}, {}, {}, {}
    for q in tagged:
        per_tag[q["tag"]] = per_tag.get(q["tag"], 0) + 1
        for w in _words(q["text"]):
            seen.setdefault(q["tag"], {}).setdefault(w, 0)
            seen[q["tag"]][w] += 1
            totals[q["tag"]] = totals.get(q["tag"], 0) + 1
        if q["author"]:
            by_author.setdefault(q["author"].lower(), {}).setdefault(q["tag"], 0)
            by_author[q["author"].lower()][q["tag"]] += 1
    vocab = len({w for counts in seen.values() for w in counts}) or 1
    words, best, best_score = _words(text), None, None
    for tag, n in per_tag.items():
        score = math.log(n / len(tagged))
        for w in words:
            score += math.log((seen.get(tag, {}).get(w, 0) + 0.5) / (totals.get(tag, 0) + 0.5 * vocab))
        score += 3 * by_author.get((author or "").lower(), {}).get(tag, 0) / max(1, sum(by_author.get((author or "").lower(), {}).values()))
        if best_score is None or score > best_score:
            best, best_score = tag, score
    return best


async def zenquotes_setting(request, datasette):
    form = await request.post_vars()
    percent = form.get("percent", "").strip()
    if not percent.isdigit():
        return back("/quotes", view="settings", error="ZenQuotes' share must be a whole number from 0 to 100.")
    percent = min(100, int(percent))
    await datasette.get_database("quotes").execute_write(
        "INSERT INTO settings (key, value) VALUES ('zenquotes_percent', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", [str(percent)])
    return back("/quotes", view="settings", msg=("Home's quote of the day now comes from your own quotes only." if percent == 0
                                  else f"ZenQuotes will be Home's quote on {percent}% of days."))


async def zenquote_add(request, datasette):
    """Quick add: today's ZenQuote into your own quotes, tagged the way yours are."""
    db = datasette.get_database("quotes")
    found = await zenquote_today()
    if not found:
        return back("/quotes", error="Couldn't reach ZenQuotes. Try again in a few minutes.")
    if await one(db, "SELECT id FROM quotes WHERE lower(trim(text)) = lower(trim(?))", [found["text"]]):
        return back("/quotes", msg="Today's ZenQuote is already in your quotes.")
    tag = await guess_tag(db, found["text"], found["author"])

    def write(conn):
        cur = conn.execute("INSERT INTO quotes (text, author, source, notes) VALUES (?, ?, '', 'from ZenQuotes')",
                           [found["text"], found["author"]])
        if tag:
            conn.execute("INSERT INTO quote_tags (quote_id, tag) VALUES (?, ?)", [cur.lastrowid, tag])

    await db.execute_write_fn(write)
    return back("/quotes", msg=f"Added \u201c{found['text'][:60]}{'…' if len(found['text']) > 60 else ''}\u201d by {found['author']}"
                                 + (f", tagged {tag}." if tag else "."))


# --- Work: courses, deadlines, assignments, priorities --------------------------------

UNI_COLOURS = ["#cba6f7", "#a6e3a1", "#89b4fa", "#fab387", "#f5c2e7", "#94e2d5"]  # Mocha: one per course


def short_due(iso):
    """6 Nov"""
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b')}"


def uni_group(days_left):
    if days_left is None:
        return "No deadline"
    if days_left < 0:
        return "Overdue"
    if days_left == 0:
        return "Today"
    if days_left <= 7:
        return "This week"
    return "Later"


async def semester_week(db):
    """Where today falls in the semester: week number, its Monday-Sunday, and weeks in all"""
    sem = await one(db, "SELECT * FROM semester WHERE id = 1")
    if not sem:
        return None
    today = date.today()
    days = (today - date.fromisoformat(sem["start"])).days
    n = days // 7 + 1
    brk = sem["break_after"]
    on_break = bool(brk) and n == brk + 1
    if brk and n > brk + 1:
        n -= 1  # the break week isn't numbered
    monday = today - timedelta(days=today.weekday())
    return dict(name=sem["name"], n=n, total=sem["weeks"], on_break=on_break, before=days < 0, after=n > sem["weeks"],
                start=monday.isoformat(), end=(monday + timedelta(days=6)).isoformat())


async def uni(request, datasette):
    db = datasette.get_database("work")
    courses = await rows(db, "SELECT * FROM course_progress")
    kinds = await rows(db, "SELECT * FROM kinds ORDER BY position, id")
    priorities = await rows(db, "SELECT * FROM priorities ORDER BY level")
    view = {"settings": "courses"}.get(request.args.get("view"), request.args.get("view"))  # the Settings tab holds the courses
    view = view if view in ("courses", "add") + (("announcements",) if "elearn" in FEATURES else ()) else "tasks"
    tasks = await rows(db, "SELECT * FROM task_list")
    ctx = dict(page="work", week=await semester_week(db), view=view, courses=courses, kinds=kinds, priorities=priorities, n_tasks=len(tasks))
    if view == "announcements":
        ctx.update(await announcements_context(datasette, request))
    if view == "courses" and "elearn" in FEATURES:
        creds = elearn_credentials()
        ctx["elearn_login"] = dict(user=creds.get("ELEARN_USER", ""), saved=bool(creds.get("ELEARN_PASS")), ready=ANNOUNCEMENTS_PYTHON.exists())
    editing = await one(db, "SELECT * FROM tasks WHERE id = ?", [request.args.get("edit", "")]) if request.args.get("edit", "").isdigit() else {}
    if editing:
        ctx.update(view="add", task=editing)
    elif view == "add":
        ctx["task"] = dict(course_id=int(request.args["course"]) if request.args.get("course", "").isdigit() else None)
    elif view == "tasks":
        pick = request.args.get("course", "")
        if pick.isdigit():
            keep = {r["id"] for r in await rows(db, "SELECT id FROM tasks WHERE course_id = ?", [int(pick)])}
            tasks = [t for t in tasks if t["id"] in keep]
            ctx["pick"] = int(pick) if any(c["id"] == int(pick) for c in courses) else None
        groups = {}
        for t in tasks:
            groups.setdefault(uni_group(t["days_left"]), []).append(t)
        ctx["done"] = await rows(db, "SELECT * FROM done_list" + (" WHERE course_id = ?" if ctx.get("pick") else ""), [ctx["pick"]] if ctx.get("pick") else [])
        ctx["groups"] = [(g, groups[g]) for g in ("Overdue", "Today", "This week", "Later", "No deadline") if g in groups]
        ctx["tiles"] = dict(
            overdue=len(groups.get("Overdue", [])),
            week=len(groups.get("Today", [])) + len(groups.get("This week", [])),
            top=sum(t["priority"] <= 2 for t in tasks),
            total=len(tasks))
    return await render(datasette, request, "app_work.html", **ctx)


def _uni_number(text):
    try:
        return float(text) if text.strip() else None
    except ValueError:
        return None


async def uni_save(request, datasette):
    """Add a task, or change one (id given)"""
    form = {k: v[0].strip() for k, v in parse_qs((await request.post_body()).decode(), keep_blank_values=True).items()}
    task_id = form.get("id", "")
    if not form.get("title"):
        return back("/work", view="add", error="A task needs a title.")
    due = form.get("due", "")
    try:
        if due:
            date.fromisoformat(due)
    except ValueError:
        return back("/work", view="add", error="That deadline isn't a date.")
    values = dict(
        title=form["title"],
        course_id=int(form["course_id"]) if form.get("course_id", "").isdigit() else None,
        kind_id=int(form["kind_id"]) if form.get("kind_id", "").isdigit() else None,
        due=due or None,
        due_time=form.get("due_time", "") if due else "",
        priority=int(form["priority"]) if form.get("priority", "") in ("1", "2", "3", "4", "5") else 3,
        weight=_uni_number(form.get("weight", "")),
        notes=form.get("notes", ""))

    def write(conn):
        if task_id:
            return conn.execute(f"UPDATE tasks SET {', '.join(f'{c} = :{c}' for c in values)} WHERE id = :id",
                                {**values, "id": task_id}).rowcount
        conn.execute(f"INSERT INTO tasks ({', '.join(values)}) VALUES ({', '.join(':' + c for c in values)})", values)
        return 1

    if not await datasette.get_database("work").execute_write_fn(write):
        return back("/work", error="That task isn't there any more.")
    return back("/work", msg=("Saved " if task_id else "Added ") + f"{values['title']}.")


async def uni_delete(request, datasette):
    """Finish a task (action done), bring it back (undo), or delete it for good"""
    form = await request.post_vars()
    db, task_id, action = datasette.get_database("work"), form.get("id"), form.get("action", "delete")
    if action == "done":
        await db.execute_write("UPDATE tasks SET done_on = date('now', 'localtime') WHERE id = ?", [task_id])
        msg = "Moved it to Done."
    elif action == "undo":
        await db.execute_write("UPDATE tasks SET done_on = NULL WHERE id = ?", [task_id])
        msg = "Back on the list."
    else:
        await db.execute_write("DELETE FROM tasks WHERE id = ?", [task_id])
        msg = "Removed the task."
    return back("/work", msg=msg, course=form.get("course"))


async def uni_course(request, datasette):
    """Make, change or delete a course (its tasks stay, without a course)"""
    form = await request.post_vars()
    action, cid = form.get("action"), form.get("id")
    code, name, term = (form.get("code") or "").strip(), (form.get("name") or "").strip(), (form.get("term") or "").strip()
    active = 1 if form.get("active") else 0
    if action in ("create", "save") and not name:
        return back("/work", view="settings", error="A course needs a name.")
    db = datasette.get_database("work")
    if action == "save_all":
        ids = [k[5:] for k in form if k.startswith("name_") and k[5:].isdigit()]
        for i in ids:
            if not (form.get(f"name_{i}") or "").strip():
                return back("/work", view="settings", error="A course needs a name.")
        for i in ids:
            await db.execute_write("UPDATE courses SET code = ?, name = ?, term = ?, active = ? WHERE id = ?",
                                   [(form.get(f"code_{i}") or "").strip(), form.get(f"name_{i}").strip(),
                                    (form.get(f"term_{i}") or "").strip(), 1 if form.get(f"active_{i}") else 0, int(i)])
        return back("/work", view="settings", msg="Saved.")
    if action == "create":
        await db.execute_write("INSERT INTO courses (code, name, term) VALUES (?, ?, ?)", [code, name, term])
        msg = f"Added {name}."
    elif action == "save":
        await db.execute_write("UPDATE courses SET code = ?, name = ?, term = ?, active = ? WHERE id = ?", [code, name, term, active, cid])
        msg = f"Saved {name}."
    else:
        await db.execute_write("DELETE FROM courses WHERE id = ?", [cid])
        msg = "Deleted the course; its tasks stay."
    return back("/work", view="settings", msg=msg)


# --- Announcements: eLearn + Outlook merged into one list, sorted by how much each thing matters ----
# Sources (scraped by scrapers/elearn/refresh.py into elearn.db): the eLearn stream, eLearn announcements and
# gradebook, and the Outlook inbox. One thing said in several places shows once: an email that repeats an announcement marks it
# "emailed", the same ad posted on several faculty pages shows once with "also on N pages", and your Work tasks are matched to it.
# Importance (classify.py in that project): 1 act on it · 2 your week · 3 content · 4 events, internships · 5 noise.

ANNOUNCEMENTS_PROJECT = ROOT / "scrapers" / "elearn"
ANNOUNCEMENTS_PYTHON = Path.home() / ".local/share/venvs/elearn/bin/python"
STREAM_KINDS = ("deadline", "grade", "announcement", "content", "enrolment", "other")
# What a file or title is about, from the words in it: first match wins
CONTENT_TOPICS = (
    ("Recording", ("recording", "video", "panopto", "zoom", "youtube")),
    ("Assessment", ("assignment", "submission", "quiz", "test", "exam", "report", "project", "case study", "proposal", "rubric", "marking")),
    ("Practical", ("practical", "tutorial", "lab", "exercise", "worksheet", "workshop", "sample input", "source files", "dataset")),
    ("Lecture", ("lecture", "slides", "handout", "chapter", "topic", "week ", "notes", "introduction", "overview")),
)
SYNC_LABELS = {"courses": "courses", "stream": "activity stream", "grades": "announcements + grades", "mail": "Outlook", "eLearn": "eLearn"}
MODULE_CODE = re.compile(r"\b([A-Z]{3}\d{4})\b")
_refresh = {}  # "proc": the refresh that is running


def refresh_running():
    proc = _refresh.get("proc")
    return bool(proc) and proc.poll() is None


def content_topic(title):
    low = title.lower()
    return next((name for name, words in CONTENT_TOPICS if any(w in low for w in words)), "File")


def course_short(course):
    """'2701-CSC3034 COMPUTATIONAL INTELLIGENCE' -> ('CSC3034', 'Computational Intelligence')"""
    m = re.match(r"^(?:\d+_\d+-|\d+-)?([A-Z]{2,4}\d{3,4})\s+(.*)$", course or "")
    return (m.group(1), m.group(2).title()) if m else ("", course or "")


def title_words(text):
    """The words that identify a message, without 'Re:' or the 'Faculty of ...:' / 'COURSE:' prefix mailers add"""
    text = re.sub(r"^((re|fw|fwd):\s*)+", "", (text or "").lower())
    if re.match(r"^(faculty of|school of|new announcement|\d{4}-[a-z]{3}\d{4})", text) and ": " in text:
        text = text.split(": ", 1)[1]
    return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2}


def days_apart(a, b):
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return 99


async def announcement_feed(el):
    """eLearn announcements + Outlook messages as one list (newest first). An email that repeats an announcement is folded into it
    (`emailed`, `mail_link`); an ad posted on several pages is kept once (`also`)."""
    feed = []
    for a in await rows(el, "SELECT * FROM announcements"):
        a.update(source="elearn", day=a["posted_at"][:10], mail_link="", emailed=False, also=[], tracked=False,
                 link=f"https://elearn.sunway.edu.my/ultra/stream/announcement-detail?courseId={a['course_id']}&announcementId={a['id']}&mode=view")
        feed.append(a)
    codes = {course_short(a["course"])[0]: a["course"] for a in feed if a["module"]}
    by_course = {}
    for a in feed:
        by_course.setdefault(a["course"], []).append(a)
    try:
        mails = await rows(el, "SELECT * FROM emails")
    except Exception:  # the emails table appears with the first Outlook read
        mails = []
    for m in mails:
        code = MODULE_CODE.search(m["subject"] + " " + m["preview"][:120])
        course = codes.get(code.group(1)) if code else None
        notice = m["addr"].startswith("no-reply_elearn")  # eLearn's own mail: the real title is in the preview
        mine = by_course.get(course, feed) if course else feed
        head = title_words(re.sub(r"^New announcement from [^:]{0,60}?(FET|\w+)\s", "", m["preview"][:160])) if notice else set()
        subject = title_words(m["subject"])
        best, score = None, 0
        for a in mine:
            if a["source"] != "elearn" or days_apart(a["posted_at"], m["received"]) > 5:
                continue
            aw = title_words(a["title"])
            if not aw:
                continue
            s = len(aw & subject) / len(aw | subject) if subject else 0
            if notice and len(aw) >= 2 and aw <= head | subject:
                s = max(s, 0.9)
            if s > score:
                best, score = a, s
        if best and score >= 0.6:
            best["emailed"], best["mail_link"] = True, m["link"]
            continue
        course = course or m["sender"]
        feed.append(dict(id=m["id"], course_id="", course=course, module=int(bool(code and codes.get(code.group(1)))), title=m["subject"], body=m["preview"],
                         posted_at=m["received"], category=m["category"], importance=m["importance"], source="email", day=m["received"][:10],
                         link="", mail_link=m["link"], emailed=False, also=[], tracked=False))
    # the same ad on several pages: keep the newest
    seen = {}
    for a in sorted(feed, key=lambda i: i["posted_at"], reverse=True):
        key = frozenset(title_words(a["title"]))
        if len(key) < 3 or a["module"]:
            continue
        if key in seen:
            seen[key]["also"].append(a)
            a["hidden"] = True
        else:
            seen[key] = a
    feed = [a for a in feed if not a.get("hidden")]
    for a in feed:
        a["code"], a["name"] = course_short(a["course"])
        a["name"] = a["name"] if a["code"] else (a["course"] if a["source"] == "elearn" else a["course"].split(" - ")[0])
        a["pages"] = len({x["course"] for x in a["also"]})
    feed.sort(key=lambda i: i["posted_at"], reverse=True)
    return feed


async def announcement_tasks(datasette, due_items, feed, assessments, stream_due):
    """Your open Work tasks, each with what eLearn/Outlook say about it (same course + the words in the title, or same due date).
    Marks the matched items `tracked`, and sets `in_tasks` + `add` (a prefilled new task) on the eLearn deadlines."""
    work = datasette.get_database("work")
    task_rows = await rows(work, "SELECT * FROM task_list WHERE done_on IS NULL ORDER BY due IS NULL, due, priority")
    course_ids = {r["name"].lower(): r["id"] for r in await rows(work, "SELECT id, name FROM courses")}
    el_courses = {c for c in {a["course"] for a in feed if a["module"]} | {a["course"] for a in assessments} if course_short(c)[0]}

    def words_of(text, drop=frozenset()):
        return {w for w in re.findall(r"[a-z]+|\d+", (text or "").lower()) if w not in ("the", "and", "of", "for", "to", "a", "in", "on", "new") and w not in drop}

    def same(task_words, text, drop):
        words = words_of(text, drop)
        nums = {w for w in task_words if w.isdigit()}
        text_words = task_words - nums
        return bool(text_words) and nums <= words and len(text_words & words) >= max(1, ceil(0.6 * len(text_words)))

    for t in task_rows:
        course = next((c for c in el_courses if t["course"] and t["course"].lower() in c.lower()), None)
        t["elearn_course"], t["found"] = course, []
        if not course:
            continue
        drop = words_of(course) | words_of(t["course"])
        words = words_of(t["title"], drop)
        for a in assessments:
            if a["course"] == course and ((t["due"] and a["due"][:10] == t["due"]) or same(words, a["name"], drop)):
                t["found"].append(dict(kind="assessment", text=a["short"], due=a["due"], score=a["score"], possible=a["possible"], id=a["id"]))
        for a in feed:
            if a["course"] == course and a["importance"] <= 2 and same(words, a["title"], drop):
                a["tracked"] = True
                t["found"].append(dict(kind=a["category"], text=a["title"], day=a["day"], via="email" if a["source"] == "email" else ("eLearn + email" if a["emailed"] else "eLearn")))
        t["found"] = t["found"][:4]
    for it in due_items + stream_due:
        title = it.get("short") or it["title"]
        drop = words_of(it["course"])
        it["in_tasks"] = any(any(f.get("id") == it.get("id") and f["kind"] == "assessment" for f in t["found"])
                             or (t["elearn_course"] == it["course"] and same(words_of(t["title"], drop | words_of(t["course"])), title, drop))
                             for t in task_rows)
        it["add"] = dict(title=re.sub(r"^Due:\s*", "", title), course_id=next((i for n, i in course_ids.items() if n in it["course"].lower()), ""),
                         due=(it.get("due") or "")[:10], due_time=(it.get("due") or "")[11:16])
    return task_rows


async def announcements_context(datasette, request):
    """Everything the Announcements tab of Work shows"""
    el = datasette.get_database("elearn")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    recent = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    course = request.args.get("course", "")
    only = " AND course = :course" if course else ""
    params = {"course": course} if course else {}

    def decorate(items):
        for it in items:
            it["code"], it["name"] = course_short(it["course"])
            it["day"] = (it.get("posted_at") or "")[:10]
        return items

    def split(items):
        return [i for i in items if i["day"] >= recent], [i for i in items if i["day"] < recent]

    def capped(items, n=8):  # the faculty pages post a lot: the newest few, the rest folded
        new, old = split(items)
        return new[:n], new[n:] + old

    feed = await announcement_feed(el)
    courses, pages = {}, {}
    for a in feed:
        if a["source"] != "elearn":  # an email's "course" is its sender: not a chip
            continue
        group = courses if a["module"] else pages
        group.setdefault(a["course"], dict(course=a["course"], n=0))["n"] += 1
    settings = {r["course"]: r["auto"] for r in await rows(el, "SELECT course, auto FROM course_settings WHERE active = 0")}
    off = set(settings)
    # chips: the modules you starred (one you switched off by hand stays, faded, so it can come back); modules switched off because
    # they aren't starred are not listed. The faculty / school / career pages are a group of their own.
    for name in off:
        if not settings[name]:
            courses.setdefault(name, dict(course=name, n=0))
    for name in set(courses) - off | {n for n in off if not settings[n]}:
        courses[name]["code"], courses[name]["name"] = course_short(name)
        courses[name]["off"] = name in off
    for name, c in pages.items():
        c["name"] = name
    feed = [a for a in feed if a["course"] not in off and (not course or a["course"] == course)]
    stream = decorate([i for i in await rows(el, "SELECT * FROM stream_items WHERE 1" + only + " ORDER BY posted_at DESC, id DESC", params) if i["course"] not in off])
    by = {k: [it for it in stream if it["kind"] == k] for k in STREAM_KINDS}
    for it in by["content"]:
        it["topic"] = content_topic(it["title"].removeprefix("Added:").strip())
    for g in by["grade"]:
        try:
            got, out = (float(x) for x in g["score"].split("/"))
            g["pct"] = round(100 * got / out) if out else None
        except ValueError:
            g["pct"] = None
        g["what"] = g["title"].removeprefix("Grade posted:").strip()
    scored = [g for g in by["grade"] if g["pct"] is not None]

    # Assessments: what's coming (any size), and big ones that passed without a result yet
    assessments = decorate([dict(a, posted_at="", short=re.sub(r"^New Assignment", "Assignment", a["name"]))
                            for a in await rows(el, "SELECT * FROM assessments WHERE 1" + only + " ORDER BY due", params) if a["course"] not in off])
    upcoming = [a for a in assessments if a["due"] >= now]
    for a in upcoming:
        a["days_left"] = (date.fromisoformat(a["due"][:10]) - date.today()).days
    waiting = [a for a in assessments if a["weight"] == "major" and a["due"] and a["due"] < now and a["score"] is None and a["due"][:10] >= recent]
    stream_due = [d for d in by["deadline"] if d["due"] >= now and not any(a["due"] == d["due"] and a["course"] == d["course"] for a in upcoming)]
    tasks = await announcement_tasks(datasette, upcoming + waiting, feed, assessments, stream_due)

    classes = split([a for a in feed if a["importance"] == 2])
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    sync = []
    for source in ("stream", "grades", "mail"):
        row = await one(el, "SELECT * FROM sync_log WHERE source IN (?, 'eLearn') ORDER BY id DESC LIMIT 1", [source])
        sync.append(dict(source=SYNC_LABELS[source], at=row.get("at", "") if row else "", error=row.get("error", "") if row else "", items=row.get("items", 0) if row else 0))
    return dict(
        an_by=by, an_course=course, an_sync=sync, an_running=refresh_running(), an_total=len(feed) + len(stream),
        an_upcoming=upcoming, an_stream_due=stream_due, an_waiting=waiting, an_tasks=tasks,
        an_important=split([a for a in feed if a["importance"] == 1]), an_classes=classes,
        an_internships=capped([a for a in feed if a["category"] == "internship"]),
        an_events=capped([a for a in feed if a["category"] in ("event", "opportunity", "general")]),
        an_other=split([a for a in feed if a["importance"] in (3, 5) and a["category"] not in ("general", "event", "opportunity", "internship")]),
        an_courses=sorted((c for c in courses.values() if "code" in c), key=lambda c: -c["n"]),
        an_pages=sorted(pages.values(), key=lambda c: -c["n"]),
        an_mail=dict(total=sum(a["source"] == "email" or a["emailed"] for a in feed), twins=sum(a["emailed"] for a in feed),
                     only=sum(a["source"] == "email" for a in feed)),
        an_avg=round(sum(g["pct"] for g in scored) / len(scored)) if scored else None,
        an_week=dict(important=sum(a["posted_at"] >= week_ago for a in feed if a["importance"] == 1),
                     classes=sum(a["posted_at"] >= week_ago for a in feed if a["importance"] == 2),
                     content=sum(c["posted_at"] >= week_ago for c in by["content"])),
        an_soon=sum(1 for a in upcoming if a["days_left"] <= 14) + len(stream_due))


async def announcements_course(request, datasette):
    """Switch a course on or off (double-click its chip): off = not read by Refresh, not shown"""
    form = await request.post_vars()
    name = form.get("course", "")
    if name:
        await datasette.get_database("elearn").execute_write(
            "INSERT INTO course_settings (course, active) VALUES (?, 0) ON CONFLICT(course) DO UPDATE SET active = 1 - active", [name])
    return Response.text("ok")


async def announcements_status(request, datasette):
    """Is a refresh running? (the page polls this and reloads itself once, when it is done)"""
    return Response.json({"running": refresh_running()})


async def announcements(request, datasette):
    """The old eLearn page moved into Work"""
    return back("/work", view="announcements", course=request.args.get("course"))


async def announcements_refresh(request, datasette):
    """Start refresh.py in the background (about a minute: eLearn stream, announcements + grades, Outlook); the page reloads when done"""
    if request.method != "POST":
        return Response.text("POST only", status=405)
    if refresh_running():
        return back("/work", view="announcements", msg="Already refreshing.")
    if not ANNOUNCEMENTS_PYTHON.exists():
        return back("/work", view="announcements", error=f"The scraper isn't set up ({ANNOUNCEMENTS_PYTHON} is missing); see {ANNOUNCEMENTS_PROJECT}/README.md.")
    _refresh["proc"] = subprocess.Popen([str(ANNOUNCEMENTS_PYTHON), "-I", str(ANNOUNCEMENTS_PROJECT / "refresh.py")], cwd=ANNOUNCEMENTS_PROJECT,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return back("/work", view="announcements", msg="Refreshing eLearn and Outlook…")


# --- Work > Settings: signing in to eLearn and Outlook ------------------------------------
# The scrapers sign in as you with your Sunway ID and password, kept in ~/.config/elearn/credentials
# (mode 600). Not in this folder or its databases: ~/Shared is an NTFS drive where every file is
# readable by everyone. The Settings tab writes that file; the password is never shown again.

ELEARN_CREDS = Path.home() / ".config" / "elearn" / "credentials"


def elearn_credentials():
    try:
        return dict(line.split("=", 1) for line in ELEARN_CREDS.read_text().splitlines() if "=" in line)
    except OSError:
        return {}


async def elearn_credentials_save(request, datasette):
    """Save the Sunway ID and password (an empty password keeps the saved one)"""
    if request.method != "POST":
        return back("/work", view="settings")
    form = await request.post_vars()
    user, password = form.get("user", "").strip(), form.get("password", "")
    password = password or elearn_credentials().get("ELEARN_PASS", "")
    if not user or not password:
        return back("/work", view="settings", error="Fill in your Sunway ID and password.")
    if any(c in user + password for c in "\r\n"):
        return back("/work", view="settings", error="The ID and password can't have line breaks.")
    ELEARN_CREDS.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(ELEARN_CREDS.parent, 0o700)
    tmp = ELEARN_CREDS.with_name(".credentials.tmp")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(f"ELEARN_USER={user}\nELEARN_PASS={password}\n")
    os.replace(tmp, ELEARN_CREDS)
    return back("/work", view="settings", msg="Saved your eLearn sign-in.")


async def elearn_sign_in(request, datasette):
    """Open a browser window to sign in by hand: eLearn (when it asks for a code) or Outlook (approve on your phone)"""
    if request.method != "POST":
        return back("/work", view="settings")
    which = (await request.post_vars()).get("which")
    if which not in ("elearn", "outlook"):
        return back("/work", view="settings")
    if not ANNOUNCEMENTS_PYTHON.exists():
        return back("/work", view="settings", error=f"The scraper isn't set up ({ANNOUNCEMENTS_PYTHON} is missing); see scrapers/elearn/README.md.")
    if not elearn_credentials().get("ELEARN_PASS"):
        return back("/work", view="settings", error="Save your Sunway ID and password first.")
    if refresh_running():
        return back("/work", view="settings", error="A refresh is running; try again when it's done.")
    args = ["elearn.py", "login"] if which == "elearn" else ["outlook.py", "--show"]
    subprocess.Popen([str(ANNOUNCEMENTS_PYTHON), "-I", str(ANNOUNCEMENTS_PROJECT / args[0]), *args[1:]], cwd=ANNOUNCEMENTS_PROJECT,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return back("/work", view="settings", msg="eLearn is opening in a browser window: sign in there (type the code if it asks), then close it."
                if which == "elearn" else "Outlook is opening in a browser window: approve the sign-in on your phone; it closes by itself.")


# --- Settings page, unlocking and locking -------------------------------------------------

def file_size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


async def settings_page(request, datasette):
    s = settings()
    if not s["password"]:  # first a password: Settings (and the locks) sit behind it
        return await render(datasette, request, "app_settings.html", page="settings", setup=True)
    pages = [dict(key=k, default=p["name"], name=s["names"].get(k, ""), shown=k not in s["hidden"], locked=k in s["locked"],
                  icon=s["icons"].get(k, "")) for k, p in ordered_pages(s) if page_installed(k)]
    names = page_names(s)
    widgets = [dict(key=k, name=w["name"], page=names[w["page"]] if w["page"] else "", on=k not in s["widgets_off"],
                    hidden=bool(w["page"]) and w["page"] in s["hidden"])
               for k, w in WIDGETS.items() if w["page"] is None or w["page"] in FEATURES]
    locked = locked_pages(datasette, request.cookies, s)
    raw = []  # every database Datasette has open, with the page it belongs to
    for name, db in datasette.databases.items():
        if name.startswith("_"):
            continue
        owner = page_of(f"/{db.route}")
        raw.append(dict(name=name, route=db.route, tables=len(await db.table_names()), views=len(await db.view_names()),
                        size=file_size(Path(db.path).stat().st_size) if db.path else "",
                        page=names[owner] if owner else "", locked=owner in locked))
    return await render(datasette, request, "app_settings.html", page="settings", pages=pages, widgets=widgets, raw=raw,
                        minutes=s["unlock_minutes"], page_icons=PAGE_ICONS)


async def settings_pages_save(request, datasette):
    """Each page's name, icon, whether it's in the menu and whether it's locked (Settings is behind the password)"""
    if request.method != "POST":
        return back("/settings")
    form = parse_qs((await request.post_body()).decode(), keep_blank_values=True)
    get = lambda key: (form.get(key) or [""])[0].strip()
    s = settings()
    if not s["password"]:
        return back("/settings", error="Set a password first.")
    keys = [k for k in PAGES if page_installed(k)]  # pages that aren't installed keep what they had
    hidden = [k for k in keys if k != "home" and not get(f"show_{k}")]
    locked = [k for k in keys if k != "home" and get(f"lock_{k}")]
    s["icons"] = {**{k: v for k, v in s["icons"].items() if k not in keys},
                  **{k: get(f"icon_{k}") for k in keys if icon_ok(get(f"icon_{k}"))}}
    names = {k: get(f"name_{k}")[:40] for k in keys}
    order = [k for k in form.get("order", []) if k in PAGES]
    s["order"] = order + [k for k in s["order"] if k not in order]
    s["names"] = {**{k: v for k, v in s["names"].items() if k not in keys},
                  **{k: v for k, v in names.items() if v and v != PAGES[k]["name"]}}
    s["hidden"] = [k for k in s["hidden"] if k not in keys] + hidden
    s["locked"] = [k for k in s["locked"] if k not in keys] + locked
    save_settings(s)
    return back("/settings", msg="Saved your pages.")


async def settings_widgets_save(request, datasette):
    """Which widgets Home shows (the switches save as soon as they're flipped)"""
    if request.method != "POST":
        return back("/settings")
    form = parse_qs((await request.post_body()).decode(), keep_blank_values=True)
    shown, on = set(form.get("present", [])) & set(WIDGETS), set(form.get("widget", []))
    s = settings()
    if not s["password"]:
        return back("/settings", error="Set a password first.")
    s["widgets_off"] = [k for k in s["widgets_off"] if k not in shown] + [k for k in WIDGETS if k in shown and k not in on]
    save_settings(s)
    return back("/settings", msg="Saved Home's widgets.")


async def settings_password_save(request, datasette):
    """Set or change the password (you're already in, so no old one), how long unlocking lasts, or remove
    it: then nothing is locked, and Settings asks for a new password next time"""
    if request.method != "POST":
        return back("/settings")
    form = await request.post_vars()
    s = settings()
    if form.get("action") == "remove":
        s.update(password="", locked=[])
        save_settings(s)
        response = back("/settings", msg="Removed the password. Nothing is locked now.")
        set_open_cookie(datasette, response, None)
        return response
    new, minutes = form.get("new", ""), form.get("minutes", "").strip()
    if not s["password"] and not new:
        return back("/settings", error="Type a password.")
    if minutes and not minutes.isdigit():
        return back("/settings", error="Minutes is a whole number (0 = until the browser closes).")
    first = not s["password"]
    if new:
        s["password"] = hash_password(new)
    if minutes:
        s["unlock_minutes"] = int(minutes)
    save_settings(s)
    response = back("/settings", msg="Password set." if first else ("Saved the new password." if new else "Saved."))
    if first:  # you've just set it: you're in
        set_open_cookie(datasette, response, open_until(s))
    return response


async def menu_order(request, datasette):
    """Dragging the top menu saves its order here (the same order Settings > Pages shows)"""
    if request.method != "POST":
        return Response.text("POST only", status=405)
    form = parse_qs((await request.post_body()).decode(), keep_blank_values=True)
    by_url = {p["url"]: k for k, p in PAGES.items()}
    order = [by_url[u] for u in form.get("href", []) if u in by_url]
    s = settings()
    s["order"] = order + [k for k in s["order"] if k not in order]
    save_settings(s)
    return Response.json({"ok": True})


async def unlock(request, datasette):
    """The password popup posts here (asking for JSON); without JavaScript it's a plain form. The
    right password unlocks every locked page. GET (an old link): Home with the popup open."""
    if request.method != "POST":
        return back("/", unlock=safe_next(request.args.get("next")) or "/")
    form = await request.post_vars()
    target = safe_next(form.get("next"))
    wants_json = "application/json" in request.headers.get("accept", "")
    if not password_ok(form.get("password", "")):
        await asyncio.sleep(0.5)  # a pause, so guessing is slow
        error = "That's not the password."
        return Response.json({"ok": False, "error": error}) if wants_json else back("/", unlock=target or "/", error=error)
    response = Response.json({"ok": True, "next": target}) if wants_json else Response.redirect(target or "/")
    set_open_cookie(datasette, response, open_until())
    return response


async def lock(request, datasette):
    """Lock every locked page again; stay where you were unless that page is locked"""
    if request.method != "POST":
        return back("/")
    target = safe_next((await request.post_vars()).get("next")) or "/"
    path = target.split("?")[0].lower()
    if page_of(path) in settings()["locked"] or path == "/settings" or path.startswith("/settings/"):
        target = "/"  # that page is shut now
    response = Response.redirect(target)
    set_open_cookie(datasette, response, None)
    return response


# --- Datasette's own pages: match the theme -------------------------------------

async def datasette_css(request, datasette):
    """static/datasette.css, after the colour variables it uses: every Mocha colour by name
    and the app's own names for them (--bg, --surface, --accent, ...)"""
    colours = {**MOCHA, **theme()["vars"]}
    css = ":root {\n" + "".join(f"  --{k}: {v};\n" for k, v in colours.items()) + "}\n"
    css += (ROOT / "static" / "datasette.css").read_text()
    return Response(css, content_type="text/css; charset=utf-8", headers={"cache-control": "no-store"})


@hookimpl
def register_routes():
    books = "|".join(f for f in ("quotes", "thoughts") if f in FEATURES)
    routes = [(r"^/$", home), (r"^/theme/datasette\.css$", datasette_css),
              (r"^/settings$", settings_page), (r"^/settings/pages$", settings_pages_save),
              (r"^/settings/widgets$", settings_widgets_save), (r"^/settings/password$", settings_password_save),
              (r"^/unlock$", unlock), (r"^/lock$", lock), (r"^/menu/order$", menu_order)]
    for feature, paths in ROUTES.items():
        if feature in FEATURES:
            routes += paths
    if "quotes" in FEATURES:
        routes += [(r"^/quotes/zenquotes$", zenquotes_setting), (r"^/quotes/zenquote-add$", zenquote_add)]
    if books:
        routes += [(rf"^/(?P<book>{books})$", notebook), (rf"^/(?P<book>{books})/save$", notebook_save),
                   (rf"^/(?P<book>{books})/delete$", notebook_delete), (rf"^/(?P<book>{books})/tags$", notebook_tags)]
    # Any case works: /HSK is /hsk
    return [("(?i)" + regex, view) for regex, view in routes]


ROUTES = {
    "chinese": [
        (r"^/hsk$", chinese),
        (r"^/hsk/move$", chinese_move),
        (r"^/hsk/add$", chinese_add),
        (r"^/hsk/settings$", chinese_settings),
    ],
    "workouts": [
        (r"^/workouts$", workouts),
        (r"^/workouts/log$", workouts_log),
        (r"^/workouts/log-plan$", workouts_log_plan),
        (r"^/workouts/delete$", workouts_delete),
        (r"^/workouts/plan/save$", plan_save),
        (r"^/workouts/plan/delete$", plan_delete),
        (r"^/workouts/plan/order$", plan_order),
        (r"^/workouts/day/order$", day_order_save),
        (r"^/workouts/schedule/week$", schedule_week),
        (r"^/workouts/schedule/date$", schedule_date),
        (r"^/workouts/strava/save$", strava_save),
        (r"^/workouts/strava/login$", strava_login),
        (r"^/workouts/strava/sync$", strava_sync_route),
        (r"^/workouts/strava/status$", strava_status),
    ],
    "finance": [
        (r"^/finance$", finance),
        (r"^/finance/expense$", expense_add),
        (r"^/finance/balances$", balances_save),
        (r"^/finance/month$", month_setting),
        (r"^/finance/category/flags$", category_flags_save),
        (r"^/finance/investments/live$", investments_live),
        (r"^/finance/refresh$", finance_refresh),
        (r"^/finance/ibkr$", ibkr_settings_save),
        (r"^/finance/delete$", finance_delete),
        (r"^/finance/budget$", budget_save),
        (r"^/finance/category$", category_save),
    ],
    "work": [
        (r"^/work$", uni),
        (r"^/work/save$", uni_save),
        (r"^/work/delete$", uni_delete),
        (r"^/work/course$", uni_course),
    ],
    "elearn": [
        (r"^/work/announcements$", announcements),
        (r"^/work/announcements/refresh$", announcements_refresh),
        (r"^/work/announcements/course$", announcements_course),
        (r"^/work/announcements/status$", announcements_status),
        (r"^/work/elearn/credentials$", elearn_credentials_save),
        (r"^/work/elearn/sign-in$", elearn_sign_in),
    ],
}
