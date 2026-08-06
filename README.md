# dqspec

YAML-declared data contracts, validated against Spark or pandas DataFrames in Databricks.

Declare what a dataset is supposed to look like in YAML, keep that YAML in git next to
the code, and assert it from a notebook or job.

```python
from dqspec import load_contract, validate_columns

contract = load_contract("telemedicine.yaml")
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
title: Telemedicine
business_owner: TBD
cadence: Monthly
end_client: Internal        # or a list: [DMHC, CMS]
report_type: Internal       # or a list: [QHP, HSD]
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
  - name: ANT
    type: string
    allowed_values: ["P", "C", "T", " "]
```

`columns` also accepts the shorthand `columns: [PRACT ID, LAST NAME]` when you only
care about names. Unknown top-level keys are preserved on `contract.raw`.

`allowed_values` constrains the values a column may hold — see
[Value constraints](#value-constraints) below.

`cadence` records how often the dataset lands and is re-validated. It is optional, but
when present it must be one of **Annual, Quarterly, Monthly, Weekly, Daily, Ad Hoc** —
a closed vocabulary, exported as `dqspec.CADENCES`, so that an audit table can be
grouped by it. Case and punctuation are forgiven and folded to the canonical spelling
(`ad-hoc` → `Ad Hoc`); anything outside the list fails at load time:

```
telemedicine.yaml: unknown cadence 'Fortnightly'; expected one of Annual, Quarterly,
Monthly, Weekly, Daily, Ad Hoc
```

It reaches Python as `contract.cadence`. Nothing validates *against* it — the data has
no timestamp to compare with — so it is documentation the code can read, not an
assertion.

Two more closed vocabularies describe where the dataset goes:

| key | Python | vocabulary |
| --- | --- | --- |
| `end_client` | `contract.end_clients` | **DMHC, DHCS, CMS, Internal** (`dqspec.END_CLIENTS`) — who receives it |
| `report_type` | `contract.report_types` | **PAAS, QHP, TAR, AAR, HSD, Internal** (`dqspec.REPORT_TYPES`) — which programme it feeds |

They are independent: the same report type can go to different clients, and one client
receives several report types. A dataset can have more than one of either, so both keys
take either form —

```yaml
end_client: DMHC                # one
end_client: [DMHC, CMS]         # several
report_type: [QHP, HSD]
```

— and both arrive as a tuple, so calling code never has to ask which spelling the YAML
used. Unstated is `()`; an unknown value or a repeated one fails at load time.

Together, `cadence`, `end_client` and `report_type` are what make an audit table worth
keeping: stamp them onto your findings and "which of our monthly QHP feeds to DMHC
failed validation this quarter?" becomes a `GROUP BY` rather than an archaeology
project.

Contracts are loaded with a strict YAML loader that **rejects duplicate keys**, since
plain YAML would keep the last one and silently drop a column entry:

```
telemedicine.yaml: duplicate key 'name' on line 4; YAML would silently keep only the last one
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
| `max_distinct_values` | `1000` | cap on distinct values `allowed_values` reads per column |

### Value constraints

A column with `allowed_values` may hold nothing else:

```yaml
  - name: ANT
    type: string
    allowed_values: ["P", "C", "T", " "]
```

The shipped `telemedicine.yaml` puts this on all 19 site columns (`ANT` … `WCR`),
writing the list once with a YAML anchor (`&site_status`) and aliasing it on the rest
so the sites cannot drift apart.

```
[ERROR] allowed_values: column 'SFO' holds 2 value(s) the contract does not allow:
        'Z', 'p'; allowed: 'P', 'C', 'T', ' '
```

Worth knowing:

- **Comparison is exact.** `'p'`, `'T '` and `''` are all violations when the list says
  `'P'`, `'T'`, `' '`. That is the point — a single space and an empty string are
  different values in the source system.
- **Nulls are violations** unless the list includes `null`. A constrained column is one
  that must be populated.
- **It reads data** — one distinct scan per constrained column, so on Spark that is a
  job each. `validate_columns()` and `checks=["column_names"]` still never touch data.
- The scan stops at `max_distinct_values` and **warns** when it does, rather than
  implying it saw everything.

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
- **`row_count` and `allowed_values` cost a scan.** `row_count` calls `df.count()`;
  `allowed_values` runs one `distinct()` per constrained column. Use
  `checks=["column_names"]` for the cheap schema-only path on large tables.
- **`%pip install` must come before other imports** in a Databricks notebook, and is
  followed by `dbutils.library.restartPython()`.
