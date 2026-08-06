# Databricks notebook source
# MAGIC %md
# MAGIC # Validate a DataFrame against a YAML contract
# MAGIC
# MAGIC Proof of concept: read a YAML file from a private GitHub repo, parse it with
# MAGIC our own Python package, and use it to validate the columns of a DataFrame.
# MAGIC
# MAGIC Pick **one** of the three loading cells below, then run the rest.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Option A — Databricks Git folder (recommended to start)
# MAGIC
# MAGIC In the workspace sidebar: **Workspace → Create → Git folder**, point it at your
# MAGIC private GitHub server, and clone. Then put this notebook inside that folder and
# MAGIC run the cell below. Nothing to install, and `git pull` picks up your edits.

# COMMAND ----------

import os
import sys

# Edit this if auto-detection below fails. Path to the cloned Git folder.
REPO_ROOT = "/Workspace/Users/henry.t.ford@jpl.org/theYaml"


def find_repo_root(fallback=REPO_ROOT):
    """First ancestor directory that actually contains src/dqspec."""
    starts = []
    try:
        ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        starts.append("/Workspace" + os.path.dirname(ctx.notebookPath().get()))
    except Exception:  # noqa: BLE001 - context is unavailable in some job runs
        pass
    starts.append(os.getcwd())  # Git folders set cwd to the notebook's directory
    starts.append(fallback)

    for start in starts:
        current = start
        for _ in range(6):
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
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

