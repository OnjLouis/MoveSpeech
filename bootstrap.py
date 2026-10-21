#!/usr/bin/env python3
"""The release verification key is public; the signing key is never shipped."""

PRODUCT = 'MoveSpeech'
REPOSITORY = 'OnjLouis/MoveSpeech'
PUBLIC_KEY = b'''-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAp22NVCJvoLx3LqWrey65
ltu1Rvq3SNAPtqWgvLcliTVw/F97Yt8T/2Ndrraix84wxu3BVHrCcFI6fkTfrN4C
gR2202OAFCY8cji259PjPQDxrafigKgec8Kv7DyqlDAUedYN97sTvntlnvHwDb0x
slUj1eRpYXMHvWVNS8YsnB8/4dMqNRbZyESwP7/KiOJBt26yGl3qlxn7Kdyq/Dym
vnC5Ghwv6iC6IiQQpQgAL+7zKFLnGJJvwOoCmZW8BIf1BKbZYQmHNSt4F/p1sXg5
Mjc/l8TIxgrEXNzJEuvniSIEas1si/Jv2E2yqsJ/DYXLMJ7T2JNnXo2enqnamATk
iv8MWtNUrH+D3eveQ9llX6zpzoeE2B+/fw9OCIBy43K6DacZ3fJNMBVEpRLkx3jf
wT6bB4rr4NGKDP2I6HLdsMdGe4ieT8UxMPhMHVRMHNY8CdtZeOev+PTSde4JiQEM
4Yd59QmzPaw/qTfnrEKV/8VtRMlnD1N9cjil+UOck8q9AgMBAAE=
-----END PUBLIC KEY-----
'''

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


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

import shutil
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

INSTALL_ROOT = Path('/data/onj-move-accessibility')
REDIRECT_HOSTS = {'github.com', 'raw.githubusercontent.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, request, fp, code, message, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in REDIRECT_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Refusing an untrusted download address or insecure redirect')


def fetch(url, limit):
    validate_url(url)
    opener = urllib.request.build_opener(SafeRedirect())
    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'MoveSpeech-Installer'}), timeout=30) as response:
        validate_url(response.geturl())
        if response.status != 200:
            raise ValueError('Download was not successful')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Download exceeds the permitted size')
    return data


def secure_download_root():
    if os.getuid() != 0 or not Path('/opt/move/MoveLauncher').is_file():
        raise ValueError('Run this command in a root SSH shell on your Ableton Move. No changes made.')
    for path in [INSTALL_ROOT.parent, *INSTALL_ROOT.parent.parents]:
        info = path.lstat()
        if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe installation parent directory')
    INSTALL_ROOT.mkdir(mode=0o755, exist_ok=True)
    info = INSTALL_ROOT.lstat()
    if INSTALL_ROOT.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Unsafe existing installation directory')


def extract_verified(data, manifest, destination):
    import io
    if len(data) != manifest['bytes'] or hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise ValueError('Downloaded package failed integrity verification')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or set(names) != set(manifest['files']):
            raise ValueError('Package contents differ from the signed inventory')
        if sum(entry.file_size for entry in entries) > MAX_PACKAGE:
            raise ValueError('Expanded package is too large')
        for entry in entries:
            item = manifest['files'][entry.filename]
            mode = entry.external_attr >> 16
            if (entry.flag_bits & 1 or stat.S_IFMT(mode) != stat.S_IFREG
                    or mode & 0o777 != item['mode'] or entry.file_size != item['bytes']):
                raise ValueError('Unsafe package file or permissions')
            with archive.open(entry) as stream:
                content = stream.read(item['bytes'] + 1)
            if len(content) != item['bytes'] or hashlib.sha256(content).hexdigest() != item['sha256']:
                raise ValueError('Package member failed verification')
            path = Path(destination) / entry.filename
            with path.open('xb') as stream:
                stream.write(content)
            path.chmod(item['mode'])
    verify_files(destination, manifest)


def bootstrap():
    secure_download_root()
    with tempfile.TemporaryDirectory(prefix='.download-', dir=INSTALL_ROOT) as directory:
        stage = Path(directory)
        print('Checking for the latest signed Move Speech installer...', flush=True)
        base = f'https://raw.githubusercontent.com/{REPOSITORY}/main/release/'
        raw = fetch(base + 'manifest.json', MAX_MANIFEST)
        signature = fetch(base + 'manifest.sig', 384)
        manifest = verify_manifest(raw, signature, stage)
        if (INSTALL_ROOT / 'release.json').is_file():
            previous = verify_manifest((INSTALL_ROOT / 'release.json').read_bytes(),
                                       (INSTALL_ROOT / 'release.sig').read_bytes(), stage)
            if manifest['sequence'] < previous['sequence'] or (manifest['sequence'] == previous['sequence'] and manifest['sha256'] != previous['sha256']):
                raise ValueError('Refusing an older or inconsistent release')
        print('Version ' + manifest['version'] + ': ' + manifest['summary'], flush=True)
        data = fetch(manifest['url'], manifest['bytes'])
        extract_verified(data, manifest, stage)
        (stage / 'release.json').write_bytes(raw)
        (stage / 'release.sig').write_bytes(signature)
        result = subprocess.run([sys.executable, '-E', '-s', '-B', str(stage / 'install.py'), *sys.argv[1:]], check=False)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    try:
        bootstrap()
    except Exception as error:
        print('Move Speech was not installed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
