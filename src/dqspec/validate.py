"""The runner: point a contract at a frame, get a :class:`ValidationResult`."""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence, Union

from .checks import REGISTRY, Options, applicable_checks
from .contract import Contract, load_contract
from .frames import view
from .results import Issue, ValidationResult

__all__ = ["validate", "validate_columns", "Options"]


def _as_contract(contract: Union[Contract, str, Any]) -> Contract:
    return contract if isinstance(contract, Contract) else load_contract(contract)


def validate(
    frame: Any,
    contract: Union[Contract, str],
    checks: Optional[Sequence[str]] = None,
    options: Optional[Options] = None,
    **option_overrides: Any,
) -> ValidationResult:
    """Run checks from ``contract`` against ``frame``.

    Args:
        frame: Spark DataFrame, pandas DataFrame, or a list of column names.
        contract: a :class:`Contract`, or a path/name passed to ``load_contract``.
        checks: check names to run. Defaults to every registered check that
            applies to this frame and contract. Pass ``["column_names"]`` to keep
            it to the cheap schema-only check.
        options: an :class:`Options` instance, or use keyword overrides.

    Returns:
        A :class:`ValidationResult`. It is falsy when any error-severity issue
        was found; call ``.raise_if_failed()`` to turn that into an exception.
    """
    spec = _as_contract(contract)
    frame_view = view(frame)

    if options is None:
        options = Options(**option_overrides)
    elif option_overrides:
        from dataclasses import replace

        options = replace(options, **option_overrides)

    if checks is None:
        selected = applicable_checks(frame_view, spec)
    else:
        selected = tuple(checks)
        unknown = [name for name in selected if name not in REGISTRY]
        if unknown:
            raise KeyError(
                "unknown check(s): {0}; registered: {1}".format(
                    ", ".join(unknown), ", ".join(sorted(REGISTRY))
                )
            )

    issues: list = []
    for name in selected:
        issues.extend(REGISTRY[name](frame_view, spec, options))

    return ValidationResult(
        contract_title=spec.title,
        checks_run=tuple(selected),
        issues=tuple(issues),
        contract_source=spec.source,
    )


def validate_columns(
    frame: Any,
    contract: Union[Contract, str],
    **option_overrides: Any,
) -> ValidationResult:
    """Column-name check only -- the fast path, and the place to start.

    Never counts rows or touches data, so it is safe on an arbitrarily large
    table: it reads the schema and nothing else.
    """
    return validate(frame, contract, checks=["column_names"], **option_overrides)
