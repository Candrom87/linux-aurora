#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Independent minimal FDT controls for deterministic fixture padding."""
import struct
import unittest
from cpu_boot import canonical_padding


def tree(padding):
    cells = lambda *values: struct.pack('>' + 'I' * len(values), *values)
    body = cells(1) + b'\0' + padding + cells(3, 1, 0) + b'\xff' + padding + cells(2, 9)
    strings = b'p\0'
    header = cells(0xd00dfeed, 56 + len(body) + len(strings), 56,
                   56 + len(body), 40, 17, 16, 0, len(strings), len(body))
    return header + bytes(16) + body + strings


class PaddingTests(unittest.TestCase):
    def test_padding_is_deterministic_and_property_data_preserved(self):
        expected = tree(bytes(3))
        self.assertEqual(canonical_padding(tree(b'abc')), expected)
        self.assertEqual(canonical_padding(tree(b'xyz')), expected)
        self.assertEqual(canonical_padding(expected), expected)
        self.assertIn(b'\xff', expected)

    def test_malformed_structure_refused(self):
        good = tree(b'abc')
        for data in (good[:30], good[:-10], good[:56] + b'\xff' * 4 + good[60:],
                     good[:68] + b'\xff' * 4 + good[72:]):
            with self.subTest(size=len(data)), self.assertRaises(ValueError): canonical_padding(data)


if __name__ == '__main__': unittest.main()
