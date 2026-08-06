"""The check registry.

Every check is a function ``(FrameView, Contract, Options) -> Iterable[Issue]``
registered under a name. Adding a new data-quality assertion means writing one
function and decorating it -- no changes to the loader, the runner, or the
result objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .contract import Contract
from .frames import FrameView, normalize_name
from .results import ERROR, WARNING, Issue

__all__ = ["Options", "register", "REGISTRY", "applicable_checks"]


@dataclass(frozen=True)
class Options:
    """Knobs shared by all checks."""

    allow_extra: bool = True
    """Columns in the frame but not the contract: warning (True) or error (False)."""

    check_order: bool = False
    """Also require contract columns to appear in the frame in contract order."""

    normalize: bool = False
    """Match names case/punctuation-insensitively ("PRACT ID" == "pract_id")."""

    strict_types: bool = False
    """Type mismatches become errors instead of warnings."""

    max_distinct_values: int = 1000
    """Cap on the distinct values ``allowed_values`` reads per column."""


CheckFn = Callable[[FrameView, Contract, Options], Iterable[Issue]]

REGISTRY: Dict[str, "Check"] = {}


@dataclass(frozen=True)
class Check:
    name: str
    fn: CheckFn
    applies: Callable[[FrameView, Contract], bool]

    def __call__(self, frame: FrameView, contract: Contract, options: Options) -> Tuple[Issue, ...]:
        return tuple(self.fn(frame, contract, options))


def register(
    name: str,
    applies: Optional[Callable[[FrameView, Contract], bool]] = None,
) -> Callable[[CheckFn], CheckFn]:
    """Register a check under ``name``.

    ``applies`` decides whether the check is meaningful for a given frame and
    contract; a check that does not apply is skipped rather than failed.
    """

    def decorator(fn: CheckFn) -> CheckFn:
        REGISTRY[name] = Check(name=name, fn=fn, applies=applies or (lambda f, c: True))
        return fn

    return decorator


def applicable_checks(frame: FrameView, contract: Contract) -> Tuple[str, ...]:
    """Names of every registered check that is meaningful here, in registry order."""
    return tuple(name for name, chk in REGISTRY.items() if chk.applies(frame, contract))


# --------------------------------------------------------------------------
# column_names -- the check this project exists for
# --------------------------------------------------------------------------


def _key(name: str, options: Options) -> str:
    return normalize_name(name) if options.normalize else name


@register("column_names")
def check_column_names(frame: FrameView, contract: Contract, options: Options) -> Iterator[Issue]:
    """Every required contract column is present; report anything unexpected."""
    actual_by_key: Dict[str, str] = {}
    for col in frame.columns:
        actual_by_key.setdefault(_key(col, options), col)

    # Near-match index, always normalized, used only to explain failures.
    near: Dict[str, List[str]] = {}
    for col in frame.columns:
        near.setdefault(normalize_name(col), []).append(col)

    matched: List[str] = []
    for spec in contract.columns:
        key = _key(spec.name, options)
        if key in actual_by_key:
            matched.append(actual_by_key[key])
            continue

        if not spec.required:
            yield Issue(
                check="column_names",
                severity=WARNING,
                column=spec.name,
                expected="present",
                actual="absent",
                message="optional column {0!r} is not in the frame".format(spec.name),
            )
            continue

        hint = ""
        candidates = [c for c in near.get(normalize_name(spec.name), []) if c not in matched]
        if candidates:
            hint = " (did you mean {0}? -- set normalize=True to accept it)".format(
                ", ".join(repr(c) for c in candidates)
            )
        yield Issue(
            check="column_names",
            severity=ERROR,
            column=spec.name,
            expected="present",
            actual="missing",
            message="missing required column {0!r}{1}".format(spec.name, hint),
        )

    expected_keys = {_key(s.name, options) for s in contract.columns}
    for col in frame.columns:
        if _key(col, options) not in expected_keys:
            yield Issue(
                check="column_names",
                severity=WARNING if options.allow_extra else ERROR,
                column=col,
                expected="not in contract",
                actual="present",
                message="unexpected column {0!r} is not declared in the contract".format(col),
            )

    if options.check_order and len(matched) > 1:
        positions = [frame.columns.index(c) for c in matched]
        if positions != sorted(positions):
            out_of_order = [
                matched[i] for i in range(1, len(positions)) if positions[i] < positions[i - 1]
            ]
            yield Issue(
                check="column_names",
                severity=WARNING,
                column=None,
                expected="contract order",
                actual="frame order",
                message="columns are not in contract order; first offenders: {0}".format(
                    ", ".join(repr(c) for c in out_of_order[:5])
                ),
            )


# --------------------------------------------------------------------------
# column_types -- proves the registry extends past names
# --------------------------------------------------------------------------

# Canonical type -> native type strings accepted for it (Spark simpleString on
# the left of each set, pandas/numpy dtype strings on the right).
_ACCEPTED: Dict[str, frozenset] = {
    "string": frozenset({"string", "str", "varchar", "char", "text", "object", "string[python]"}),
    "integer": frozenset(
        {
            "int", "integer", "bigint", "smallint", "tinyint", "long", "short", "byte",
            "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64",
            "int64[pyarrow]", "int32[pyarrow]",
        }
    ),
    "float": frozenset({"float", "double", "real", "decimal", "numeric", "float32", "float64"}),
    "boolean": frozenset({"boolean", "bool", "bool_"}),
    "date": frozenset({"date"}),
    "timestamp": frozenset({"timestamp", "timestamp_ntz", "datetime64[ns]", "datetime64[us]"}),
    "binary": frozenset({"binary", "bytes"}),
}

# YAML spellings folded onto a canonical type.
_CANONICAL: Dict[str, str] = {}
for _canon, _natives in _ACCEPTED.items():
    _CANONICAL[_canon] = _canon
    for _native in _natives:
        _CANONICAL.setdefault(_native, _canon)
_CANONICAL.update({"datetime": "timestamp", "number": "float", "num": "float"})

_PARAMS = re.compile(r"\(.*\)$")


def _canonical(type_string: str) -> Optional[str]:
    base = _PARAMS.sub("", str(type_string).strip().lower())
    return _CANONICAL.get(base)


def _contract_declares_types(frame: FrameView, contract: Contract) -> bool:
    return frame.has_types and any(spec.type for spec in contract.columns)


@register("column_types", applies=_contract_declares_types)
def check_column_types(frame: FrameView, contract: Contract, options: Options) -> Iterator[Issue]:
    """Declared column types match the frame's actual types."""
    severity = ERROR if options.strict_types else WARNING
    actual_by_key = {_key(c, options): c for c in frame.columns}

    for spec in contract.columns:
        if not spec.type:
            continue
        actual_name = actual_by_key.get(_key(spec.name, options))
        if actual_name is None:
            continue  # already reported by column_names

        actual_type = frame.dtypes.get(actual_name, "")
        want, got = _canonical(spec.type), _canonical(actual_type)

        if want is None:
            yield Issue(
                check="column_types",
                severity=WARNING,
                column=spec.name,
                expected=spec.type,
                actual=actual_type,
                message="contract declares unknown type {0!r} for {1!r}; not checked".format(
                    spec.type, spec.name
                ),
            )
        elif want != got:
            yield Issue(
                check="column_types",
                severity=severity,
                column=spec.name,
                expected=spec.type,
                actual=actual_type,
                message="column {0!r} is {1}, contract expects {2}".format(
                    spec.name, actual_type, spec.type
                ),
            )