print("repo root:", REPO_ROOT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Option B — `%pip install` straight from the private git server
# MAGIC
# MAGIC Requires cluster network egress to your internal GitHub host. Store a PAT in a
# MAGIC Databricks secret scope first — never paste a token into a notebook cell.
# MAGIC
# MAGIC ```
# MAGIC databricks secrets create-scope github
# MAGIC databricks secrets put-secret github pat
# MAGIC ```
# MAGIC
# MAGIC This needs **three separate cells**: a `%pip` magic has to be the first line of
# MAGIC its own cell, so it cannot share a cell with the `dbutils.secrets.get` call, and
# MAGIC `restartPython()` has to come after it. The `$token` is Databricks variable
# MAGIC substitution into the magic — **verify it resolves on your runtime** before
# MAGIC relying on it. If it does not, store the whole `https://<pat>@host/org/repo.git`
# MAGIC URL as a single secret and substitute that instead.

# COMMAND ----------

# token = dbutils.secrets.get(scope="github", key="pat")

# COMMAND ----------

# %pip install git+https://$token@github.your-company.com/your-org/theYaml.git@main

# COMMAND ----------

# dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Option C — wheel on a Unity Catalog Volume
# MAGIC
# MAGIC Build locally with `python -m build --wheel`, upload `dist/*.whl` to a Volume.
# MAGIC Most locked-down option; no cluster egress to git needed.

# COMMAND ----------

# %pip install /Volumes/main/default/libs/dqspec-0.1.0-py3-none-any.whl
# dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Load the contract

# COMMAND ----------

import dqspec
from dqspec import load_contract, validate, validate_columns

print("dqspec", dqspec.__version__)
print("packaged contracts:", dqspec.list_packaged_contracts())

# A bare name resolves to the YAML shipped inside the package. You can also pass an
# absolute path, e.g. "/Volumes/main/default/contracts/telemedicine.yaml".
contract = load_contract("telemedicine.yaml")

print(contract)
print("owner:", contract.business_owner)
print("cadence:", contract.cadence)
print("end client(s):", contract.end_clients)
print("report type(s):", contract.report_types)
print("columns:", contract.column_names)
print("assertions:", dict(contract.assertions))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Build a demo DataFrame
# MAGIC
# MAGIC Replace this with your real table:
# MAGIC `df = spark.table("main.credentialing.practitioner_privileges")`
# MAGIC
# MAGIC Note the contract's column names contain spaces (`PRACT ID`), so any Spark SQL
# MAGIC referring to them needs backticks: ``SELECT `PRACT ID` FROM ...``

# COMMAND ----------

from pyspark.sql.types import StringType, StructField, StructType

schema = StructType([StructField(name, StringType(), True) for name in contract.column_names])

# The site columns are constrained to P/C/T/blank by the contract, so the demo row
# takes its value from the contract rather than making one up.
row = tuple(
    (spec.allowed_values[0] if spec.allowed_values else "x") for spec in contract.columns
)

df = spark.createDataFrame([row] * 150, schema)
display(df.limit(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Validate the column names — the happy path
# MAGIC
# MAGIC `validate_columns` reads the schema only. It never scans the data, so it is
# MAGIC safe on a table of any size.

# COMMAND ----------

result = validate_columns(df, contract)
print(result.summary())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Now break it on purpose
# MAGIC
# MAGIC Drop a required column, misspell another, and add one nobody declared.

# COMMAND ----------

bad_df = (
    df.drop("DEGREE")                                # required column missing
      .withColumnRenamed("PRACT ID", "pract_id")     # near miss on naming
      .withColumn("SCRATCH_COL", df["ANT"])          # undeclared extra
)

bad = validate_columns(bad_df, contract)
print(bad.summary())

# COMMAND ----------

# MAGIC %md
# MAGIC The near-miss is called out by name rather than just reported missing. If your
# MAGIC upstream really does deliver `pract_id`, turn on `normalize` and the match
# MAGIC succeeds — case, spaces, underscores and hyphens are all folded together.

# COMMAND ----------

print(validate_columns(bad_df, contract, normalize=True).summary())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Findings as a DataFrame
# MAGIC
# MAGIC Every issue is a flat record, so results can be displayed, or appended to a
# MAGIC Delta audit table to trend data quality over time.

# COMMAND ----------

from dqspec import RESULT_SCHEMA_DDL

findings = spark.createDataFrame(bad.to_records(), RESULT_SCHEMA_DDL)
display(findings)

# (spark.createDataFrame([], RESULT_SCHEMA_DDL) if not bad.issues else findings)
#   .withColumn("contract", lit(contract.title))
#   .withColumn("validated_at", current_timestamp())
#   .write.mode("append").saveAsTable("main.dq.contract_findings")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Everything the contract declares
# MAGIC
# MAGIC With no `checks=` argument, every check that applies to this frame and contract
# MAGIC runs — here that adds `column_types`, `allowed_values` on the site columns, and
# MAGIC the `row_count` assertion already in the YAML. Both `row_count` and
# MAGIC `allowed_values` read data (a `count()` and a `distinct()` per constrained column),
# MAGIC so keep them out of the loop on huge tables by passing `checks=["column_names"]`.

# COMMAND ----------

full = validate(df, contract)
print(full.summary())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Fail the job
# MAGIC
# MAGIC `raise_if_failed()` raises on error-severity issues only; warnings never fail a
# MAGIC run. Put this at the end of an ingestion task to stop bad data moving downstream.

# COMMAND ----------

validate_columns(df, contract).raise_if_failed()
print("contract satisfied")

# COMMAND ----------

# Uncomment to see the failure mode:
# validate_columns(bad_df, contract).raise_if_failed()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Where this goes next
# MAGIC
# MAGIC Adding an assertion type is one function in `src/dqspec/checks.py`:
# MAGIC
# MAGIC ```python
# MAGIC @register("no_nulls", applies=lambda frame, contract: frame.kind == "spark")
# MAGIC def check_no_nulls(frame, contract, options):
# MAGIC     for spec in contract.columns:
# MAGIC         ...
# MAGIC         yield Issue(check="no_nulls", severity=ERROR, column=spec.name, message=...)
# MAGIC ```
# MAGIC
# MAGIC It is picked up by `validate()` automatically — the loader, the runner and the
# MAGIC result objects do not change.
