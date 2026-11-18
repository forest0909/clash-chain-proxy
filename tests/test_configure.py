import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configure', ROOT / 'scripts/configure.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.profiles = self.directory / 'profiles'
        self.profiles.mkdir()
        self.sub = {'uid': 'sub', 'name': 'Demo', 'file': 'sub.yaml',
                    'option': {k: k for k in ('proxies', 'groups', 'rules', 'script')}}
        self.write('sub.yaml', {'proxy-groups': [{'name': 'Upstream'}]})
        self.write('proxies.yaml', {'prepend': [{'type': 'socks5', 'name': 'Other', 'server': 'other'}]})
        self.write('groups.yaml', {'prepend': [{'type': 'select', 'name': 'OtherGroup', 'proxies': ['DIRECT']}]})
        self.write('rules.yaml', {'prepend': ['DOMAIN,keep.example,DIRECT'], 'append': ['MATCH,DIRECT']})
        (self.profiles / 'script.js').write_text('function main(config) { config.originalRan = true; return config; }\n')
        self.config = {'connection': {'server': '192.0.2.1', 'port': 1080, 'username': 'demo', 'password': 'test-only'},
                       'custom_rules': ['DOMAIN-SUFFIX,extra.example,MyIP']}

    def write(self, name, data):
        (self.profiles / name).write_text(yaml.safe_dump(data))

    def plan(self, config=None, prepare=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return m.plan(self.directory, self.sub, self.config if config is None else config, prepare, False)

    def apply(self, result):
        with contextlib.redirect_stdout(io.StringIO()):
            m.apply(self.directory, result, 'sub')

    def test_preview_merge_repeat_and_restore(self):
        before = {p: p.read_bytes() for p in self.profiles.iterdir()}
        result = self.plan()
        self.assertEqual(before, {p: p.read_bytes() for p in self.profiles.iterdir()})
        self.apply(result)
        proxies = m.load(self.profiles / 'proxies.yaml')['prepend']
        self.assertEqual([p['name'] for p in proxies], ['MyAI', 'Other'])
        rules = m.load(self.profiles / 'rules.yaml')
        self.assertIn('DOMAIN,keep.example,DIRECT', rules['prepend'])
        self.assertEqual(rules['prepend'][0], self.config['custom_rules'][0])
        self.assertEqual(rules['append'], ['MATCH,DIRECT'])
        self.assertEqual(result, self.plan())
        self.apply(self.plan())
        backups = list((self.directory / '.claude-chain-backups').iterdir())
        self.assertEqual(len(backups), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            m.restore(self.directory, backups[0].name, False)
            m.restore(self.directory, backups[0].name, True)
        self.assertEqual(before, {p: p.read_bytes() for p in self.profiles.iterdir()})

    def test_manual_credentials_and_custom_rules_survive(self):
        self.apply(self.plan({}, True))
        data = m.load(self.profiles / 'proxies.yaml')
        data['prepend'][0].update(self.config['connection'])
        self.write('proxies.yaml', data)
        rules = m.load(self.profiles / 'rules.yaml')
        rules['prepend'].insert(0, 'DOMAIN,manual.example,MyIP')
        self.write('rules.yaml', rules)
        result = self.plan({})
        self.assertEqual(yaml.safe_load(result[self.profiles / 'proxies.yaml'])['prepend'][0]['password'], 'test-only')
        self.assertIn('DOMAIN,manual.example,MyIP', yaml.safe_load(result[self.profiles / 'rules.yaml'])['prepend'])
        backup = next((self.directory / '.claude-chain-backups').iterdir())
        with self.assertRaises(m.ConfigError):
            m.restore(self.directory, backup.name, True)

    def test_script_preserves_original_and_breaks_feedback(self):
        script = self.plan()[self.profiles / 'script.js'].decode()
        source = script + '''\nconst config = main({"proxy-groups": [
          {name:"MyIP",proxies:["MyAI","Upstream"]},
          {name:"Upstream",proxies:["MyIP","MyAI","Node"]},
          {name:"Empty",proxies:["MyAI"]}]});
          console.log(JSON.stringify(config));'''
        result = subprocess.run(['node', '-e', source], check=True, capture_output=True, text=True)
        config = json.loads(result.stdout)
        self.assertTrue(config['originalRan'])
        self.assertEqual(config['proxy-groups'][0]['proxies'], ['MyAI', 'Upstream'])
        self.assertEqual(config['proxy-groups'][1]['proxies'], ['Node'])
        self.assertEqual(config['proxy-groups'][2]['proxies'], ['DIRECT'])

    def test_missing_credentials_and_name_conflict_stop(self):
        with self.assertRaises(m.ConfigError):
            self.plan({})
        self.write('groups.yaml', {'prepend': [{'name': 'MyIP', 'type': 'select', 'proxies': ['DIRECT']}]})
        with self.assertRaises(m.ConfigError):
            self.plan()

    def test_scan_ten_subscriptions_only_two_candidates_and_no_secrets(self):
        self.apply(self.plan())
        subs = []
        for index in range(10):
            sub = dict(self.sub, uid=f'sub{index}', name=f'Demo {index}', type='remote')
            if index not in (2, 7):
                sub['option'] = {}
            subs.append(sub)
        (self.directory / 'profiles.yaml').write_text(yaml.safe_dump({'items': subs, 'current': 'sub7'}))
        before = {p: p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}
        result = subprocess.run(['python3', str(ROOT / 'scripts/configure.py'),
                                 '--dir', str(self.directory), '--list-configured'],
                                capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data['total'], 10)
        self.assertEqual([s['uid'] for s in data['subscriptions']], ['sub2', 'sub7'])
        self.assertEqual([s['current'] for s in data['subscriptions']], [False, True])
        self.assertFalse(data['subscriptions'][0]['managed'])
        for secret in ('192.0.2.1', 'test-only', 'username', 'password'):
            self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(before, {p: p.read_bytes() for p in self.directory.rglob('*') if p.is_file()})

    def test_scan_managed_pending_and_broken_subscriptions(self):
        self.apply(self.plan({}, True))
        broken = dict(self.sub, uid='broken', name='Broken', option=dict(self.sub['option'], proxies='missing'))
        data = m.configured_subscriptions(self.directory, [broken, self.sub], 'sub')
        self.assertEqual([s['uid'] for s in data['subscriptions']], ['sub'])
        self.assertTrue(data['subscriptions'][0]['managed'])
        self.assertEqual([s['uid'] for s in data['skipped']], ['broken'])
        self.write('groups.yaml', {'prepend': [{'name': 'MyIP', 'type': 'select', 'proxies': ['DIRECT']}]})
        data = m.configured_subscriptions(self.directory, [self.sub], 'sub')
        self.assertEqual(data['subscriptions'], [])
        self.assertEqual(len(data['skipped']), 1)

    def test_scan_invalid_yaml_continues_without_disclosing_content(self):
        self.apply(self.plan())
        (self.profiles / 'bad.yaml').write_text('password: [secret-not-for-output')
        broken = dict(self.sub, uid='broken', option=dict(self.sub['option'], proxies='bad'))
        data = m.configured_subscriptions(self.directory, [broken, self.sub], 'sub')
        self.assertEqual([s['uid'] for s in data['subscriptions']], ['sub'])
        self.assertEqual(len(data['skipped']), 1)
        self.assertNotIn('secret-not-for-output', json.dumps(data))

    def test_scan_rejects_write_and_restore_flags(self):
        for extra in (['--apply'], ['--restore', 'missing'], ['--list']):
            result = subprocess.run(['python3', str(ROOT / 'scripts/configure.py'),
                                     '--dir', str(self.directory), '--list-configured'] + extra,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('--list-configured', result.stderr)


if __name__ == '__main__':
    unittest.main()
