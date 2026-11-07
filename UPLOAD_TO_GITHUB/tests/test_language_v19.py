import json
import unittest
from pathlib import Path
from query_language import repair_query

ROOT=Path(__file__).resolve().parents[1]
class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules=json.loads((ROOT/'forum_rules.json').read_text(encoding='utf8'))
    def test_common_typo(self):
        q,edits=repair_query('Что за убисйтво без причины?',self.rules)
        self.assertIn('убийство',q)
        self.assertIn(('убисйтво','убийство'),edits)
    def test_penalty_typo(self):
        q,edits=repair_query('Какая накаазние за дм?',self.rules)
        self.assertIn('наказание',q)
    def test_unknown_not_hallucinated(self):
        q,edits=repair_query('абракадабра',self.rules)
        self.assertEqual(q,'абракадабра')
        self.assertFalse(edits)
    def test_rule_count(self):
        self.assertEqual(len(self.rules),942)
if __name__=='__main__':unittest.main()
