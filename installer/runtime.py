"""Start and stop only the exact native Move process identities."""

import os
import signal
import subprocess
import time
from pathlib import Path

from device import LEGACY_BUILDS, secure

PROC_ROOT = Path('/proc')
START_TIMEOUT_SECONDS = 60
# Schwung clears its failed-boot counter after 15 seconds. Do not report a
# successful restart early enough for the next operation to defeat that check.
HEALTHY_START_SECONDS = 20


def process_identity(pid):
    directory = PROC_ROOT / str(pid)
    try:
        stat = (directory / 'stat').read_text().split(') ', 1)[1].split()
        if stat[0] == 'Z':
            return None
        return (int(pid), (directory / 'comm').read_text().strip(),
                os.readlink(directory / 'exe'), int(stat[1]), stat[19])
    except (FileNotFoundError, ProcessLookupError):
        return None


def processes(device, state):
    target, candidate = device.paths(state)
    accepted = {str(target), str(candidate)}
    accepted.update(str(device.root / name) for name in LEGACY_BUILDS)
    result = []
    identities = [value for directory in PROC_ROOT.glob('[0-9]*')
                  if (value := process_identity(int(directory.name))) is not None]
    launchers = set()
    for pid, name, executable, parent, stamp in identities:
        if name not in ('Move', 'MoveOriginal', 'MoveLauncher', 'MoveSamplingSpe') and not name.startswith('MoveSpeech-'):
            continue
        if name == 'MoveLauncher' and executable == str(device.native_dir / 'MoveLauncher'):
            secure(executable)
            role = 'launcher'
            launchers.add(pid)
        elif executable in accepted:
            secure(executable)
            role = 'native'
        else:
            raise RuntimeError('Unexpected Move process executable: ' + executable)
        result.append((pid, role, stamp))
    # A launcher can be waiting in a shell/boot selector before the native
    # executable exists. Track its actual descendants, not similarly named apps.
    family = set(launchers)
    while True:
        children = {pid for pid, name, exe, parent, stamp in identities if parent in family}
        expanded = family | children
        if expanded == family:
            break
        family = expanded
    known = {pid for pid, role, stamp in result}
    result.extend((pid, 'startup', stamp) for pid, name, exe, parent, stamp in identities
                  if pid in family and pid not in known)
    return result


def stop_move(device, state):
    tracked = {}
    def remaining():
        for pid, role, stamp in processes(device, state):
            identity = process_identity(pid)
            if identity is not None and identity[4] == stamp:
                tracked[pid] = identity
        # Parent exit may orphan a selector. Retain its captured identity but
        # never signal a recycled PID or a process which exec'd unrelated code.
        return [pid for pid, identity in tracked.items()
                if (current := process_identity(pid)) is not None
                and (current[0], current[2], current[4]) == (identity[0], identity[2], identity[4])]
    remaining()
    subprocess.run([str(device.init), 'stop'], timeout=15, check=True)
    for pid in remaining():
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 8
    while remaining() and time.monotonic() < deadline:
        time.sleep(0.2)
    for pid in remaining():
        print('Stopping verified unresponsive Move process', pid, flush=True)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 3
    while remaining() and time.monotonic() < deadline:
        time.sleep(0.2)
    if remaining():
        raise RuntimeError('Move processes remain; no mount change attempted')


def start_move(device, state):
    subprocess.run([str(device.init), 'start'], timeout=15, check=True)
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    stable_identity, stable_since = None, 0
    while time.monotonic() < deadline:
        found = [(pid, stamp) for pid, role, stamp in processes(device, state) if role == 'native']
        identity = found[0] if len(found) == 1 else None
        if identity != stable_identity:
            stable_identity, stable_since = identity, time.monotonic()
        if identity and time.monotonic() - stable_since >= HEALTHY_START_SECONDS:
            return identity[0]
        time.sleep(0.25)
    raise RuntimeError('Move did not reach a stable process')
