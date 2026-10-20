# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Write a parse tree to a cache and read it back whole, every token position included.

WHY THIS EXISTS. lark's Token pickles only its type, value, start position, line and column
(`Token.__reduce__`, lark 1.3.1); its end line, end column and end position come back as None. So a
tree read from the parse cache (`mio check`'s whole-file cache, and every segment of a segmented
parse) was not the tree a fresh parse built, and anything that read an end position got None from a
cached tree and a number from a fresh one. Found when `mio fmt` moved to the segmented parse and its
quoted-path rewrite, which splices the source by each token's end position, cut the wrong text.

A tree read back through `loads` is identical to the one given to `dumps`: every node, every token's
type and value, and all six token position fields (start_pos, line, column, end_pos, end_line,
end_column). Tree positions live on `meta`, which pickles whole already. Readers of a tree never need
to know whether it came from a cache.
"""
import io
import pickle

from lark import Token

PROTOCOL = pickle.HIGHEST_PROTOCOL


def _token(type_, value, start_pos, line, column, end_line, end_column, end_pos):
    """A Token with every position field, as a fresh parse makes it."""
    return Token(type_, value, start_pos, line, column, end_line, end_column, end_pos)


class _TreePickler(pickle.Pickler):
    def reducer_override(self, obj):
        if isinstance(obj, Token):
            return _token, (obj.type, str(obj), obj.start_pos, obj.line, obj.column,
                            obj.end_line, obj.end_column, obj.end_pos)
        return NotImplemented


def dumps(obj):
    """`obj` (a tree, or a structure holding trees) as bytes that read back with every position."""
    buf = io.BytesIO()
    _TreePickler(buf, protocol=PROTOCOL).dump(obj)
    return buf.getvalue()


def dump(obj, fh):
    """Write `obj` to an open binary file, keeping every token position."""
    _TreePickler(fh, protocol=PROTOCOL).dump(obj)


# Reading needs nothing special: `_token` is found by name, so pickle.load / pickle.loads read it.
loads = pickle.loads
load = pickle.load
