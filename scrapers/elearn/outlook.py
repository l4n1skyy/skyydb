"""Outlook on the web (Sunway Microsoft 365): sign in with the stored credentials and read the inbox list.
The MFA step needs your phone: the number shown in the window is entered in Microsoft Authenticator.
Usage: python -I outlook.py [--show]   -> dump/outlook.json"""
import sys
import time
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import elearn

HERE = Path(__file__).parent
DB = elearn.DB_DIR
PROFILE = Path.home() / ".local/share/elearn/profile-outlook"
MAIL = "https://outlook.office.com/mail/"


def sign_in(page, user, pw):
    """Walk the Microsoft login: email -> password -> (approve on phone) -> stay signed in"""
    deadline = time.time() + 240
    while time.time() < deadline:
        url = page.url
        if "outlook.office.com/mail" in url or "outlook.cloud.microsoft/mail" in url:
            if page.locator("[role=option], [role=listbox]").count():
                return True
        try:
            if page.locator("input[type=email]:visible").count():
                page.fill("input[type=email]", f"{user}@imail.sunway.edu.my")
                page.click("#idSIButton9")
            elif page.locator("#i0118:visible").count():
                page.fill("#i0118", pw)
                page.click("#idSIButton9")
            elif page.locator("#idChkBx_SAOTCAS_TD").count():
                if not page.locator("#idChkBx_SAOTCAS_TD").is_checked():
                    page.check("#idChkBx_SAOTCAS_TD")
                print("approve on your phone; number:", page.locator("#idRichContext_DisplaySign").inner_text(), flush=True)
            elif page.locator("#KmsiCheckboxField").count():  # stay signed in?
                page.check("#KmsiCheckboxField")
                page.click("#idSIButton9")
        except Exception:
            pass
        page.wait_for_timeout(1500)
    return False


API_JS = """async () => {
    // Outlook's own page holds an access token for its mail API; use it for read-only GETs of the inbox
    let token = null;
    for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!/accesstoken/i.test(k)) continue;
        const v = JSON.parse(localStorage.getItem(k));
        if ((v.target || '').includes('outlook.office.com/Mail.ReadWrite') && Number(v.expiresOn) * 1000 > Date.now()) token = v.secret;
    }
    if (!token) return {error: 'no token'};
    let url = 'https://outlook.office.com/api/v2.0/me/mailfolders/inbox/messages?$top=100&$orderby=ReceivedDateTime desc' +
              '&$select=Id,ConversationId,Subject,From,ReceivedDateTime,BodyPreview,IsRead,WebLink';
    const out = [];
    for (let n = 0; url && n < 60; n++) {
        const r = await fetch(url, {headers: {Authorization: 'Bearer ' + token}});
        if (!r.ok) return {error: 'HTTP ' + r.status, out};
        const d = await r.json();
        out.push(...d.value);
        url = d['@odata.nextLink'];
    }
    return {out};
}"""


def read_inbox(page):
    """Every message in the inbox (all pages) as raw API rows"""
    got = page.evaluate(API_JS)
    if got.get("error"):
        raise RuntimeError("Outlook API: " + got["error"])
    return got["out"]


def local(stamp):
    """'2026-11-03T04:12:00Z' -> '2026-11-03 12:12' in local time"""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")


def save_mail(messages):
    """Store the messages (classified like announcements). Returns (seen, new)."""
    import sqlite3
    import classify
    db = sqlite3.connect(DB / "elearn.db")
    db.executescript((DB / "schema" / "elearn.sql").read_text())
    new = 0
    for m in messages:
        sender = (m.get("From") or {}).get("EmailAddress") or {}
        subject = (m.get("Subject") or "").strip()
        preview = classify.clean(m.get("BodyPreview"))
        course = subject if classify.MODULE.match(subject) else sender.get("Name", "")
        category, importance = classify.classify(subject, preview, course)
        cur = db.execute("""INSERT INTO emails (id, conv, sender, addr, subject, preview, received, link, is_read, category, importance)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(id) DO UPDATE SET is_read = excluded.is_read, category = excluded.category, importance = excluded.importance""",
                         (m["Id"], m.get("ConversationId", ""), sender.get("Name", ""), sender.get("Address", ""), subject, preview,
                          local(m["ReceivedDateTime"]), m.get("WebLink", ""), int(bool(m.get("IsRead"))), category, importance))
    seen_before = db.execute("SELECT count(*) FROM emails").fetchone()[0]
    db.commit()
    db.close()
    return len(messages), seen_before


def open_inbox(ctx, user, pw):
    """A page on the inbox, signed in. Raises when a login needs the phone (run `outlook.py --show` once)."""
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(MAIL, wait_until="domcontentloaded")
    if not sign_in(page, user, pw):
        raise RuntimeError("Outlook needs you to sign in: run `outlook.py --show` and approve on your phone")
    page.wait_for_timeout(3000)
    return page


def main():
    user, pw = elearn.credentials()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), executable_path="/usr/bin/chromium", headless="--show" not in sys.argv,
                                                   chromium_sandbox=True, viewport={"width": 1400, "height": 900})
        try:
            seen, total = save_mail(read_inbox(open_inbox(ctx, user, pw)))
            print(f"read {seen} messages ({total} stored)")
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
