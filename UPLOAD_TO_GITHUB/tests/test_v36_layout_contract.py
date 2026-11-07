import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class LayoutContract(unittest.TestCase):
    def setUp(self):
        self.code=(ROOT/'main.py').read_text('utf-8')
    def test_account_list_is_independent_of_editor(self):
        self.assertIn("self.account_search=QLineEdit()",self.code)
        self.assertIn("self.account_filter=QComboBox()",self.code)
        self.assertIn("split.setSizes([510,340])",self.code)
    def test_command_editor_lists_levels(self):
        self.assertIn("self.level_list=QListWidget()",self.code)
        self.assertIn("self._visible_commands=",self.code)
        self.assertIn("self.chosen_index()",self.code)
    def test_regular_commands_have_level_list(self):
        self.assertIn("self.command_tiers=QListWidget()",self.code)
        self.assertIn("self.command_tiers.currentItem().data(Qt.UserRole)",self.code)

if __name__=='__main__':unittest.main()
