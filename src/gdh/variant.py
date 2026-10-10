"""Godot's binary Variant format (core/io/marshalls.cpp), as its remote debugger sends it over TCP: each message is a
32-bit little-endian length, then an encoded Variant, an Array [message, thread id, data].

Only what the debugger's messages hold is decoded: nil, bool, int, float, strings, string names and node paths
(as text), vectors and colors (as lists), arrays, dictionaries and packed arrays. Anything else raises VariantError,
and its caller skips the message, whose length it knows.
"""
import struct

NIL, BOOL, INT, FLOAT, STRING = 0, 1, 2, 3, 4
VECTOR2, VECTOR2I, RECT2, RECT2I, VECTOR3, VECTOR3I = 5, 6, 7, 8, 9, 10
TRANSFORM2D, VECTOR4, VECTOR4I, PLANE, QUATERNION, AABB, BASIS, TRANSFORM3D, PROJECTION, COLOR = range(11, 21)
STRING_NAME, NODE_PATH, RID, OBJECT, CALLABLE, SIGNAL, DICTIONARY, ARRAY = range(21, 29)
PACKED_BYTE, PACKED_INT32, PACKED_INT64, PACKED_FLOAT32, PACKED_FLOAT64, PACKED_STRING = range(29, 35)
PACKED_VECTOR2, PACKED_VECTOR3, PACKED_COLOR, PACKED_VECTOR4 = range(35, 39)

FLAG_64 = 1 << 16
# Floats in a row for each fixed-size math type (single precision, or double with FLAG_64).
FLOATS = {VECTOR2: 2, RECT2: 4, VECTOR3: 3, TRANSFORM2D: 6, VECTOR4: 4, PLANE: 4, QUATERNION: 4, AABB: 6, BASIS: 9,
          TRANSFORM3D: 12, PROJECTION: 16, COLOR: 4}
INTS = {VECTOR2I: 2, RECT2I: 4, VECTOR3I: 3, VECTOR4I: 4}


class VariantError(Exception):
    pass


def _pad(n):
    return (4 - n % 4) % 4


class Reader:
    def __init__(self, data):
        self.data = data
        self.at = 0

    def take(self, n):
        if self.at + n > len(self.data):
            raise VariantError("The data ends inside a value.")
        out = self.data[self.at:self.at + n]
        self.at += n
        return out

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def string(self):
        n = self.u32()
        text = self.take(n).decode("utf-8", errors="replace")
        self.take(_pad(n))
        return text

    def value(self):
        header = self.u32()
        kind = header & 0xFF
        wide = bool(header & FLAG_64)
        if kind == NIL:
            return None
        if kind == BOOL:
            return bool(self.u32())
        if kind == INT:
            return struct.unpack("<q" if wide else "<i", self.take(8 if wide else 4))[0]
        if kind == FLOAT:
            return struct.unpack("<d" if wide else "<f", self.take(8 if wide else 4))[0]
        if kind in (STRING, STRING_NAME):
            return self.string()
        if kind in FLOATS:
            n = FLOATS[kind]
            return list(struct.unpack(f"<{n}{'d' if wide else 'f'}", self.take(n * (8 if wide else 4))))
        if kind in INTS:
            n = INTS[kind]
            return list(struct.unpack(f"<{n}i", self.take(n * 4)))
        if kind == ARRAY:
            self._container_type((header >> 16) & 3)
            count = self.u32() & 0x7FFFFFFF
            return [self.value() for _ in range(count)]
        if kind == DICTIONARY:
            self._container_type((header >> 16) & 3)
            self._container_type((header >> 18) & 3)
            count = self.u32() & 0x7FFFFFFF
            out = {}
            for _ in range(count):
                key = self.value()
                out[key if isinstance(key, (str, int, float, bool, type(None))) else repr(key)] = self.value()
            return out
        if kind == PACKED_BYTE:
            n = self.u32()
            out = self.take(n)
            self.take(_pad(n))
            return bytes(out)
        if kind in (PACKED_INT32, PACKED_INT64, PACKED_FLOAT32, PACKED_FLOAT64):
            n = self.u32()
            code, size = {PACKED_INT32: ("i", 4), PACKED_INT64: ("q", 8), PACKED_FLOAT32: ("f", 4),
                          PACKED_FLOAT64: ("d", 8)}[kind]
            return list(struct.unpack(f"<{n}{code}", self.take(n * size)))
        if kind == PACKED_STRING:
            return [self.string() for _ in range(self.u32())]
        if kind in (PACKED_VECTOR2, PACKED_VECTOR3, PACKED_COLOR, PACKED_VECTOR4):
            n = self.u32()
            per = {PACKED_VECTOR2: 2, PACKED_VECTOR3: 3, PACKED_COLOR: 4, PACKED_VECTOR4: 4}[kind]
            wide_items = wide and kind != PACKED_COLOR
            size = 8 if wide_items else 4
            flat = struct.unpack(f"<{n * per}{'d' if wide_items else 'f'}", self.take(n * per * size))
            return [list(flat[i:i + per]) for i in range(0, len(flat), per)]
        if kind == NODE_PATH:
            return self._node_path()
        raise VariantError(f"Variant type {kind} isn't decoded.")

    def _container_type(self, kind):
        """A typed array's or dictionary's element type: none, a builtin's number, a class name, or a script."""
        if kind == 1:
            self.u32()
        elif kind in (2, 3):
            self.string()

    def _node_path(self):
        names = self.u32()
        if not names & 0x80000000:
            raise VariantError("An old-format NodePath.")
        names &= 0x7FFFFFFF
        subnames = self.u32()
        flags = self.u32()
        if flags & 2:
            subnames += 1  # the property, in the old encoding
        parts = [self.string() for _ in range(names + subnames)]
        path = "/".join(parts[:names])
        if flags & 1:
            path = "/" + path
        if subnames:
            path += ":" + ":".join(parts[names:])
        return path


def decode(data):
    """A Variant from its bytes."""
    return Reader(data).value()


def encode(value):
    """A Variant's bytes, for what the debugger takes: None, bool, int, float, str, list and dict."""
    if value is None:
        return struct.pack("<I", NIL)
    if isinstance(value, bool):
        return struct.pack("<II", BOOL, int(value))
    if isinstance(value, int):
        if -(2 ** 31) <= value < 2 ** 31:
            return struct.pack("<Ii", INT, value)
        return struct.pack("<Iq", INT | FLAG_64, value)
    if isinstance(value, float):
        return struct.pack("<Id", FLOAT | FLAG_64, value)
    if isinstance(value, str):
        raw = value.encode()
        return struct.pack("<II", STRING, len(raw)) + raw + b"\0" * _pad(len(raw))
    if isinstance(value, (list, tuple)):
        return struct.pack("<II", ARRAY, len(value)) + b"".join(encode(v) for v in value)
    if isinstance(value, dict):
        return struct.pack("<II", DICTIONARY, len(value)) + b"".join(encode(k) + encode(v) for k, v in value.items())
    raise VariantError(f"Can't encode {type(value).__name__}.")


def message(name, data, thread=1):
    """A debugger message as it goes over TCP: its length, then [name, thread, data]."""
    body = encode([name, thread, list(data)])
    return struct.pack("<I", len(body)) + body
