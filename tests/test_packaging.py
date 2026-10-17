"""Packaging is source-only and side-effect free; runtime actions stay explicit."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from demo.prepare import prepare, verify_prepared


class PackagingTests(unittest.TestCase):
    def test_prepare_verify_no_process_or_network(self):
        with tempfile.TemporaryDirectory() as tmp, patch('subprocess.run', side_effect=AssertionError('no subprocess')), \
                patch('socket.socket.connect', side_effect=AssertionError('no network')):
            root = Path(tmp).resolve()/'context'
            result = prepare(root)
            self.assertEqual(result['model_calls'],0)
            verify_prepared(root)
            self.assertFalse((root/'attempt').exists())
            with self.assertRaises(FileExistsError): prepare(root)
            (root/'unexpected.txt').write_text('extra')
            with self.assertRaises(ValueError): verify_prepared(root)

    def test_changed_file_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'context'; prepare(root)
            (root/'run.sh').write_text('changed')
            with self.assertRaises(ValueError): verify_prepared(root)

    def test_symlink_output_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); (root/'link').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError): prepare(root/'link'/'context')
