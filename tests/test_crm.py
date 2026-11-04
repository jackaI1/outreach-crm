import contextlib
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from outreach_crm.__main__ import main
from outreach_crm.core import CRM, address
from outreach_crm.demo_mail import receiver
from outreach_crm.mail import DeliveryError, Settings, deliver


class CRMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'crm.sqlite3'
        self.crm = CRM(self.path)
        self.addCleanup(self.crm.close)
        company = self.crm.company('Example Instruments', 'your article on test fixtures', 'https://instruments.example/research')
        self.cid = self.crm.contact(company, 'Demo Engineer', 'engineer@instruments.example', 'Test lead',
                                    'I am interested in how you validate small sensors.')
        self.settings = Settings('sender@example.invalid', host='127.0.0.1', port=2525, security='plain')

    def message(self, kind='initial'):
        return self.crm.compose(self.cid, self.settings.user, 'Demo Sender', kind)

    def send(self, kind='initial', days=7, transport=lambda m, s: None):
        return self.crm.send(self.cid, self.message(kind), self.settings, kind, days, transport)

    def test_company_research_contact_and_pipeline_persist(self):
        with contextlib.closing(CRM(self.path)) as reopened:
            contact = reopened.get(self.cid)
            self.assertEqual(contact['source'], 'https://instruments.example/research')
            self.assertEqual(contact['role'], 'Test lead')
            self.assertEqual(reopened.pipeline(), dict(researched=1, emailed=0, followed_up=0, replied=0, interview=0))
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_unique_contact_and_company_and_foreign_key(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.crm.contact(1, 'Other', 'ENGINEER@instruments.example', 'Lead', 'Reason')
        with self.assertRaises(sqlite3.IntegrityError):
            self.crm.company('example instruments', 'Research', 'Source')
        with self.assertRaises(sqlite3.IntegrityError):
            self.crm.contact(999, 'Other', 'other@example.invalid', 'Lead', 'Reason')

    def test_research_and_reason_required(self):
        with self.assertRaises(ValueError):
            self.crm.company('Empty', ' ', 'https://example.invalid')
        with self.assertRaises(ValueError):
            self.crm.contact(1, 'Other', 'other@example.invalid', 'Lead', '')

    def test_invalid_addresses_and_missing_contact(self):
        for value in ('x', 'a@b', 'A <a@b.example>', 'a@b.example\nBcc:x@y.example'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                address(value)
        with self.assertRaises(ValueError):
            self.crm.get(999)

    def test_preview_personalizes_without_saving_or_sending(self):
        message = self.message()
        self.assertIn('Hi Demo,', message.get_content())
        self.assertIn('your article on test fixtures', message.get_content())
        self.assertIn('validate small sensors', message.get_content())
        self.assertEqual(message['Subject'], 'Question about Example Instruments')
        self.assertEqual(self.crm.history(self.cid)['outgoing'], [])
        self.assertEqual(self.crm.get(self.cid)['stage'], 'researched')

    def test_strict_custom_template_and_header_injection(self):
        path = Path(self.tmp.name) / 'custom.txt'
        for body in ('Hello ${unknown}\n\nBody', 'Hello\nBcc: injected@example.invalid\n\nBody', 'No body', 'Hello\n\n'):
            path.write_text(body)
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.crm.compose(self.cid, self.settings.user, 'Sender', template=path)
        path.write_text('Hello ${company}\n\nI read ${source}. ${reason}')
        self.assertIn('https://instruments.example/research', self.crm.compose(self.cid, self.settings.user, 'Sender', template=path).get_content())

    def test_sent_snapshot_and_due_date(self):
        self.send()
        c = self.crm.get(self.cid)
        self.assertEqual(c['stage'], 'emailed')
        self.assertEqual(c['follow_up'], (date.today() + timedelta(days=7)).isoformat())
        self.assertEqual(self.crm.due(), [])
        self.assertEqual(len(self.crm.due(c['follow_up'])), 1)
        row = self.crm.history(self.cid)['outgoing'][0]
        self.assertEqual(row['state'], 'sent')
        self.assertIn('test fixtures', row['body'])
        self.assertEqual(row['recipient'], 'engineer@instruments.example')
        self.assertTrue(row['sent_at'])
        self.assertTrue(row['message_id'].startswith('<'))

    def test_real_smtp_delivery_and_cleanup(self):
        with receiver() as server:
            port = server.server_address[1]
            settings = Settings(self.settings.user, host='127.0.0.1', port=port, security='plain')
            message = self.message()
            self.crm.send(self.cid, message, settings)
            received = BytesParser(policy=policy.default).parsebytes(server.messages[0])
            self.assertEqual(received['To'], message['To'])
            self.assertEqual(received['Message-ID'], message['Message-ID'])
            self.assertEqual(received.get_content().replace('\r\n', '\n'), message.get_content())
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)

    def test_receiver_closes_on_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'example failure'):
            with receiver() as server:
                port = server.server_address[1]
                raise RuntimeError('example failure')
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)

    def test_duplicate_initial_blocked_before_transport(self):
        self.send()
        called = []
        with self.assertRaises(ValueError):
            self.send(transport=lambda *args: called.append(True))
        self.assertEqual(called, [])
        self.assertEqual(len(self.crm.history(self.cid)['outgoing']), 1)

    def test_followup_requires_initial_and_due_date(self):
        with self.assertRaises(ValueError):
            self.send('followup')
        self.send()
        with self.assertRaises(ValueError):
            self.send('followup')
        self.crm.remind(self.cid, date.today().isoformat())
        message = self.message('followup')
        self.assertEqual(message['In-Reply-To'], self.crm.history(self.cid)['outgoing'][0]['message_id'])
        self.send('followup')
        self.assertEqual(self.crm.get(self.cid)['stage'], 'followed_up')
        self.assertEqual(len(self.crm.history(self.cid)['outgoing']), 2)

    def test_rejected_delivery_is_not_counted_and_can_retry(self):
        def reject(*args):
            raise DeliveryError(False)
        with self.assertRaises(DeliveryError):
            self.send(transport=reject)
        self.assertEqual(self.crm.get(self.cid)['stage'], 'researched')
        self.assertEqual(self.crm.history(self.cid)['outgoing'][0]['state'], 'failed')
        self.send()
        self.assertEqual(self.crm.get(self.cid)['stage'], 'emailed')

    def test_uncertain_delivery_blocks_retry_until_resolved(self):
        def disconnect(*args):
            raise DeliveryError(True)
        with self.assertRaises(DeliveryError):
            self.send(transport=disconnect)
        with self.assertRaises(ValueError):
            self.send()
        self.crm.resolve(1, 'sent', 'Confirmed in the sent mailbox')
        self.assertEqual(self.crm.get(self.cid)['stage'], 'emailed')
        self.assertEqual(self.crm.history(self.cid)['outgoing'][0]['resolution'], 'Confirmed in the sent mailbox')
        with self.assertRaises(ValueError):
            self.crm.resolve(1, 'not-sent', 'Must not rewrite a completed delivery')

    def test_interrupted_delivery_can_be_confirmed_not_sent(self):
        def interrupted(*args):
            raise RuntimeError('private provider detail')
        with self.assertRaisesRegex(ValueError, 'Delivery interrupted'):
            self.send(transport=interrupted)
        self.crm.resolve(1, 'not-sent', 'No accepted message in mailbox')
        self.send()
        self.assertEqual(self.crm.pipeline()['emailed'], 1)

    def test_concurrent_sender_sees_committed_pending_attempt(self):
        def second_sender(*args):
            with contextlib.closing(CRM(self.path)) as other:
                with self.assertRaisesRegex(ValueError, 'unfinished delivery'):
                    other.send(self.cid, self.message(), self.settings, transport=lambda *a: None)
        self.send(transport=second_sender)
        self.assertEqual(len(self.crm.history(self.cid)['outgoing']), 1)

    def test_reply_during_send_wins_over_outgoing_state(self):
        def reply_during_delivery(*args):
            with contextlib.closing(CRM(self.path)) as other:
                other.reply(self.cid, 'Fictional reply during delivery')
        self.send(transport=reply_during_delivery)
        self.assertEqual(self.crm.get(self.cid)['stage'], 'replied')
        self.assertIsNone(self.crm.get(self.cid)['follow_up'])
        self.assertEqual(self.crm.history(self.cid)['outgoing'][0]['state'], 'sent')

    def test_manual_reply_clears_reminder_and_prevents_more_outreach(self):
        self.send(days=0)
        self.crm.reply(self.cid, 'Thanks, let us talk.')
        self.assertEqual(self.crm.due(), [])
        self.assertEqual(self.crm.history(self.cid)['replies'][0]['source'], 'manual')
        with self.assertRaises(ValueError):
            self.send('followup')
        self.crm.interview(self.cid)
        self.crm.reply(self.cid, 'Here is a time.')
        self.assertEqual(self.crm.get(self.cid)['stage'], 'interview')

    def test_interview_and_reminder_require_right_stage(self):
        with self.assertRaises(ValueError):
            self.crm.interview(self.cid)
        with self.assertRaises(ValueError):
            self.crm.remind(self.cid, date.today().isoformat())
        self.send()
        with self.assertRaises(ValueError):
            self.crm.remind(self.cid, 'not a date')

    def reply_file(self, sender='engineer@instruments.example', reference=None):
        reply = EmailMessage()
        reply['From'] = sender
        reply['Message-ID'] = '<fictional-reply@instruments.example>'
        reply['In-Reply-To'] = reference or self.crm.history(self.cid)['outgoing'][0]['message_id']
        reply.set_content('Thanks for the note. Let us talk next week.')
        path = Path(self.tmp.name) / 'reply.eml'
        path.write_bytes(reply.as_bytes())
        return path

    def test_import_reply_matches_thread_and_rejects_duplicate(self):
        self.send()
        path = self.reply_file()
        self.assertEqual(self.crm.import_reply(path), self.cid)
        self.assertEqual(self.crm.get(self.cid)['stage'], 'replied')
        self.assertEqual(self.crm.history(self.cid)['replies'][0]['source'], 'eml')
        with self.assertRaises(sqlite3.IntegrityError):
            self.crm.import_reply(path)
        self.assertEqual(len(self.crm.history(self.cid)['replies']), 1)

    def test_import_reply_rejects_unknown_thread_and_wrong_sender(self):
        self.send()
        for sender, ref in [('other@example.invalid', None), ('engineer@instruments.example', '<unknown@example.invalid>')]:
            with self.subTest(sender=sender), self.assertRaises(ValueError):
                self.crm.import_reply(self.reply_file(sender, ref))
        self.assertEqual(self.crm.get(self.cid)['stage'], 'emailed')

    def test_no_recipient_substitution_or_invalid_days(self):
        message = self.message()
        message.replace_header('To', 'other@example.invalid')
        with self.assertRaises(ValueError):
            self.crm.send(self.cid, message, self.settings, transport=lambda *a: None)
        for days in (-1, 366):
            with self.assertRaises(ValueError):
                self.send(days=days)
        self.assertEqual(self.crm.history(self.cid)['outgoing'], [])

    def test_demo_is_non_destructive_and_never_sends_to_external_host(self):
        with self.assertRaises(ValueError):
            self.crm.seed_demo()
        self.assertEqual(len(self.crm.contacts()), 1)
        with contextlib.closing(CRM(Path(self.tmp.name) / 'demo.sqlite3')) as demo:
            demo.seed_demo()
            settings = Settings('sender@example.invalid', 'fake-secret')
            called = []
            with self.assertRaisesRegex(ValueError, 'Demo databases'):
                demo.send(1, demo.compose(1, settings.user, 'Sender'), settings, transport=lambda *a: called.append(1))
            self.assertEqual(called, [])
            self.assertEqual(len(demo.contacts()), 5)


