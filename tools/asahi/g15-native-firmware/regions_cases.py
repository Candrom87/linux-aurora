#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Invented segment metadata; no retained bytes, device or physical access."""
import copy
import random
import struct

UPPER = 0xfffffc0000000000
TEXT = (0x10000, 0x14000)
DATA = (0x20000, 0x24000)


def node(name, props=(), children=()):
    return [list(props) + [('name', name.encode() + b'\0')], list(children)]


def encode(tree):
    props, children = tree
    out = bytearray(struct.pack('<II', len(props), len(children)))
    for name, data in props:
        out += name.encode().ljust(32, b'\0') + struct.pack('<I', len(data)) + data
        out += b'\0' * (-len(data) % 4)
    for child in children:
        out += encode(child)
    return bytes(out)


def base(text=TEXT, data=DATA, dram=(0x10000, 0x100000)):
    ranges = b''.join(struct.pack('<QQQII', start, UPPER + (0 if i == 0 else text[1] - text[0]),
                                   start, end - start, 1 if i == 0 else 0)
                      for i, (start, end) in enumerate((text, data)))
    return node('device-tree', children=[node('chosen', [
        ('dram-base', struct.pack('<Q', dram[0])), ('dram-size', struct.pack('<Q', dram[1]))]),
        node('arm-io', children=[node('sgx', [('compatible', b'gpu,t6034\0')]),
            node('gfx-asc', [('segment-names', b'__TEXT;__DATA\0'), ('segment-ranges', ranges)],
                 [node('iop-gfx-nub', [('pre-loaded', struct.pack('<I', 1)), ('segment-ranges', ranges)])])])])


def replace(tree, path, key, value):
    owner = tree
    for index in path:
        owner = owner[1][index]
    owner[0][:] = [(name, value if name == key else old) for name, old in owner[0]]


def cases():
    first = base()
    rows = [('base', encode(first), 0x6034, TEXT, DATA, True)]
    def add(name, tree, chip=0x6034, text=TEXT, data=DATA, valid=False):
        rows.append((name, encode(tree) if isinstance(tree, list) else tree, chip, text, data, valid))
    plain = base(); replace(plain, [1, 1], 'segment-names', b'__TEXT;__DATA')
    add('complete-name-without-nul', plain, valid=True)
    for shift in (0x4000, 0x8000, 0x10000):
        text = tuple(v + shift for v in TEXT); data = tuple(v + shift for v in DATA)
        add('relocated-' + str(shift), base(text, data, (0x10000, 0x100000)), text=text, data=data, valid=True)
    text = (0x10000, 0x74000); data = (0x100000, 0x240000)
    add('larger-invented-image', base(text, data, (0x10000, 0x400000)), text=text, data=data, valid=True)
    for chip in (0, 0x6030, 0x6031):
        add('chip-' + str(chip), first, chip=chip)
    for text, data in [((0x14000, 0x18000), DATA), (TEXT, (0x24000, 0x28000)),
                       ((TEXT[0], TEXT[0]), DATA), ((TEXT[0], TEXT[0] - 1), DATA),
                       (TEXT, (DATA[0], DATA[0])), (TEXT, (DATA[0], DATA[0] - 1))]:
        add('different-caller-range', first, text=text, data=data)
    for path, key in [([0], 'dram-base'), ([0], 'dram-size'), ([1, 0], 'compatible'),
                      ([1, 1], 'segment-names'), ([1, 1], 'segment-ranges'),
                      ([1, 1, 0], 'pre-loaded'), ([1, 1, 0], 'segment-ranges')]:
        owner = first
        for index in path:
            owner = owner[1][index]
        raw = next(value for name, value in owner[0] if name == key)
        for length in range(len(raw)):
            bad = copy.deepcopy(first); replace(bad, path, key, raw[:length])
            add('short-' + key, bad, valid=key == 'segment-names' and raw[:length] == b'__TEXT;__DATA')
        bad = copy.deepcopy(first); replace(bad, path, key, raw + b'\0'); add('long-' + key, bad)
        bad = copy.deepcopy(first)
        owner = bad
        for index in path:
            owner = owner[1][index]
        owner[0].append((key, raw)); add('duplicate-' + key, bad)
        owner[0][:] = [(name, value) for name, value in owner[0] if name != key]
        add('missing-' + key, bad)
    for key, value in [('dram-size', 0), ('dram-size', 0xffffffffffffffff),
                       ('dram-base', 0xffffffffffffc000), ('dram-base', 0x14000), ('dram-size', 0x10000)]:
        bad = copy.deepcopy(first); replace(bad, [0], key, struct.pack('<Q', value)); add('DRAM-' + key, bad)
    for value in [b'gpu,t6031\0', b'gpu,t6034', b'gpu,t6034\0extra']:
        bad = copy.deepcopy(first); replace(bad, [1, 0], 'compatible', value); add('wrong-compatible', bad)
    for value in [b'__TEXT', b'__TEXT;__DATA\0extra', b'__DATA;__TEXT', b'__TEXT;__DATA\0\0']:
        bad = copy.deepcopy(first); replace(bad, [1, 1], 'segment-names', value); add('wrong-names', bad)
    for value in (0, 2, 0xffffffff):
        bad = copy.deepcopy(first); replace(bad, [1, 1, 0], 'pre-loaded', struct.pack('<I', value)); add('preloaded', bad)
    raw = next(value for key, value in first[1][1][1][1][0] if key == 'segment-ranges')
    for row in (0, 1):
        for offset, fmt, value in [(0, 'Q', 0), (0, 'Q', 0x10001), (8, 'Q', UPPER + 0x8000),
                                   (16, 'Q', 0), (24, 'I', 0), (24, 'I', 0x4001), (28, 'I', 2)]:
            bad = copy.deepcopy(first); changed = bytearray(raw); struct.pack_into('<' + fmt, changed, row * 32 + offset, value)
            replace(bad, [1, 1], 'segment-ranges', bytes(changed)); replace(bad, [1, 1, 0], 'segment-ranges', bytes(changed))
            add('bad-row-' + str(row) + '-' + str(offset), bad)
    bad = copy.deepcopy(first); replace(bad, [1, 1, 0], 'segment-ranges', raw[:32] + raw[:32]); add('nub-disagrees', bad)
    add('overlap', base(TEXT, TEXT), data=TEXT)
    bad_text = (0x10001, 0x14001); add('unaligned-source', base(bad_text, DATA), text=bad_text)
    high_text = (0x40000000000, 0x40000004000); high_data = (0x40000010000, 0x40000014000)
    add('unaddressable-source', base(high_text, high_data, (high_text[0], 0x100000)), text=high_text, data=high_data)
    for path, index in [([], 0), ([], 1), ([1], 0), ([1], 1), ([1, 1], 0)]:
        bad = copy.deepcopy(first); owner = bad
        for item in path:
            owner = owner[1][item]
        owner[1].append(copy.deepcopy(owner[1][index])); add('duplicate-child', bad)
        owner[1] = owner[1][:-1]; owner[1].pop(index); add('missing-child', bad)
    encoded = encode(first)
    for length in range(len(encoded)):
        add('truncated-' + str(length), encoded[:length])
    rng = random.Random(0x1716034)
    for index in range(64):
        add('random-' + str(index), rng.randbytes(rng.randrange(512)))
    add('oversize', bytes(0x200001))
    return rows
