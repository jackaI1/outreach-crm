"""Send five fictional messages locally and leave no server or database behind."""
import json
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outreach_crm.core import CRM
from outreach_crm.demo_mail import receiver
from outreach_crm.mail import Settings


def main():
    with tempfile.TemporaryDirectory(prefix="outreach-crm-demo-") as directory:
        crm = CRM(Path(directory) / "demo.sqlite3")
        try:
            crm.seed_demo()
            with receiver() as server:
                print(json.dumps(server.owner))
                port = server.server_address[1]
                settings = Settings("sender@example.invalid", host="127.0.0.1", port=port, security="plain")
                for contact_id in (2, 3, 4, 5):
                    message = crm.compose(contact_id, settings.user, "Demo Sender")
                    crm.send(contact_id, message, settings, follow_up_days=0)
                followup = crm.compose(3, settings.user, "Demo Sender", "followup")
                crm.send(3, followup, settings, "followup", follow_up_days=0)
                crm.reply(4, "Fictional reply: thanks for reaching out.")
                crm.reply(5, "Fictional reply: let's arrange a conversation.")
                crm.interview(5)
                result = {"received_locally": len(server.messages), "pipeline": crm.pipeline(),
                          "due_followups": len(crm.due()), "real_emails_sent": 0}
                assert result["received_locally"] == 5
                assert all(count == 1 for count in result["pipeline"].values())
                assert result["due_followups"] == 2
                print(json.dumps(result, indent=2))
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex(("127.0.0.1", port)) != 0, "Receiver port still open"
            print("Cleanup verified: local mail port closed; temporary database removed on exit.")
        finally:
            crm.close()


if __name__ == "__main__":
    main()