# --------------------------------------------------------------------------
# allowed_values -- the first check that reads values, not just the schema
# --------------------------------------------------------------------------


def _contract_constrains_values(frame: FrameView, contract: Contract) -> bool:
    return frame.can_read_values and any(s.allowed_values is not None for s in contract.columns)


def _show(value: Any) -> str:
    return "null" if value is None else repr(value)


def _join(values: Sequence[Any], cap: int = 10) -> str:
    shown = ", ".join(_show(v) for v in values[:cap])
    hidden = len(values) - cap
    return shown if hidden <= 0 else "{0}, ... (+{1} more)".format(shown, hidden)


@register("allowed_values", applies=_contract_constrains_values)
def check_allowed_values(frame: FrameView, contract: Contract, options: Options) -> Iterator[Issue]:
    """Every value in a column is one its ``allowed_values`` list permits.

    NOTE: this reads data -- one distinct scan per constrained column, which on
    Spark is a job each. Exclude it with
    ``validate(df, contract, checks=["column_names"])`` when you only want the
    schema checks.

    Nulls (and NaN) are violations unless the contract lists ``null`` among the
    allowed values, since a constrained column is one that must be populated.
    """
    limit = max(int(options.max_distinct_values), 1)
    actual_by_key = {_key(c, options): c for c in frame.columns}

    for spec in contract.columns:
        if spec.allowed_values is None:
            continue
        actual_name = actual_by_key.get(_key(spec.name, options))
        if actual_name is None:
            continue  # already reported by column_names

        # One over the cap, so a full page tells us the scan was truncated.
        found = frame.distinct_values(actual_name, limit + 1)
        truncated = len(found) > limit
        offenders = [v for v in found[:limit] if v not in spec.allowed_values]

        if offenders:
            yield Issue(
                check="allowed_values",
                severity=ERROR,
                column=spec.name,
                expected=_join(spec.allowed_values),
                actual=_join(offenders),
                message="column {0!r} holds {1} value(s) the contract does not allow: "
                "{2}; allowed: {3}".format(
                    spec.name, len(offenders), _join(offenders), _join(spec.allowed_values)
                ),
            )

        if truncated:
            yield Issue(
                check="allowed_values",
                severity=WARNING,
                column=spec.name,
                expected="at most {0} distinct values".format(limit),
                actual="more than {0}".format(limit),
                message="only the first {0} distinct values of {1!r} were checked; raise "
                "max_distinct_values to widen the scan".format(limit, spec.name),
            )


