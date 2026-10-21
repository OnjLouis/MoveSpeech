"""Read-only ELF inspection and bounded native-function discovery."""

import bisect
import hashlib
import struct

HEADER = struct.Struct('<16sHHIQQQIHHHHHH')
PROGRAM = struct.Struct('<IIQQQQQQ')
SECTION = struct.Struct('<IIQQQQIIQQ')
MAX_IMAGE_BYTES = 128 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def branch(source, target, link):
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError('Unreachable speech branch')
    return struct.pack('<I', (0x94000000 if link else 0x14000000) | ((delta >> 2) & 0x3ffffff))


def branch_target(word, address):
    if word & 0x7c000000 != 0x14000000:
        raise ValueError('Expected a native branch instruction')
    delta = word & 0x3ffffff
    if delta & (1 << 25):
        delta -= 1 << 26
    return address + (delta << 2), bool(word & 0x80000000)


def normalized(code, address):
    result = bytearray(len(code))
    previous = 0
    for offset, (word,) in enumerate(struct.iter_unpack('<I', code)):
        original = word
        global_pair = previous & 0x9f000000 == 0x90000000 and (word >> 5) & 31 == previous & 31
        if word & 0x7c000000 == 0x14000000:
            target, link = branch_target(word, address + offset * 4)
            if not address <= target < address + len(code):
                word &= 0xfc000000
        elif word & 0x1f000000 == 0x10000000:
            word &= 0x9f00001f
        elif word & 0x1f000000 == 0x11000000 and global_pair:
            word &= ~(0xfff << 10)
        elif word & 0x3b000000 == 0x18000000:
            word &= 0xff00001f
        elif word & 0x3b400000 == 0x39400000 and global_pair:
            word &= ~(0xfff << 10)
        struct.pack_into('<I', result, offset * 4, word)
        previous = original
    return bytes(result)


class Image:
    def __init__(self, data):
        if not HEADER.size <= len(data) <= MAX_IMAGE_BYTES:
            raise ValueError('Invalid firmware size')
        self.data = data
        h = HEADER.unpack_from(data)
        if h[0][:7] != b'\x7fELF\x02\x01\x01' or h[1] not in (2, 3) or h[2] != 183:
            raise ValueError('Expected native AArch64 little-endian firmware')
        self.entry, self.phoff, self.shoff = h[4:7]
        self.phnum, self.shnum, self.shstr = h[10], h[12], h[13]
        if h[8] != HEADER.size or h[9] != PROGRAM.size or h[11] != SECTION.size:
            raise ValueError('Unsupported ELF header sizes')
        if not 1 <= self.phnum <= 64 or not 1 <= self.shnum <= 4096 or not self.shstr < self.shnum:
            raise ValueError('Invalid ELF table counts')
        self.programs = [PROGRAM.unpack(self.read(self.phoff + i * PROGRAM.size, PROGRAM.size)) for i in range(self.phnum)]
        sections = [SECTION.unpack(self.read(self.shoff + i * SECTION.size, SECTION.size)) for i in range(self.shnum)]
        strings = self.read(sections[self.shstr][4], sections[self.shstr][5])
        self.sections = {}
        for section in sections:
            end = strings.find(b'\0', section[0])
            if end < 0:
                raise ValueError('Invalid ELF section name')
            name = strings[section[0]:end].decode('ascii')
            if name in self.sections:
                raise ValueError('Duplicate ELF section')
            self.sections[name] = section
        text = self.sections['.text']
        self.base, self.text_offset = text[3:5]
        self.code = self.read(text[4], text[5])
        header = self.sections['.eh_frame_hdr']
        unwind = self.read(header[4], header[5])
        if unwind[:4] != b'\x01\x1b\x03\x3b' or len(unwind) < 12:
            raise ValueError('Unsupported native unwind table')
        count = struct.unpack_from('<I', unwind, 8)[0]
        if count < 2 or count > 200000 or 12 + count * 8 > len(unwind):
            raise ValueError('Invalid native function count')
        self.functions = [header[3] + struct.unpack_from('<i', unwind, 12 + i * 8)[0] for i in range(count)]
        if self.functions != sorted(set(self.functions)) or self.functions[0] < self.base or self.functions[-1] >= self.base + len(self.code):
            raise ValueError('Invalid native function boundaries')

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > len(self.data):
            raise ValueError('Firmware read outside executable')
        return self.data[offset:offset + length]

    def function(self, address):
        index = bisect.bisect_right(self.functions, address) - 1
        if not 0 <= index < len(self.functions) - 1:
            raise ValueError('Native function is outside the supported table')
        return self.functions[index], self.functions[index + 1]

    def discover(self, fingerprints):
        by_size = {}
        for name, item in fingerprints.items():
            by_size.setdefault(item['bytes'], []).append((name, item))
        matches = {name: [] for name in fingerprints}
        for start, end in zip(self.functions, self.functions[1:]):
            wanted = by_size.get(end - start)
            if not wanted:
                continue
            digest = sha(normalized(self.code[start - self.base:end - self.base], start))
            for name, item in wanted:
                if digest == item['sha256']:
                    matches[name].append(start + item['site_offset'])
        if any(len(found) != 1 for found in matches.values()):
            failed = ', '.join(name for name, found in matches.items() if len(found) != 1)
            raise ValueError('Missing or ambiguous firmware functions: ' + failed)
        return {name: found[0] for name, found in matches.items()}
