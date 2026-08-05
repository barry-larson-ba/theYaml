# dqspec

YAML-declared data contracts, validated against Spark or pandas DataFrames in Databricks.

Declare what a dataset is supposed to look like in YAML, keep that YAML in git next to
the code, and assert it from a notebook or job.

```python
from dqspec import load_contract, validate_columns

contract = load_contract("expected_columns.yaml")
validate_columns(df, contract).raise_if_failed()
```

For how the package is put together and why, see
[docs/EXPLAINER.md](docs/EXPLAINER.md).

## Layout

```
src/dqspec/
  contract.py    YAML -> Contract / ColumnSpec. Strict: bad contracts fail at load time.
  frames.py      Duck-typed adapter over Spark / pandas / a bare list of column names.
  checks.py      The check registry. Add an assertion = add one function here.
  validate.py    The runner.
  results.py     Issue / ValidationResult.
  contracts/     The YAML contracts, shipped as package data.
notebooks/
  validate_dataframe.py   Databricks notebook source for the whole flow.
tests/
```

PyYAML is the only dependency. **pyspark and pandas are never imported** — frames are
duck-typed — so installing this on a cluster cannot shadow or reinstall the runtime's
own pyspark.

## Getting it into Databricks

### A. Git folder (start here)

Workspace → Create → Git folder → point at the private GitHub host. Then in a notebook
inside the repo:

```python
import sys; sys.path.insert(0, "/Workspace/Users/you@your-company.com/theYaml/src")
import dqspec
```

`notebooks/validate_dataset.py` does this for you — it walks up from the notebook
looking for `src/dqspec`, with an editable `REPO_ROOT` fallback.

No install, no PAT at runtime, and `git pull` picks up edits. If your admin has not yet
added the private host under **Settings → Linked accounts → Git provider**
(GitHub Enterprise Server + the server URL), this is the one thing to get sorted first.

### B. `%pip install` from the private git server

Needs cluster network egress to the internal host. Keep the PAT in a secret scope, and
use **three cells** — a `%pip` magic must be the first line of its own cell:

```python
# cell 1
token = dbutils.secrets.get(scope="github", key="pat")
```
```
# cell 2
%pip install git+https://$token@github.your-company.com/your-org/theYaml.git@main
```
```python
# cell 3
dbutils.library.restartPython()
```

`$token` is Databricks variable substitution into the magic — verify it resolves on
your runtime. If it does not, store the full `https://<pat>@host/org/repo.git` URL as
a single secret and substitute that.

### C. Wheel on a Unity Catalog Volume

```bash
python -m build --wheel        # -> dist/dqspec-0.1.0-py3-none-any.whl
```

Upload to a Volume, then `%pip install /Volumes/main/default/libs/dqspec-0.1.0-py3-none-any.whl`.
The contract YAML is package data, so it travels inside the wheel.

## Contract format

```yaml
%YAML 1.2
---
title: Expected Columns
business_owner: TBD
assertions:
  row_count:
    greater_than: 100
    less_than: 1000000
columns:
  - name: PRACT ID
    type: string
  - name: NPI
    type: integer
    required: false
    description: Optional until the 2026 feed lands
```

`columns` also accepts the shorthand `columns: [PRACT ID, LAST NAME]` when you only
care about names. Unknown top-level keys are preserved on `contract.raw`.

Contracts are loaded with a strict YAML loader that **rejects duplicate keys**, since
plain YAML would keep the last one and silently drop a column entry:

```
expected_columns.yaml: duplicate key 'name' on line 4; YAML would silently keep only the last one
```

Anchors and `<<:` merge keys still work — overriding a merged default is not a duplicate.

## Validating

```python
validate_columns(df, contract)          # names only; reads the schema, never scans data
validate(df, contract)                  # every check that applies to this frame + contract
validate(df, contract, checks=["column_names", "column_types"])
```

`frame` may be a Spark DataFrame, a pandas DataFrame, or a plain list of column names.

Options (keyword arguments to either function):

| option | default | effect |
| --- | --- | --- |
| `allow_extra` | `True` | undeclared columns warn; `False` makes them errors |
| `normalize` | `False` | match names ignoring case/spaces/underscores (`PRACT ID` == `pract_id`) |
| `check_order` | `False` | also warn when columns are not in contract order |
| `strict_types` | `False` | type mismatches become errors instead of warnings |

### Results

`ValidationResult` is falsy when any **error**-severity issue exists; warnings never
fail a run.

```python
result.ok            # bool
result.errors        # tuple[Issue]
result.warnings      # tuple[Issue]
result.summary()     # printable report
result.to_records()  # list[dict] -> spark.createDataFrame(..., RESULT_SCHEMA_DDL)
result.raise_if_failed()
```

Missing columns that look like a rename are called out rather than just reported
missing:

```
[ERROR] column_names: missing required column 'PRACT ID'
        (did you mean 'pract_id'? -- set normalize=True to accept it)
```

## Adding an assertion

One function in `checks.py`, and `validate()` picks it up:

```python
from dqspec.checks import register
from dqspec.results import ERROR, Issue

@register("no_nulls", applies=lambda frame, contract: frame.kind == "spark")
def check_no_nulls(frame, contract, options):
    for spec in contract.columns:
        if not contract.assertions.get("no_nulls", {}).get(spec.name):
            continue
        ...
        yield Issue(check="no_nulls", severity=ERROR, column=spec.name, message="...")
```

`applies` decides whether the check is meaningful for a given frame and contract; a
check that does not apply is skipped, not failed. That is how `row_count` stays quiet
when handed a bare list of column names, and how `column_types` stays quiet when the
contract declares no types.

## Development

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"
pytest
```

## Gotchas

- **Column names with spaces.** The current contract uses `PRACT ID`, `LAST NAME` etc.
  Spark tolerates these, but any SQL referring to them needs backticks:
  ``SELECT `PRACT ID` FROM ...``. Delta with column mapping disabled also rejects
  ` ,;{}()\n\t=` in column names on write. If you control the upstream, snake_case is
  less friction; if you do not, `normalize=True` bridges the two spellings.
- **`row_count` costs a scan.** It calls `df.count()`. Use
  `checks=["column_names"]` for the cheap schema-only path on large tables.
- **`%pip install` must come before other imports** in a Databricks notebook, and is
  followed by `dbutils.library.restartPython()`.
