"""Install and manage the MeoW hook without exposing the private endpoint."""
import argparse
import datetime
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

FILES = ('notify.py', 'semantic_notify.py', 'reliable_delivery.py', 'summary.schema.json')
SOURCE = Path(__file__).resolve().parent


def locations():
    home = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))).resolve()
    return home, home/'notifications/meow-notify-skill'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_text(path, text):
    from uuid import uuid4
    temp = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temp.write_text(text, encoding='utf-8')
        if os.name != 'nt':
            temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def notify_command(runtime):
    return [sys.executable, '-X', 'utf8', str(runtime/'notify.py')]


def edit_notify(raw, value):
    """Replace only root notify, accepting multiline TOML arrays and comments."""
    parsed = tomllib.loads(raw)
    lines = raw.splitlines(keepends=True)
    start = end = None
    if 'notify' in parsed:
        for index, line in enumerate(lines):
            # Stop at the first table: nested notify keys must not be touched.
            if re.match(r'^\s*\[', line):
                break
            if re.match(r'^\s*(?:notify|"notify"|\'notify\')\s*=', line):
                start = index
                for stop in range(index+1, len(lines)+1):
                    try:
                        fragment = tomllib.loads(''.join(lines[index:stop]))
                    except tomllib.TOMLDecodeError:
                        continue
                    if 'notify' in fragment:
                        end = stop
                        break
                break
        if start is None or end is None:
            raise ValueError('unsupported_notify_layout')
    replacement = '' if value is None else 'notify = '+json.dumps(value, ensure_ascii=False)+'\n'
    if start is None:
        updated = replacement+raw
    else:
        updated = ''.join(lines[:start])+replacement+''.join(lines[end:])
    result = tomllib.loads(updated)
    expected = dict(parsed)
    if value is None:
        expected.pop('notify', None)
    else:
        expected['notify'] = value
    if result != expected:
        raise ValueError('config_validation_failed')
    return updated


def validate_endpoint(endpoint):
    target = urlsplit(endpoint)
    if (target.scheme != 'https' or target.hostname != 'api.chuckfang.com'
            or target.port not in (None, 443) or target.username or target.password
            or target.fragment or not target.path.strip('/')
            or any(char.isspace() for char in endpoint)):
        raise ValueError('invalid_endpoint')
    return endpoint


def backup(config, runtime):
    folder = runtime/'backups'
    folder.mkdir(exist_ok=True)
    if config.exists():
        name = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.toml.bak'
        target = folder/name
        shutil.copyfile(config, target)
        if os.name != 'nt':
            target.chmod(0o600)


def spawn_worker(runtime):
    flags = (getattr(subprocess, 'CREATE_NO_WINDOW', 0)
             | getattr(subprocess, 'DETACHED_PROCESS', 0)
             | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    subprocess.Popen([sys.executable, '-X', 'utf8', str(runtime/'reliable_delivery.py'), '--worker'],
                     cwd=runtime, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != 'nt')


def install(args):
    home, runtime = locations()
    config = home/'config.toml'
    raw = config.read_text(encoding='utf-8-sig') if config.exists() else ''
    parsed = tomllib.loads(raw)
    previous = parsed.get('notify')
    if previous is not None and (not isinstance(previous, list)
                                 or not all(isinstance(item, str) for item in previous)):
        raise ValueError('unsupported_previous_notify')
    existing = read(runtime/'settings.json') if (runtime/'settings.json').exists() else {}
    command = notify_command(runtime)
    if previous == command:
        previous = existing.get('previous_notify')
    if previous and str(runtime/'notify.py') in previous:
        raise ValueError('recursive_previous_notify')
    endpoint = os.environ.get(args.endpoint_env) or existing.get('endpoint')
    if not endpoint:
        if not sys.stdin.isatty():
            raise ValueError('endpoint_required_use_environment')
        endpoint = getpass.getpass('MeoW HTTPS endpoint (hidden): ')
    validate_endpoint(endpoint.strip())
    updated = edit_notify(raw, command)
    runtime.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        runtime.chmod(0o700)
    backup(config, runtime)
    for name in FILES:
        if (SOURCE/name).resolve() != (runtime/name).resolve():
            shutil.copyfile(SOURCE/name, runtime/name)
    settings = dict(endpoint=endpoint.strip(), mode=args.mode, previous_notify=previous)
    atomic_text(runtime/'settings.json', json.dumps(settings, ensure_ascii=False, indent=2))
    atomic_text(config, updated)
    # Pending, already validated payloads can resume without another model call.
    spawn_worker(runtime)
    print('Installed; mode='+args.mode+'. Restart Codex to reload notification configuration.')


def uninstall():
    home, runtime = locations()
    config = home/'config.toml'
    if not (runtime/'settings.json').exists():
        print('No managed installation found.')
        return
    raw = config.read_text(encoding='utf-8-sig') if config.exists() else ''
    if tomllib.loads(raw).get('notify') != notify_command(runtime):
        raise ValueError('notify_changed_refusing_to_overwrite')
    updated = edit_notify(raw, read(runtime/'settings.json').get('previous_notify'))
    backup(config, runtime)
    atomic_text(config, updated)
    print('Restored previous notifier. Local state and backups retained.')


def status():
    home, runtime = locations()
    settings = read(runtime/'settings.json') if (runtime/'settings.json').exists() else {}
    raw = (home/'config.toml').read_text(encoding='utf-8-sig') if (home/'config.toml').exists() else ''
    counts = {}
    for path in (runtime/'queue').glob('*.json'):
        try:
            state = read(path).get('status')
            state = state if state in ('accepted', 'pending', 'failed') else 'invalid'
        except (OSError, ValueError):
            state = 'unreadable'
        counts[state] = counts.get(state, 0)+1
    mode = settings.get('mode')
    print(json.dumps(dict(installed=tomllib.loads(raw).get('notify') == notify_command(runtime),
                          configured=bool(settings), mode=mode if mode in ('summary','status') else None,
                          queue=counts)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('install')
    setup.add_argument('--mode', choices=('summary', 'status'), default='summary')
    setup.add_argument('--endpoint-env', default='MEOW_ENDPOINT')
    for name in ('status', 'test', 'retry', 'uninstall'):
        sub.add_parser(name)
    args = parser.parse_args()
    if args.command == 'install':
        install(args)
    elif args.command == 'uninstall':
        uninstall()
    elif args.command == 'status':
        status()
    else:
        _, runtime = locations()
        if not (runtime/'settings.json').exists():
            raise ValueError('not_installed')
        sys.path.insert(0, str(runtime))
        import reliable_delivery as delivery
        if args.command == 'test':
            result = delivery.post({'title':'推送测试', 'msg':'通知连接测试，请在手机端确认收到。'}, root=runtime)
            print(json.dumps(result))
            return 0 if result.get('accepted') else 1
        for path in (runtime/'queue').glob('*.json'):
            job = read(path)
            if job.get('status') == 'failed':
                job.update(status='pending', attempts=0, next_attempt=0)
                delivery.save(path, job)
        spawn_worker(runtime)
        print('Pending delivery resumed; failed jobs reset for one bounded retry cycle.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        # Exceptions may include the private endpoint or local paths. Never echo them.
        print('Operation failed: '+type(error).__name__+'. Check local configuration and command usage.', file=sys.stderr)
        sys.exit(1)
