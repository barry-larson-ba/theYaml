"""Load and parse a YAML data contract into typed Python objects.

The parsing layer is deliberately strict: a malformed contract should fail
loudly here, at load time, rather than silently validating nothing later.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

import yaml

__all__ = [
    "ColumnSpec",
    "Contract",
    "ContractError",
    "load_contract",
    "parse_contract",
    "packaged_contract_path",
    "list_packaged_contracts",
]


class ContractError(ValueError):
    """Raised when a YAML file is not a well-formed contract."""


@dataclass(frozen=True)
class ColumnSpec:
    """One entry under ``columns:`` in the YAML."""

    name: str
    type: Optional[str] = None
    required: bool = True
    description: Optional[str] = None

    @classmethod
    def from_yaml(cls, raw: Any, index: int) -> "ColumnSpec":
        # Shorthand: `columns: [PRACT ID, LAST NAME]`
        if isinstance(raw, str):
            return cls(name=raw)

        if not isinstance(raw, Mapping):
            raise ContractError(
                "columns[{0}] must be a mapping or a string, got {1}".format(
                    index, type(raw).__name__
                )
            )

        if "name" not in raw:
            raise ContractError("columns[{0}] is missing the required key 'name'".format(index))

        name = raw["name"]
        if not isinstance(name, str) or not name.strip():
            raise ContractError("columns[{0}].name must be a non-empty string".format(index))

        col_type = raw.get("type")
        if col_type is not None and not isinstance(col_type, str):
            raise ContractError("columns[{0}].type must be a string if present".format(index))

        required = raw.get("required", True)
        if not isinstance(required, bool):
            raise ContractError("columns[{0}].required must be true or false".format(index))

        description = raw.get("description")
        if description is not None and not isinstance(description, str):
            raise ContractError("columns[{0}].description must be a string if present".format(index))

        return cls(
            name=name,
            type=col_type.strip().lower() if col_type else None,
            required=required,
            description=description,
        )


@dataclass(frozen=True)
class Contract:
    """A parsed data contract."""

    title: str
    columns: Tuple[ColumnSpec, ...]
    business_owner: Optional[str] = None
    assertions: Mapping[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def column_names(self) -> Tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def required_column_names(self) -> Tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.required)

    def column(self, name: str) -> Optional[ColumnSpec]:
        for spec in self.columns:
            if spec.name == name:
                return spec
        return None

    def __repr__(self) -> str:  # pragma: no cover - display only
        return "Contract(title={0!r}, columns={1}, assertions={2})".format(
            self.title, len(self.columns), sorted(self.assertions)
        )


class _DuplicateKey(ContractError):
    """Internal: raised by the loader, re-raised with the source path attached."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys.

    Plain YAML keeps the *last* of a repeated key without complaint, which in a
    contract means a column entry silently disappears:

        columns:
          - name: LAST NAME
          - name: FIRST NAME
            name: MIDDLE INITIAL   # <- FIRST NAME is gone, and it parses cleanly

    The document stays structurally valid, so nothing downstream can notice. A
    repeated key in a contract is always a mistake, so reject it at load time.
    """


_MERGE_TAG = "tag:yaml.org,2002:merge"


def _no_duplicate_keys(loader: "_StrictLoader", node: Any, deep: bool = False) -> Dict[Any, Any]:
    seen = set()
    for key_node, _ in node.value:
        # Checked before SafeConstructor flattens `<<:` merge keys, so only keys
        # literally written in this mapping are compared. A key that overrides a
        # merged default is deliberate, not a duplicate -- and the merge key
        # itself has no constructor at this stage, so skip it.
        if key_node.tag == _MERGE_TAG:
            continue
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in seen
        except TypeError:
            continue  # unhashable key; construct_mapping raises its own error below
        if duplicate:
            raise _DuplicateKey(
                "duplicate key {0!r} on line {1}; YAML would silently keep only the "
                "last one".format(key, key_node.start_mark.line + 1)
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys
)


def parse_contract(text: str, source: Optional[str] = None) -> Contract:
    """Parse contract YAML held in a string.

    Useful when the YAML arrives from somewhere that is not a filesystem path
    (a widget, a Delta table, ``dbutils.fs.head``, ...).
    """
    try:
        data = yaml.load(text, Loader=_StrictLoader)
    except _DuplicateKey as exc:
        raise ContractError("{0}: {1}".format(source or "<string>", exc)) from None
    except yaml.YAMLError as exc:
        raise ContractError("{0} is not valid YAML: {1}".format(source or "<string>", exc)) from exc

    if data is None:
        raise ContractError("{0} is empty".format(source or "<string>"))
    if not isinstance(data, Mapping):
        raise ContractError(
            "{0} must contain a YAML mapping at the top level, got {1}".format(
                source or "<string>", type(data).__name__
            )
        )

    raw_columns = data.get("columns")
    if raw_columns is None:
        raise ContractError("{0} has no 'columns:' section".format(source or "<string>"))
    if isinstance(raw_columns, (str, bytes)) or not isinstance(raw_columns, Sequence):
        raise ContractError("{0}: 'columns' must be a list".format(source or "<string>"))
    if len(raw_columns) == 0:
        raise ContractError("{0}: 'columns' is an empty list".format(source or "<string>"))

    columns = tuple(ColumnSpec.from_yaml(raw, i) for i, raw in enumerate(raw_columns))

    seen: Dict[str, int] = {}
    for i, spec in enumerate(columns):
        if spec.name in seen:
            raise ContractError(
                "{0}: duplicate column {1!r} at columns[{2}] and columns[{3}]".format(
                    source or "<string>", spec.name, seen[spec.name], i
                )
            )
        seen[spec.name] = i

    assertions = data.get("assertions")
    if assertions is None:
        assertions = {}
    if not isinstance(assertions, Mapping):
        raise ContractError("{0}: 'assertions' must be a mapping".format(source or "<string>"))

    return Contract(
        title=str(data.get("title") or "untitled contract"),
        columns=columns,
        business_owner=data.get("business_owner"),
        assertions=dict(assertions),
        source=source,
        raw=dict(data),
    )


def load_contract(source: Union[str, "os.PathLike[str]", io.IOBase]) -> Contract:
    """Load a contract from a path or an open file object.

    ``source`` may be a local path, a ``/Volumes/...`` Unity Catalog path, a
    ``/Workspace/...`` path, or anything else the driver can open with ``open()``.
    Bare names such as ``"expected_columns.yaml"`` that do not exist on disk are
    resolved against the contracts packaged inside ``dqspec``.
    """
    if hasattr(source, "read"):
        text = source.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        return parse_contract(text, source=getattr(source, "name", "<file>"))

    path = Path(source)
    if not path.exists() and path.parent == Path("."):
        packaged = packaged_contract_path(path.name)
        if packaged is not None:
            path = packaged

    if not path.exists():
        raise ContractError(
            "contract not found: {0}\nPackaged contracts available: {1}".format(
                source, ", ".join(list_packaged_contracts()) or "(none)"
            )
        )

    return parse_contract(path.read_text(encoding="utf-8"), source=str(path))


def _contracts_dir() -> Optional[Path]:
    try:
        from importlib.resources import files  # Python 3.9+
    except ImportError:  # pragma: no cover - Python < 3.9
        return Path(__file__).parent / "contracts"
    try:
        return Path(str(files("dqspec") / "contracts"))
    except (ModuleNotFoundError, TypeError):  # pragma: no cover
        return Path(__file__).parent / "contracts"


def packaged_contract_path(name: str) -> Optional[Path]:
    """Absolute path to a contract shipped inside the package, or ``None``."""
    directory = _contracts_dir()
    if directory is None:
        return None
    candidate = directory / name
    if candidate.exists():
        return candidate
    if not name.endswith((".yaml", ".yml")):
        for suffix in (".yaml", ".yml"):
            candidate = directory / (name + suffix)
            if candidate.exists():
                return candidate
    return None


def list_packaged_contracts() -> Tuple[str, ...]:
    """Names of every contract YAML shipped inside the package."""
    directory = _contracts_dir()
    if directory is None or not directory.is_dir():
        return ()
    return tuple(sorted(p.name for p in directory.iterdir() if p.suffix in (".yaml", ".yml")))
