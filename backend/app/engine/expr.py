"""A tiny, safe expression language for P&L line definitions.

Line formulas are stored in the "P&L line structure" master, so they must be
editable by finance users and must never be executed with ``eval``.  The
grammar is deliberately small::

    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/') unary)*
    unary   := ('+' | '-') unary | atom
    atom    := NUMBER | REF | FUNC '(' expr (',' expr)* ')' | '(' expr ')'
    REF     := '[' kind ':' body ']'   e.g. [L:sales_total], [C:Power & Fuel|total]
    FUNC    := SUM | NEG | ABS | MIN | MAX

References are resolved by a callback supplied by the engine, which returns a
numpy array (one value per plant x product-group cell) or a scalar.
Division by zero yields 0, matching Excel's ``IFERROR(...,0)`` convention used
throughout costing workbooks.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable, Union

import numpy as np

Value = Union[float, np.ndarray]

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)"
    r"|(?P<ref>\[[^\[\]]+\])"
    r"|(?P<func>[A-Za-z_][A-Za-z_0-9]*)\s*(?=\()"
    r"|(?P<op>[-+*/(),])"
    r")"
)

FUNCTIONS = {"SUM", "NEG", "ABS", "MIN", "MAX"}


class ExpressionError(ValueError):
    """Raised for syntax errors or unknown references."""


@dataclass(frozen=True)
class Ref:
    kind: str
    body: str

    @property
    def text(self) -> str:
        return f"[{self.kind}:{self.body}]" if self.body else f"[{self.kind}]"


@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class BinOp:
    op: str
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class UnaryNeg:
    operand: "Node"


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple["Node", ...]


Node = Union[Ref, Num, BinOp, UnaryNeg, Call]


def _tokenize(text: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ExpressionError(f"Unexpected character at position {pos}: {text[pos:pos + 15]!r}")
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise ExpressionError("Unexpected end of expression")
        self.i += 1
        return tok

    def expect_op(self, op: str) -> None:
        kind, val = self.take()
        if kind != "op" or val != op:
            raise ExpressionError(f"Expected {op!r} but found {val!r}")

    def parse(self) -> Node:
        node = self.expr()
        if self.peek() is not None:
            raise ExpressionError(f"Unexpected token {self.peek()[1]!r}")
        return node

    def expr(self) -> Node:
        node = self.term()
        while (tok := self.peek()) and tok[0] == "op" and tok[1] in "+-":
            self.take()
            node = BinOp(tok[1], node, self.term())
        return node

    def term(self) -> Node:
        node = self.unary()
        while (tok := self.peek()) and tok[0] == "op" and tok[1] in "*/":
            self.take()
            node = BinOp(tok[1], node, self.unary())
        return node

    def unary(self) -> Node:
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in "+-":
            self.take()
            operand = self.unary()
            return UnaryNeg(operand) if tok[1] == "-" else operand
        return self.atom()

    def atom(self) -> Node:
        kind, val = self.take()
        if kind == "num":
            return Num(float(val))
        if kind == "ref":
            inner = val[1:-1]
            if ":" not in inner:              # body-less references such as [MCADJ]
                if not inner.strip().isidentifier():
                    raise ExpressionError(f"Reference {val} must look like [KIND:body]")
                return Ref(inner.strip().upper(), "")
            rkind, body = inner.split(":", 1)
            return Ref(rkind.strip().upper(), body.strip())
        if kind == "func":
            name = val.upper()
            if name not in FUNCTIONS:
                raise ExpressionError(f"Unknown function {val!r}")
            self.expect_op("(")
            args = [self.expr()]
            while (tok := self.peek()) and tok == ("op", ","):
                self.take()
                args.append(self.expr())
            self.expect_op(")")
            return Call(name, tuple(args))
        if kind == "op" and val == "(":
            node = self.expr()
            self.expect_op(")")
            return node
        raise ExpressionError(f"Unexpected token {val!r}")


_CACHE: dict[str, Node] = {}


def parse(text: str) -> Node:
    """Parse an expression (results are cached; nodes are immutable)."""
    if text not in _CACHE:
        if not text or not text.strip():
            _CACHE[text] = Num(0.0)
        else:
            _CACHE[text] = _Parser(_tokenize(text)).parse()
    return _CACHE[text]


def refs(node: Node) -> Iterable[Ref]:
    """Yield every reference used by an expression tree."""
    if isinstance(node, Ref):
        yield node
    elif isinstance(node, BinOp):
        yield from refs(node.left)
        yield from refs(node.right)
    elif isinstance(node, UnaryNeg):
        yield from refs(node.operand)
    elif isinstance(node, Call):
        for a in node.args:
            yield from refs(a)


def _safe_div(a: Value, b: Value) -> Value:
    with np.errstate(divide="ignore", invalid="ignore"):
        res = np.divide(a, b)
    if isinstance(res, np.ndarray):
        res = np.where(np.isfinite(res), res, 0.0)
    elif not np.isfinite(res):
        res = 0.0
    return res


def evaluate(node: Node | str, resolve: Callable[[Ref], Value]) -> Value:
    """Evaluate an expression; ``resolve`` maps a Ref to a value."""
    if isinstance(node, str):
        node = parse(node)
    if isinstance(node, Num):
        return node.value
    if isinstance(node, Ref):
        return resolve(node)
    if isinstance(node, UnaryNeg):
        return -evaluate(node.operand, resolve)
    if isinstance(node, BinOp):
        left = evaluate(node.left, resolve)
        right = evaluate(node.right, resolve)
        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        if node.op == "*":
            return left * right
        return _safe_div(left, right)
    if isinstance(node, Call):
        vals = [evaluate(a, resolve) for a in node.args]
        if node.name == "SUM":
            total: Value = 0.0
            for v in vals:
                total = total + v
            return total
        if node.name == "NEG":
            return -vals[0]
        if node.name == "ABS":
            return np.abs(vals[0])
        if node.name == "MIN":
            return np.minimum.reduce(np.broadcast_arrays(*vals)) if len(vals) > 1 else vals[0]
        if node.name == "MAX":
            return np.maximum.reduce(np.broadcast_arrays(*vals)) if len(vals) > 1 else vals[0]
    raise ExpressionError(f"Cannot evaluate node {node!r}")


def to_text(node: Node) -> str:
    """Render an expression tree back to text (used for display/round-trips)."""
    if isinstance(node, Num):
        v = node.value
        return str(int(v)) if float(v).is_integer() else repr(v)
    if isinstance(node, Ref):
        return node.text
    if isinstance(node, UnaryNeg):
        return f"-({to_text(node.operand)})"
    if isinstance(node, BinOp):
        return f"({to_text(node.left)} {node.op} {to_text(node.right)})"
    if isinstance(node, Call):
        return f"{node.name}({', '.join(to_text(a) for a in node.args)})"
    raise ExpressionError(f"Unknown node {node!r}")
