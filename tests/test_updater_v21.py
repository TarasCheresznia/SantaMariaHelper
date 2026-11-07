import unittest
from app_updater import version_tuple, GITHUB_REPO
class UpdaterTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(version_tuple('v21.0.1'),(21,0,1))
        self.assertIsNone(version_tuple('nightly'))
        self.assertLess(version_tuple('21.0.0'),version_tuple('v21.0.1'))
    def test_repo(self):
        self.assertTrue(GITHUB_REPO.fullmatch('owner/repo'))
        self.assertFalse(GITHUB_REPO.fullmatch('https://example.com/evil'))
