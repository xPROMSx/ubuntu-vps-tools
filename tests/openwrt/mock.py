#!/usr/bin/env python3
"""Offline command doubles. Never invoke a real APK operation or host service."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

root = Path(os.environ['PW_TEST_ROOT'])
state_file = root / 'state.json'
state = json.loads(state_file.read_text())
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps([command, args, bool(os.environ.get('https_proxy'))]) + '\n')


def save():
    state_file.write_text(json.dumps(state))


def runtime(version):
    path = root / 'usr/bin/xray'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "Xray {version} fixture"\n')
    path.chmod(0o755)


def service(name, action):
    if name == 'dnsmasq' and action == '--version':
        print('Compile time options: ' + ('' if state.get('dns_without_nftset') else 'nftset'))
        return 0
    svc = state['services'][name]
    proc = root / 'proc' / {'passwall2': '101', 'passwall2_server': '102', 'dnsmasq': '103'}[name]
    if action in ('enabled', 'running'):
        return 0 if svc[action] else 1
    if action in ('enable', 'disable'):
        svc['enabled'] = action == 'enable'
    if action in ('start', 'restart', 'stop'):
        if action != 'stop' and state.get('start_failure'):
            return 1
        svc['running'] = action != 'stop'
        proc.mkdir(parents=True, exist_ok=True)
        pending = name == 'passwall2' and svc['running'] and (state.get('process_delay', 0) or state.get('no_processes'))
        state['process_pending'] = bool(pending)
        (proc / 'cmdline').write_bytes(
            f'{root}/tmp/etc/{name}/fixture\0'.encode() if svc['running'] and not pending else b'')
    save()
    return 0


if command in state['services']:
    sys.exit(service(command, args[0]))
if command == 'id':
    print(state.get('uid', 0))
elif command == 'df':
    print('Filesystem 1024-blocks Used Available Capacity Mounted on')
    print('fixture 9999999 0 9999999 0% /')
elif command == 'nslookup':
    sys.exit(1 if state.get('dns_query_failure') else 0)
elif command == 'sleep':
    assert args == ['1'], 'Only one-second startup polling is expected'
    state['sleep_calls'] = state.get('sleep_calls', 0) + 1
    if state.get('process_pending') and not state.get('no_processes') and state['sleep_calls'] >= state['process_delay']:
        (root / 'proc/101/cmdline').write_bytes(f'{root}/tmp/etc/passwall2/fixture\0'.encode())
        state['process_pending'] = False
    save()
elif command == 'uci':
    if 'set' in args:
        key, value = args[-1].split('=', 1)
        state.setdefault('uci', {})[key] = value
        save()
    elif 'commit' in args:
        pass
    elif 'changes' in args:
        print(state.get('pending_uci', ''), end='')
    else:
        value = state.get('uci', {}).get(args[-1])
        if value is None:
            sys.exit(1)
        print(value)
elif command == 'jsonfilter':
    data = json.loads(Path(args[args.index('-i') + 1]).read_text())
    key = args[args.index('-e') + 1][2:]
    if key == 'assets[*].name':
        print('\n'.join(a['name'] for a in data['assets']))
        sys.exit(0)
    asset = re.fullmatch(r'assets\[(\d+)\]\.(\w+)', key)
    if asset:
        print(data['assets'][int(asset[1])].get(asset[2], ''))
        sys.exit(0)
    if key not in data:
        sys.exit(1)
    value = data[key]
    print(str(value).lower() if isinstance(value, bool) else value)
elif command == 'wget':
    if '--help' in args:
        print('--tries')
    else:
        via_proxy = bool(os.environ.get('https_proxy'))
        if state.get('network_failure') or (state.get('direct_failure') and not via_proxy):
            sys.exit(1)
        dest = Path(args[args.index('-O') + 1])
        if 'api.github.com' in args[-1]:
            if state.get('api_failure'):
                sys.exit(1)
            payload = b'github apk fixture\n'
            name = 'luci-app-passwall2-26.10.29-r1.apk'
            digest = hashlib.sha256(payload).hexdigest()
            if state.get('bad_digest'):
                digest = '0' * 64
            data = dict(tag_name='26.10.29-1', prerelease=state.get('prerelease', False), draft=False,
                        assets=[dict(name=name, size=len(payload), digest='sha256:' + digest,
                                     browser_download_url='https://github.com/Openwrt-Passwall/openwrt-passwall2/releases/download/26.10.29-1/' + name)])
            dest.write_text(json.dumps(data))
        elif 'github.com/Openwrt-Passwall/' in args[-1]:
            if state.get('download_failure'):
                sys.exit(1)
            dest.write_bytes(b'github apk fixture\n')
        else:
            dest.write_text('offline signing key fixture\n')
elif command == 'apk':
    if '--version' in args:
        print('apk-tools 3.0.5, compiled for fixture.')
        sys.exit(0)
    # Preserve subcommand options while removing manager global options.
    clean = []
    i = 0
    while i < len(args):
        if args[i] in ('--repositories-file', '--keys-dir', '--cache-dir', '--timeout'):
            i += 2
        elif args[i] in ('--no-network', '--allow-untrusted'):
            i += 1
        else:
            clean.append(args[i])
            i += 1
    op, *tail = clean
    installed = state['installed']
    available = state['available']
    if op == 'version':
        if '--check' in tail:
            sys.exit(0 if re.fullmatch(r'[0-9]+(?:\.[0-9]+)*(?:-r[0-9]+)?', tail[-1]) else 1)
        def version(v):
            return tuple(map(int, re.split(r'\.|-r', v)))
        a, b = map(version, tail[-2:])
        print('<' if a < b else '>' if a > b else '=')
    elif op == 'list':
        for pkg, ver in installed.items():
            print(pkg, ver)
    elif op == 'update':
        if state.get('network_failure') or (state.get('direct_failure') and not os.environ.get('https_proxy')):
            sys.exit(1)
    elif op == 'policy':
        pkg = tail[-1]
        if pkg in available:
            print(f'{pkg} policy:\n  {available[pkg]}:\n    https://master.dl.sourceforge.net/project/openwrt-passwall-build/releases/packages-25.12/x86_64/passwall2/packages.adb')
    elif op == 'fetch':
        if '=' in tail[-1]:
            print('ERROR: unable to select package', file=sys.stderr)
            sys.exit(1)
        assert tail[-1] == 'luci-app-passwall2', tail
        if '--url' in tail:
            # APK 3.0.5 can skip URL output when a local archive already exists.
            if '--simulate' not in tail and (Path.cwd() / 'luci-app-passwall2-26.10.29-r1.apk').exists():
                sys.exit(0)
            print(state.get('signed_url', 'https://master.dl.sourceforge.net/project/openwrt-passwall-build/releases/packages-25.12/x86_64/passwall2/luci-app-passwall2-26.10.29-r1.apk'))
        else:
            assert '--output' in tail and '--simulate' not in tail, tail
            if state.get('bad_feed_signature') or state.get('corrupt_feed_package') or state.get('feed_download_failure'):
                print('ERROR: package identity verification/download failed', file=sys.stderr)
                sys.exit(1)
            dest = Path(tail[tail.index('--output') + 1])
            (dest / 'luci-app-passwall2-26.10.29-r1.apk').write_text('index-verified APK fixture')
    elif op == 'adbdump':
        print('info:\n  name: luci-app-passwall2\n  version: ' + state.get('signed_version', '26.10.29-r1'))
    elif op == 'extract':
        if state.get('feed_extract_failure'):
            print('UNTRUSTED signature', file=sys.stderr)
            sys.exit(1)
        dest = Path(tail[tail.index('--destination') + 1]) / 'usr/lib/lua/luci/passwall2/util_xray.lua'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f'local xray_min_version = "{state.get("minimum", "26.7.11")}"\n')
    elif op == 'query':
        fields = tail[tail.index('--fields') + 1]
        if fields == 'installed-size':
            print('Installed-Size: 4096')
        else:
            for pkg in state['closure']:
                print('Name: ' + pkg)
    elif op == 'cache':
        if state.get('download_failure'):
            sys.exit(1)
        cache = root / 'etc/apk/cache'
        cache.mkdir(exist_ok=True)
        for arg in tail:
            if '=' in arg:
                name, version = arg.split('=', 1)
                (cache / f'{name}-{version}.12345678.apk').write_text('signed dependency fixture')
        state['cached'] = True
        save()
    elif op == 'verify':
        sys.exit(1 if state.get('bad_dependency_signature') or state.get('bad_feed_signature') or state.get('standalone_untrusted') else 0)
    elif op in ('add', 'fix'):
        if '--simulate' in tail:
            if state.get('offline_needs_cache') and '--no-network' in args and not state.get('cached'):
                sys.exit(1)
            print(state['plan'], end='')
            sys.exit(state.get('simulate_failure', 0))
        state['commits'] = state.get('commits', 0) + 1
        save()
        if state.get('commit_failure'):
            sys.exit(1)
        for action, pkg, ver in state['actions']:
            if action in ('Purging', 'Removing'):
                installed.pop(pkg, None)
            else:
                installed[pkg] = '0.0-r1' if state.get('post_version_mismatch') and pkg == 'luci-app-passwall2' else ver
                if pkg == 'xray-core':
                    runtime(ver.split('-r')[0])
                if pkg == 'luci-app-passwall2':
                    (root / 'usr/share/passwall2/0_default_config').write_text('vendor default fixture\n')
                    for svc in ('passwall2', 'passwall2_server'):
                        init = root / 'etc/init.d' / svc
                        if not init.exists():
                            init.symlink_to(Path(__file__).resolve())
        # Model unwanted postinst effects: manager must restore original settings.
        (root / 'etc/config/passwall2').write_text('postinst changed configuration\n')
        (root / 'etc/config/dhcp').write_text('postinst changed DHCP\n')
        touched = {p for _, p, _ in state['actions']}
        if 'luci-app-passwall2' in touched:
            for svc in ('passwall2', 'passwall2_server'):
                state['services'][svc]['enabled'] = True
        if 'dnsmasq-full' in touched:
            state['services']['dnsmasq']['enabled'] = True
        for arg in tail:
            if not arg.startswith('--'):
                world = root / 'etc/apk/world'
                name = 'luci-app-passwall2' if arg.endswith('.apk') else re.split(r'[@=<>~]', arg.lstrip('!'))[0]
                lines = [s for s in world.read_text().splitlines() if re.split(r'[@=<>~]', s.lstrip('!'))[0] != name]
                entry = name + '><Q1fixture=' if arg.endswith('.apk') else arg
                world.write_text('\n'.join([*lines, entry]) + '\n')
        save()
    else:
        raise AssertionError(f'Unexpected APK command: {clean}')
else:
    raise AssertionError(f'Unexpected double: {command}')
