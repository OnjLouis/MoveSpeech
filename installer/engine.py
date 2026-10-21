import hashlib
import io
import json
import os
import shutil
import stat
import zipfile
from pathlib import Path

import runtime
from device import Device, LEGACY_INIT_SHA, atomic, digest, render_init, remove_init_blocks, secure
from patcher import build, inspect
from verify import load_package, verify_manifest


class Operation:
    def __init__(self, package, device=None, lifecycle=None):
        self.package = Path(package)
        self.device = device or Device()
        self.lifecycle = lifecycle or runtime
        self.manifest = load_package(self.package)
        self.catalog = json.loads((self.package / 'catalog.json').read_text(encoding='utf-8'))
        self.plan = None

    def preflight(self, action='install'):
        device = self.device
        device.prepare()
        if hasattr(os, 'statvfs') and os.statvfs(device.init.parent).f_flag & os.ST_RDONLY:
            raise RuntimeError('The system partition is read-only; Move was not stopped')
        if shutil.disk_usage(device.init.parent).free < 64 * 1024:
            raise RuntimeError('At least 64 KiB of free system-partition space is required')
        if (device.root / 'release.json').exists():
            previous = verify_manifest((device.root / 'release.json').read_bytes(),
                                       (device.root / 'release.sig').read_bytes(), device.root)
            if (self.manifest['sequence'] < previous['sequence']
                    or (self.manifest['sequence'] == previous['sequence'] and self.manifest['sha256'] != previous['sha256'])):
                raise RuntimeError('Refusing an older or inconsistent installer release')
        info, original = device.stock()
        if action == 'uninstall' and (device.root / 'state.json').is_file():
            installed = device.read_state()
            state = dict(installed, native_name=info['native_name'], stock_sha256=info['sha256'])
            target, candidate = device.paths(state)
            existing = device.existing_mount(target, state['patch_sha256'])
            current = device.init.read_bytes()
            base_init = (device.original_init(self.catalog['init_sha256'])
                         if hashlib.sha256(current).hexdigest() == LEGACY_INIT_SHA
                         else remove_init_blocks(current))
            profile = self.catalog['profiles'].get(info['sha256'],
                        dict(version='unrecognised (removal only)', hardware_verified=False))
            self.plan = dict(action=action, info=info, profile=profile, state=state,
                             existing=existing, original_init=base_init, generated=None,
                             init_mode=stat.S_IMODE(device.init.stat().st_mode))
            return {key: value for key, value in self.plan.items() if key not in ('generated', 'original_init')}
        image, profile = inspect(original, self.catalog)
        base_init = device.original_init(self.catalog['init_sha256'])
        target = device.native_dir / info['native_name']
        existing = device.existing_mount(target, profile['candidate_sha256'])
        candidate_name = existing.name if existing and digest(existing) == profile['candidate_sha256'] else 'MoveSpeech-' + profile['candidate_sha256'][:16]
        state = dict(schema=1, native_name=info['native_name'], candidate=candidate_name,
                     stock_sha256=info['sha256'], patch_sha256=profile['candidate_sha256'],
                     version=self.manifest['version'])
        candidate = device.root / candidate_name
        if candidate.exists():
            secure(candidate)
            if digest(candidate) != state['patch_sha256']:
                raise RuntimeError('An existing candidate has unexpected contents; no overwrite attempted')
        generated = build(original, (self.package / 'hooks.bin').read_bytes(), self.catalog)
        if shutil.disk_usage(device.root).free < 100 * 1024 * 1024:
            raise RuntimeError('At least 100 MiB of free space on the data partition is required')
        self.plan = dict(action=action, info=info, profile=profile, state=state, existing=existing,
                         original_init=base_init, generated=generated,
                         init_mode=stat.S_IMODE(device.init.stat().st_mode))
        return {key: value for key, value in self.plan.items() if key not in ('generated', 'original_init')}

    def snapshot(self):
        root = self.device.root
        names = set(self.manifest['files']) | {'release.json', 'release.sig', 'state.json', 'enabled', 'move.init.original'}
        files = {}
        for name in sorted(names):
            path = root / name
            if path.exists() or path.is_symlink():
                secure(path)
                if not path.is_file():
                    raise RuntimeError('Unexpected managed-file type: ' + name)
                if path.stat().st_size > 4 * 1024 * 1024:
                    raise RuntimeError('Unexpected managed-file size: ' + name)
                files[name] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            else:
                files[name] = None
        snapshot = dict(files=files, init=(self.device.init.read_bytes(), stat.S_IMODE(self.device.init.stat().st_mode)))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('startup-script', snapshot['init'][0])
            for name, value in files.items():
                if value is not None:
                    archive.writestr('installed/' + name, value[0])
            archive.writestr('restore-index.json', json.dumps(dict(init_mode=snapshot['init'][1],
                            files={name: value[1] if value else None for name, value in files.items()})))
        atomic(root / 'rollback.zip', buffer.getvalue(), 0o600)
        return snapshot

    def restore_files(self, snapshot):
        for name, value in snapshot['files'].items():
            path = self.device.root / name
            if value is None:
                if path.exists() or path.is_symlink():
                    secure(path)
                    path.unlink()
            else:
                atomic(path, value[0], value[1])
        atomic(self.device.init, *snapshot['init'])

    def deploy(self):
        root = self.device.root
        for name, item in self.manifest['files'].items():
            atomic(root / name, (self.package / name).read_bytes(), item['mode'])
        for name in ('release.json', 'release.sig'):
            atomic(root / name, (self.package / name).read_bytes())
        atomic(root / 'move.init.original', self.plan['original_init'])
        atomic(root / 'state.json', (json.dumps(self.plan['state'], indent=2) + '\n').encode('utf-8'), 0o600)
        atomic(root / 'enabled', b'', 0o600)
        atomic(self.device.init, render_init(self.plan['original_init']), 0o755)
        load_package(root)

    def apply(self, action='install', report=print):
        if self.plan is None or self.plan['action'] != action:
            self.preflight(action)
        device = self.device
        state = self.plan['state']
        target, candidate = device.paths(state)
        snapshot = self.snapshot()
        existing = self.plan['existing']
        was_running = bool(self.lifecycle.processes(device, state))
        modified_runtime = False
        created_candidate = False
        try:
            if action == 'install':
                if not candidate.exists():
                    atomic(candidate, self.plan['generated'], 0o755)
                    created_candidate = True
                device.system.set_capabilities(candidate)
                # A migration to the public installer can adopt the exact
                # accepted executable without interrupting its process.
                adopt = existing is not None and os.path.samefile(existing, candidate)
                if not adopt:
                    report('Stopping Move briefly...')
                    modified_runtime = True
                    self.lifecycle.stop_move(device, state)
                    if existing is not None and device.system.mounted(target):
                        if not os.path.samefile(target, existing):
                            raise RuntimeError('Executable mount changed during installation')
                        device.system.unmount(target)
                self.deploy()
                if adopt:
                    if not device.activate():
                        raise RuntimeError('Adopted runtime failed startup verification')
                else:
                    report('Starting Move with speech fixes...')
                    self.lifecycle.start_move(device, state)
                if not device.system.mounted(target) or not os.path.samefile(target, candidate) or digest(target) != state['patch_sha256']:
                    raise RuntimeError('Speech did not activate after installation')
                report('Move Speech installed. Sampling, Slice, sample controls and step velocity are enabled.')
            elif action == 'uninstall':
                report('Restoring unmodified Move...')
                modified_runtime = True
                self.lifecycle.stop_move(device, state)
                if existing is not None and device.system.mounted(target):
                    if not os.path.samefile(target, existing):
                        raise RuntimeError('Executable mount changed during uninstall')
                    device.system.unmount(target)
                if digest(target) != state['stock_sha256']:
                    raise RuntimeError('Original native executable did not verify')
                atomic(device.init, self.plan['original_init'], self.plan['init_mode'])
                enabled = device.root / 'enabled'
                if enabled.exists():
                    secure(enabled)
                    enabled.unlink()
                self.lifecycle.start_move(device, state)
                if device.system.mounted(target) or digest(target) != state['stock_sha256']:
                    raise RuntimeError('Unmodified Move restoration did not verify')
                report('Move Speech disabled and original startup restored. No voice settings were changed.')
            else:
                raise ValueError('Unknown installer action')
        except Exception:
            report('Installation failed; restoring the previous configuration...')
            if modified_runtime:
                self.lifecycle.stop_move(device, state)
                if device.system.mounted(target):
                    known = candidate if candidate.exists() and os.path.samefile(target, candidate) else existing
                    if known is None or not os.path.samefile(target, known):
                        raise RuntimeError('Recovery refused an unrelated executable mount')
                    device.system.unmount(target)
            self.restore_files(snapshot)
            if modified_runtime and was_running:
                self.lifecycle.start_move(device, state)
            if created_candidate and candidate.exists() and not (device.system.mounted(target) and os.path.samefile(target, candidate)):
                secure(candidate)
                candidate.unlink()
            raise
