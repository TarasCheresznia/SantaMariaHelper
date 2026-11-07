import unittest,json
from pathlib import Path
from fast_retrieval import FastRuleIndex
R=Path(__file__).resolve().parents[1]
class TestFast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.idx=FastRuleIndex(json.loads((R/'forum_rules.json').read_text(encoding='utf8')))
    def test_dm(self):
        self.assertEqual(self.idx.find('что такое dm наказание')[0]['id'],'5.7')
    def test_ambiguous_alliance_on_delivery(self):
        self.assertTrue(self.idx.find('союз на поставке'))  # candidates, not a verified violation
    def test_unknown(self):
        self.assertEqual(self.idx.find('абракадабраюврп'),[])
