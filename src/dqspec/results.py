"""Result objects returned by the validators.

Kept separate from the checks so that adding a new check never means touching
the reporting surface: every check just emits :class:`Issue` records.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = ["ERROR", "WARNING", "Issue", "ValidationResult", "ValidationFailed", "RESULT_SCHEMA_DDL"]

ERROR = "error"
WARNING = "warning"

# Explicit schema for ValidationResult.to_records() -> spark.createDataFrame(...).
# Needed because most rows carry NULLs and Spark cannot infer types from those.
RESULT_SCHEMA_DDL = (
    "check string, severity string, column string, "
    "expected string, actual string, message string"
)


class ValidationFailed(AssertionError):
    """Raised by :meth:`ValidationResult.raise_if_failed`."""

    def __init__(self, result: "ValidationResult"):
        super().__init__(result.summary())
        self.result = result


@dataclass(frozen=True)
class Issue:
    """A single contract violation."""

    check: str
    severity: str
    message: str
    column: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None

    def as_record(self) -> Dict[str, Optional[str]]:
        return {
            "check": self.check,
            "severity": self.severity,
            "column": self.column,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }


@dataclass
class ValidationResult:
    """Outcome of running one or more checks against a frame."""

    contract_title: str
    checks_run: Tuple[str, ...] = ()
    issues: Tuple[Issue, ...] = ()
    contract_source: Optional[str] = None

    @property
    def errors(self) -> Tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == ERROR)

    @property
    def warnings(self) -> Tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == WARNING)

    @property
    def ok(self) -> bool:
        """True when nothing failed at ``error`` severity. Warnings do not fail."""
        return not self.errors

    def __bool__(self) -> bool:
        return self.ok

    def raise_if_failed(self) -> "ValidationResult":
        """Raise :class:`ValidationFailed` if any error-severity issue exists.

        Call this in a job to make the notebook/task fail loudly. Returns self
        so it can be chained: ``validate(df, c).raise_if_failed()``.
        """
        if not self.ok:
            raise ValidationFailed(self)
        return self

    def to_records(self) -> List[Dict[str, Optional[str]]]:
        """Plain dicts, ready for ``spark.createDataFrame(..., RESULT_SCHEMA_DDL)``."""
        return [i.as_record() for i in self.issues]

    def summary(self) -> str:
        head = "{0}: {1} ({2} error(s), {3} warning(s)) [checks: {4}]".format(
            self.contract_title,
            "PASS" if self.ok else "FAIL",
            len(self.errors),
            len(self.warnings),
            ", ".join(self.checks_run) or "none",
        )
        if not self.issues:
            return head
        lines = [head]
        for issue in self.issues:
            lines.append("  [{0}] {1}: {2}".format(issue.severity.upper(), issue.check, issue.message))
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.summary()

    def __repr__(self) -> str:  # pragma: no cover - display only
        return "<ValidationResult {0} errors={1} warnings={2}>".format(
            "PASS" if self.ok else "FAIL", len(self.errors), len(self.warnings)
        )
