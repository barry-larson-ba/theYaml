import pytest

from dqspec import (
    Options,
    ValidationFailed,
    load_contract,
    parse_contract,
    validate,
    validate_columns,
)

CONTRACT = parse_contract(
    """
title: Small
assertions:
  row_count:
    greater_than: 2
columns:
  - name: PRACT ID
    type: string
  - name: AGE
    type: integer
"""
)


def test_matching_column_names_pass():
    result = validate_columns(["PRACT ID", "AGE"], CONTRACT)
    assert result.ok
    assert bool(result) is True
    assert result.issues == ()
    assert result.checks_run == ("column_names",)


def test_missing_column_is_an_error():
    result = validate_columns(["PRACT ID"], CONTRACT)
    assert not result.ok
    assert len(result.errors) == 1
    assert result.errors[0].column == "AGE"
    assert "missing required column 'AGE'" in result.errors[0].message


def test_extra_column_warns_but_passes_by_default():
    result = validate_columns(["PRACT ID", "AGE", "SURPRISE"], CONTRACT)
    assert result.ok
    assert [w.column for w in result.warnings] == ["SURPRISE"]


def test_extra_column_can_be_made_fatal():
    result = validate_columns(["PRACT ID", "AGE", "SURPRISE"], CONTRACT, allow_extra=False)
    assert not result.ok
    assert result.errors[0].column == "SURPRISE"


def test_near_miss_names_are_explained_not_just_rejected():
    result = validate_columns(["pract_id", "AGE"], CONTRACT)
    assert not result.ok
    assert "did you mean 'pract_id'?" in result.errors[0].message


def test_normalize_accepts_the_near_miss():
    result = validate_columns(["pract_id", "age"], CONTRACT, normalize=True)
    assert result.ok
    assert result.issues == ()


def test_column_order_is_ignored_unless_asked_for():
    assert validate_columns(["AGE", "PRACT ID"], CONTRACT).ok
    result = validate_columns(["AGE", "PRACT ID"], CONTRACT, check_order=True)
    assert result.ok  # order problems are warnings
    assert "not in contract order" in result.warnings[0].message


def test_optional_column_absence_warns_only():
    contract = parse_contract("columns:\n  - name: A\n  - name: B\n    required: false\n")
    result = validate_columns(["A"], contract)
    assert result.ok
    assert "optional column 'B'" in result.warnings[0].message


def test_raise_if_failed():
    validate_columns(["PRACT ID", "AGE"], CONTRACT).raise_if_failed()
    with pytest.raises(ValidationFailed) as exc:
        validate_columns(["PRACT ID"], CONTRACT).raise_if_failed()
    assert "FAIL" in str(exc.value)
    assert exc.value.result.errors[0].column == "AGE"


def test_bare_column_list_skips_checks_it_cannot_run():
    result = validate(["PRACT ID", "AGE"], CONTRACT)
    assert result.checks_run == ("column_names",)  # no types, no row count available


def test_value_checks_are_skipped_when_there_is_no_data_to_read():
    contract = parse_contract("columns:\n  - name: A\n    allowed_values: [P, C]\n")
    result = validate(["A"], contract)
    assert result.checks_run == ("column_names",)  # a name list carries no values
    assert result.ok


def test_unknown_check_name_is_rejected():
    with pytest.raises(KeyError, match="unknown check"):
        validate(["PRACT ID"], CONTRACT, checks=["not_a_check"])


def test_string_frame_gets_a_helpful_error():
    with pytest.raises(TypeError, match="Did you mean"):
        validate_columns("PRACT ID", CONTRACT)


def test_contract_can_be_passed_as_a_name():
    result = validate_columns(list(load_contract("telemedicine.yaml").column_names),
                              "telemedicine.yaml")
    assert result.ok


def test_options_object_and_overrides_compose():
    base = Options(allow_extra=False)
    result = validate(["PRACT ID", "AGE", "X"], CONTRACT, checks=["column_names"],
                      options=base, allow_extra=True)
    assert result.ok


# --------------------------------------------------------------------------
# pandas-backed checks (types + row count). Skipped when pandas is absent.
# --------------------------------------------------------------------------

pd = pytest.importorskip("pandas")


