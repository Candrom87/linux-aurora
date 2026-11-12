# SPDX-License-Identifier: MIT
import struct

def node(name, props=(), children=()):
    return {'p':[(b'name', name+b'\0'), *props], 'c':list(children)}


def encode(tree):
    out = bytearray(struct.pack('<II', len(tree['p']), len(tree['c'])))
    for key, data in tree['p']:
        out += key.ljust(32,b'\0')+struct.pack('<I',len(data))+data
        out += bytes(-len(data)%4)
    for child in tree['c']:
        out += encode(child)
    return bytes(out)


def selected(tree, name):
    if tree['p'][0][1] == name+b'\0':
        return tree
    for child in tree['c']:
        found = selected(child, name)
        if found is not None:
            return found


def prop(tree, name, value):
    tree['p'] = [(key, value if key == name else data) for key,data in tree['p']]


def block(text, marker):
    start=text.index(marker);end=text.index('{',start)+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]
