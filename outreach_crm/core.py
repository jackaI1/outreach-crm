"""SQLite persistence and outreach rules; the CLI never sets send status directly."""
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, make_msgid, parseaddr
from pathlib import Path
from string import Template

from .mail import DeliveryError, deliver

STAGES = ("researched", "emailed", "followed_up", "replied", "interview")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value, label):
    value = value.strip()
    if not value or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(f"{label} must contain readable text.")
    return value


def address(value):
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value):
        raise ValueError("Use a single plain email address, without a display name.")
    return value.lower()


class CRM:
    def __init__(self, path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomically create new databases with owner-only permissions.
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS companies (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          research TEXT NOT NULL, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contacts (
          id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
          name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE, role TEXT NOT NULL,
          reason TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'researched'
            CHECK(stage IN ('researched','emailed','followed_up','replied','interview')),
          follow_up TEXT);
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL REFERENCES contacts(id),
          kind TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, sender TEXT NOT NULL,
          recipient TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
          sent_at TEXT, state TEXT NOT NULL CHECK(state IN ('sending','sent','failed','uncertain')),
          follow_up_days INTEGER NOT NULL, resolution TEXT);
        CREATE TABLE IF NOT EXISTS replies (
          id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL REFERENCES contacts(id),
          received_at TEXT NOT NULL, body TEXT NOT NULL, message_id TEXT UNIQUE, source TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS messages_contact ON messages(contact_id);
        """)

    def close(self):
        self.db.close()

    def company(self, name, research, source):
        with self.db:
            cur = self.db.execute("INSERT INTO companies(name,research,source) VALUES(?,?,?)",
                                  (text(name, "Company"), text(research, "Research"), text(source, "Source")))
        return cur.lastrowid

    def contact(self, company_id, name, email, role, reason):
        with self.db:
            cur = self.db.execute("INSERT INTO contacts(company_id,name,email,role,reason) VALUES(?,?,?,?,?)",
                                  (company_id, text(name, "Name"), address(email), text(role, "Role"), text(reason, "Reason")))
        return cur.lastrowid

    def get(self, contact_id):
        row = self.db.execute("""SELECT contacts.*, companies.name AS company, research, source
                               FROM contacts JOIN companies ON companies.id=company_id WHERE contacts.id=?""", (contact_id,)).fetchone()
        if row is None:
            raise ValueError("Contact not found.")
        return dict(row)

    def contacts(self):
        return [self.get(r[0]) for r in self.db.execute("SELECT id FROM contacts ORDER BY id").fetchall()]

    def pipeline(self):
        counts = dict.fromkeys(STAGES, 0)
        counts.update(dict(self.db.execute("SELECT stage, count(*) FROM contacts GROUP BY stage").fetchall()))
        return counts

    def due(self, on=None):
        on = date.fromisoformat(on) if on else date.today()
        return [c for c in self.contacts() if c["stage"] in {"emailed", "followed_up"}
                and c["follow_up"] and date.fromisoformat(c["follow_up"]) <= on]

    def compose(self, contact_id, sender, sender_name, kind="initial", template=None):
        c = self.get(contact_id)
        if kind not in {"initial", "followup"}:
            raise ValueError("Unknown template kind.")
        template = Path(template) if template else Path(__file__).parent / "templates" / f"{kind}.txt"
        values = dict(c, first_name=c["name"].split()[0], sender_name=text(sender_name, "Sender name"))
        try:
            rendered = Template(template.read_text(encoding="utf-8")).substitute(values)
        except (KeyError, ValueError):
            raise ValueError("Template has an unknown or malformed $placeholder.") from None
        subject, separator, body = rendered.partition("\n\n")
        if not separator or not subject.strip() or not body.strip() or "\n" in subject or "\r" in subject:
            raise ValueError("Template needs one subject line, a blank line, then a body.")
        message = EmailMessage()
        message["From"] = address(sender)
        message["To"] = c["email"]
        message["Subject"] = subject.strip()
        message["Message-ID"] = make_msgid(domain=sender.split("@")[1])
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        if kind == "followup":
            previous = self.db.execute("SELECT message_id FROM messages WHERE contact_id=? AND state='sent' ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()
            if previous:
                message["In-Reply-To"] = previous[0]
                message["References"] = previous[0]
        message.set_content(body.strip() + "\n")
        return message

    def send(self, contact_id, message, settings, kind="initial", follow_up_days=7, transport=deliver):
        if not 0 <= follow_up_days <= 365:
            raise ValueError("Follow-up days must be between 0 and 365.")
        settings.validate()
        if self.db.execute("SELECT 1 FROM metadata WHERE key='demo'").fetchone() and not settings.local:
            raise ValueError("Demo databases can send only to a local mail receiver.")
        if not settings.local and address(settings.user) != address(str(message["From"])):
            raise ValueError("Sender must match CRM_SMTP_USER.")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            c = self.get(contact_id)
            if str(message["To"]) != c["email"]:
                raise ValueError("Message recipient does not match this contact.")
            if c["stage"] in {"replied", "interview"}:
                raise ValueError("Contact has replied; continue the conversation in your mailbox.")
            if self.db.execute("SELECT 1 FROM messages WHERE contact_id=? AND state IN ('sending','uncertain')", (contact_id,)).fetchone():
                raise ValueError("Resolve the unfinished delivery before sending again.")
            sent = self.db.execute("SELECT sent_at FROM messages WHERE contact_id=? AND state='sent' ORDER BY id DESC LIMIT 1", (contact_id,)).fetchone()
            if kind not in {"initial", "followup"}:
                raise ValueError("Unknown message kind.")
            if (kind == "initial" and sent) or (kind == "followup" and not sent):
                raise ValueError("Use initial once, then followup after a successful email.")
            if kind == "followup" and c["follow_up"] and date.fromisoformat(c["follow_up"]) > date.today():
                raise ValueError("Follow-up is not due yet; change its date deliberately with remind.")
            cur = self.db.execute("""INSERT INTO messages(contact_id,kind,subject,body,sender,recipient,message_id,created_at,state,follow_up_days)
              VALUES(?,?,?,?,?,?,?,?,'sending',?)""", (contact_id, kind, str(message["Subject"]), message.get_content(), str(message["From"]),
                  c["email"], str(message["Message-ID"]), now(), follow_up_days))
            attempt = cur.lastrowid
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        try:
            transport(message, settings)
        except DeliveryError as exc:
            with self.db:
                self.db.execute("UPDATE messages SET state=? WHERE id=?", ("uncertain" if exc.uncertain else "failed", attempt))
            raise
        except Exception:
            with self.db:
                self.db.execute("UPDATE messages SET state='uncertain' WHERE id=?", (attempt,))
            raise ValueError("Delivery interrupted; check history and resolve before retrying.") from None
        self._finish(attempt)
        return attempt

    def _finish(self, attempt):
        with self.db:
            row = self.db.execute("SELECT * FROM messages WHERE id=?", (attempt,)).fetchone()
            self.db.execute("UPDATE messages SET state='sent', sent_at=? WHERE id=?", (now(), attempt))
            # A reply recorded during SMTP wins over an outgoing state update.
            self.db.execute("""UPDATE contacts SET stage=?, follow_up=? WHERE id=? AND stage NOT IN ('replied','interview')""",
                            ("emailed" if row["kind"] == "initial" else "followed_up",
                             (date.today() + timedelta(days=row["follow_up_days"])).isoformat(), row["contact_id"]))

    def resolve(self, attempt, outcome, note):
        row = self.db.execute("SELECT state FROM messages WHERE id=?", (attempt,)).fetchone()
        if row is None or row[0] not in {"sending", "uncertain"}:
            raise ValueError("Only unfinished deliveries can be resolved.")
        if outcome not in {"sent", "not-sent"}:
            raise ValueError("Outcome must be sent or not-sent.")
        note = text(note, "Resolution note")
        with self.db:
            self.db.execute("UPDATE messages SET resolution=? WHERE id=?", (note, attempt))
            if outcome == "not-sent":
                self.db.execute("UPDATE messages SET state='failed' WHERE id=?", (attempt,))
        if outcome == "sent":
            self._finish(attempt)

    def reply(self, contact_id, body, message_id=None, source="manual"):
        self.get(contact_id)
        with self.db:
            self.db.execute("INSERT INTO replies(contact_id,received_at,body,message_id,source) VALUES(?,?,?,?,?)",
                            (contact_id, now(), text(body, "Reply"), message_id, source))
            self.db.execute("UPDATE contacts SET stage=CASE WHEN stage='interview' THEN stage ELSE 'replied' END, follow_up=NULL WHERE id=?", (contact_id,))

    def import_reply(self, path):
        msg = BytesParser(policy=policy.default).parsebytes(Path(path).read_bytes())
        references = re.findall(r"<[^<>\s]+>", str(msg.get("In-Reply-To", "")) + " " + str(msg.get("References", "")))
        if not references or not msg.get("Message-ID"):
            raise ValueError("Reply needs Message-ID and In-Reply-To or References headers.")
        rows = self.db.execute("SELECT DISTINCT contact_id FROM messages WHERE state='sent' AND message_id IN (%s)" % ",".join("?" * len(references)), references).fetchall()
        if len(rows) != 1:
            raise ValueError("Reply does not identify exactly one known sent conversation.")
        contact_id = rows[0][0]
        if parseaddr(str(msg.get("From", "")))[1].lower() != self.get(contact_id)["email"]:
            raise ValueError("Reply sender does not match the contact. Record a manual note if needed.")
        part = msg.get_body(preferencelist=("plain",))
        if part is None:
            raise ValueError("Reply needs a plain-text body; record a manual note instead.")
        self.reply(contact_id, part.get_content(), str(msg["Message-ID"]), "eml")
        return contact_id

    def interview(self, contact_id):
        if self.get(contact_id)["stage"] not in {"replied", "interview"}:
            raise ValueError("Record the reply before marking an interview.")
        with self.db:
            self.db.execute("UPDATE contacts SET stage='interview', follow_up=NULL WHERE id=?", (contact_id,))

    def remind(self, contact_id, on):
        date.fromisoformat(on)
        if self.get(contact_id)["stage"] not in {"emailed", "followed_up"}:
            raise ValueError("Reminders are for contacts awaiting a reply.")
        with self.db:
            self.db.execute("UPDATE contacts SET follow_up=? WHERE id=?", (on, contact_id))

    def history(self, contact_id):
        return {"contact": self.get(contact_id),
                "outgoing": [dict(r) for r in self.db.execute("SELECT * FROM messages WHERE contact_id=? ORDER BY id", (contact_id,))],
                "replies": [dict(r) for r in self.db.execute("SELECT * FROM replies WHERE contact_id=? ORDER BY id", (contact_id,))]}

    def seed_demo(self):
        if self.db.execute("SELECT 1 FROM contacts UNION ALL SELECT 1 FROM companies UNION ALL SELECT 1 FROM messages LIMIT 1").fetchone():
            raise ValueError("Demo needs an empty database; existing records were kept.")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('demo','true')")
        for i, company in enumerate(("Sample Sensor Works", "Example Thin Films", "Demo Microdevices", "Fictional Lab Tools", "Example Coatings"), 1):
            cid = self.company(company, "your fictional article on miniature sensors", f"https://company{i}.example/research")
            self.contact(cid, f"Demo Contact {i}", f"contact{i}@company{i}.example", "Engineering team lead",
                         "I am interested in how your team tests small devices and would value a short conversation.")
