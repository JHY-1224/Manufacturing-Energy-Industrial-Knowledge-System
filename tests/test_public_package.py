import tempfile,unittest
from pathlib import Path
from scripts.check_public_package import check

class PublicPackageTests(unittest.TestCase):
    def test_reject_runtime_and_private_files_without_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            result=check(Path(directory),['.env','data/runtime/knowledge.sqlite3','.venv/lib/secret.py'])
            self.assertEqual(len(result['errors']),3);self.assertFalse(result['passed']);self.assertFalse(result['private_runtime_read'])

    def test_detect_real_shaped_secrets_not_filter_literals(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=root/'README.md'
            p.write_text('-----BEGIN '+'PRIVATE KEY-----\n'+'A'*64+'\n')
            self.assertIn('private_key',[r['problem'] for r in check(root,['README.md'])['errors']])
            p.write_text("The scanner recognizes the literal -----BEGIN PRIVATE KEY----- marker.")
            self.assertTrue(check(root,['README.md'])['passed'])
            p.write_text('gh'+'p_'+'x'*36)
            self.assertIn('github_token',[r['problem'] for r in check(root,['README.md'])['errors']])

    def test_symlinks_and_renamed_binary_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'README.md').write_bytes(b'abc\0def');(root/'LICENSE').symlink_to(root/'README.md')
            self.assertEqual({r['problem'] for r in check(root,['README.md','LICENSE'])['errors']},{'binary_content','not_regular_file'})
