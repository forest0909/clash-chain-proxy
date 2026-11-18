import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package', ROOT / 'tools/package.py')
pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


class PackageTest(unittest.TestCase):
    def test_build_package_includes_only_skill_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'clash-chain-proxy.skill'
            archived = pkg.build_package(output)
            self.assertEqual(
                archived,
                [
                    'clash-chain-proxy/SKILL.md',
                    'clash-chain-proxy/agents/openai.yaml',
                    'clash-chain-proxy/assets/claude-rules.yaml',
                    'clash-chain-proxy/assets/user-config.example.yaml',
                    'clash-chain-proxy/scripts/configure.py',
                ],
            )
            with zipfile.ZipFile(output) as zf:
                self.assertEqual(sorted(zf.namelist()), sorted(archived))


if __name__ == '__main__':
    unittest.main()
