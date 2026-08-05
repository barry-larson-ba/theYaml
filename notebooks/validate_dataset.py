# Databricks notebook source
# MAGIC %md
# MAGIC # Validating a dataset against a YAML contract
# MAGIC
# MAGIC End-to-end walkthrough: load a contract from YAML in this repo, point it at a
# MAGIC practitioner-privileges dataset, and prove the columns are what we agreed on.
# MAGIC
# MAGIC | | |
# MAGIC |---|---|
# MAGIC | **Contract** | `src/dqspec/contracts/expected_columns.yaml` |
# MAGIC | **Dataset** | `practitioner_privileges` (built below; swap for your real table) |
# MAGIC | **Runs on** | any cluster / SQL warehouse with Python — no libraries to install |
# MAGIC
# MAGIC Start here. `validate_dataframe.py` in this folder covers the other two ways to
# MAGIC get the package onto a cluster (`%pip install` from git, and a wheel on a Volume).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Put the package on the path
# MAGIC
# MAGIC This notebook lives in a **Databricks Git folder**, so the package is already on
# MAGIC disk next to it — nothing to install, and `git pull` picks up changes.
# MAGIC
# MAGIC The cell below finds the repo root by walking up from the notebook looking for
# MAGIC `src/dqspec`. If auto-detection fails, set `REPO_ROOT` by hand — it is the
# MAGIC folder you cloned into, e.g. `/Workspace/Users/henry.t.ford@jpl.org/theYaml`.

# COMMAND ----------

import os
import sys

# Edit this if auto-detection below fails. Path to the cloned Git folder.
REPO_ROOT = "/Workspace/Users/henry.t.ford@jpl.org/theYaml"


def _notebook_dir():
    """Workspace path of the folder containing this notebook."""
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    return "/Workspace" + os.path.dirname(ctx.notebookPath().get())


