# How dqspec is built and what it does

A walkthrough of the package for anyone who needs to extend it, review it, or decide
whether to adopt it. The [README](../README.md) covers *using* it; this covers *why it
is shaped the way it is*.

---

## 1. The problem

A dataset arrives in Databricks. Before anything downstream touches it, someone wants to
know: are the columns the ones we agreed on?

Today that knowledge lives in three bad places — a spreadsheet, someone's head, or a
hardcoded `assert set(df.columns) == {...}` buried in a notebook. None of them are
reviewable, none survive the author leaving, and none can be read by a business owner.

`dqspec` moves that knowledge into a YAML file that lives in git:

```yaml
title: Expected Columns
business_owner: TBD
columns:
  - name: PRACT ID
    type: string
  - name: LAST NAME
    type: string
```

and gives you one line to enforce it:

```python
validate_columns(df, load_contract("expected_columns.yaml")).raise_if_failed()
```

The YAML is the contract. It goes through pull request review like any other change,
and a non-engineer can read it.

---

## 2. The shape of the whole thing

Five modules, each with one job. Data flows left to right:

```
  expected_columns.yaml
          |
          |  contract.py      parse + validate the YAML itself
          v
      Contract  ────────────┐
   (ColumnSpec, assertions) │
                            │
  df (Spark / pandas /      │   checks.py       one function per assertion
      list of names)        │      │
          |                 │      │  column_names
          |  frames.py      │      │  column_types
          v                 │      │  row_count
      FrameView  ───────────┤      │
   (columns, dtypes, kind)  │      v
                            └──> validate.py    pick applicable checks, run them
                                     │
                                     v
                                 results.py
                              ValidationResult
                            (Issue, Issue, ...)
```

The two inputs are normalised independently — YAML becomes a `Contract`, any kind of
dataframe becomes a `FrameView` — and only then do they meet. Every check sees the same
two shapes no matter where either side came from.

That is the whole trick. Everything below is a consequence of it.

---

## 3. Module by module

### `contract.py` — YAML in, typed objects out

Turns a YAML file into a frozen `Contract` holding a tuple of `ColumnSpec`.

The design decision here is **strictness at load time**. A contract that is malformed —
missing `columns:`, a duplicate column, `assertions:` that is a list instead of a
mapping — raises `ContractError` the moment you load it, naming the offending index:

```
columns[4] is missing the required key 'name'
expected_columns.yaml: duplicate column 'DEGREE' at columns[3] and columns[11]
```

The argument for this is narrower than "malformed input is bad", and worth stating
precisely, because the obvious version of it is not very convincing.

The unconvincing version: a contract that parses down to *zero* columns passes every
validation. That is true — an empty contract against an 8-column frame returns
`PASS (0 errors, 8 warnings)`, because every real column is merely an undeclared extra.
But total loss like that is unlikely, and you would probably notice.

The version that matters is **an assertion you wrote silently not being enforced**. This
is not hypothetical; it happened here. The first draft of this module coerced the
assertions block with `data.get("assertions") or {}`, so a malformed `assertions: []`
became `{}` — and the `row_count` rule in the YAML would simply never have run. Nothing
would have said so. The result would have read `PASS`, and `checks_run` would not have
listed `row_count`, which nobody reads when a run is green.

That is the asymmetry the strictness is buying against: most bugs announce themselves,
but a data contract that fails open produces *silence*, and silence is exactly what
success looks like. The cost of being strict is a loud error on a file that was broken
anyway. The cost of being lenient is undetectable.

### The duplicate-key loader

Structural strictness alone would not have been enough, because the likeliest way to
lose a column leaves the structure perfectly valid:

```yaml
columns:
  - name: LAST NAME
  - name: FIRST NAME
    name: MIDDLE INITIAL     # copy-paste slip
```

YAML says a repeated key keeps the last one. `safe_load` returns two entries,
`FIRST NAME` is gone, and there is nothing malformed for a parser to object to — the
contract simply stops asking for a column, and validation passes on data that is
missing it.

So `parse_contract` does not use `safe_load`. It uses `_StrictLoader`, a `SafeLoader`
subclass whose mapping constructor rejects repeated keys:

```
expected_columns.yaml: duplicate key 'name' on line 4; YAML would silently keep only the last one
```

This applies to every mapping in the document, so a `row_count:` declared twice under
`assertions:` is caught the same way.

