import tempfile, unittest
from pathlib import Path
from admin_accounts import AccountStore,can_use_command, LEVEL_NAMES

class TestAccounts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'accounts.json'
        self.store=AccountStore(self.path)
    def tearDown(self):self.tmp.cleanup()
    def test_login_case_insensitive_and_hash(self):
        u=self.store.create('Taras_Admin','some-safe-password',8,owner=True)
        self.assertNotIn('some-safe-password',self.path.read_text('utf-8'))
        self.assertIsNotNone(self.store.authenticate('taras_admin','some-safe-password'))
        self.assertIsNone(self.store.authenticate('taras_admin','bad'))
        self.assertEqual(self.store.authenticate('TARAS_ADMIN','some-safe-password')['id'],u['id'])
    def test_duplicate_and_password_length(self):
        self.store.create('first','safe-password',6)
        with self.assertRaises(ValueError):self.store.create('FIRST','other-pass',6)
        with self.assertRaises(ValueError):self.store.create('other','short',2)
    def test_change_level_password_and_delete_protect_owner(self):
        owner=self.store.create('owner','strong-owner-password',8,owner=True)
        other=self.store.create('other','safe-other-password',2)
        self.store.set_level(other['id'],6)
        self.assertEqual(self.store.authenticate('other','safe-other-password')['level'],6)
        self.store.reset_password(other['id'],'fresh-password')
        self.assertIsNone(self.store.authenticate('other','safe-other-password'))
        self.assertIsNotNone(self.store.authenticate('other','fresh-password'))
        with self.assertRaises(ValueError):self.store.delete(owner['id'],other['id'])
        self.store.delete(other['id'],owner['id'])
        self.assertEqual(len(AccountStore(self.path).users),1)
    def test_command_permissions(self):
        for lvl in (2,3,4,5,6,7,8):
            for requirement in (2,3,4,5,6,7,8):
                self.assertEqual(can_use_command(lvl,{'level':requirement}),requirement==lvl)
        self.assertTrue(can_use_command(2, {'level':2,'available_levels':[2,5]}))
        self.assertTrue(can_use_command(5, {'level':2,'available_levels':[2,5]}))
        self.assertFalse(can_use_command(3, {'level':2,'available_levels':[2,5]}))
        self.assertFalse(can_use_command(7, {'level':2,'available_levels':[2,5]}))
        self.assertEqual(LEVEL_NAMES[6],'ЗГА')

    def test_screenshot_level_command_counts(self):
        import json
        commands=json.loads((Path(__file__).resolve().parent.parent/'commands.json').read_text('utf-8'))
        visible=lambda level:[c for c in commands if can_use_command(level,c)]
        self.assertEqual(len(visible(2)),49)
        self.assertEqual(len(visible(5)),133)
        self.assertEqual(len(visible(3)),0)
        self.assertEqual(len(visible(4)),0)
        self.assertEqual(len(visible(6)),0)
        self.assertEqual(len(visible(8)),0)
        self.assertEqual(len(visible(7)),80)
        self.assertIn('/spec',{x['command'] for x in visible(2)})
        self.assertIn('/offwarn',{x['command'] for x in visible(5)})
        self.assertNotIn('/ban',{x['command'] for x in visible(2)})
