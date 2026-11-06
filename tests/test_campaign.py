import unittest
from outreach_crm.campaign import interval, summarize, read_ledger
from pathlib import Path

class CampaignTests(unittest.TestCase):
    def test_wilson_interval_and_empty(self):
        self.assertEqual(interval(0, 0), (None, None))
        low, high = interval(50, 100)
        self.assertAlmostEqual(low, .4038, places=3)
        self.assertAlmostEqual(high, .5962, places=3)

    def test_cohort_grouping_and_deduplicated_outcomes(self):
        rows = read_ledger(Path('examples/campaign.csv'))
        report = summarize(rows, '2026-02-01', '2026-02-28', '2026-01-01', '2026-01-31')
        self.assertEqual(report['current']['sends'], 10)
        self.assertEqual(report['current']['counts']['reply'], 4)
        self.assertEqual(report['previous']['sends'], 10)
        self.assertEqual(report['current']['rates']['reply'], .4)
        self.assertTrue(any(g['template'] == 'initial-a' and g['segment'] == 'sensors' for g in report['groups']))

    def test_rejects_invalid_outcome(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bad.csv'
            p.write_text('send_id,sent_date,template,segment,open,click,reply,conversion\n1,2026-01-01,a,b,2,0,0,0\n')
            with self.assertRaises(ValueError):
                read_ledger(p)

class ExportTests(unittest.TestCase):
    def test_exports_editable_workbook_and_slide(self):
        import tempfile
        from openpyxl import load_workbook
        from pptx import Presentation
        from outreach_crm.campaign import export
        rows = read_ledger(Path('examples/campaign.csv'))
        report = summarize(rows, '2026-02-01', '2026-02-28', '2026-01-01', '2026-01-31')
        with tempfile.TemporaryDirectory() as d:
            export(report, d)
            book = load_workbook(Path(d) / 'campaign-summary.xlsx')
            self.assertEqual(book['Summary']['C4'].value, .4)
            self.assertEqual(len(book['Summary']._charts), 1)
            self.assertGreater(book['Template and segment'].max_row, 1)
            slide = Presentation(Path(d) / 'campaign-summary.pptx').slides[0]
            self.assertTrue(any(shape.has_chart for shape in slide.shapes))
            self.assertTrue(any('do not establish a cause' in shape.text for shape in slide.shapes if shape.has_text_frame))