One carve-out: the check runs **before** PyYAML flattens `<<:` merge keys, and skips the
merge key itself. That means keys written literally in one mapping are compared, while a
key that deliberately overrides a merged default is left alone — `<<: *defaults` plus an
explicit `type: integer` is the point of merge keys, not a mistake.

Three entry points:

| function | for |
| --- | --- |
| `load_contract(path_or_name_or_file)` | the normal case |
| `parse_contract(text)` | YAML from a widget, a Delta cell, `dbutils.fs.head` |
| `packaged_contract_path(name)` | when you need the file itself, not the parsed form |

`load_contract` has one convenience worth knowing: a bare name with no directory
component that does not exist on disk (`"expected_columns.yaml"`) is resolved against
the contracts packaged inside `dqspec`. An absolute path
(`/Volumes/main/default/contracts/x.yaml`) is used as given. So the same call works
whether the YAML ships in the wheel or lives on a Volume, and a typo produces a list of
what *is* available rather than a bare `FileNotFoundError`.

Anything in the YAML that the parser does not model is kept on `contract.raw`. Adding a
key to the YAML never loses data, even before the code knows what the key means.

### `frames.py` — the adapter, and the reason there is no pyspark dependency

`view(frame)` returns a `FrameView`: column names, a `{name: dtype}` map, a `kind`
label, and an optional row-count callable.

It accepts a Spark DataFrame, a pandas DataFrame, or a plain list of column names, and
it does so **without importing either library**. Dispatch is by shape:

```python
schema = getattr(frame, "schema", None)
if schema is not None and hasattr(schema, "fields"):   # Spark
    ...
if hasattr(frame, "columns") and hasattr(frame, "dtypes"):   # pandas
    ...
```

Order matters here and it is a real trap: a Spark DataFrame *also* has `.columns` and
`.dtypes`, but Spark's `.dtypes` is a list of `(name, type)` tuples while pandas' is a
Series aligned to the columns. Checking for `.schema.fields` first is what keeps Spark
out of the pandas branch.

Two things fall out of duck-typing rather than importing:

**The package cannot disturb a Databricks cluster.** `pyspark` is not in
`dependencies`, so `%pip install` will never try to resolve, upgrade, or shadow the
runtime's own pyspark. On Databricks that is the difference between a library that
installs in four seconds and one that breaks the cluster's Spark session.

**The test suite runs without Spark.** `validate_columns(["A", "B"], contract)` is a
legitimate call, so most of the tests are pure Python and run in a second. That is not
a testing hack — it is genuinely useful when you have a schema but no data, such as
validating a contract against a table's metadata before the load runs.

`FrameView` also reports what it *cannot* do. A bare list of column names has no dtypes
and no row count, so `has_types` and `can_count_rows` are false. Section 4 explains why
that matters.

### `checks.py` — the registry

Every check is a function with the same signature:

```python
(FrameView, Contract, Options) -> Iterable[Issue]
```

registered by a decorator:

```python
@register("row_count", applies=_has_row_count_assertion)
def check_row_count(frame, contract, options):
    ...
    yield Issue(check="row_count", severity=ERROR, ...)
```

Three checks ship today. `column_names` is the one this project exists for;
`column_types` and `row_count` are there to prove the extension point is real, and
because your YAML already had a `row_count` assertion in it.

**The `applies` predicate is the important part.** It answers "is this check meaningful
for this frame and this contract?" — separately from whether it passes. `row_count`
applies only when the frame can be counted *and* the contract declares a `row_count`
assertion. `column_types` applies only when the frame exposes dtypes *and* at least one
column declares a `type`.

A check that does not apply is **skipped, not failed**. This is what lets `validate()`
have no arguments beyond the frame and the contract: it runs everything that makes
sense and stays quiet about the rest. Without it, either every contract would have to
declare every assertion, or callers would have to hand-maintain a list of check names
that tracks the YAML. Both push work onto the caller that the library can do itself.

`result.checks_run` records what actually ran, so "it passed" is never ambiguous about
what was actually examined.

### `validate.py` — the runner

Small on purpose. `validate()` does five things:

1. Coerce `contract` — accept a `Contract` or anything `load_contract` understands.
2. Coerce `frame` to a `FrameView`.
3. Resolve options — an `Options` instance, keyword overrides, or both (overrides win,
   via `dataclasses.replace`).
4. Select checks — the caller's list, validated against the registry, or every
   applicable check.
5. Run them in registry order and collect the issues.

