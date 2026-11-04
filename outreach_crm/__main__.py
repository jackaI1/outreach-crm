import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .core import CRM
from .mail import DeliveryError, Settings


def parser():
    p = argparse.ArgumentParser(description="Keep company research, deliberate emails and follow-ups in one local notebook.")
    p.add_argument("--db", default=str(Path.home() / ".local/share/outreach-crm/crm.sqlite3"), help="Local database path")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create an empty private notebook")
    sub.add_parser("demo", help="Add five fictional contacts to an empty, local-only notebook")
    company = sub.add_parser("company", help="Save research before contacting someone")
    for flag in ("name", "research", "source"):
        company.add_argument("--" + flag, required=True)
    contact = sub.add_parser("contact", help="Save who to contact and why this person")
    contact.add_argument("--company", required=True, type=int)
    for flag in ("name", "email", "role", "reason"):
        contact.add_argument("--" + flag, required=True)
    sub.add_parser("list", help="Show contacts with their stage and follow-up date")
    sub.add_parser("pipeline", help="Count contacts at each stage")
    due = sub.add_parser("due", help="Show reminders due today, or on a chosen date")
    due.add_argument("--on")
    send = sub.add_parser("send", help="Preview by default; --deliver sends one email")
    send.add_argument("id", type=int)
    send.add_argument("--kind", choices=("initial", "followup"), default="initial")
    send.add_argument("--template", help="Text file: subject, blank line, then body with $placeholders")
    send.add_argument("--sender-name", default="Jack")
    send.add_argument("--follow-up-days", type=int, default=7)
    send.add_argument("--deliver", action="store_true")
    history = sub.add_parser("history", help="Read research, sent attempts and replies together")
    history.add_argument("id", type=int)
    reply = sub.add_parser("reply", help="Record a reply manually")
    reply.add_argument("id", type=int)
    reply.add_argument("--note", required=True)
    imp = sub.add_parser("import-reply", help="Read an exported .eml reply, matching its conversation")
    imp.add_argument("path")
    interview = sub.add_parser("interview", help="Mark an interview after a reply")
    interview.add_argument("id", type=int)
    remind = sub.add_parser("remind", help="Move the next follow-up date")
    remind.add_argument("id", type=int)
    remind.add_argument("--on", required=True)
    resolve = sub.add_parser("resolve", help="After checking the mailbox, resolve an interrupted send")
    resolve.add_argument("attempt", type=int)
    resolve.add_argument("--outcome", required=True, choices=("sent", "not-sent"))
    resolve.add_argument("--note", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    crm = None
    try:
        crm = CRM(args.db)
        command = args.command
        result = {"database": str(Path(args.db).expanduser())}
        if command == "demo":
            crm.seed_demo()
            result = crm.pipeline()
        elif command == "company":
            result = {"company_id": crm.company(args.name, args.research, args.source)}
        elif command == "contact":
            result = {"contact_id": crm.contact(args.company, args.name, args.email, args.role, args.reason)}
        elif command == "list":
            result = crm.contacts()
        elif command == "pipeline":
            result = crm.pipeline()
        elif command == "due":
            result = crm.due(args.on)
        elif command == "history":
            result = crm.history(args.id)
        elif command == "send":
            sender = os.environ.get("CRM_SMTP_USER", "demo@example.invalid")
            message = crm.compose(args.id, sender, args.sender_name, args.kind, args.template)
            if not args.deliver:
                print("PREVIEW ONLY. Nothing sent or saved. Add --deliver to send.\n")
                print(message.as_string())
                return 0
            settings = Settings.load()
            # The explicit sender is mandatory for any real or local delivery.
            if not settings.user:
                raise ValueError("Set CRM_SMTP_USER to the sending address before delivery.")
            result = {"sent_attempt": crm.send(args.id, message, settings, args.kind, args.follow_up_days)}
        elif command == "reply":
            crm.reply(args.id, args.note)
            result = {"replied": args.id}
        elif command == "import-reply":
            result = {"replied": crm.import_reply(args.path)}
        elif command == "interview":
            crm.interview(args.id)
            result = {"interview": args.id}
        elif command == "remind":
            crm.remind(args.id, args.on)
            result = {"follow_up": args.on}
        elif command == "resolve":
            crm.resolve(args.attempt, args.outcome, args.note)
            result = {"resolved_attempt": args.attempt}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, DeliveryError) as exc:
        # SQLite messages include constraint names, not bound private record contents.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if crm is not None:
            crm.close()


if __name__ == "__main__":
    raise SystemExit(main())