def _frame(rows=3, age_dtype="int64"):
    return pd.DataFrame(
        {"PRACT ID": ["a"] * rows, "AGE": pd.Series([30] * rows, dtype=age_dtype)}
    )


def test_pandas_frame_runs_every_applicable_check():
    result = validate(_frame(), CONTRACT)
    assert set(result.checks_run) == {"column_names", "column_types", "row_count"}
    assert result.ok, result.summary()


def test_pandas_type_mismatch_warns_by_default_and_can_be_fatal():
    frame = _frame(age_dtype="float64")
    warned = validate(frame, CONTRACT, checks=["column_types"])
    assert warned.ok
    assert "expects integer" in warned.warnings[0].message

    strict = validate(frame, CONTRACT, checks=["column_types"], strict_types=True)
    assert not strict.ok


def test_pandas_row_count_assertion():
    result = validate(_frame(rows=1), CONTRACT, checks=["row_count"])
    assert not result.ok
    assert "row count 1 violates greater_than 2" in result.errors[0].message


# --------------------------------------------------------------------------
# allowed_values -- the site-status columns in the shipped contract
# --------------------------------------------------------------------------

SITE_CONTRACT = parse_contract(
    "columns:\n"
    "  - name: ANT\n"
    "    type: string\n"
    "    allowed_values: [\"P\", \"C\", \"T\", \" \"]\n"
)


def _sites(values):
    return pd.DataFrame({"ANT": values})


def test_allowed_site_statuses_pass():
    result = validate(_sites(["P", "C", "T", " ", "P"]), SITE_CONTRACT, checks=["allowed_values"])
    assert result.ok
    assert result.issues == ()


def test_a_value_outside_the_list_is_an_error():
    result = validate(_sites(["P", "X", "C", "X"]), SITE_CONTRACT, checks=["allowed_values"])
    assert not result.ok
    issue = result.errors[0]
    assert issue.column == "ANT"
    assert "does not allow: 'X'" in issue.message
    assert "allowed: 'P', 'C', 'T', ' '" in issue.message


def test_case_and_padding_are_not_quietly_accepted():
    result = validate(_sites(["p", "T ", ""]), SITE_CONTRACT, checks=["allowed_values"])
    assert not result.ok
    assert "3 value(s)" in result.errors[0].message


def test_nulls_violate_a_constrained_column():
    result = validate(_sites(["P", None, "C"]), SITE_CONTRACT, checks=["allowed_values"])
    assert not result.ok
    assert "does not allow: null" in result.errors[0].message


def test_nulls_pass_when_the_contract_lists_null():
    contract = parse_contract("columns:\n  - name: ANT\n    allowed_values: [P, null]\n")
    assert validate(_sites(["P", None]), contract, checks=["allowed_values"]).ok


def test_the_scan_is_capped_and_says_so():
    result = validate(
        _sites(["P", "C", "T"]), SITE_CONTRACT, checks=["allowed_values"], max_distinct_values=2
    )
    assert "only the first 2 distinct values" in result.warnings[0].message
    assert result.ok  # truncation warns; it never invents a failure


def test_a_missing_constrained_column_is_left_to_column_names():
    result = validate(pd.DataFrame({"OTHER": ["P"]}), SITE_CONTRACT, checks=["allowed_values"])
    assert result.issues == ()


def test_the_shipped_contract_validates_a_clean_frame():
    contract = load_contract("telemedicine.yaml")
    frame = pd.DataFrame({name: ["P", "C", "T", " "] for name in contract.column_names})
    result = validate(frame, contract, checks=["column_names", "allowed_values"])
    assert result.ok, result.summary()

    frame.loc[0, "SFO"] = "Z"
    failed = validate(frame, contract, checks=["allowed_values"])
    assert not failed.ok
    assert failed.errors[0].column == "SFO"


def test_result_records_are_flat_strings_for_spark():
    from dqspec import RESULT_SCHEMA_DDL

    records = validate_columns(["PRACT ID"], CONTRACT).to_records()
    assert len(records) == 1
    fields = [f.split()[0] for f in RESULT_SCHEMA_DDL.split(", ")]
    assert set(records[0]) == set(fields)
    assert all(v is None or isinstance(v, str) for v in records[0].values())
