import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from automation.metrics import report, snapshot
from outreach_crm.core import CRM

ROOT = Path(__file__).resolve().parents[1]


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'crm.sqlite3'
        self.crm = CRM(self.db)
        self.addCleanup(self.crm.close)

    def test_empty_and_missing_database(self):
        self.assertEqual(snapshot(self.db, date(2026, 11, 2))['due'], 0)
        with self.assertRaises(sqlite3.OperationalError):
            snapshot(self.db.parent / 'missing', date.today())
        self.assertFalse((self.db.parent / 'missing').exists())

    def test_counts_follow_crm_reminder_rules(self):
        company = self.crm.company('Example', 'Research', 'Source')
        for i, (stage, due) in enumerate([('emailed', '2026-11-01'), ('followed_up', '2026-11-02'), ('emailed', '2026-11-03'), ('replied', '2026-11-01'), ('interview', None), ('researched', None)]):
            cid = self.crm.contact(company, 'Example', f'{i}@example.org', 'Role', 'Reason')
            with self.crm.db:
                self.crm.db.execute('UPDATE contacts SET stage=?, follow_up=? WHERE id=?', (stage, due, cid))
        counts = snapshot(self.db, date(2026, 11, 2))
        self.assertEqual(counts, dict(total=6, researched=1, emailed=2, followed_up=1, replied=1, interview=1, due=2, overdue=1))

    def test_csv_appends_with_one_header(self):
        path = self.db.parent / 'report.csv'
        report(self.db, path, date(2026, 11, 2))
        report(self.db, path, date(2026, 11, 9))
        with path.open(newline='') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r['date'] for r in rows], ['2026-11-02', '2026-11-09'])
        self.assertEqual(len(path.read_text().splitlines()), 3)

    def test_workflow_nodes_and_connections(self):
        workflow = json.loads((ROOT / 'automation/weekly-metrics.json').read_text())
        nodes = {n['name']: n for n in workflow['nodes']}
        self.assertEqual({n['type'] for n in nodes.values()}, {'n8n-nodes-base.scheduleTrigger', 'n8n-nodes-base.executeCommand', 'n8n-nodes-base.emailSend'})
        self.assertIn('metrics.py', nodes['Snapshot and append CSV']['parameters']['command'])
        self.assertIn('JSON.parse($json.stdout).due', nodes['Email summary']['parameters']['text'])
        self.assertEqual(workflow['connections']['Every Monday']['main'][0][0]['node'], 'Snapshot and append CSV')
        self.assertEqual(workflow['connections']['Snapshot and append CSV']['main'][0][0]['node'], 'Email summary')
        self.assertFalse(workflow['active'])


if __name__ == '__main__':
    unittest.main()
