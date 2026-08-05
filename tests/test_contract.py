import pytest

from dqspec import ContractError, list_packaged_contracts, load_contract, parse_contract


def test_loads_the_packaged_contract_by_bare_name():
    contract = load_contract("expected_columns.yaml")
    assert contract.title == "Expected Columns"
    assert len(contract.columns) == 28
    assert contract.column_names[0] == "PRACT ID"
    assert contract.assertions["row_count"] == {"greater_than": 100, "less_than": 1000000}


def test_yaml_1_2_directive_is_accepted():
    # The shipped file starts with "%YAML 1.2"; PyYAML must not choke on it.
    assert "expected_columns.yaml" in list_packaged_contracts()
    assert load_contract("expected_columns").title == "Expected Columns"


def test_every_column_carries_its_declared_type():
    contract = load_contract("expected_columns.yaml")
    assert {c.type for c in contract.columns} == {"string"}
    assert contract.column("DEGREE").required is True
    assert contract.column("NOT A COLUMN") is None


def test_string_shorthand_for_columns():
    contract = parse_contract("title: t\ncolumns: [A, B]\n")
    assert contract.column_names == ("A", "B")
    assert contract.column("A").type is None


def test_optional_columns():
    contract = parse_contract(
        "columns:\n  - name: A\n  - name: B\n    required: false\n"
    )
    assert contract.required_column_names == ("A",)


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "empty"),
        ("- a\n- b\n", "mapping at the top level"),
        ("title: t\n", "no 'columns:' section"),
        ("columns: {}\n", "must be a list"),
        ("columns: []\n", "empty list"),
        ("columns:\n  - type: string\n", "missing the required key 'name'"),
        ("columns:\n  - name: A\n  - name: A\n", "duplicate column"),
        ("columns: [A]\nassertions: []\n", "'assertions' must be a mapping"),
        ("columns: [A]\n  bad indent:\n", "not valid YAML"),
    ],
)
def test_malformed_contracts_fail_loudly(text, fragment):
    with pytest.raises(ContractError) as exc:
        parse_contract(text, source="t.yaml")
    assert fragment in str(exc.value)


class TestDuplicateKeys:
    """A repeated YAML key silently drops content; the loader must reject it."""

    def test_duplicate_key_inside_a_column_entry(self):
        # Without the strict loader this parses cleanly as TWO columns, with
        # 'FIRST NAME' gone and nothing to indicate it ever existed.
        text = (
            "columns:\n"
            "  - name: LAST NAME\n"
            "  - name: FIRST NAME\n"
            "    name: MIDDLE INITIAL\n"
        )
        with pytest.raises(ContractError) as exc:
            parse_contract(text, source="t.yaml")
        assert "duplicate key 'name'" in str(exc.value)
        assert "line 4" in str(exc.value)
        assert "t.yaml" in str(exc.value)

    def test_duplicate_top_level_key(self):
        with pytest.raises(ContractError, match="duplicate key 'title'"):
            parse_contract("title: a\ntitle: b\ncolumns: [A]\n")

    def test_duplicate_key_in_assertions(self):
        text = "columns: [A]\nassertions:\n  row_count:\n    min: 1\n    min: 2\n"
        with pytest.raises(ContractError, match="duplicate key 'min'"):
            parse_contract(text)

    def test_duplicate_type_key_is_caught(self):
        text = "columns:\n  - name: A\n    type: string\n    type: integer\n"
        with pytest.raises(ContractError, match="duplicate key 'type'"):
            parse_contract(text)

    def test_repeated_key_in_sibling_mappings_is_fine(self):
        # 'name' appears once per entry -- different mappings, not a duplicate.
        contract = parse_contract("columns:\n  - name: A\n  - name: B\n")
        assert contract.column_names == ("A", "B")

    def test_nested_structures_still_parse(self):
        # Regression: replacing the mapping constructor must not break nested
        # lists/dicts, anchors, or the packaged contract.
        contract = parse_contract(
            "title: t\n"
            "assertions:\n"
            "  row_count: {greater_than: 1, less_than: 9}\n"
            "  allowed_values:\n"
            "    DEGREE: [MD, DO, NP, PA]\n"
            "columns:\n"
            "  - name: DEGREE\n"
            "    type: string\n"
        )
        assert contract.assertions["row_count"] == {"greater_than": 1, "less_than": 9}
        assert contract.assertions["allowed_values"]["DEGREE"] == ["MD", "DO", "NP", "PA"]
        assert contract.column_names == ("DEGREE",)

    def test_anchors_and_merge_keys_still_work(self):
        contract = parse_contract(
            "defaults: &d\n"
            "  type: string\n"
            "columns:\n"
            "  - <<: *d\n"
            "    name: A\n"
        )
        assert contract.column("A").type == "string"

    def test_overriding_a_merged_default_is_not_a_duplicate(self):
        # `type` appears once literally and once via the merge; that is the whole
        # point of merge keys, so it must not trip the duplicate check.
        contract = parse_contract(
            "defaults: &d\n"
            "  type: string\n"
            "columns:\n"
            "  - <<: *d\n"
            "    name: A\n"
            "  - <<: *d\n"
            "    name: B\n"
            "    type: integer\n"
        )
        assert contract.column("A").type == "string"
        assert contract.column("B").type == "integer"

    def test_duplicate_still_caught_in_a_mapping_that_also_merges(self):
        text = (
            "defaults: &d\n"
            "  type: string\n"
            "columns:\n"
            "  - <<: *d\n"
            "    name: A\n"
            "    name: B\n"
        )
        with pytest.raises(ContractError, match="duplicate key 'name'"):
            parse_contract(text)

    def test_the_shipped_contract_is_free_of_duplicates(self):
        assert len(load_contract("expected_columns.yaml").columns) == 28


def test_missing_file_lists_what_is_available():
    with pytest.raises(ContractError) as exc:
        load_contract("/no/such/contract.yaml")
    assert "expected_columns.yaml" in str(exc.value)
