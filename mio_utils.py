# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
mio_utils.py
Shared utilities for the mio CLI.
"""

def tree_depth(tree, depth=0):
    """Return the maximum depth of a Lark parse tree."""
    if not hasattr(tree, "children"):
        return depth
    if not tree.children:
        return depth
    return max(tree_depth(c, depth + 1) for c in tree.children)
