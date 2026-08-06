"""Duck-typed adapter over "things that have columns".

Nothing here imports pyspark or pandas. A frame is inspected by shape, so the
same validator runs on a Spark DataFrame, a pandas DataFrame, or a bare list of
column names (handy in unit tests and when you only have a schema, not data).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = ["FrameView", "view", "normalize_name"]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Fold a column name for fuzzy comparison.

    ``"PRACT ID"``, ``"pract_id"`` and ``"Pract-Id"`` all normalize to ``pract_id``.
    Used for near-match hints, and for matching outright when
    ``Options.normalize`` is on.
    """
    return _NON_ALNUM.sub("_", str(name).strip().lower()).strip("_")


def _is_missing(value: Any) -> bool:
    """True for None, NaN and NaT -- everything that means "no value here"."""
    if value is None:
        return True
    try:
        return bool(value != value)  # only NaN/NaT are unequal to themselves
    except Exception:  # pragma: no cover - exotic value types
        return False


@dataclass(frozen=True)
class FrameView:
    """Uniform read-only view of a frame's schema."""

    columns: Tuple[str, ...]
    dtypes: Dict[str, str]
    kind: str
    _row_count: Optional[Callable[[], int]] = None
    _distinct: Optional[Callable[[str, int], Sequence[Any]]] = None

    @property
    def has_types(self) -> bool:
        return bool(self.dtypes)

    @property
    def can_count_rows(self) -> bool:
        return self._row_count is not None

    @property
    def can_read_values(self) -> bool:
        """True when the frame carries data, not just a schema."""
        return self._distinct is not None

    def row_count(self) -> int:
        if self._row_count is None:
            raise TypeError("cannot count rows on a {0} frame".format(self.kind))
        return self._row_count()

    def distinct_values(self, column: str, limit: int) -> Tuple[Any, ...]:
        """Up to ``limit`` distinct values of ``column``, in no particular order.

        Reads data. ``None``, ``NaN`` and ``NaT`` all come back as ``None`` so a
        caller compares against one spelling of "missing".
        """
        if self._distinct is None:
            raise TypeError("cannot read values from a {0} frame".format(self.kind))
        if column not in self.columns:
            raise KeyError("no column {0!r} in this {1} frame".format(column, self.kind))
        if limit < 1:
            raise ValueError("limit must be at least 1, got {0}".format(limit))

        values: List[Any] = [
            None if _is_missing(v) else v for v in self._distinct(column, int(limit))
        ]
        try:
            return tuple(dict.fromkeys(values))  # order-preserving dedupe
        except TypeError:  # pragma: no cover - unhashable values in the column
            return tuple(values)


def _spark_view(df: Any) -> FrameView:
    fields = df.schema.fields

    def distinct(column: str, limit: int) -> Sequence[Any]:
        # df[column], not df.select(column): a name with a space would otherwise
        # be parsed as a SQL expression.
        return [row[0] for row in df.select(df[column]).distinct().limit(limit).collect()]

    return FrameView(
        columns=tuple(f.name for f in fields),
        dtypes={f.name: f.dataType.simpleString() for f in fields},
        kind="spark",
        _row_count=df.count,
        _distinct=distinct,
    )


def _pandas_view(df: Any) -> FrameView:
    columns = tuple(str(c) for c in df.columns)
    dtypes = {str(c): str(dt) for c, dt in zip(df.columns, df.dtypes)}

    def distinct(column: str, limit: int) -> Sequence[Any]:
        return list(df[column].drop_duplicates().head(limit))

    return FrameView(
        columns=columns,
        dtypes=dtypes,
        kind="pandas",
        _row_count=lambda: len(df.index),
        _distinct=distinct,
    )


def _names_view(names: Sequence[Any]) -> FrameView:
    return FrameView(columns=tuple(str(n) for n in names), dtypes={}, kind="columns")


def view(frame: Any) -> FrameView:
    """Adapt ``frame`` to a :class:`FrameView`.

    Accepts a Spark DataFrame, a pandas DataFrame, or any sequence of column
    names. Raises ``TypeError`` for anything else.
    """
    if isinstance(frame, FrameView):
        return frame

    # Spark: has a StructType schema with .fields. Checked first because a Spark
    # DataFrame also exposes .columns, and only .schema carries the types.
    schema = getattr(frame, "schema", None)
    if schema is not None and hasattr(schema, "fields"):
        return _spark_view(frame)

    # pandas / any frame exposing aligned .columns and .dtypes
    if hasattr(frame, "columns") and hasattr(frame, "dtypes"):
        return _pandas_view(frame)

    # Spark Connect / older shapes that only expose .columns
    columns = getattr(frame, "columns", None)
    if columns is not None and not callable(columns):
        return _names_view(list(columns))

    if isinstance(frame, (str, bytes)):
        raise TypeError(
            "expected a DataFrame or a list of column names, got a string. "
            "Did you mean [{0!r}]?".format(frame)
        )

    if isinstance(frame, Sequence):
        return _names_view(frame)

    raise TypeError(
        "don't know how to read columns from {0}; pass a Spark DataFrame, a "
        "pandas DataFrame, or a list of column names".format(type(frame).__name__)
    )
