import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging'))
from portable_privacy import assert_portable_privacy
from portable_builder import _copy_files, RUNTIME_EXCLUDED_PARTS


class PortablePrivacyTests(unittest.TestCase):
    def test_copy_omits_conda_history_and_metadata_but_preserves_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source', root / 'target'
            for name in ('conda-meta/history', 'conda-meta/python.json',
                         'Lib/site-packages/demo.dist-info/direct_url.json',
                         'python.exe', 'Lib/site-packages/demo/LICENSE'):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('data', encoding='utf-8')
            _copy_files(source, target, excluded_parts=RUNTIME_EXCLUDED_PARTS)
            self.assertFalse((target / 'conda-meta').exists())
            self.assertFalse(list(target.rglob('direct_url.json')))
            self.assertTrue((target / 'python.exe').exists())
            self.assertTrue((target / 'Lib/site-packages/demo/LICENSE').exists())
            assert_portable_privacy(target)

    def test_detects_plain_escaped_user_paths_and_local_install_urls(self):
        for content in (r'C:\Users\private-user\cache',
                        r'{"source":"C:\\Users\\private-user\\cache"}',
                        'file:///C:/local-build/wheel', 'file:///home/private-user/wheel'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'direct_url.json').write_text(content, encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'privacy checks failed') as error:
                    assert_portable_privacy(root)
                self.assertNotIn('private-user', str(error.exception))

    def test_rejects_conda_history_cookies_and_task_history(self):
        for name in ('runtime/conda-meta/history', 'cookies.txt', 'app/history.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('[]', encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'privacy checks failed'):
                    assert_portable_privacy(root)

    def test_build_root_checked_and_public_attribution_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / 'LICENSE.txt'
            metadata.write_text('Author <author@example.org> https://github.com/project', encoding='utf-8')
            assert_portable_privacy(root, private_roots=(r'I:\private-build',))
            metadata.write_text(r'I:\private-build\environment', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'local personal/build path'):
                assert_portable_privacy(root, private_roots=(r'I:\private-build',))

    def test_upstream_build_paths_and_documented_examples_are_not_our_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'upstream.py').write_text(
                'file:///c:/a/b C:/Users/runneradmin/build C:/Users/MyUser/example', encoding='utf-8')
            (root / 'sbom.json').write_text('path+file:///D:/a/project/build', encoding='utf-8')
            assert_portable_privacy(root)
            with self.assertRaisesRegex(RuntimeError, 'local personal/build path'):
                assert_portable_privacy(root, private_roots=('C:/Users/MyUser',))
