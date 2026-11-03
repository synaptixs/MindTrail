"""Exercise browser event wiring for discoverable scoped interval navigation."""
from pathlib import Path
import subprocess
import unittest


class ReviewNavigationTests(unittest.TestCase):
    def test_user_navigation_and_blind_identity_contracts(self):
        result=subprocess.run(['node','--test',str(Path(__file__).parent/'review_navigation.test.cjs')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
