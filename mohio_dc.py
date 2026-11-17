# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""`dataclasses.fields` and `dataclasses.is_dataclass`, answered once per class.

The compiler walks its syntax tree with these two on every node, and `mio check` of a large
application calls them millions of times. Their answer depends only on the node's CLASS, never on
the instance, and an AST class is defined once and never changes while the compiler runs. So the
answer is computed the first time a class is asked about and kept. Same values, same order, the same
TypeError for something that is not a dataclass: only the repeat work goes.

A class altered after it was first asked about (fields added at run time) would be answered from the
first answer. Nothing in the compiler does that; a class that needs it must not be walked through here."""
import dataclasses as _dataclasses

_FIELDS = {}
_IS_DATACLASS = {}


def fields(class_or_instance):
    """`dataclasses.fields`, kept per class."""
    cls = class_or_instance if isinstance(class_or_instance, type) else type(class_or_instance)
    try:
        return _FIELDS[cls]
    except KeyError:
        found = _dataclasses.fields(cls)    # raises TypeError for a non-dataclass, as before; not kept
        _FIELDS[cls] = found
        return found


def is_dataclass(obj):
    """`dataclasses.is_dataclass`, kept per class."""
    cls = obj if isinstance(obj, type) else type(obj)
    try:
        return _IS_DATACLASS[cls]
    except KeyError:
        answer = _dataclasses.is_dataclass(cls)
        _IS_DATACLASS[cls] = answer
        return answer
