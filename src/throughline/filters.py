# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""Safe evaluator for the SR-0045 boolean filter language (SR-0103).

Project files are untrusted input (NFR-0022): a filter string from a coverage
rule, a query, or a ``tl:table``/``tl:matrix`` directive must never reach
``eval``/``exec``. The expression is parsed to an AST with the standard-library
parser — which does not execute code — and walked with a strict node allowlist,
so an expression can read only the supplied namespace and can reach neither
builtins, imports, nor object internals.
"""
from __future__ import annotations

import ast
import operator

# Comparison operators the language allows. Everything absent here — arithmetic,
# bitwise, `is` — is rejected, so a filter stays a pure predicate over values.
_COMPARISONS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}

# The only methods callable in a filter — dict/str reads that cannot mutate or
# reach internals. `format`/`format_map` are excluded on purpose: a crafted
# format string can walk to __class__.
_METHODS = frozenset({
    "get", "keys", "values", "items",
    "startswith", "endswith", "lower", "upper", "strip",
})


class FilterError(ValueError):
    """A filter expression could not be parsed or evaluated (SR-0045)."""


def safe_eval(expr: str, namespace: dict) -> object:
    """Evaluate one filter expression against ``namespace`` without eval/exec.

    Raises :class:`FilterError` on a malformed or disallowed expression so
    callers can report it and fail fast (SR-0103, NFR-0022)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FilterError(f"could not parse filter: {e.msg}") from e
    try:
        return _eval(tree.body, namespace)
    except FilterError:
        raise
    except Exception as e:  # noqa: BLE001 - normalise to a typed error
        raise FilterError(str(e)) from e


def _eval(node, ns):  # noqa: PLR0911 - one branch per allowed node type
    if isinstance(node, ast.BoolOp):
        return _eval_boolop(node, ns)
    if isinstance(node, ast.UnaryOp):
        val = _eval(node.operand, ns)
        if isinstance(node.op, ast.Not):
            return not val
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        raise FilterError(f"unsupported operator '{type(node.op).__name__}'")
    if isinstance(node, ast.Compare):
        return _eval_compare(node, ns)
    if isinstance(node, ast.Name):
        if node.id in ns:
            return ns[node.id]
        raise FilterError(f"unknown name '{node.id}'")
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval(e, ns) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(e, ns) for e in node.elts)
    if isinstance(node, ast.Set):
        return {_eval(e, ns) for e in node.elts}
    if isinstance(node, ast.Subscript):
        return _eval(node.value, ns)[_eval_index(node, ns)]
    if isinstance(node, ast.Call):
        return _eval_call(node, ns)
    raise FilterError(f"unsupported expression '{type(node).__name__}'")


def _eval_boolop(node, ns):
    if isinstance(node.op, ast.And):
        result: object = True
        for operand in node.values:
            result = _eval(operand, ns)
            if not result:
                return result
        return result
    result = False
    for operand in node.values:
        result = _eval(operand, ns)
        if result:
            return result
    return result


def _eval_compare(node, ns):
    left = _eval(node.left, ns)
    for op, right_node in zip(node.ops, node.comparators):
        fn = _COMPARISONS.get(type(op))
        if fn is None:
            raise FilterError(f"unsupported comparison '{type(op).__name__}'")
        right = _eval(right_node, ns)
        if not fn(left, right):
            return False
        left = right
    return True


def _eval_index(node, ns):
    idx = node.slice
    if isinstance(idx, ast.Slice):
        raise FilterError("slices are not allowed in filters")
    return _eval(idx, ns)


def _eval_call(node, ns):
    func = node.func
    if not isinstance(func, ast.Attribute):
        raise FilterError("only method calls on values are allowed")
    if func.attr not in _METHODS:
        raise FilterError(f"method '{func.attr}' is not allowed in filters")
    if node.keywords:
        raise FilterError("keyword arguments are not allowed in filters")
    receiver = _eval(func.value, ns)
    method = getattr(receiver, func.attr, None)
    if not callable(method):
        raise FilterError(f"value has no method '{func.attr}'")
    return method(*[_eval(a, ns) for a in node.args])