def find_repo_root(fallback=REPO_ROOT):
    """First ancestor directory that actually contains src/dqspec."""
    starts = []
    try:
        starts.append(_notebook_dir())
    except Exception:  # noqa: BLE001 - context is unavailable in some job runs
        pass
    starts.append(os.getcwd())  # Git folders set cwd to the notebook's directory
    starts.append(fallback)

    for start in starts:
        current = start
        for _ in range(6):  # notebooks/ -> repo root is one hop; allow for deeper nesting
            if os.path.isdir(os.path.join(current, "src", "dqspec")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    raise RuntimeError(
        "Could not find src/dqspec. Set REPO_ROOT to your Git folder path.\n"
        "Tried: {0}".format(", ".join(starts))
    )


REPO_ROOT = find_repo_root()
SRC = os.path.join(REPO_ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

print("repo root:", REPO_ROOT)

# COMMAND ----------

import dqspec
from dqspec import RESULT_SCHEMA_DDL, load_contract, validate, validate_columns

print("dqspec", dqspec.__version__, "loaded from", os.path.dirname(dqspec.__file__))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Load the contract
# MAGIC
# MAGIC A bare filename resolves to the YAML shipped inside the package. To let a data
# MAGIC steward edit contracts without a code deploy, pass an absolute path instead:
# MAGIC
# MAGIC ```python
# MAGIC contract = load_contract("/Volumes/main/credentialing/contracts/expected_columns.yaml")
# MAGIC ```

# COMMAND ----------

contract = load_contract("expected_columns.yaml")

print("title        :", contract.title)
print("owner        :", contract.business_owner)
print("source       :", contract.source)
print("columns      :", len(contract.columns))
print("assertions   :", dict(contract.assertions))

# COMMAND ----------

# MAGIC %md
# MAGIC The parsed contract is ordinary Python, so it displays like anything else. This
# MAGIC is the view to send a business owner when asking "is this still right?"

# COMMAND ----------

display(
    spark.createDataFrame(
        [
            (i, c.name, c.type, c.required, c.description)
            for i, c in enumerate(contract.columns)
        ],
        "position int, column string, type string, required boolean, description string",
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. The dataset
# MAGIC
# MAGIC A practitioner-privileges roster: who they are, where they are credentialed, and
# MAGIC a `Y`/`N` flag per facility.
# MAGIC
# MAGIC **In real use, delete this cell and read your table:**
# MAGIC
# MAGIC ```python
# MAGIC df = spark.table("main.credentialing.practitioner_privileges")
# MAGIC ```
# MAGIC
# MAGIC Note the contract drives the schema below — the split between identity fields and
# MAGIC facility codes comes out of the YAML, not out of a second hardcoded list.

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

IDENTITY_FIELDS = contract.column_names[:9]
FACILITY_CODES = contract.column_names[9:]

print("identity :", IDENTITY_FIELDS)
print("facility :", FACILITY_CODES)

# (id, last, first, mi, degree, primary facility, home priv 1, home priv 2, telemedicine, granted)
PRACTITIONERS = [
    ("OKONKWO", "ADAEZE", "N", "MD", "SFO", "CARDIOLOGY", "ECHOCARDIOGRAPHY", "TELE-CARDIOLOGY", "SFO SCL RWC"),
    ("HALVORSEN", "BIRGIT", "L", "DO", "OAK", "INTERNAL MEDICINE", "", "", "OAK ANT"),
    ("NAKAMURA", "KENJI", "T", "MD", "SJO", "ORTHOPEDIC SURGERY", "SPORTS MEDICINE", "", "SJO SCL SLN"),
    ("ABDULLAH", "RASHIDA", "", "MD", "SAC", "NEPHROLOGY", "CRITICAL CARE", "TELE-NEPHROLOGY", "SAC VAC ROS WCR"),
    ("PETROVA", "MILENA", "K", "NP", "RWC", "FAMILY MEDICINE", "", "TELE-PRIMARY CARE", "RWC SFO"),
    ("BOATENG", "KWESI", "A", "MD", "FRE", "EMERGENCY MEDICINE", "", "", "FRE FRS MAN"),
    ("LINDQVIST", "SOREN", "", "MD", "SRF", "ANESTHESIOLOGY", "PAIN MEDICINE", "", "SRF SRO SSF"),
    ("MWANGI", "NJERI", "W", "PA", "SSC", "GENERAL SURGERY", "", "", "SSC SCL"),
    ("VARGAS", "ESTEBAN", "R", "MD", "VAL", "PSYCHIATRY", "", "TELE-BEHAVIORAL HEALTH", "VAL VAC SAC"),
    ("THORNBURY", "GWENDOLYN", "P", "MD", "SLN", "OBSTETRICS", "GYNECOLOGY", "", "SLN SJO ROS"),
]


def to_row(index, practitioner):
    last, first, mi, degree, primary, priv1, priv2, tele, granted = practitioner
    identity = ("P{0:06d}".format(100000 + index), last, first, mi, degree, primary, priv1, priv2, tele)
    held = set(granted.split())
    return identity + tuple("Y" if code in held else "N" for code in FACILITY_CODES)


schema = StructType([StructField(name, StringType(), True) for name in contract.column_names])
rows = [to_row(i, PRACTITIONERS[i % len(PRACTITIONERS)]) for i in range(150)]

spark.createDataFrame(rows, schema).createOrReplaceTempView("practitioner_privileges")

# From here on the notebook reads a table, exactly as it would in production.
df = spark.table("practitioner_privileges")
display(df.limit(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Validate — the happy path
# MAGIC
# MAGIC `validate_columns` reads the schema and nothing else. It never scans the data, so
# MAGIC it costs the same on 150 rows or 150 billion.

# COMMAND ----------

result = validate_columns(df, contract)

print(result.summary())
print("\nok:", result.ok)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Now the realistic case: upstream changed something
# MAGIC
# MAGIC Three things go wrong at once, and all three happen in real life:
# MAGIC
# MAGIC 1. `DEGREE` was **dropped** from the feed.
# MAGIC 2. `PRACT ID` was **renamed** to `pract_id` when someone re-exported it.
# MAGIC 3. `LOAD_TIMESTAMP` was **added** by a new ingestion step.

# COMMAND ----------

drifted = (
    df.drop("DEGREE")
    .withColumnRenamed("PRACT ID", "pract_id")
    .withColumn("LOAD_TIMESTAMP", F.current_timestamp())
)

drift_result = validate_columns(drifted, contract)
print(drift_result.summary())

# COMMAND ----------

# MAGIC %md
# MAGIC Three findings, three different severities' worth of meaning:
# MAGIC
# MAGIC - **`DEGREE` missing** — a genuine break. The contract said it would be there.
# MAGIC - **`PRACT ID` missing, `pract_id` unexpected** — reported as one thought, not
# MAGIC   two. The near-match hint names the flag that fixes it.
# MAGIC - **`LOAD_TIMESTAMP` unexpected** — a *warning*, not an error. An extra column
# MAGIC   rarely breaks anything downstream, and a validator that fails the pipeline over
# MAGIC   one gets switched off within a week.
# MAGIC
# MAGIC So the run failed on `DEGREE` and the rename, which is the correct outcome.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Accepting the rename
# MAGIC
# MAGIC If `pract_id` is the new reality and you are fine with it, `normalize=True` folds
# MAGIC case, spaces, underscores and hyphens together — so `PRACT ID`, `pract_id` and
# MAGIC `Pract-Id` all satisfy the same contract entry. `DEGREE` still fails, correctly.

# COMMAND ----------

print(validate_columns(drifted, contract, normalize=True).summary())

# COMMAND ----------

# MAGIC %md
# MAGIC ### Tightening up instead
# MAGIC
# MAGIC If this dataset feeds something that breaks on unexpected columns, promote the
# MAGIC warning: `allow_extra=False` makes `LOAD_TIMESTAMP` an error too.

# COMMAND ----------

print(validate_columns(drifted, contract, allow_extra=False).summary())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Findings as a DataFrame
# MAGIC
# MAGIC Every issue is a flat record, so results are queryable rather than a string
# MAGIC somebody has to be watching the notebook to see.

# COMMAND ----------

findings = spark.createDataFrame(drift_result.to_records(), RESULT_SCHEMA_DDL)
display(findings)

# COMMAND ----------

# MAGIC %md
# MAGIC Stamp it and append, and data quality becomes something you can trend over time.
# MAGIC Uncomment once you have a catalog and schema to write to.

# COMMAND ----------

audit = (
    findings.withColumn("contract", F.lit(contract.title))
    .withColumn("dataset", F.lit("practitioner_privileges"))
    .withColumn("validated_at", F.current_timestamp())
)
display(audit)

# audit.write.mode("append").saveAsTable("main.dq.contract_findings")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. The gate
# MAGIC
# MAGIC What this actually looks like in a pipeline: one call that stops bad data moving
# MAGIC downstream. `raise_if_failed()` raises on errors only — warnings never fail a run.

# COMMAND ----------

from dqspec import ValidationFailed


def gate(frame, contract_name, dataset, strict=False):
    """Validate `frame`, log every finding, raise if anything is an error."""
    spec = load_contract(contract_name)
    outcome = validate_columns(frame, spec, allow_extra=not strict)

    print("{0}: {1}".format(dataset, outcome.summary()))
    # In a job, append outcome.to_records() to your audit table here, tagged with
    # `dataset`, so a failure is recorded even though the next line raises.

    return outcome.raise_if_failed()


gate(df, "expected_columns.yaml", dataset="practitioner_privileges")
print("\n-> contract satisfied, safe to continue")

# COMMAND ----------

try:
    gate(drifted, "expected_columns.yaml", dataset="practitioner_privileges")
except ValidationFailed as exc:
    print("\n-> pipeline stopped:", len(exc.result.errors), "error(s)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Everything else the contract declares
# MAGIC
# MAGIC With no `checks=` argument, `validate()` runs every check that applies to this
# MAGIC frame *and* this contract — here that adds `column_types` and the `row_count`
# MAGIC assertion already in the YAML:
# MAGIC
# MAGIC ```yaml
# MAGIC assertions:
# MAGIC   row_count:
# MAGIC     greater_than: 100
# MAGIC     less_than: 1000000
# MAGIC ```
# MAGIC
# MAGIC **`row_count` calls `df.count()`** — a real scan. Keep it out of hot paths on
# MAGIC large tables with `checks=["column_names"]`, which is what `validate_columns` does.

# COMMAND ----------

full = validate(df, contract)
print(full.summary())
print("\nchecks that ran:", full.checks_run)

# COMMAND ----------

# 150 rows is fine; 40 is not.
print(validate(df.limit(40), contract, checks=["row_count"]).summary())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Where to go next
# MAGIC
# MAGIC 1. **Point it at a real table.** Replace the cell in section 3 with
# MAGIC    `spark.table(...)` and see what the contract says about production data.
# MAGIC 2. **Fill in `business_owner`** in the YAML. It is `TBD` today, and the whole
# MAGIC    point of a contract is that someone owns it.
# MAGIC 3. **Add assertions.** `docs/EXPLAINER.md` §7 — a new rule is one function in
# MAGIC    `src/dqspec/checks.py`, picked up by `validate()` automatically.
# MAGIC 4. **Decide where contracts live.** In the package (reviewed via PR, shipped in
# MAGIC    the wheel) or on a Volume (edited by stewards, no deploy). `load_contract`
# MAGIC    takes either, so this can be decided per contract and changed later.
