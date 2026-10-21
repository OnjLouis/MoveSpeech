import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from trust import PRODUCT, PUBLIC_KEY, REPOSITORY

MAX_MANIFEST = 65536
MAX_PACKAGE = 4 * 1024 * 1024
REQUIRED_FILES = frozenset({'catalog.json', 'detect_native.py', 'device.py', 'engine.py',
                          'firmware.py', 'hooks.bin', 'install.py', 'patcher.py', 'runtime.py',
                          'startup_patch.py', 'trust.py', 'verify.py', 'Manual.html'})


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate release metadata field')
        result[key] = value
    return result


def verify_manifest(raw, signature, directory, openssl='openssl'):
    if not raw or len(raw) > MAX_MANIFEST or len(signature) != 384:
        raise ValueError('Missing or malformed release signature')
    with tempfile.TemporaryDirectory(prefix='.signature-', dir=directory) as temporary:
        root = Path(temporary)
        (root / 'key.pem').write_bytes(PUBLIC_KEY)
        (root / 'manifest').write_bytes(raw)
        (root / 'signature').write_bytes(signature)
        result = subprocess.run([openssl, 'dgst', '-sha256', '-verify', str(root / 'key.pem'),
                                 '-signature', str(root / 'signature'), str(root / 'manifest')],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if result.returncode:
        raise ValueError('Release signature is invalid; nothing has been installed')
    manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    if not isinstance(manifest, dict):
        raise ValueError('Invalid signed release metadata')
    if (manifest.get('schema') != 1 or manifest.get('product') != PRODUCT
            or not re.fullmatch(r'\d+\.\d+\.\d+', manifest.get('version', ''))
            or type(manifest.get('sequence')) is not int or manifest['sequence'] < 1):
        raise ValueError('Invalid signed release metadata')
    expected_url = f'https://github.com/{REPOSITORY}/releases/download/v{manifest["version"]}/MoveSpeech-{manifest["version"]}.zip'
    summary = manifest.get('summary')
    if (not isinstance(summary, str) or not 1 <= len(summary) <= 500
            or any(ord(char) < 32 or ord(char) == 127 for char in summary)):
        raise ValueError('Invalid release summary')
    if manifest.get('url') != expected_url:
        raise ValueError('Release does not use the expected GitHub download')
    if (type(manifest.get('bytes')) is not int or not 0 < manifest['bytes'] <= MAX_PACKAGE
            or not re.fullmatch('[0-9a-f]{64}', manifest.get('sha256', ''))):
        raise ValueError('Invalid package integrity metadata')
    inventory = manifest.get('files')
    if not isinstance(inventory, dict) or set(inventory) != REQUIRED_FILES:
        raise ValueError('Invalid signed file inventory')
    folded = set()
    for name, item in inventory.items():
        path = PurePosixPath(name)
        if (path.is_absolute() or len(path.parts) != 1 or '\\' in name
                or path.name != name or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', name)
                or name.casefold() in folded):
            raise ValueError('Unsafe or ambiguous package filename')
        folded.add(name.casefold())
        if (not isinstance(item, dict) or type(item.get('bytes')) is not int
                or not 0 <= item['bytes'] <= MAX_PACKAGE or item.get('mode') not in (420, 493)
                or not re.fullmatch('[0-9a-f]{64}', item.get('sha256', ''))):
            raise ValueError('Invalid signed file record')
    return manifest


def verify_files(directory, manifest):
    for name, item in manifest['files'].items():
        path = Path(directory) / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Required package file is missing: ' + name)
        if path.stat().st_size != item['bytes']:
            raise ValueError('Package file has unexpected size: ' + name)
        data = path.read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Package file failed verification: ' + name)
        if os.name != 'nt' and path.stat().st_mode & 0o777 != item['mode']:
            raise ValueError('Package file permissions differ: ' + name)


def load_package(directory):
    directory = Path(directory)
    for name, limit in [('release.json', MAX_MANIFEST), ('release.sig', 384)]:
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError('Missing or oversized release metadata')
    manifest = verify_manifest((directory / 'release.json').read_bytes(),
                               (directory / 'release.sig').read_bytes(), directory)
    verify_files(directory, manifest)
    return manifest
