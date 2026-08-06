"""dqspec -- YAML-declared data contracts, validated against DataFrames.

    from dqspec import load_contract, validate_columns

    contract = load_contract("telemedicine.yaml")
    result = validate_columns(df, contract)
    print(result.summary())
    result.raise_if_failed()

Depends on PyYAML only. pyspark and pandas are duck-typed, never imported, so
installing this on a Databricks cluster cannot disturb the runtime.
"""

from .checks import REGISTRY, Options, register
from .contract import (
    CADENCES,
    END_CLIENTS,
    REPORT_TYPES,
    ColumnSpec,
    Contract,
    ContractError,
    list_packaged_contracts,
    load_contract,
    packaged_contract_path,
    parse_contract,
)
from .frames import FrameView, normalize_name, view
from .results import (
    ERROR,
    RESULT_SCHEMA_DDL,
    WARNING,
    Issue,
    ValidationFailed,
    ValidationResult,
)
from .validate import validate, validate_columns

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # contracts
    "Contract",
    "ColumnSpec",
    "ContractError",
    "CADENCES",
    "END_CLIENTS",
    "REPORT_TYPES",
    "load_contract",
    "parse_contract",
    "packaged_contract_path",
    "list_packaged_contracts",
    # validation
    "validate",
    "validate_columns",
    "Options",
    # results
    "ValidationResult",
    "ValidationFailed",
    "Issue",
    "ERROR",
    "WARNING",
    "RESULT_SCHEMA_DDL",
    # extension points
    "register",
    "REGISTRY",
    "FrameView",
    "view",
    "normalize_name",
]
