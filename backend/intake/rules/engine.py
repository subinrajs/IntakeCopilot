"""Deterministic contrast-safety rules read from data/rules.yaml.

Each rule's `when` is parsed with `ast` and evaluated by a tiny interpreter that accepts only a
safe subset (boolean logic, comparisons, literals, variables and two whitelisted functions).
Anything else is rejected when the rules file is loaded, so a bad rule fails fast in tests.
"""

import ast
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

RuleResult = Literal["clear", "needs_labs", "needs_review"]
SEVERITY: dict[RuleResult, int] = {"clear": 0, "needs_labs": 1, "needs_review": 2}


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    when: str
    result: Literal["needs_labs", "needs_review"]
    message: str


class RulesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    params: dict[str, Any]
    rules: list[RuleSpec]
    red_flags: dict[Literal["P1", "P2"], list[str]]


class FiredRule(BaseModel):
    id: str
    result: Literal["needs_labs", "needs_review"]
    message: str


class ContrastCheck(BaseModel):
    rules_version: int
    result: RuleResult
    effective_contrast: Literal["none", "iv", "optional"]
    fired: list[FiredRule]


@dataclass(frozen=True)
class ContrastInput:
    modality: str | None
    protocol_contrast: Literal["none", "iv", "optional"]
    contrast_requested: bool | None
    egfr: float | None
    egfr_date: date | None
    allergies: list[str]
    medications: list[str]
    as_of: date


def effective_contrast(
    protocol_contrast: Literal["none", "iv", "optional"], requested: bool | None
) -> Literal["none", "iv", "optional"]:
    """An 'optional' protocol uses contrast when the request asks for it; unknown stays optional."""
    if protocol_contrast != "optional":
        return protocol_contrast
    if requested is None:
        return "optional"
    return "iv" if requested else "none"


class UnsafeExpression(ValueError):
    pass


_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: lambda a, b: a is b,
    ast.IsNot: lambda a, b: a is not b,
}
_ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _any_match(items: list[str] | None, terms: list[str]) -> bool:
    lowered = [str(i).lower() for i in items or []]
    return any(term.lower() in item for item in lowered for term in terms)


class _Interpreter:
    def __init__(self, variables: dict[str, Any], as_of: date | None) -> None:
        self.variables = variables
        self.as_of = as_of

    def _days_since(self, value: date | None) -> int | None:
        if value is None or self.as_of is None:
            return None
        return (self.as_of - value).days

    def eval(self, node: ast.AST) -> Any:
        match node:
            case ast.Expression(body=body):
                return self.eval(body)
            case ast.BoolOp(op=ast.And(), values=values):
                return all(self.eval(v) for v in values)
            case ast.BoolOp(op=ast.Or(), values=values):
                return any(self.eval(v) for v in values)
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return not self.eval(operand)
            case ast.Compare(left=left, ops=ops, comparators=comparators):
                current = self.eval(left)
                for op, comparator in zip(ops, comparators, strict=True):
                    right = self.eval(comparator)
                    if isinstance(op, _ORDERING) and (current is None or right is None):
                        return False  # a comparison involving a missing value is false
                    if not _COMPARE[type(op)](current, right):
                        return False
                    current = right
                return True
            case ast.Constant(value=value):
                return value
            case ast.List(elts=elts) | ast.Tuple(elts=elts):
                return [self.eval(e) for e in elts]
            case ast.Name(id=name):
                return self.variables[name]
            case ast.Call(func=ast.Name(id="days_since"), args=[arg]):
                return self._days_since(self.eval(arg))
            case ast.Call(func=ast.Name(id="any_match"), args=[items, terms]):
                return _any_match(self.eval(items), self.eval(terms))
        raise UnsafeExpression(f"unsupported expression: {ast.dump(node)}")


def _check_safe(tree: ast.AST, names: set[str]) -> None:
    """Static check at load time: only the supported node types and known variable names."""
    allowed = (
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.Compare,
        ast.Constant,
        ast.List,
        ast.Tuple,
        ast.Name,
        ast.Call,
        ast.Load,
        *_COMPARE.keys(),
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise UnsafeExpression(f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id in ("days_since", "any_match")
        ):
            raise UnsafeExpression("only days_since() and any_match() may be called")
        if isinstance(node, ast.Name) and node.id not in names | {"days_since", "any_match"}:
            raise UnsafeExpression(f"unknown name {node.id!r}")


VARIABLES = {
    "modality",
    "contrast",
    "contrast_requested",
    "egfr",
    "egfr_date",
    "allergies",
    "medications",
}


class RulesEngine:
    def __init__(self, spec: RulesFile) -> None:
        self.spec = spec
        names = VARIABLES | set(spec.params)
        self._compiled: list[tuple[RuleSpec, ast.Expression]] = []
        for rule in spec.rules:
            tree = ast.parse(rule.when, mode="eval")
            _check_safe(tree, names)
            self._compiled.append((rule, tree))

    @classmethod
    def from_file(cls, path: Path) -> "RulesEngine":
        return cls(RulesFile.model_validate(yaml.safe_load(path.read_text())))

    @property
    def version(self) -> int:
        return self.spec.version

    def check(self, data: ContrastInput) -> ContrastCheck:
        contrast = effective_contrast(data.protocol_contrast, data.contrast_requested)
        variables: dict[str, Any] = {
            **self.spec.params,
            "modality": data.modality,
            "contrast": contrast,
            "contrast_requested": data.contrast_requested,
            "egfr": data.egfr,
            "egfr_date": data.egfr_date,
            "allergies": data.allergies,
            "medications": data.medications,
        }
        interpreter = _Interpreter(variables, data.as_of)
        fired = [
            FiredRule(
                id=rule.id,
                result=rule.result,
                message=rule.message.format(**{k: _fmt(v) for k, v in variables.items()}),
            )
            for rule, tree in self._compiled
            if interpreter.eval(tree)
        ]
        worst: RuleResult = max(
            (f.result for f in fired), key=lambda r: SEVERITY[r], default="clear"
        )
        return ContrastCheck(
            rules_version=self.version, result=worst, effective_contrast=contrast, fired=fired
        )


def _fmt(value: Any) -> Any:
    return f"{value:g}" if isinstance(value, float) else value
