"""Isolated regressions with command doubles and rewritten filesystem paths.

These tests do not install OpenWrt or validate real repository signatures.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / 'openwrt/passwall2-apk-repo.sh').read_text()
MAIN = 'luci-app-passwall2'
PW = '26.10.29-r1'
XR = '26.10.21-r1'
COMPONENTS = ['chinadns-ng', 'dnsmasq-full', 'kmod-nft-socket', 'kmod-nft-tproxy', 'kmod-nft-nat']
BUSYBOX = os.environ.get('BUSYBOX', shutil.which('busybox'))
if not BUSYBOX:
    raise RuntimeError('BusyBox ash is required (install busybox or set BUSYBOX).')


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pw-offline-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ['etc/config', 'etc/apk/keys', 'etc/apk/repositories.d', 'lib/apk/repositories.d',
                          'etc/init.d', 'usr/bin', 'usr/share/passwall2', 'usr/share/v2ray', 'sbin', 'tmp', 'root', 'proc', 'bin']:
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.state = dict(installed={MAIN: PW, 'xray-core': '26.9.9-r1', **dict.fromkeys(COMPONENTS, '1.0-r1')},
                          available={MAIN: PW, 'xray-core': XR},
                          closure=[MAIN, 'xray-core', *COMPONENTS, 'libexample'], plan='OK: 1 MiB in 12 packages\n', actions=[],
                          services={s: dict(enabled=s == 'passwall2', running=s == 'passwall2') for s in ['passwall2', 'passwall2_server', 'dnsmasq']})
        self.put('etc/openwrt_release', "DISTRIB_RELEASE='25.12.1'\nDISTRIB_ARCH='x86_64'\n")
        self.put('etc/config/passwall2', "config global\n option enabled '1'\n option node 'saved-node'\n")
        self.put('etc/config/dhcp', 'saved DHCP configuration\n')
        self.put('etc/config/firewall', 'foreign firewall settings\n')
        self.put('etc/apk/world', MAIN + '=26.9.12-r1\nforeign~1.0\n')
        self.put('etc/apk/repositories.d/official.list', 'https://downloads.openwrt.org/example/packages.adb\n')
        self.put('usr/share/passwall2/direct_ip', 'user rules\n')
        self.put('usr/share/v2ray/geoip.dat', 'user geodata\n')
        self.put('usr/share/passwall2/app.sh', 'new code must not be restored from old backup\n')
        self.put('sbin/fw4', '#!/bin/sh\nexit 0\n', True)
        self.put('usr/bin/xray', '#!/bin/sh\necho "Xray 26.10.25 fixture"\n', True)
        self.put('usr/lib/lua/luci/passwall2/util_xray.lua', 'local xray_min_version = "26.7.11"\n')
        mock = REPO / 'tests/openwrt/mock.py'
        for name in ['id', 'df', 'uci', 'apk', 'jsonfilter', 'wget']:
            (self.root / 'bin' / name).symlink_to(mock)
        for svc in self.state['services']:
            (self.root / 'etc/init.d' / svc).symlink_to(mock)
        self.put('proc/101/cmdline', f'{self.root}/tmp/etc/passwall2/fixture\0')
        # Translate literal absolute paths, leaving paths within $WORK and help intact.
        prefixes = r'/(?:etc/|lib/|usr/bin/xray|usr/lib/|usr/share/|sbin/fw4|root/|tmp/|var/lock/|proc/|overlay)'
        lines = []
        for line in SOURCE.splitlines(True):
            if not line.startswith('  sh /root/'):
                line = re.sub(r'(?<![\w}])(' + prefixes + ')', lambda m: str(self.root) + m[1], line)
                line = line.replace(' -C /', f' -C {self.root}')
                line = line.replace('${path#/}', '${path#"$PW_TEST_ROOT"/}')
            lines.append(line)
        self.script = self.root / 'manager.sh'
        # Some BusyBox builds prefer their own applets over PATH executables.
        # Shell functions keep command doubles authoritative in both ash builds.
        doubles = '\n'.join(f'{name}() {{ "$PW_TEST_ROOT/bin/{name}" "$@"; }}'
                            for name in ['id', 'df', 'uci', 'apk', 'jsonfilter', 'wget'])
        translated = ''.join(lines).replace('set -eu\n', 'set -eu\n' + doubles + '\n', 1)
        self.script.write_text(translated.replace(
            '52802b143489214e13b78f96599a147a638205cc22d9dd6d71229504e38ddc00',
            hashlib.sha256(b'offline signing key fixture\n').hexdigest()))
        self.env = {**os.environ, 'PW_TEST_ROOT': str(self.root), 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH']}
        self.env.pop('PROXY', None)

    def put(self, name, content, executable=False):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if executable:
            path.chmod(0o755)

    def plan(self, actions):
        self.state['actions'] = actions
        self.state['plan'] = ''.join(f'({i}/{len(actions)}) {a} {p} ({v})\n' for i, (a, p, v) in enumerate(actions, 1)) + 'OK: 10 MiB in 20 packages\n'

    def run_manager(self, *args, answer='yes\n', snippet=None):
        (self.root / 'state.json').write_text(json.dumps(self.state))
        cmd = [BUSYBOX, 'ash', str(self.script), *args]
        if snippet is not None:
            cmd = [BUSYBOX, 'ash', '-c', 'PW2_SOURCE_ONLY=1 . "$1"; ' + snippet, 'test', str(self.script)]
        result = subprocess.run(cmd, env=self.env, input=answer, text=True, capture_output=True, timeout=20)
        self.state = json.loads((self.root / 'state.json').read_text())
        self.calls = [json.loads(s) for s in (self.root / 'calls.jsonl').read_text().splitlines()] if (self.root / 'calls.jsonl').exists() else []
        self.output = result.stdout + result.stderr
        return result

    def assert_no_mutation(self):
        self.assertEqual(self.state.get('commits', 0), 0)
        self.assertFalse([c for c in self.calls if c[0] in self.state['services'] and c[1][0] not in ('enabled', 'running')])

    def test_default_summary_and_commands(self):
        result = self.run_manager()
        self.assertEqual(result.returncode, 0, self.output)
        for text in [PW, XR, '26.10.25', 'APK installed:', 'Runtime:', 'Result: Уже актуально', 'Commands:', 'sh /root/passwall2-apk-repo.sh check install']:
            self.assertIn(text, self.output)
        self.assert_no_mutation()

    def test_root_guard_is_preserved(self):
        self.state['uid'] = 1000
        result = self.run_manager('install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Run as root.', self.output)
        self.assert_no_mutation()

    def test_absent_passwall_proposes_install(self):
        del self.state['installed'][MAIN]
        self.plan([('Installing', MAIN, PW)])
        result = self.run_manager()
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('Требуется установка', self.output)
        self.assert_no_mutation()

    def test_current_update_does_not_install_or_restart(self):
        result = self.run_manager('update')
        self.assertEqual(result.returncode, 0, self.output)
        self.assert_no_mutation()

    def test_targeted_update_preserves_newer_runtime_and_settings(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.plan([('Upgrading', MAIN, PW)])
        configs = {p: p.read_bytes() for p in (self.root / 'etc/config').iterdir()}
        runtime = (self.root / 'usr/bin/xray').read_bytes()
        result = self.run_manager('update')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.state.get('commits'), 1)
        self.assertEqual(self.state['installed']['xray-core'], '26.9.9-r1')
        self.assertEqual((self.root / 'usr/bin/xray').read_bytes(), runtime)
        for path, content in configs.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertTrue(self.state['services']['passwall2']['enabled'])
        self.assertTrue(self.state['services']['passwall2']['running'])
        self.assertFalse(self.state['services']['passwall2_server']['enabled'])
        self.assertFalse(self.state['services']['passwall2_server']['running'])
        commits = [c for c in self.calls if c[0] == 'apk' and 'add' in c[1] and '--simulate' not in c[1]]
        self.assertEqual(len(commits), 1)
        self.assertIn(MAIN + '=' + PW, commits[0][1])
        self.assertFalse(any(a.startswith('xray-core') for a in commits[0][1]))
        self.assertEqual((self.root / 'etc/apk/world').read_text(), 'foreign~1.0\n' + MAIN + '\n')

    def test_missing_xray_installed(self):
        del self.state['installed']['xray-core']
        (self.root / 'usr/bin/xray').unlink()
        self.plan([('Installing', 'xray-core', XR)])
        result = self.run_manager('install')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.state['installed']['xray-core'], XR)

    def test_missing_binary_current_apk_repaired_once(self):
        self.state['installed']['xray-core'] = XR
        (self.root / 'usr/bin/xray').unlink()
        self.plan([('Reinstalling', 'xray-core', XR)])
        result = self.run_manager('install')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.state.get('commits'), 1)
        self.assertTrue(any(c[0] == 'apk' and 'fix' in c[1] and '--simulate' not in c[1] for c in self.calls))

    def test_check_does_not_change_persistent_files(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('check')
        self.assertEqual(result.returncode, 0, self.output)
        for p, content in before.items():
            self.assertEqual(Path(p).read_bytes(), content)
        self.assertFalse((self.root / 'etc/apk/repositories.d/passwall2.list').exists())
        self.assert_no_mutation()

    def test_network_failure_prints_local_summary_and_commands(self):
        self.state['network_failure'] = True
        result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        for text in ['Не удалось проверить', PW, '26.10.25', 'Commands:']:
            self.assertIn(text, self.output)
        self.assert_no_mutation()

    def test_github_api_failure_is_not_repository_failure(self):
        self.state['api_failure'] = True
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('check')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('Prerelease: unknown', self.output)
        self.assertIn('Обновление доступно', self.output)

    def test_prerelease_requires_explicit_flag(self):
        self.state['prerelease'] = True
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Known prerelease', self.output)
        result = self.run_manager('check', '--allow-prerelease')
        self.assertEqual(result.returncode, 0, self.output)

    def test_excluded_and_sensitive_plans_blocked(self):
        for pkg in ['sing-box', 'luci-i18n-passwall2-ru', 'haproxy', 'shadowsocks-rust-sslocal', 'kernel', 'firewall4', 'netifd', 'kmod-ath9k']:
            with self.subTest(package=pkg):
                self.state['installed'][MAIN] = '26.9.12-r1'
                self.plan([('Upgrading', MAIN, PW), ('Installing', pkg, '1.0-r1')])
                result = self.run_manager('update')
                self.assertNotEqual(result.returncode, 0, self.output)
                self.assertEqual(self.state.get('commits', 0), 0)
                self.assertIn('Error:', self.output)

    def test_unnecessary_xray_change_blocked(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.plan([('Upgrading', MAIN, PW), ('Upgrading', 'xray-core', XR)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('suitable Xray runtime', self.output)
        self.assert_no_mutation()

    def test_custom_missing_xray_refuses_without_changes(self):
        self.state['uci'] = {'passwall2.@global_app[0].xray_file': str(self.root / 'custom/xray')}
        result = self.run_manager('install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('custom path', self.output)
        self.assert_no_mutation()

    def test_status_and_help_never_access_network(self):
        for args in [('status',), ('help',)]:
            (self.root / 'calls.jsonl').unlink(missing_ok=True)
            result = self.run_manager(*args)
            self.assertEqual(result.returncode, 0, self.output)
            self.assertFalse(any(c[0] == 'wget' for c in self.calls))
            for c in self.calls:
                if c[0] == 'apk' and 'list' in c[1]:
                    self.assertIn('--no-network', c[1])

    def test_direct_and_proxy_download(self):
        result = self.run_manager('check')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertFalse(any(c[2] for c in self.calls if c[0] == 'wget' and '-O' in c[1]))
        (self.root / 'calls.jsonl').unlink()
        self.state['direct_failure'] = True
        self.env['PROXY'] = 'http://proxy.invalid:8080'
        result = self.run_manager('check')
        self.assertEqual(result.returncode, 0, self.output)
        downloads = [c for c in self.calls if c[0] == 'wget' and '-O' in c[1]]
        self.assertEqual([c[2] for c in downloads], [False, True, False, True])

    def test_download_failure_precedes_stopping_services(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['download_failure'] = True
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_mutation()

    def test_failed_commit_not_retried_and_configs_restored(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['commit_failure'] = True
        self.env['PROXY'] = 'http://proxy.invalid:8080'
        original = (self.root / 'etc/config/passwall2').read_bytes()
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.get('commits'), 1)
        self.assertEqual((self.root / 'etc/config/passwall2').read_bytes(), original)
        self.assertIn('NOT confirmed', self.output)
        self.assertTrue((self.root / 'root/passwall2-apk-backup/config.tgz').exists())

    def test_dns_variant_replacement_preserves_dhcp(self):
        del self.state['installed']['dnsmasq-full']
        self.state['installed']['dnsmasq'] = '2.90-r1'
        self.state['services']['dnsmasq'] = dict(enabled=True, running=True)
        original = (self.root / 'etc/config/dhcp').read_bytes()
        self.plan([('Purging', 'dnsmasq', '2.90-r1'), ('Installing', 'dnsmasq-full', '2.91-r1')])
        result = self.run_manager('install')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual((self.root / 'etc/config/dhcp').read_bytes(), original)
        self.assertEqual(self.state['services']['dnsmasq'], dict(enabled=True, running=True))
        self.assertNotIn('dnsmasq', self.state['installed'])
        world = (self.root / 'etc/apk/world').read_text()
        self.assertNotIn('!dnsmasq', world)
        self.assertIn('dnsmasq-full\n', world)

    def test_previously_disabled_stopped_main_is_preserved(self):
        self.state['services']['passwall2'] = dict(enabled=False, running=False)
        (self.root / 'proc/101/cmdline').write_bytes(b'')
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.state['services']['passwall2'], dict(enabled=False, running=False))

    def test_startup_failure_remains_failure(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['start_failure'] = True
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.get('commits'), 1)
        self.assertIn('not confirmed', self.output)
        self.assertNotIn('Completed.', self.output)

    def test_unknown_minimum_and_downgrade_are_blocked(self):
        self.state['minimum'] = 'unknown'
        result = self.run_manager('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Cannot confirm Xray minimum', self.output)
        self.state['minimum'] = '26.7.11'
        self.state['installed'][MAIN] = '26.11.1-r1'
        result = self.run_manager('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('downgrade blocked', self.output)
        self.assert_no_mutation()

    def test_install_preserves_older_working_passwall(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        result = self.run_manager('install')
        self.assertEqual(result.returncode, 0, self.output)
        self.assert_no_mutation()

    def test_feed_idempotence_and_foreign_feed_preservation(self):
        official = (self.root / 'etc/apk/repositories.d/official.list').read_text()
        self.put('etc/apk/repositories.d/old.list', 'https://example.org/other/packages.adb\nhttps://master.dl.sourceforge.net/project/openwrt-passwall-build/old/packages.adb\n')
        self.put('lib/apk/repositories.d/vendor.list', 'https://example.org/vendor/packages.adb\nhttps://master.dl.sourceforge.net/project/openwrt-passwall-build/old/packages.adb\n')
        result = self.run_manager(snippet='require_system; prepare; persist_repo; persist_repo')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual((self.root / 'etc/apk/repositories.d/official.list').read_text(), official)
        self.assertEqual((self.root / 'etc/apk/repositories.d/old.list').read_text(), 'https://example.org/other/packages.adb\n')
        self.assertEqual((self.root / 'etc/apk/repositories.d/vendor.list').read_text(), 'https://example.org/vendor/packages.adb\n')
        feed = (self.root / 'etc/apk/repositories.d/passwall2.list').read_text().splitlines()
        self.assertEqual(len(feed), 3)
        self.assertEqual(len(set(feed)), 3)

    def test_malformed_apk_action_stream_refused(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        for plan in ['(1/2) Upgrading luci-app-passwall2 (26.9.12-r1 -> 26.10.29-r1)\nOK: done\n',
                     '(broken) Upgrading luci-app-passwall2 (26.10.29-r1)\nOK: done\n',
                     'OK: done\n']:
            self.state['plan'] = plan
            result = self.run_manager('check')
            self.assertNotEqual(result.returncode, 0, self.output)
            self.assert_no_mutation()

    def test_clean_install_defaults_without_starting_server(self):
        self.state['installed'] = {}
        self.state['services'] = {s: dict(enabled=False, running=False) for s in self.state['services']}
        (self.root / 'proc/101/cmdline').write_bytes(b'')
        (self.root / 'usr/bin/xray').unlink()
        (self.root / 'etc/config/passwall2').unlink()
        self.plan([('Installing', MAIN, PW), ('Installing', 'xray-core', XR),
                   *(('Installing', p, '1.0-r1') for p in COMPONENTS)])
        result = self.run_manager('install')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.state.get('commits'), 1)
        self.assertEqual((self.root / 'etc/config/passwall2').read_text(), 'vendor default fixture\n')
        self.assertEqual((self.root / 'usr/share/v2ray/geoip.dat').read_text(), 'user geodata\n')
        self.assertEqual(self.state['uci']['firewall.passwall2'], 'include')
        for service in self.state['services'].values():
            self.assertEqual(service, dict(enabled=False, running=False))


if __name__ == '__main__':
    unittest.main()
