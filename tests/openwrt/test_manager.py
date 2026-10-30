"""Isolated regressions with command doubles and rewritten filesystem paths.

These tests do not install OpenWrt or validate real repository signatures.
"""
import hashlib
import json
import os
import pty
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
COMPONENTS = ['geoview', 'kmod-nft-socket', 'kmod-nft-tproxy']
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
                          uci={'passwall2.@global[0].enabled': '1', 'passwall2.@global[0].node': 'saved-node',
                               'passwall2.saved-node': 'nodes', 'passwall2.saved-node.type': 'Xray'},
                          closure=[MAIN, 'xray-core', *COMPONENTS, 'libexample'], plan='OK: 1 MiB in 12 packages\n', actions=[],
                          services={s: dict(enabled=s == 'passwall2', running=s == 'passwall2') for s in ['passwall2', 'passwall2_server', 'dnsmasq']})
        self.put('etc/openwrt_release', "DISTRIB_RELEASE='25.12.1'\nDISTRIB_ARCH='x86_64'\n")
        self.put('etc/config/passwall2', "config global\n option enabled '1'\n option node 'saved-node'\nconfig nodes 'saved-node'\n option type 'Xray'\n")
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
        for name in ['id', 'df', 'uci', 'apk', 'jsonfilter', 'wget', 'sleep', 'dnsmasq', 'nslookup']:
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
                            for name in ['id', 'df', 'uci', 'apk', 'jsonfilter', 'wget', 'sleep', 'dnsmasq', 'nslookup'])
        doubles += '\ntimeout() { shift; "$@"; }'
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

    def asu_install(self):
        self.state['installed'].pop(MAIN, None)
        for svc in ('passwall2', 'passwall2_server'):
            (self.root / 'etc/init.d' / svc).unlink(missing_ok=True)
            self.state['services'][svc] = dict(enabled=False, running=False)
        (self.root / 'proc/101/cmdline').write_bytes(b'')
        self.plan([('Installing', MAIN, PW)])

    def run_manager(self, *args, answer='yes\n', snippet=None, cwd=None):
        (self.root / 'state.json').write_text(json.dumps(self.state))
        cmd = [BUSYBOX, 'ash', str(self.script), *args]
        if snippet is not None:
            cmd = [BUSYBOX, 'ash', '-c', 'PW2_SOURCE_ONLY=1 . "$1"; ' + snippet, 'test', str(self.script)]
        result = subprocess.run(cmd, env=self.env, input=answer, text=True, capture_output=True, timeout=20, cwd=cwd)
        self.state = json.loads((self.root / 'state.json').read_text())
        self.calls = [json.loads(s) for s in (self.root / 'calls.jsonl').read_text().splitlines()] if (self.root / 'calls.jsonl').exists() else []
        self.output = result.stdout + result.stderr
        return result

    def assert_no_mutation(self):
        self.assertEqual(self.state.get('commits', 0), 0)
        self.assertFalse([c for c in self.calls if c[0] in self.state['services'] and c[1][0] not in ('enabled', 'running', '--version')])

    def test_noninteractive_no_arguments_is_safe(self):
        result = self.run_manager()
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('терминал', self.output)
        self.assertEqual(self.calls, [])

    def test_menu_invalid_input_and_exit_do_not_touch_system(self):
        (self.root / 'state.json').write_text(json.dumps(self.state))
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen([BUSYBOX, 'ash', str(self.script)], stdin=slave, stdout=slave, stderr=slave, env=self.env)
            os.close(slave)
            os.write(master, b'x\n4\n')
            proc.wait(timeout=10)
            output = b''
            while True:
                try:
                    block = os.read(master, 8192)
                    if not block:
                        break
                    output += block
                except OSError:
                    break
            text = output.decode()
            self.assertEqual(proc.returncode, 0, text)
            for label in ('PASSWALL2 MANAGER', '1. Установка', '2. Обновление', '3. Проверка обновлений', '4. Выход', 'Некорректный выбор'):
                self.assertIn(label, text)
            self.assertFalse((self.root / 'calls.jsonl').exists())
        finally:
            os.close(master)

    def test_menu_check_returns_and_eof_exits(self):
        (self.root / 'state.json').write_text(json.dumps(self.state))
        self.state['installed'][MAIN] = '26.9.12-r1'
        (self.root / 'state.json').write_text(json.dumps(self.state))
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen([BUSYBOX, 'ash', str(self.script)], stdin=slave, stdout=slave, stderr=slave, env=self.env)
            os.close(slave)
            os.write(master, b'3\n\n4\n')
            proc.wait(timeout=15)
            output = b''
            while True:
                try:
                    block = os.read(master, 8192)
                    if not block:
                        break
                    output += block
                except OSError:
                    break
            text = output.decode()
            self.assertEqual(proc.returncode, 0, text)
            self.assertIn('Доступно обновление', text)
            self.assertIn('Нажмите Enter', text)
            self.assertGreaterEqual(text.count('PASSWALL2 MANAGER'), 2)
            self.assertEqual(json.loads((self.root / 'state.json').read_text()).get('commits', 0), 0)
        finally:
            os.close(master)

        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen([BUSYBOX, 'ash', str(self.script)], stdin=slave, stdout=slave, stderr=slave, env=self.env)
            os.close(slave)
            os.write(master, b'\x04')
            proc.wait(timeout=10)
            self.assertEqual(proc.returncode, 0)
        finally:
            os.close(master)

    def test_check_reports_latest_without_feed_or_mutation(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        result = self.run_manager('check')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('Доступно обновление', self.output)
        self.assertIn(PW, self.output)
        self.assertIn('26.10.25', self.output)
        self.assert_no_mutation()
        self.assertFalse(any(c[0] == 'apk' and any(x in c[1] for x in ('update', 'add', 'fix')) for c in self.calls))
        self.assertFalse(any(c[0] == 'wget' and 'sourceforge' in c[1][-1] for c in self.calls))

    def test_api_error_is_not_current(self):
        self.state['api_failure'] = True
        result = self.run_manager('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Не удалось проверить обновления', self.output)
        self.assertIn('26.10.25', self.output)
        self.assertNotIn('Уже актуально', self.output)
        self.assert_no_mutation()

    def test_missing_and_newer_versions_do_not_offer_downgrade(self):
        del self.state['installed'][MAIN]
        result = self.run_manager('check')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('не установлен', self.output)
        self.state['installed'][MAIN] = '26.11.1-r1'
        result = self.run_manager('check')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('новее', self.output)
        self.assert_no_mutation()

    def test_bad_digest_blocks_update_before_simulation(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['bad_digest'] = True
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SHA-256', self.output)
        self.assertFalse(any(c[0] == 'apk' and 'add' in c[1] for c in self.calls))
        self.assert_no_mutation()

    def test_incompatible_xray_blocks_update(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['minimum'] = '27.0.0'
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('несовместим', self.output)
        self.assert_no_mutation()

    def test_dnsmasq_replacement_preserves_dhcp_and_service(self):
        self.state['dns_without_nftset'] = True
        self.state['installed']['dnsmasq'] = '2.90-r1'
        self.state['services']['dnsmasq'] = dict(enabled=True, running=True)
        self.state['closure'].append('dnsmasq-full')
        original = (self.root / 'etc/config/dhcp').read_bytes()
        self.plan([('Purging', 'dnsmasq', '2.90-r1'), ('Installing', 'dnsmasq-full', '2.91-r1')])
        result = self.run_manager('install')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('dnsmasq-full', self.output)
        self.assertEqual((self.root / 'etc/config/dhcp').read_bytes(), original)
        self.assertEqual(self.state['services']['dnsmasq'], dict(enabled=True, running=True))
        self.assertTrue(any(c[0] == 'nslookup' for c in self.calls))

    def test_dns_query_failure_is_not_reported_as_success(self):
        self.state['dns_without_nftset'] = True
        self.state['dns_query_failure'] = True
        self.state['installed']['dnsmasq'] = '2.90-r1'
        self.state['services']['dnsmasq'] = dict(enabled=True, running=True)
        self.state['closure'].append('dnsmasq-full')
        self.plan([('Purging', 'dnsmasq', '2.90-r1'), ('Installing', 'dnsmasq-full', '2.91-r1')])
        result = self.run_manager('install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DNS query failed', self.output)
        self.assertNotIn('Готово.', self.output)
        self.assertTrue((self.root / 'root/passwall2-apk-backup/config.tgz').exists())

    def test_missing_library_is_cached_then_plan_is_rechecked(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['offline_needs_cache'] = True
        self.plan([('Installing', 'libexample', '1.0-r1'), ('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertTrue(self.state.get('cached'))
        self.assertEqual(self.state['installed']['libexample'], '1.0-r1')
        self.assertTrue(any(c[0] == 'apk' and 'cache' in c[1] and 'libexample=1.0-r1' in c[1] for c in self.calls))
        self.assertTrue(any(c[0] == 'apk' and 'verify' in c[1] and '--allow-untrusted' not in c[1] for c in self.calls))

    def test_unsigned_dependency_blocks_update(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['offline_needs_cache'] = True
        self.state['bad_dependency_signature'] = True
        self.plan([('Installing', 'libexample', '1.0-r1'), ('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Подпись зависимости не подтверждена', self.output)
        self.assert_no_mutation()

    def test_signed_fetch_uses_name_url_despite_exact_fetch_failure(self):
        self.asu_install()
        self.state['standalone_untrusted'] = True
        result = self.run_manager('install')
        self.assertEqual(result.returncode, 0, self.output)
        fetches = [c for c in self.calls if c[0] == 'apk' and 'fetch' in c[1]]
        self.assertTrue(fetches)
        self.assertTrue(all(c[1][-1] == MAIN and '--keys-dir' in c[1] and '--allow-untrusted' not in c[1] for c in fetches))
        self.assertTrue(any('--url' not in c[1] and '--output' in c[1] for c in fetches))
        self.assertFalse(any(c[0] == 'apk' and 'verify' in c[1] for c in self.calls))
        extracts = [c for c in self.calls if c[0] == 'apk' and 'extract' in c[1]]
        self.assertEqual(len(extracts), 1)
        args = extracts[0][1]
        self.assertIn('--allow-untrusted', args)
        self.assertIn('--keys-dir', args)
        self.assertEqual(Path(args[args.index('--destination') + 1]).name, 'extracted')
        self.assertTrue(all('extract' in c[1] for c in self.calls
                            if c[0] == 'apk' and '--allow-untrusted' in c[1]))

    def test_signed_url_with_existing_local_apk_uses_simulation_and_output_dir(self):
        archive = self.root / (MAIN + '-' + PW + '.apk')
        archive.write_bytes(b'x' * 366525)
        self.asu_install()
        before = set(self.root.glob('*.apk'))
        result = self.run_manager('install', cwd=self.root)
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(set(self.root.glob('*.apk')), before)
        self.assertEqual(archive.read_bytes(), b'x' * 366525)
        fetches = [c for c in self.calls if c[0] == 'apk' and 'fetch' in c[1]]
        self.assertTrue(fetches)
        for _, args, _ in fetches:
            if '--url' in args:
                self.assertIn('--simulate', args)
            else:
                self.assertNotIn('--simulate', args)
            directory = Path(args[args.index('--output') + 1])
            self.assertNotEqual(directory, self.root)
            self.assertEqual(directory.name, 'package')
            self.assertTrue(directory.is_relative_to(self.root / 'tmp'))

    def test_check_result_colors_for_available_and_current(self):
        snippet = ("local_status; latest_release() { PW_AVAIL=26.10.29-r1; }; "
                   "GREEN=$(printf '\\033[32m'); RESET=$(printf '\\033[0m'); check_release")
        self.state['installed'][MAIN] = '26.9.12-r1'
        result = self.run_manager(snippet=snippet)
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('\x1b[32m  Итог: Доступно обновление до ' + PW, self.output)
        self.state['installed'][MAIN] = PW
        result = self.run_manager(snippet=snippet)
        self.assertEqual(result.returncode, 0, self.output)
        line = next(line for line in result.stdout.splitlines() if 'Итог:' in line)
        self.assertEqual(line, '  Итог: Уже актуально.')

    def test_signed_fetch_rejects_wrong_source_version_and_signature(self):
        for setting in ({'signed_url': 'https://example.org/luci-app-passwall2-26.10.29-r1.apk'},
                        {'signed_version': '26.9.12-r1'}, {'bad_feed_signature': True},
                        {'corrupt_feed_package': True}, {'feed_download_failure': True},
                        {'feed_extract_failure': True}):
            with self.subTest(setting=setting):
                self.state.update(setting)
                self.asu_install()
                result = self.run_manager('install')
                self.assertNotEqual(result.returncode, 0, self.output)
                self.assert_no_mutation()
                for key in setting:
                    self.state.pop(key)

    def test_late_package_postcheck_failure_reports_unconfirmed_recovery(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['post_version_mismatch'] = True
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Service recovery is NOT confirmed', self.output)
        self.assertNotIn('Готово.', self.output)
        self.assertTrue((self.root / 'root/passwall2-apk-backup/config.tgz').exists())

    def test_root_guard_is_preserved(self):
        self.state['uid'] = 1000
        result = self.run_manager('install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Run as root.', self.output)
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
        self.assertTrue(any(x.endswith(MAIN + '-' + PW + '.apk') for x in commits[0][1]))
        self.assertIn('--allow-untrusted', commits[0][1])
        simulations = [c for c in self.calls if c[0] == 'apk' and 'add' in c[1] and '--simulate' in c[1]]
        self.assertTrue(any(any(x.endswith(MAIN + '-' + PW + '.apk') for x in c[1]) for c in simulations))
        self.assertFalse(any(a.startswith('xray-core') for a in commits[0][1]))
        self.assertEqual((self.root / 'etc/apk/world').read_text(), 'foreign~1.0\n' + MAIN + '\n')
        self.assertEqual(self.state.get('sleep_calls', 0), 0)

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

    def test_excluded_and_sensitive_plans_blocked(self):
        for pkg in ['sing-box', 'luci-i18n-passwall2-ru', 'haproxy', 'shadowsocks-rust-sslocal', 'kernel', 'firewall4', 'netifd', 'kmod-ath9k']:
            with self.subTest(package=pkg):
                self.state['installed'][MAIN] = '26.9.12-r1'
                self.plan([('Upgrading', MAIN, PW), ('Installing', pkg, '1.0-r1')])
                result = self.run_manager('update')
                self.assertNotEqual(result.returncode, 0, self.output)
                self.assertEqual(self.state.get('commits', 0), 0)
                self.assertIn('Ошибка:', self.output)

    def test_unnecessary_xray_change_blocked(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.plan([('Upgrading', MAIN, PW), ('Upgrading', 'xray-core', XR)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Небезопасное дополнительное действие', self.output)
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
        self.assertEqual([c[2] for c in downloads], [False, True])

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

    def test_previously_disabled_stopped_main_is_preserved(self):
        for enabled, running in [(False, False), (True, False), (False, True)]:
            with self.subTest(enabled=enabled, running=running):
                expected = dict(enabled=enabled, running=running)
                self.state['services']['passwall2'] = expected.copy()
                (self.root / 'proc/101/cmdline').write_bytes(f'{self.root}/tmp/etc/passwall2/fixture\0'.encode() if running else b'')
                self.state['installed'][MAIN] = '26.9.12-r1'
                self.plan([('Upgrading', MAIN, PW)])
                result = self.run_manager('update')
                self.assertEqual(result.returncode, 0, self.output)
                self.assertEqual(self.state['services']['passwall2'], expected)

    def test_startup_failure_remains_failure(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['start_failure'] = True
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.get('commits'), 1)
        self.assertIn('not confirmed', self.output)
        self.assertIn('Service recovery is NOT confirmed', self.output)
        self.assertNotIn('Готово.', self.output)

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
            result = self.run_manager('update')
            self.assertNotEqual(result.returncode, 0, self.output)
            self.assert_no_mutation()

    def test_clean_install_defaults_without_starting_server(self):
        self.state['installed'] = {}
        self.state['services'] = {s: dict(enabled=False, running=False) for s in self.state['services']}
        (self.root / 'proc/101/cmdline').write_bytes(b'')
        (self.root / 'usr/bin/xray').unlink()
        (self.root / 'etc/config/passwall2').unlink()
        for svc in ('passwall2', 'passwall2_server'):
            (self.root / 'etc/init.d' / svc).unlink()
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

    def test_asu_restores_saved_active_proxy_without_starting_server(self):
        self.asu_install()
        original = (self.root / 'etc/config/passwall2').read_bytes()
        result = self.run_manager('install')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual((self.root / 'etc/config/passwall2').read_bytes(), original)
        self.assertEqual(self.state['services']['passwall2'], dict(enabled=True, running=True))
        self.assertEqual(self.state['services']['passwall2_server'], dict(enabled=False, running=False))
        self.assertFalse(any(c[0] == 'passwall2_server' and c[1][0] in ('start', 'restart') for c in self.calls))
        operations = [c for c in self.calls if c[0] == 'apk' and 'add' in c[1]]
        self.assertTrue(operations)
        self.assertTrue(all('--allow-untrusted' not in c[1] for c in operations))
        self.assertTrue(all('--keys-dir' in c[1] for c in operations))

    def test_install_rejects_optional_core_required_by_plan(self):
        self.asu_install()
        self.state['closure'].append('sing-box')
        self.plan([('Installing', MAIN, PW), ('Installing', 'sing-box', '1.0-r1')])
        result = self.run_manager('install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Excluded optional component', self.output)
        self.assert_no_mutation()

    def test_asu_disabled_or_incomplete_config_does_not_start_proxy(self):
        for override in [
            {'passwall2.@global[0].enabled': '0'},
            {'passwall2.@global[0].node': ''},
            {'passwall2.saved-node': 'other-section-type'},
            {'passwall2.saved-node.type': ''},
        ]:
            with self.subTest(override=override):
                self.asu_install()
                self.state['uci'] = {'passwall2.@global[0].enabled': '1', 'passwall2.@global[0].node': 'saved-node',
                                     'passwall2.saved-node': 'nodes', 'passwall2.saved-node.type': 'Xray', **override}
                result = self.run_manager('install')
                self.assertEqual(result.returncode, 0, self.output)
                for svc in ('passwall2', 'passwall2_server'):
                    self.assertEqual(self.state['services'][svc], dict(enabled=False, running=False))

    def test_delayed_passwall_processes_finish_before_timeout(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['process_delay'] = 2
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertEqual(self.state['sleep_calls'], 2)

    def test_missing_passwall_processes_fail_even_with_unrelated_xray(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['no_processes'] = True
        self.put('proc/201/cmdline', f'{self.root}/usr/bin/xray\0run\0-config\0unrelated.json\0')
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state['sleep_calls'], 15)
        self.assertEqual(self.state['commits'], 1)
        self.assertEqual(sum(c[0] == 'passwall2' and c[1][0] == 'restart' for c in self.calls), 1)
        self.assertIn('15 second timeout', self.output)
        self.assertNotIn('Готово.', self.output)
        self.assertTrue((self.root / 'root/passwall2-apk-backup/start-passwall2.log').exists())

    def test_all_proxy_modes_disabled_allow_processless_restart(self):
        self.state['installed'][MAIN] = '26.9.12-r1'
        self.state['uci']['passwall2.@global[0].enabled'] = '0'
        self.state['no_processes'] = True
        self.plan([('Upgrading', MAIN, PW)])
        result = self.run_manager('update')
        self.assertEqual(result.returncode, 0, self.output)
        self.assertIn('no resident processes are expected', self.output)
        self.assertEqual(self.state.get('sleep_calls', 0), 0)


if __name__ == '__main__':
    unittest.main()
