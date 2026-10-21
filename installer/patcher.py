"""Build a verified speech patch locally; never distribute native firmware."""

import json
import struct
from pathlib import Path

from firmware import Image, PROGRAM, branch, branch_target, sha

HOOK_HELPERS = {'sampling_call': 'draw_text', 'gain_call': 'draw_text',
                'slice_count_call': 'draw_text', 'slice_waveform_call': 'waveform_draw',
                'sample_waveform_tail': 'waveform_draw', 'step_velocity_call': 'draw_lines'}


def load_catalog():
    return json.loads(Path(__file__).with_name('catalog.json').read_text(encoding='utf-8'))


def inspect(original, catalog):
    if catalog.get('schema') != 1 or catalog.get('product') != 'MoveSpeech':
        raise ValueError('Invalid firmware catalog')
    profile = catalog['profiles'].get(sha(original))
    if profile is None or len(original) != profile['bytes']:
        raise ValueError('Unsupported firmware; no installation changes attempted')
    image = Image(original)
    fingerprints = profile.get('fingerprints', catalog['fingerprints'])
    if set(fingerprints) != set(catalog['fingerprints']):
        raise ValueError('Firmware profile omitted required function checks')
    found = image.discover(fingerprints)
    if found != profile['addresses']:
        raise ValueError('Discovered firmware functions differ from the signed profile')
    for name in catalog['entries']:
        address = found[name]
        word = struct.unpack_from('<I', image.code, address - image.base)[0]
        target, link = branch_target(word, address)
        helper = HOOK_HELPERS[name]
        if target != found[helper] or link != (name != 'sample_waveform_tail'):
            raise ValueError('Firmware branch does not call the verified native helper')
    if 'step_velocity_call' in catalog['entries']:
        caller = found['velocity_caller']
        target, link = branch_target(struct.unpack_from('<I', image.code, caller-image.base)[0], caller)
        if target != image.function(found['step_velocity_call'])[0] or not link:
            raise ValueError('Velocity caller does not use the verified native ABI')
    return image, profile


def build(original, payload, catalog=None):
    catalog = load_catalog() if catalog is None else catalog
    if len(payload) != catalog['payload_bytes'] or sha(payload) != catalog['payload_sha256']:
        raise ValueError('Invalid speech hook payload')
    image, profile = inspect(original, catalog)
    segment, payload_address = catalog['segment_address'], catalog['payload_address']
    if image.phnum != 12 or payload_address != segment + 0x1000 or segment < len(original):
        raise ValueError('Unsupported firmware program headers')
    if any(p[3] + p[6] > segment for p in image.programs if p[0] == 1):
        raise ValueError('Speech hook would overlap firmware memory')
    adjusted = bytearray(payload)
    for fixup in catalog['fixups']:
        offset = fixup['offset']
        word = struct.unpack_from('<I', adjusted, offset)[0]
        old_target, link = branch_target(word, payload_address + offset)
        adjusted[offset:offset + 4] = branch(payload_address + offset, profile['addresses'][fixup['helper']], link)
    table = bytearray()
    phdr_count = 0
    for index, header in enumerate(image.programs):
        raw = original[image.phoff + index * PROGRAM.size:image.phoff + (index + 1) * PROGRAM.size]
        if header[0] == 6:
            phdr_count += 1
            raw = PROGRAM.pack(6, 4, segment, segment, segment, 13 * PROGRAM.size, 13 * PROGRAM.size, 8)
        table.extend(raw)
    if phdr_count != 1:
        raise ValueError('Expected one firmware program-header segment')
    size = 0x1000 + len(adjusted)
    table.extend(PROGRAM.pack(1, 5, segment, segment, segment, size, size, 0x10000))
    result = bytearray(original)
    for name, entry in catalog['entries'].items():
        address = profile['addresses'][name]
        offset = image.text_offset + address - image.base
        result[offset:offset + 4] = branch(address, payload_address + entry, name != 'sample_waveform_tail')
    struct.pack_into('<Q', result, 32, segment)
    struct.pack_into('<H', result, 56, 13)
    result.extend(bytes(segment - len(result)))
    result.extend(table)
    result.extend(bytes(payload_address - len(result)))
    result.extend(adjusted)
    if sha(result) != profile['candidate_sha256']:
        raise ValueError('Generated firmware does not match the verified build')
    return result
