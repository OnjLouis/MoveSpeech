import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from detect_native import identify_directory
from verify import load_package

CAPS = 'cap_ipc_lock,cap_sys_nice,cap_sys_resource=ep'
LEGACY_INIT_SHA = 'fcec9240636a7c64bd88edb58628a4dff6ee055010250e078b36c009e7a99fd9'
LEGACY_BUILDS = {
    'MoveSamplingSpeech': 'cc0be1348de0aaec548c19667a95f31abc3f68b9891fa9dc0d1e8fb857241092',
    'MoveSamplingSpeech-v3': '1fa4d2d6712920a5ab6abc34090f7105c37f7dc92c5718b7ff62e76553d1d9ac',
    'MoveSamplingSpeech-v4': 'c1b75071c2bbb891d5ae4d9b084079cd9c7f4dc94d127052c4d2a4175b5a0c02',
    'MoveSamplingSpeech-v5': '745f819497f2a64fb06485ccc73bcf20691d593bf95335ae2f896e3df484227c',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def secure(path):
    path = Path(path)
    for item in [path, *path.parents]:
        info = item.lstat()
        if item.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Unsafe ownership, permissions or symlink: ' + str(item))
    return path


def atomic(path, data, mode=0o644):
    path = Path(path)
    secure(path.parent)
    if path.exists() or path.is_symlink():
        secure(path)
        if not path.is_file():
            raise RuntimeError('Replacement target is not a regular file')
    descriptor, temporary = tempfile.mkstemp(prefix='.new-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if os.name == 'posix':
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def init_block(action):
    return (f'    # BEGIN MoveSpeech {action}\n'
            '    if [ -f /data/onj-move-accessibility/startup_patch.py ]; then\n'
            f'        /usr/bin/python3 -E -s -B /data/onj-move-accessibility/startup_patch.py {action} || true\n'
            '    fi\n'
            f'    # END MoveSpeech {action}\n').encode('ascii')


def render_init(original):
    start = b'if [ "$1" = "start" ]; then\n'
    stop = b'\nfi\n'
    if original.count(start) != 1 or not original.endswith(stop):
        raise RuntimeError('Unrecognised Move startup script')
    result = original.replace(start, start + init_block('activate'), 1)
    return result[:-len(stop)] + b'\n' + init_block('deactivate') + stop


def remove_init_blocks(current):
    if b'# BEGIN MoveSpeech' not in current and b'# END MoveSpeech' not in current:
        return current
    start = b'if [ "$1" = "start" ]; then\n'
    active = start + init_block('activate')
    inactive = b'\n' + init_block('deactivate')
    if current.count(active) != 1 or current.count(inactive) != 1:
        raise RuntimeError('Speech startup blocks were independently changed; no overwrite attempted')
    result = current.replace(active, start, 1).replace(inactive, b'', 1)
    if b'# BEGIN MoveSpeech' in result or b'# END MoveSpeech' in result:
        raise RuntimeError('Unexpected additional speech startup block')
    return result


class System:
    def bind(self, source, target):
        subprocess.run(['/bin/mount', '--bind', str(source), str(target)], check=True, timeout=5)

    def unmount(self, target):
        subprocess.run(['/bin/umount', str(target)], check=True, timeout=5)

    def mounted(self, target):
        return any(line.split()[4] == str(target) for line in Path('/proc/self/mountinfo').read_text().splitlines())

    def capabilities(self, path):
        result = subprocess.check_output(['/usr/sbin/getcap', str(path)], text=True, timeout=5).strip()
        return result == str(path) + ' ' + CAPS

    def set_capabilities(self, path):
        subprocess.run(['/usr/sbin/setcap', CAPS, str(path)], check=True, timeout=5)
        if not self.capabilities(path):
            raise RuntimeError('Speech executable capabilities did not verify')


class Device:
    def __init__(self, base=Path('/'), system=None):
        self.base = Path(base)
        self.root = self.base / 'data/onj-move-accessibility'
        self.native_dir = self.base / 'opt/move'
        self.init = self.base / 'etc/init.d/move'
        self.system = system or System()

    def prepare(self):
        if os.getuid() != 0:
            raise RuntimeError('A root SSH shell on Move is required')
        secure(self.root.parent)
        self.root.mkdir(mode=0o755, exist_ok=True)
        secure(self.root)
        secure(self.native_dir)
        secure(self.init)

    def stock(self):
        # A non-recursive directory bind exposes the untouched native file,
        # even when its ordinary pathname has our file mount over it.
        with tempfile.TemporaryDirectory(prefix='.inspect-', dir=self.root) as directory:
            view = Path(directory) / 'native'
            view.mkdir(mode=0o700)
            self.system.bind(self.native_dir, view)
            try:
                info = identify_directory(view)
                native = Path(info['native_path'])
                secure(native)
                if not self.system.capabilities(native):
                    raise RuntimeError('Unsupported native executable capabilities')
                data = native.read_bytes()
                info['native_name'] = native.name
                info['native_path'] = str(self.native_dir / native.name)
                return info, data
            finally:
                self.system.unmount(view)

    def read_state(self):
        secure(self.root / 'state.json')
        load_package(self.root)
        state = json.loads((self.root / 'state.json').read_text(encoding='utf-8'))
        if (state.get('schema') != 1 or state.get('native_name') not in ('Move', 'MoveOriginal')
                or not re.fullmatch(r'MoveSpeech-[0-9a-f]{16}|MoveSamplingSpeech(?:-v[345])?', state.get('candidate', ''))):
            raise RuntimeError('Invalid installed speech configuration')
        catalog = json.loads((self.root / 'catalog.json').read_text(encoding='utf-8'))
        profile = catalog['profiles'].get(state.get('stock_sha256'))
        if profile is None or state.get('patch_sha256') != profile['candidate_sha256']:
            raise RuntimeError('Installed state does not match its verified firmware profile')
        return state

    def paths(self, state):
        return self.native_dir / state['native_name'], self.root / state['candidate']

    def activate(self):
        if not (self.root / 'enabled').is_file():
            return False
        secure(self.root / 'enabled')
        state = self.read_state()
        target, candidate = self.paths(state)
        secure(target)
        secure(candidate)
        if digest(candidate) != state['patch_sha256'] or not self.system.capabilities(candidate):
            raise RuntimeError('Installed speech executable failed verification')
        if self.system.mounted(target):
            if os.path.samefile(target, candidate) and digest(target) == state['patch_sha256']:
                return True
            raise RuntimeError('An unrelated executable mount is present')
        if digest(target) != state['stock_sha256']:
            print('Move firmware has changed. Run the Move Speech installer again; stock Move will start.', flush=True)
            return False
        from runtime import processes
        if processes(self, state):
            raise RuntimeError('Move is already running; no live executable replacement attempted')
        self.system.bind(candidate, target)
        if not os.path.samefile(target, candidate) or digest(target) != state['patch_sha256']:
            self.system.unmount(target)
            raise RuntimeError('Speech mount verification failed')
        return True

    def deactivate(self, state):
        target, candidate = self.paths(state)
        if not self.system.mounted(target):
            return
        if not os.path.samefile(target, candidate):
            raise RuntimeError('Refusing to remove an unrelated executable mount')
        from runtime import processes
        if processes(self, state):
            raise RuntimeError('Move is still running; its executable mount was left intact')
        self.system.unmount(target)

    def existing_mount(self, target, permitted_sha):
        if not self.system.mounted(target):
            return None
        for candidate in self.root.iterdir():
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if os.path.samefile(target, candidate):
                secure(candidate)
                actual = digest(candidate)
                if actual == permitted_sha or LEGACY_BUILDS.get(candidate.name) == actual:
                    return candidate
        raise RuntimeError('An unknown executable mount is present; no changes attempted')

    def original_init(self, expected_sha):
        current = self.init.read_bytes()
        if hashlib.sha256(current).hexdigest() == expected_sha:
            return current
        backup = self.root / 'move.init.original'
        secure(backup)
        original = backup.read_bytes()
        if hashlib.sha256(original).hexdigest() != expected_sha:
            raise RuntimeError('Original Move startup backup does not match')
        if current != render_init(original) and hashlib.sha256(current).hexdigest() != LEGACY_INIT_SHA:
            raise RuntimeError('Move startup was changed independently; no overwrite attempted')
        return original
