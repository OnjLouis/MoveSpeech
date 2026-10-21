#!/usr/bin/env python3
import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from device import Device, atomic, secure
from engine import Operation
from verify import load_package

VERSION = '1.0.0'
TERMINAL_PHASES = {'installed', 'uninstalled', 'failed'}


def write_status(device, operation_id, phase, **values):
    data = dict(operation=operation_id, phase=phase, time=time.time(), **values)
    atomic(device.root / 'operation.json', (json.dumps(data) + '\n').encode('utf-8'), 0o600)
    return data


def cleanup_stage(stage, manifest):
    secure(stage)
    expected = set(manifest['files']) | {'release.json', 'release.sig'}
    if {path.name for path in stage.iterdir()} != expected:
        raise RuntimeError('Unexpected staging contents; retained for inspection')
    for path in stage.iterdir():
        secure(path)
        if not path.is_file():
            raise RuntimeError('Unexpected staging file type')
    for path in stage.iterdir():
        path.unlink()
    stage.rmdir()


def worker(args, package):
    import fcntl
    device = Device()
    device.prepare()
    if (package.parent != device.root or not package.name.startswith('.operation-')
            or args.lock_fd is None):
        raise RuntimeError('Unexpected installer worker context')
    actual = os.fstat(args.lock_fd)
    expected = (device.root / 'install.lock').stat()
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise RuntimeError('Worker lock does not match this installation')
    fcntl.flock(args.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = load_package(package)
    operation_id = args.worker
    action = 'uninstall' if args.uninstall else 'install'
    phase = 'failed'
    try:
        write_status(device, operation_id, 'working', action=action)
        operation = Operation(package, device)
        operation.preflight(action)
        operation.apply(action, report=lambda message: print(message, flush=True))
        phase = 'uninstalled' if args.uninstall else 'installed'
        write_status(device, operation_id, phase, action=action, version=manifest['version'])
    except Exception as error:
        print('Move Speech operation failed: ' + str(error), flush=True)
        write_status(device, operation_id, 'failed', action=action, error=str(error)[:2048])
    finally:
        try:
            cleanup_stage(package, manifest)
        except Exception as error:
            print('Installer staging retained: ' + str(error), flush=True)
        log = device.root / 'install.log'
        if log.exists() and log.stat().st_size > 131072:
            with log.open('rb') as stream:
                stream.seek(-65536, os.SEEK_END)
                tail = stream.read(65536)
            atomic(log, tail, 0o600)
        os.close(args.lock_fd)
    return 0 if phase != 'failed' else 1


def launch(args, package, operation):
    import fcntl
    device = operation.device
    lock_path = device.root / 'install.lock'
    if lock_path.exists() or lock_path.is_symlink():
        secure(lock_path)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    stage = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Preflight under the same lock that the detached worker inherits.
        operation.preflight('uninstall' if args.uninstall else 'install')
        manifest = operation.manifest
        stage = Path(tempfile.mkdtemp(prefix='.operation-', dir=device.root))
        for name in [*manifest['files'], 'release.json', 'release.sig']:
            source = package / name
            atomic(stage / name, source.read_bytes(), manifest['files'].get(name, {}).get('mode', 0o644))
        load_package(stage)
        previous_log = device.root / 'install.log'
        if previous_log.exists():
            secure(previous_log)
            previous_log.replace(device.root / 'install.previous.log')
        operation_id = uuid.uuid4().hex
        write_status(device, operation_id, 'starting', action='uninstall' if args.uninstall else 'install')
        command = [sys.executable, '-E', '-s', '-B', str(stage / 'install.py'), '--worker', operation_id,
                   '--lock-fd', str(descriptor)]
        if args.uninstall:
            command.append('--uninstall')
        with (device.root / 'install.log').open('wb', buffering=0) as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True, pass_fds=(descriptor,))
        print('The operation will continue safely if this SSH session disconnects.', flush=True)
        os.close(descriptor)
        descriptor = None
        deadline = time.monotonic() + 240
        last_phase = None
        while time.monotonic() < deadline:
            state = json.loads((device.root / 'operation.json').read_text())
            if state['operation'] == operation_id:
                if state['phase'] != last_phase:
                    print('Status: ' + state['phase'], flush=True)
                    last_phase = state['phase']
                if state['phase'] in TERMINAL_PHASES:
                    # Status can be written just before final cleanup.
                    process.wait(timeout=20)
                    print((device.root / 'install.log').read_text(errors='replace')[-16384:], end='', flush=True)
                    return process.returncode
            if process.poll() is not None:
                raise RuntimeError('Installer worker stopped unexpectedly. Inspect install.log before retrying.')
            time.sleep(0.5)
        raise RuntimeError('Operation exceeded its wait limit. Check the installed status before retrying.')
    except BlockingIOError:
        raise RuntimeError('Another Move Speech operation is already running')
    finally:
        if descriptor is not None:
            os.close(descriptor)
            if stage is not None and stage.exists():
                cleanup_stage(stage, operation.manifest)


def main():
    parser = argparse.ArgumentParser(description='Install reversible Move accessibility fixes on the device.')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--uninstall', action='store_true', help='restore unmodified native Move and its startup')
    actions.add_argument('--check', action='store_true', help='check compatibility without changing startup or restarting Move')
    actions.add_argument('--status', action='store_true', help='show the installed state and last operation')
    parser.add_argument('--yes', action='store_true', help='approve the brief restart without an interactive prompt')
    parser.add_argument('--version', action='version', version='Move Speech ' + VERSION)
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    parser.add_argument('--lock-fd', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    if args.worker:
        return worker(args, package)
    device = Device()
    device.prepare()
    if args.status:
        for name in ('state.json', 'operation.json'):
            path = device.root / name
            if path.exists():
                secure(path)
                print(name + ': ' + path.read_text()[:8192], flush=True)
        print('Speech startup: ' + ('enabled' if (device.root / 'enabled').is_file() else 'disabled'))
        return 0
    operation = Operation(package, device)
    plan = operation.preflight('uninstall' if args.uninstall else 'install')
    print('Firmware: ' + plan['profile']['version'] + '; layout: ' + plan['info']['layout'], flush=True)
    if not args.uninstall and not plan['profile']['hardware_verified']:
        print('This firmware profile passed offline tests but has not had a physical-device trial.', flush=True)
    if args.check:
        print('Compatibility and local patch build passed. No startup changes or restart performed.', flush=True)
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            raise RuntimeError('Non-interactive installation needs --yes; no restart was performed')
        answer = input('Move may restart briefly. Continue? [y/N] ').strip().casefold()
        if answer not in ('y', 'yes'):
            print('Cancelled. No startup changes or restart performed.')
            return 0
    return launch(args, package, operation)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('Move Speech: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
