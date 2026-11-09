import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import date

from outreach_crm.target_audit import read_rows
from outreach_crm.target_book import TargetBook
from scripts.target_demo import FIXTURES, seed


class TargetBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.book = TargetBook(self.path / 'book.sqlite3')
        self.addCleanup(self.book.db.close)
        seed(self.book)

    def reimport(self, **changes):
        s = self.book.snapshot()
        s.update(changes)
        return self.book.import_reports(*[s[k] for k in ('targets','accounts','contacts','opportunities','territories')],
                                        date.fromisoformat(s['today']), s['stale_days'])

    def test_different_layouts_and_excel_sheet(self):
        self.assertEqual(len(self.book.rows()), 7)
        self.assertEqual(self.book.rows()[4]['target']['company'], 'Cirrus Micro')
        self.assertEqual(self.book.rows()[4]['target']['_source'], 'partner-b.xlsx:4')
        self.assertEqual(sum(r['match']=='review' for r in self.book.rows()), 3)

    def test_duplicate_ids_rejected_atomically(self):
        original = self.book.snapshot()
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.reimport(targets=original['targets']*2)
        self.assertEqual(self.book.snapshot(), original)
        self.assertEqual(len(self.book.funnel()), 4)

    def test_funnel_dedupes_reimports_and_updates_existing(self):
        self.reimport()
        self.assertEqual(len(self.book.funnel()), 4)
        ops = self.book.snapshot()['opportunities']
        ops[0]['stage'] = 'Proposal'
        self.reimport(opportunities=ops+ops[:1])
        self.assertEqual(len(self.book.funnel()), 4)
        self.assertEqual(next(o for o in self.book.funnel() if o['id']=='O1')['stage'], 'Proposal')
        bad = dict(ops[0], amount='999')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.reimport(opportunities=ops+[bad])
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.reimport(opportunities=[dict(ops[0], account_id='A2')])

    def test_opportunity_invalid_dates_amount_and_orphans(self):
        original = self.book.snapshot()
        for change in ({'amount':'nan'}, {'amount':'-1'}, {'created':'2026-02-09'}, {'created':'bad'}, {'account_id':'missing'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.reimport(opportunities=[dict(original['opportunities'][0], **change)])
        self.assertEqual(self.book.snapshot(), original)

    def test_review_requires_candidate_then_reimport_invalidates_decision(self):
        with self.assertRaises(ValueError):
            self.book.advance('T3','outreach','2026-02-08','Example')
        with self.assertRaises(ValueError):
            self.book.resolve('T3','A1','Example')
        self.book.resolve('T3','A3','Checked the fictional source')
        r = self.book.rows()[2]
        self.assertEqual((r['match'],r['account_id']), ('reviewed','A3'))
        self.assertEqual(self.book.db.execute('SELECT note FROM decisions').fetchone()[0], 'Checked the fictional source')
        self.book.advance('T3','outreach','2026-02-08','Example')
        self.reimport()
        self.assertEqual(self.book.rows()[2]['match'], 'review')
        self.assertEqual(self.book.rows()[2]['status'], 'outreach')
        self.assertEqual(len(self.book.review_history('T3')),1)

    def test_single_below_threshold_candidate_can_be_approved(self):
        s=self.book.snapshot()
        s['targets'][2]['company']='Meridian Labs'
        s['accounts']=[a for a in s['accounts'] if a['id']!='A4']
        s['accounts'][2]['name']='Meridian Lab West'
        self.reimport(targets=s['targets'], accounts=s['accounts'])
        self.book.resolve('T3','A3','Checked manually')
        self.assertEqual(self.book.rows()[2]['account_id'], 'A3')

    def test_duplicate_target_resolution_cannot_double_link(self):
        self.book.resolve('T6','A6','Primary entry')
        with self.assertRaisesRegex(ValueError, 'already linked'):
            self.book.resolve('T7','A6','Duplicate')

    def test_status_history_persists_and_respects_asof(self):
        with self.assertRaises(ValueError):
            self.book.advance('T1','engaged','2026-02-08','Skip')
        self.book.advance('T1','outreach','2026-02-08','First message')
        self.book.advance('T1','engaged','2026-02-09','Reply')
        with TargetBook(self.path / 'book.sqlite3') as reopened:
            self.assertEqual(reopened.rows('2026-02-08')[0]['status'], 'outreach')
            self.assertEqual(reopened.rows('2026-02-09')[0]['status'], 'engaged')
            self.assertNotIn('stalled', reopened.rows('2026-02-09')[0]['flags'])
            self.assertEqual(len(reopened.history('T1')), 2)
        with self.assertRaises(ValueError):
            self.book.advance('T1','qualified','2026-02-08','Backdate')
        with self.assertRaises(ValueError):
            self.book.rows('2026-02-07')

    def test_bad_header_mapping_fails_visibly(self):
        f=self.path/'bad.csv'
        f.write_text('Company,Company\nA,B\n')
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            read_rows(f,'targets')
        f.write_text('Company\nA\n')
        with self.assertRaisesRegex(ValueError,'Missing columns'):
            read_rows(f,'targets')
        with self.assertRaises(ValueError):
            read_rows(FIXTURES/'partner-b.xlsx','targets',sheet='Wrong')

    def test_cli_reads_kept_notebook(self):
        completed = subprocess.run([sys.executable,'-m','outreach_crm.target_audit','--db',str(self.path/'book.sqlite3'),'review'],
                                   check=True,capture_output=True,text=True)
        self.assertEqual(len(json.loads(completed.stdout)),3)

    def test_target_identity_cannot_inherit_another_company_history(self):
        targets=self.book.snapshot()['targets']
        targets[0]['company']='Another business'
        with self.assertRaisesRegex(ValueError,'changed company'):
            self.reimport(targets=targets)

    def test_unmatched_target_recency_includes_saved_outreach(self):
        self.assertIn('stalled',self.book.rows()[3]['flags'])
        self.book.advance('T4','outreach','2026-02-08','Fictional first contact without a CRM account')
        self.assertNotIn('stalled',self.book.rows()[3]['flags'])
        self.assertIn('stalled',self.book.rows('2026-03-11')[3]['flags'])
