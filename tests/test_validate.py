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


def test_unknown_check_name_is_rejected():
    with pytest.raises(KeyError, match="unknown check"):
        validate(["PRACT ID"], CONTRACT, checks=["not_a_check"])


def test_string_frame_gets_a_helpful_error():
    with pytest.raises(TypeError, match="Did you mean"):
        validate_columns("PRACT ID", CONTRACT)


def test_contract_can_be_passed_as_a_name():
    result = validate_columns(list(load_contract("expected_columns.yaml").column_names),
                              "expected_columns.yaml")
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


def test_result_records_are_flat_strings_for_spark():
    from dqspec import RESULT_SCHEMA_DDL

    records = validate_columns(["PRACT ID"], CONTRACT).to_records()
    assert len(records) == 1
    fields = [f.split()[0] for f in RESULT_SCHEMA_DDL.split(", ")]
    assert set(records[0]) == set(fields)
    assert all(v is None or isinstance(v, str) for v in records[0].values())
