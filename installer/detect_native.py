"""Identify stock or Schwung-wrapped Move by its executable, not its name."""

import hashlib
import struct
from pathlib import Path


def native_elf(path):
    if path.is_symlink():
        raise ValueError('Native executable must not be a symbolic link')
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        header = stream.read(64)
    return (len(header) == 64 and header[:6] == b'\x7fELF\x02\x01'
            and struct.unpack_from('<H', header, 16)[0] in (2, 3)
            and struct.unpack_from('<H', header, 18)[0] == 183)


def identify(root=Path('/')):
    directory = Path(root) / 'opt' / 'move'
    return identify_directory(directory)


def identify_directory(directory):
    directory = Path(directory)
    entry = directory / 'Move'
    original = directory / 'MoveOriginal'
    if native_elf(entry):
        native = entry
        layout = 'stock'
    else:
        if not entry.is_file():
            raise ValueError('Move launcher is missing')
        with entry.open('rb') as stream:
            launcher = stream.read(16384)
        if (not launcher.startswith(b'#!') or b'/opt/move/MoveOriginal' not in launcher
                or not any(marker in launcher for marker in (b'schwung-shim.so', b'move-anything-shim.so'))):
            raise ValueError('Unrecognised Move launcher; no changes attempted')
        if not native_elf(original):
            raise ValueError('The wrapped native Move executable is missing or invalid')
        native = original
        layout = 'schwung'
    digest = hashlib.sha256()
    with native.open('rb') as stream:
        before = native.stat()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        after = native.stat()
    if (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns):
        raise ValueError('Native executable changed during inspection')
    return dict(native_path=str(native), layout=layout, sha256=digest.hexdigest(), bytes=after.st_size)
