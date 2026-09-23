import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_build_one_stop_package import builder


class DownloadFallbackTest(unittest.TestCase):
    def test_forbidden_uses_same_path_and_checks_hash(self):
        url = 'https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/pool/test.deb'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.deb'
            error = builder.urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)
            with patch.object(builder.urllib.request, 'urlopen', side_effect=[error, io.BytesIO(b'valid')]) as fetch:
                builder.download(url, path, hashlib.sha256(b'valid').hexdigest(), False)
            self.assertEqual(path.read_bytes(), b'valid')
            self.assertEqual(fetch.call_args.args[0].full_url, 'https://ports.ubuntu.com/ubuntu-ports/pool/test.deb')

    def test_bad_hash_never_leaves_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.deb'
            with patch.object(builder.urllib.request, 'urlopen', return_value=io.BytesIO(b'wrong')):
                with self.assertRaisesRegex(builder.BuildError, 'SHA256 mismatch'):
                    builder.download('https://example.org/test.deb', path, '0' * 64, False)
            self.assertFalse(path.exists())

    def test_unpinned_download_does_not_switch_hosts(self):
        url = 'https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/pool/test.deb'
        with tempfile.TemporaryDirectory() as directory:
            error = builder.urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)
            with patch.object(builder.urllib.request, 'urlopen', side_effect=error) as fetch:
                with self.assertRaises(builder.BuildError):
                    builder.download(url, Path(directory) / 'test.deb', '', False)
            self.assertEqual(fetch.call_count, 1)
