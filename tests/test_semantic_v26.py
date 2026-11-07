import json,unittest
from pathlib import Path
from semantic_retrieval import semantic_candidates
ROOT=Path(__file__).resolve().parents[1]
class TestSemanticV26(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules=json.loads((ROOT/'forum_rules.json').read_text(encoding='utf8'))
    def test_semantic_reformulation_finds_rule(self):
        # The phrasing is intentionally NOT a hard-coded rule phrase.
        r=semantic_candidates('почему меня наказали?',self.rules,{'search_queries':['убийство без причины DM']})
        self.assertTrue(any('5.7 ' in x['text'] for x in r))
    def test_topic_from_model(self):
        name='Общие правила Мафии'
        r=semantic_candidates('мафия убийство',self.rules,{'relevant_topics':[name]},limit=15)
        self.assertTrue(r)
        self.assertTrue(any(x['topic']==name for x in r))
    def test_no_concocted_evidence(self):
        self.assertEqual(semantic_candidates('абракадабр',self.rules,{'relevant_topics':['Несуществующая тема']}),[])
if __name__=='__main__':unittest.main()