# --------------------------------------------------------------------------
# row_count -- reads assertions:, the growth path for the rest of the YAML
# --------------------------------------------------------------------------

_COMPARATORS: Dict[str, Tuple[Callable[[Any, Any], bool], str]] = {
    "greater_than": (lambda a, b: a > b, "> {0}"),
    "greater_than_or_equal_to": (lambda a, b: a >= b, ">= {0}"),
    "less_than": (lambda a, b: a < b, "< {0}"),
    "less_than_or_equal_to": (lambda a, b: a <= b, "<= {0}"),
    "equal_to": (lambda a, b: a == b, "== {0}"),
    "min": (lambda a, b: a >= b, ">= {0}"),
    "max": (lambda a, b: a <= b, "<= {0}"),
}


def _has_row_count_assertion(frame: FrameView, contract: Contract) -> bool:
    return frame.can_count_rows and isinstance(contract.assertions.get("row_count"), Mapping)


@register("row_count", applies=_has_row_count_assertion)
def check_row_count(frame: FrameView, contract: Contract, options: Options) -> Iterator[Issue]:
    """Row count satisfies every comparator under ``assertions.row_count``.

    NOTE: this triggers a full count, which on a large Spark table is a real
    scan. Exclude it with ``validate(df, contract, checks=["column_names"])``
    when you only want the cheap schema checks.
    """
    rules = contract.assertions.get("row_count") or {}
    actual = frame.row_count()

    for rule, bound in rules.items():
        comparator = _COMPARATORS.get(rule)
        if comparator is None:
            yield Issue(
                check="row_count",
                severity=WARNING,
                column=None,
                expected=str(rule),
                actual=str(actual),
                message="unknown row_count rule {0!r}; known rules: {1}".format(
                    rule, ", ".join(sorted(_COMPARATORS))
                ),
            )
            continue

        predicate, template = comparator
        if not predicate(actual, bound):
            yield Issue(
                check="row_count",
                severity=ERROR,
                column=None,
                expected=template.format(bound),
                actual=str(actual),
                message="row count {0} violates {1} {2}".format(actual, rule, bound),
            )
