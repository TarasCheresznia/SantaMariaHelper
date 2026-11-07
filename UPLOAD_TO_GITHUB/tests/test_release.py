import json, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class BundleTests(unittest.TestCase):
    def test_rules(self):
        rules=json.loads((ROOT/'forum_rules.json').read_text(encoding='utf-8'))
        self.assertEqual(len(rules),942)
        self.assertTrue(all(all(k in r for k in ('id','text','topic','section','url')) for r in rules))
        self.assertGreaterEqual(len({r['topic'] for r in rules}),30)
        self.assertTrue(any(r['text'].startswith('5.7 ') and 'DM' in r['text'] for r in rules))
    def test_commands(self):
        cmds=json.loads((ROOT/'commands.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(cmds),185)
        self.assertEqual(len(cmds),len({c["command"] for c in cmds}))
        self.assertTrue(all('command' in c and 'description' in c for c in cmds))
    def test_cloud_url(self):
        text=(ROOT/'main.py').read_text(encoding='utf-8')
        self.assertIn('workers.dev',text)
        self.assertIn("post_json('/answer'",text)
        self.assertIn("post_json('/interpret'",text)
        self.assertIn("find_evidence(self.question,self.rules,interpretation,self.index",text)
    def test_source_files(self):
        for fn in ('main.py','binder_engine.py','forum_sync.py','search_engine.py','rule_integrity.py'):
            compile((ROOT/fn).read_text(encoding='utf-8'),fn,'exec')
if __name__=='__main__':unittest.main()
