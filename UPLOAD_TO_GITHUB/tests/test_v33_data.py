import json,unittest
from pathlib import Path
from collections import Counter
from meaning_search import number_sort,topic_catalog,find_evidence
from fast_retrieval import FastRuleIndex, tokens
ROOT=Path(__file__).resolve().parents[1]
class DataV33(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.rules=json.loads((ROOT/'forum_rules.json').read_text(encoding='utf-8'))
  cls.commands=json.loads((ROOT/'commands.json').read_text(encoding='utf-8'))
  cls.index=FastRuleIndex(cls.rules)
 def test_commands_screen_sources_preserved(self):
  names={c['command'] for c in self.commands}
  for name in ('/acceptbonus','/acceptdisband','/acheat','/acreatoromo','/acreatepromo','/addobject','/antislide','/asellbiz','/banip','/bansoc','/bizfsettings','/capturesettings','/checkhit','/clearobject','/endcapture','/freezedist','/givekeys','/gzcolor','/hlock','/loadzone','/muterep','/offgivekeys','/offmakeadmin','/orgstat','/removeobject','/resetmakeup','/setleader','/setskin','/skipquest','/sunjail','/templeader','/tpc','/unbansoc','/unfzdist','/voiceup','/wesettings'):
   self.assertIn(name,names,name)
 def test_unique_natural_order(self):
  self.assertEqual(sorted(['1.10','2','1','1.2','10'],key=number_sort),['1','1.2','1.10','2','10'])
 def test_punctuation_no_longer_hides_rule(self):
  tokens_r=tokens('Запрещены любые IC союзы.');self.assertIn('союзы',tokens_r)
  match=self.index.find('запрещены IC союзы',limit=80)
  self.assertTrue(any('Запрещены любые IC союзы.' in r['text'] for r in match))
 def test_supply_alliance_without_canned_answer(self):
  r=find_evidence('Союз на поставке',self.rules,{'search_queries':['поставка союз организаций'],'relevant_topics':['Правила поставок для Гос. и Крайм организаций']},self.index)
  self.assertTrue(any(x['topic']=='Правила поставок для Гос. и Крайм организаций' and 'Запрещены любые IC союзы' in x['text'] for x in r))
 def test_dm_5_7(self):
  r=find_evidence('Что такое ДМ / DM?',self.rules,index=self.index)
  self.assertTrue(any(x['topic']=='Общие правила сервера' and x.get('rule_number')=='5.7' for x in r))
 def test_categories(self):
  self.assertEqual(len(self.rules),942)
  sections=Counter(r['section'] for r in self.rules)
  self.assertEqual(len(sections),6)
  self.assertEqual(len(topic_catalog(self.rules)),34)
 def test_no_made_up_rule_numbers(self):
  # Unnumbered headings are kept unnumbered; do not invent a clause number.
  unnumbered=[r for r in self.rules if not r.get('rule_number')]
  self.assertEqual(len(unnumbered),12)
  self.assertTrue(all(r.get('text') for r in unnumbered))
if __name__=='__main__':unittest.main()