class MailTests(unittest.TestCase):
    def test_no_plaintext_remote_credentials(self):
        with self.assertRaises(ValueError):
            Settings('sender@example.invalid', 'secret', 'smtp.gmail.com', 587, 'plain').validate()
        with self.assertRaises(ValueError):
            Settings('sender@example.invalid').validate()
        with self.assertRaises(ValueError):
            Settings('sender@example.invalid', host='127.0.0.1', security='bad').validate()

    def test_environment_password_takes_precedence_over_keychain(self):
        with patch.dict(os.environ, {'CRM_SMTP_USER': 'sender@example.invalid', 'CRM_SMTP_PASSWORD': 'fake-secret'}, clear=True), patch('subprocess.run') as run:
            settings = Settings.load()
            self.assertEqual(settings.password, 'fake-secret')
            self.assertNotIn('fake-secret', repr(settings))
            run.assert_not_called()

    def test_keychain_uses_service_and_account_without_logging(self):
        with patch.dict(os.environ, {'CRM_SMTP_USER': 'sender@example.invalid'}, clear=True), patch('sys.platform', 'darwin'), patch('subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = 'fake-keychain-secret\n'
            self.assertEqual(Settings.load().password, 'fake-keychain-secret')
            self.assertEqual(run.call_args.args[0], ['security', 'find-generic-password', '-s', 'outreach-crm-gmail', '-a', 'sender@example.invalid', '-w'])

    def test_missing_keychain_is_clear_and_safe(self):
        with patch.dict(os.environ, {'CRM_SMTP_USER': 'sender@example.invalid'}, clear=True), patch('sys.platform', 'darwin'), patch('subprocess.run') as run:
            run.return_value.returncode = 44
            with self.assertRaisesRegex(ValueError, 'app password'):
                Settings.load()

    def test_ssl_and_starttls_authenticate_before_sending(self):
        for security, port in [('ssl', 465), ('starttls', 587)]:
            with self.subTest(security=security), patch('outreach_crm.mail.smtplib.SMTP_SSL') as secure, patch('outreach_crm.mail.smtplib.SMTP') as smtp:
                client = (secure if security == 'ssl' else smtp).return_value
                client.send_message.return_value = {}
                deliver(EmailMessage(), Settings('sender@example.invalid', 'fake-secret', port=port, security=security))
                client.login.assert_called_once_with('sender@example.invalid', 'fake-secret')
                client.send_message.assert_called_once()
                client.close.assert_called_once()
                if security == 'starttls':
                    client.starttls.assert_called_once()
                    calls = [c[0] for c in client.method_calls]
                    self.assertLess(calls.index('starttls'), calls.index('login'))
                    self.assertLess(calls.index('login'), calls.index('send_message'))

    def test_transport_failures_redact_provider_secrets(self):
        import smtplib
        for exc, uncertain in [(smtplib.SMTPAuthenticationError(535, b'fake-secret'), False),
                               (smtplib.SMTPServerDisconnected('fake-secret'), True),
                               (smtplib.SMTPRecipientsRefused({'x': (550, b'fake-secret')}), False)]:
            with self.subTest(exception=type(exc).__name__), patch('outreach_crm.mail.smtplib.SMTP_SSL') as smtp:
                client = smtp.return_value
                if isinstance(exc, smtplib.SMTPAuthenticationError):
                    client.login.side_effect = exc
                else:
                    client.send_message.side_effect = exc
                with self.assertRaises(DeliveryError) as caught:
                    deliver(EmailMessage(), Settings('sender@example.invalid', 'fake-secret'))
                self.assertEqual(caught.exception.uncertain, uncertain)
                self.assertNotIn('fake-secret', str(caught.exception))
                client.close.assert_called_once()


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'demo.sqlite3'

    def cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(['--db', str(self.path), *args])
        return code, output.getvalue(), errors.getvalue()

    def test_cli_preview_never_loads_credentials(self):
        self.assertEqual(self.cli('demo')[0], 0)
        with patch('outreach_crm.__main__.Settings.load', side_effect=AssertionError('must not read credentials')):
            code, output, _ = self.cli('send', '1')
        self.assertEqual(code, 0)
        self.assertIn('PREVIEW ONLY', output)
        self.assertEqual(json.loads(self.cli('pipeline')[1])['researched'], 5)
        self.assertEqual(json.loads(self.cli('history', '1')[1])['outgoing'], [])

    def test_cli_real_local_send_then_reply_interview(self):
        self.cli('demo')
        with receiver() as server, patch.dict(os.environ, {'CRM_SMTP_USER': 'sender@example.invalid',
                'CRM_SMTP_HOST': '127.0.0.1', 'CRM_SMTP_PORT': str(server.server_address[1]), 'CRM_SMTP_SECURITY': 'plain'}, clear=True):
            code, out, err = self.cli('send', '1', '--deliver', '--follow-up-days', '0')
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)['sent_attempt'], 1)
            self.assertEqual(len(server.messages), 1)
        self.assertEqual(len(json.loads(self.cli('due')[1])), 1)
        self.assertEqual(self.cli('reply', '1', '--note', 'A fictional reply.')[0], 0)
        self.assertEqual(self.cli('interview', '1')[0], 0)
        self.assertEqual(json.loads(self.cli('pipeline')[1])['interview'], 1)

    def test_cli_validation_errors_exit_nonzero(self):
        self.cli('demo')
        code, _, error = self.cli('send', '999')
        self.assertEqual(code, 1)
        self.assertIn('Contact not found', error)
        self.assertEqual(self.cli('due', '--on', 'invalid')[0], 1)
        self.assertEqual(self.cli('demo')[0], 1)

    def test_module_and_demo_work_in_separate_processes(self):
        process = subprocess.run([sys.executable, '-m', 'outreach_crm', '--db', str(self.path), 'demo'], capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['researched'], 5)
        demo = subprocess.run([sys.executable, 'scripts/demo.py'], capture_output=True, text=True, timeout=15)
        self.assertEqual(demo.returncode, 0, demo.stderr)
        self.assertIn('"received_locally": 5', demo.stdout)
        self.assertIn('Cleanup verified', demo.stdout)


if __name__ == '__main__':
    unittest.main()
