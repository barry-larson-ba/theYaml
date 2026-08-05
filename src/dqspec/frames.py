"""Duck-typed adapter over "things that have columns".

Nothing here imports pyspark or pandas. A frame is inspected by shape, so the
same validator runs on a Spark DataFrame, a pandas DataFrame, or a bare list of
column names (handy in unit tests and when you only have a schema, not data).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

__all__ = ["FrameView", "view", "normalize_name"]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Fold a column name for fuzzy comparison.

    ``"PRACT ID"``, ``"pract_id"`` and ``"Pract-Id"`` all normalize to ``pract_id``.
    Used for near-match hints, and for matching outright when
    ``Options.normalize`` is on.
    """
    return _NON_ALNUM.sub("_", str(name).strip().lower()).strip("_")


@dataclass(frozen=True)
class FrameView:
    """Uniform read-only view of a frame's schema."""

    columns: Tuple[str, ...]
    dtypes: Dict[str, str]
    kind: str
    _row_count: Optional[Callable[[], int]] = None

    @property
    def has_types(self) -> bool:
        return bool(self.dtypes)

    @property
    def can_count_rows(self) -> bool:
        return self._row_count is not None

    def row_count(self) -> int:
        if self._row_count is None:
            raise TypeError("cannot count rows on a {0} frame".format(self.kind))
        return self._row_count()


def _spark_view(df: Any) -> FrameView:
    fields = df.schema.fields
    return FrameView(
        columns=tuple(f.name for f in fields),
        dtypes={f.name: f.dataType.simpleString() for f in fields},
        kind="spark",
        _row_count=df.count,
    )


def _pandas_view(df: Any) -> FrameView:
    columns = tuple(str(c) for c in df.columns)
    dtypes = {str(c): str(dt) for c, dt in zip(df.columns, df.dtypes)}
    return FrameView(columns=columns, dtypes=dtypes, kind="pandas", _row_count=lambda: len(df.index))


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
