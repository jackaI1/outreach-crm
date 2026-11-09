import unittest
from datetime import date
from outreach_crm.target_audit import audit

class TargetAuditTest(unittest.TestCase):
    def test_duplicate_exact_accounts_go_to_review(self):
        result = audit([{'company': 'Acme Inc'}], [
            {'id': '1', 'name': 'Acme Inc'}, {'id': '2', 'name': 'Acme LLC'}], [], [], [], date(2026, 1, 31), 30)
        self.assertEqual(result[0]['match'], 'review')
        self.assertEqual(set(result[0]['candidates']), {'1', '2'})

    def test_conflicts_and_stale_are_visible(self):
        result = audit([{'company': 'Acme', 'territory': 'East', 'contact': 'a@example.com'}],
            [{'id': '1', 'name': 'Acme Inc', 'territory': 'West', 'last_activity': '2025-12-01'}],
            [{'account_id': '1', 'email': 'b@example.com'}], [],
            [{'territory': 'East', 'sales': 'Sam', 'architect': 'Lee'}], date(2026, 1, 31), 30)
        self.assertEqual(result[0]['match'], 'exact')
        self.assertIn('territory conflict', result[0]['flags'])
        self.assertIn('contact missing', result[0]['flags'])
        self.assertIn('stalled', result[0]['flags'])

class MatchingEdges(unittest.TestCase):
    def run_audit(self, targets, accounts, contacts=None, territories=None, stale=30):
        return audit(targets, accounts, contacts or [], [], territories or [], date(2026, 1, 31), stale)

    def test_normalization_and_unique_fuzzy(self):
        from outreach_crm.target_audit import key
        self.assertEqual(key('  AURÓRA & Sons, Inc. '), 'aurora and sons')
        r = self.run_audit([{'company':'Boreal Photonics'}], [{'id':'1','name':'Boreal Photonic Ltd'}])[0]
        self.assertEqual(r['match'], 'fuzzy')
        self.assertGreater(r['scores']['1'], .92)

    def test_fuzzy_competitors_and_duplicate_targets_review(self):
        accounts = [{'id':'1','name':'Boreal Photonic'}, {'id':'2','name':'Boreal Photonics Lab'}]
        r = self.run_audit([{'company':'Boreal Photonics'}], accounts)[0]
        self.assertEqual(r['match'], 'review')
        self.assertNotIn('account_id', r)
        rows = self.run_audit([{'company':'Acme Inc'}, {'company':'Acme LLC'}], [{'id':'1','name':'Acme'}])
        self.assertTrue(all(r['match']=='review' and 'duplicate target' in r['flags'] for r in rows))

    def test_conflicting_duplicate_account_ids_are_never_auto_linked(self):
        r = self.run_audit([{'company':'Acme'}], [{'id':'1','name':'Acme'}, {'id':'1','name':'Other'}])[0]
        self.assertEqual(r['match'], 'review')
        self.assertIn('duplicate account ID', r['flags'])

    def test_contacts_owners_missing_and_conflicting(self):
        r = self.run_audit([{'company':'Acme','territory':'East','contact':'A@example.com'}],
            [{'id':'1','name':'Acme','territory':'West','sales':'Wrong','last_activity':'bad'}],
            [{'id':'c1','account_id':'1','email':'a@example.com'}, {'id':'c2','account_id':'2','email':'a@example.com'}],
            [{'territory':'East','sales':'Sam','architect':'Lee'}])[0]
        for flag in ('territory conflict','sales conflict','CRM architect missing','contact conflict','duplicate contact','invalid activity date'):
            self.assertIn(flag, r['flags'])

    def test_stale_boundary_future_and_duplicate_territories(self):
        base = {'id':'1','name':'Acme','last_activity':'2026-01-01'}
        self.assertNotIn('stalled', self.run_audit([{'company':'Acme'}], [base])[0]['flags'])
        base['last_activity'] = '2025-12-31'
        self.assertIn('stalled', self.run_audit([{'company':'Acme'}], [base])[0]['flags'])
        base['last_activity'] = '2026-02-01'
        self.assertIn('future activity', self.run_audit([{'company':'Acme'}], [base])[0]['flags'])
        ts = [{'territory':'East','sales':'Sam','architect':'Lee'}]*2
        self.assertIn('unassigned', self.run_audit([{'company':'Acme','territory':'East'}], [base], territories=ts)[0]['flags'])
        with self.assertRaises(ValueError):
            self.run_audit([{'company':'Acme'}], [base], stale=-1)
