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

    def test_github_release_detection(self):
        from unittest.mock import patch
        from app_updater import check_release
        latest={'tag_name':'v36.0.7','draft':False,'prerelease':False,'assets':[
            {'name':'SantaMariaHelper.exe','browser_download_url':'https://github.com/TarasCheresznia/SantaMariaHelper/releases/download/v36.0.7/SantaMariaHelper.exe','size':53400000},
            {'name':'SantaMariaHelper.exe.sha256','browser_download_url':'https://github.com/TarasCheresznia/SantaMariaHelper/releases/download/v36.0.7/SantaMariaHelper.exe.sha256'}]}
        class Response:
            status_code=200
            def raise_for_status(self):pass
            def json(self):return latest
        with patch('app_updater.requests.get',return_value=Response()):
            self.assertEqual(check_release('TarasCheresznia/SantaMariaHelper',current='36.0.6')['version'],'v36.0.7')
            self.assertIsNone(check_release('TarasCheresznia/SantaMariaHelper',current='36.0.7'))