An unknown check name raises `KeyError` listing what is registered, rather than
silently running fewer checks than you asked for. Same principle as the contract
parser: the failure modes that matter here are the quiet ones.

`validate_columns()` is a thin wrapper pinned to `checks=["column_names"]`. It exists
because it has a property worth guaranteeing by name: **it never touches data.** It
reads the schema and nothing else, so it is safe on a table of any size. `validate()`
with no arguments may pull in `row_count`, which is a full `count()` — a real scan on a
billion-row table.

### `results.py` — what comes back

An `Issue` is flat and stringly-typed on purpose: `check`, `severity`, `message`,
`column`, `expected`, `actual`. No nesting, no check-specific subclasses.

`ValidationResult` collects them and offers four ways to consume the outcome:

```python
result.ok               # bool; result is falsy when it failed
result.summary()        # printable report for a notebook
result.to_records()     # list[dict] -> spark.createDataFrame(..., RESULT_SCHEMA_DDL)
result.raise_if_failed()  # raise ValidationFailed
```

The flat shape is what makes `to_records()` work. Findings go straight into a Spark
DataFrame and from there into a Delta audit table, so data quality becomes something
you can trend over time rather than a string in a notebook someone has to be watching.

`RESULT_SCHEMA_DDL` is exported alongside it because most rows carry NULLs, and Spark
cannot infer column types from those — `createDataFrame` needs the schema handed to it.

---

## 4. The severity model

Two levels, and one rule:

> **A result fails if and only if it contains an error. Warnings never fail a run.**

What lands where:

| finding | severity | why |
| --- | --- | --- |
| required column missing | error | the contract is broken |
| undeclared extra column | warning by default | upstream added a field; usually benign, sometimes the first sign of a schema change. `allow_extra=False` promotes it |
| optional column absent | warning | `required: false` said this was allowed |
| type mismatch | warning by default | `strict_types=True` promotes it |
| columns out of order | warning | order almost never breaks anything by name |
| row count out of bounds | error | the assertion was explicit |

The defaults are deliberately loose in one direction only. Things that are *definitely*
wrong fail; things that are *probably* fine but worth seeing warn. A validator that
fails on a harmless extra column gets switched off within a week, and a validator that
is switched off catches nothing. Every warning can be promoted to an error by
configuration when you know your situation is stricter.

---

## 5. One call, end to end

```python
result = validate_columns(bad_df, contract)
```

where `bad_df` is missing `DEGREE`, has `pract_id` where the contract says `PRACT ID`,
and carries an undeclared `SCRATCH_COL`.

1. `validate_columns` calls `validate(..., checks=["column_names"])`.
2. `contract` is already a `Contract`, so it passes through untouched.
3. `view(bad_df)` sees `.schema.fields` → `FrameView(kind="spark", ...)` with the
   column names and `{name: "string"}` dtypes.
4. `Options()` — all defaults.
5. `"column_names"` is in the registry, so it is selected.
6. `check_column_names` runs:
   - Builds `actual_by_key`, mapping each frame column to itself (`normalize` is off,
     so the key *is* the name).
   - Builds `near`, mapping the *normalized* form of every frame column to the real
     ones. `pract_id` and `PRACT ID` both normalize to `pract_id`. This index exists
     only to explain failures.
   - Walks the contract in order. `PRACT ID` is not in `actual_by_key` → error, but
     `near["pract_id"]` has an unclaimed candidate, so the message says so. `DEGREE` is
     missing with no near match → plain error. The other 26 match.
   - Walks the frame for anything not in the contract → `pract_id` and `SCRATCH_COL`
     warn.
7. Four `Issue`s become a `ValidationResult`.

```
Expected Columns: FAIL (2 error(s), 2 warning(s)) [checks: column_names]
  [ERROR] column_names: missing required column 'PRACT ID' (did you mean 'pract_id'? -- set normalize=True to accept it)
  [ERROR] column_names: missing required column 'DEGREE'
  [WARNING] column_names: unexpected column 'pract_id' is not declared in the contract
  [WARNING] column_names: unexpected column 'SCRATCH_COL' is not declared in the contract
```

The near-match hint is the part worth defending. Set-difference validation tells you
`PRACT ID` is missing and, separately, that `pract_id` is unexpected, and leaves you to
notice these are the same fact. Naming it — and naming the flag that fixes it — turns a
five-minute puzzle into a decision. Renames and case drift are the overwhelmingly
common failure here, so the tool should be good at exactly that.

---

## 6. Type checking

YAML types are vocabulary, not Spark types. `type: string` should be satisfied by
Spark's `string`, pandas' `object`, and pandas 3's `str` alike, because the contract is
describing the data, not the engine.

So `checks.py` keeps a canonical-type table:

```python
_ACCEPTED = {
    "string":  {"string", "str", "varchar", "char", "text", "object", "string[python]"},
    "integer": {"int", "bigint", "int64", "int32", ...},
    "float":   {"float", "double", "decimal", "float64", ...},
    ...
}
```

and inverts it into `_CANONICAL` at import so both sides of a comparison fold to the
same canonical name. Parameterised Spark types are stripped first, so `decimal(10,2)`
folds to `float`.

A type the table does not recognise produces a warning saying it was **not checked**,
rather than a pass. An unrecognised assertion that silently succeeds is the worst
outcome available — you would believe you had coverage you did not.

---

## 7. Adding an assertion

The design target was that a new data-quality rule touches one file. It does:

```python
from dqspec.checks import register
from dqspec.results import ERROR, Issue

@register("no_nulls", applies=lambda frame, contract: "no_nulls" in contract.assertions)
def check_no_nulls(frame, contract, options):
    for column in contract.assertions["no_nulls"]:
        ...
        yield Issue(check="no_nulls", severity=ERROR, column=column, message="...")
```

`validate()` picks it up automatically. The loader, the runner, and the result objects
do not change — and neither does any calling notebook, which is the point.

If the check needs data rather than schema, gate it on `frame.kind == "spark"` in
`applies` so it stays out of the way of schema-only callers.

---

## 8. Getting it into Databricks

Three delivery paths, all supported by the same layout:

| | mechanism | needs |
| --- | --- | --- |
| **A** | Git folder + `sys.path` | private host registered as a Git provider |
| **B** | `%pip install git+https://...` | cluster egress to the git host, PAT in a secret scope |
| **C** | wheel on a Unity Catalog Volume | nothing at runtime |

The layout choice that makes B and C work is that **the contract YAML lives inside the
package**, at `src/dqspec/contracts/`, declared as `package-data` in `pyproject.toml`.
A contracts directory at the repo root would work fine for A and then silently vanish
from the wheel — you would install the code and no contracts. The built wheel contains
`dqspec/contracts/expected_columns.yaml`; that is worth re-checking whenever a new
contract is added.

This is not the only sensible arrangement. Contracts in the package are
version-controlled with the code and reviewed with it; contracts on a Volume can be
updated by a data steward without a deploy. `load_contract` takes either, so the choice
can be made per contract and changed later.

---

## 9. What it deliberately does not do

- **No row-level validation.** Nothing checks individual values today. `row_count` is
  the only assertion that reads data, and it only counts.
- **No contract generation.** There is no `infer_contract(df)`. A contract that was
  generated from the data asserts that the data looks like it did on the day you ran
  the generator, which is not an agreement anybody made.
- **No cross-table assertions.** One contract describes one dataset.
- **No enforcement.** It reports and raises. Quarantining bad data, alerting, and
  retries belong to the pipeline.

Known limits, honestly:

- **Content can still be lost in ways the loader cannot see.** The duplicate-key case
  is closed (§3), but a column entry deleted in a bad merge resolution, or a `columns:`
  list truncated by an editor, produces a smaller contract that is entirely valid.
  Nothing can distinguish that from someone deliberately shortening it. Contract edits
  deserve the same review attention as code, and the column count is worth eyeballing
  in a diff.
- **Duplicate column names in a frame pass silently.** Spark permits them; the checks
  match by name and would see the duplicate as satisfying the same contract entry.
- **Near-match hints are best-effort.** The candidate list excludes columns already
  matched by *earlier* contract entries only, so an unusual renaming pattern can
  produce a hint that a later entry will claim. The hint is advisory; the error is not.
- **`check_order` uses first occurrence** of each name, so it inherits the duplicate
  caveat above.

---

## 10. Reading the source

In dependency order, 712 lines including comments, docstrings and `__init__.py`:

| file | lines | read it for |
| --- | --- | --- |
| `results.py` | 89 | the vocabulary everything else emits |
| `frames.py` | 79 | how Spark and pandas are unified |
| `contract.py` | 184 | YAML parsing and its failure modes |
| `checks.py` | 236 | the registry and the three checks |
| `validate.py` | 66 | how they compose |

`tests/test_validate.py` is the fastest way to see intended behaviour — every rule in
section 4 has a test named after it.
