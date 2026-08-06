import pytest

from dqspec import (
    CADENCES,
    END_CLIENTS,
    REPORT_TYPES,
    ContractError,
    list_packaged_contracts,
    load_contract,
    parse_contract,
)


SITE_COLUMNS = (
    "ANT", "FRE", "FRS", "MAN", "OAK", "ROS", "RWC", "SAC", "SCL", "SFO",
    "SJO", "SLN", "SRF", "SRO", "SSC", "SSF", "VAC", "VAL", "WCR",
)


def test_loads_the_packaged_contract_by_bare_name():
    contract = load_contract("telemedicine.yaml")
    assert contract.title == "Telemedicine"
    assert contract.cadence == "Monthly"
    assert contract.end_clients == ("Internal",)
    assert contract.report_types == ("Internal",)
    assert len(contract.columns) == 28
    assert contract.column_names[0] == "PRACT ID"
    assert contract.assertions["row_count"] == {"greater_than": 100, "less_than": 1000000}


def test_yaml_1_2_directive_is_accepted():
    # The shipped file starts with "%YAML 1.2"; PyYAML must not choke on it.
    assert "telemedicine.yaml" in list_packaged_contracts()
    assert load_contract("telemedicine").title == "Telemedicine"


def test_every_column_carries_its_declared_type():
    contract = load_contract("telemedicine.yaml")
    assert {c.type for c in contract.columns} == {"string"}
    assert contract.column("DEGREE").required is True
    assert contract.column("NOT A COLUMN") is None


def test_every_site_column_shares_one_allowed_value_list():
    # The YAML writes the list once and aliases it; if an edit breaks the anchor
    # the sites drift apart silently, so assert every one of them.
    contract = load_contract("telemedicine.yaml")
    for name in SITE_COLUMNS:
        assert contract.column(name).allowed_values == ("P", "C", "T", " "), name


def test_columns_without_the_constraint_are_unconstrained():
    contract = load_contract("telemedicine.yaml")
    assert contract.column("PRACT ID").allowed_values is None
    assert [c.name for c in contract.columns if c.allowed_values] == list(SITE_COLUMNS)


def test_string_shorthand_for_columns():
    contract = parse_contract("title: t\ncolumns: [A, B]\n")
    assert contract.column_names == ("A", "B")
    assert contract.column("A").type is None


@pytest.mark.parametrize("cadence", CADENCES)
def test_every_documented_cadence_is_accepted(cadence):
    contract = parse_contract("columns: [A]\ncadence: {0}\n".format(cadence))
    assert contract.cadence == cadence


@pytest.mark.parametrize(
    "written, canonical",
    [
        ("monthly", "Monthly"),
        ("QUARTERLY", "Quarterly"),
        ("ad-hoc", "Ad Hoc"),
        ("ad hoc", "Ad Hoc"),
        ("AdHoc", "Ad Hoc"),
        ("  Daily  ", "Daily"),
    ],
)
def test_cadence_spelling_is_forgiven_and_folded(written, canonical):
    # Spelling varies between people; the stored value must not, or grouping a
    # findings table by cadence splits one cadence into four.
    contract = parse_contract("columns: [A]\ncadence: '{0}'\n".format(written))
    assert contract.cadence == canonical


def test_cadence_is_optional():
    assert parse_contract("columns: [A]\n").cadence is None
    assert parse_contract("columns: [A]\ncadence: null\n").cadence is None


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("columns: [A]\ncadence: Fortnightly\n", "unknown cadence 'Fortnightly'"),
        ("columns: [A]\ncadence: every 30 days\n", "unknown cadence"),
        ("columns: [A]\ncadence: 30\n", "'cadence' must be a string, got int"),
        ("columns: [A]\ncadence: [Monthly]\n", "'cadence' must be a string, got list"),
    ],
)
def test_an_unusable_cadence_is_rejected_at_load_time(text, fragment):
    with pytest.raises(ContractError) as exc:
        parse_contract(text, source="t.yaml")
    assert fragment in str(exc.value)


def test_the_rejection_names_the_permitted_cadences():
    with pytest.raises(ContractError) as exc:
        parse_contract("columns: [A]\ncadence: Hourly\n")
    for cadence in CADENCES:
        assert cadence in str(exc.value)


@pytest.mark.parametrize("client", END_CLIENTS)
def test_every_documented_end_client_is_accepted(client):
    contract = parse_contract("columns: [A]\nend_client: {0}\n".format(client))
    assert contract.end_clients == (client,)


@pytest.mark.parametrize("report_type", REPORT_TYPES)
def test_every_documented_report_type_is_accepted(report_type):
    contract = parse_contract("columns: [A]\nreport_type: {0}\n".format(report_type))
    assert contract.report_types == (report_type,)


def test_one_value_and_several_both_land_as_a_tuple():
    # A caller should never have to ask which form the YAML used.
    assert parse_contract("columns: [A]\nend_client: DMHC\n").end_clients == ("DMHC",)
    assert parse_contract("columns: [A]\nend_client: [DMHC]\n").end_clients == ("DMHC",)
    assert parse_contract(
        "columns: [A]\nend_client: [DMHC, DHCS, CMS]\n"
    ).end_clients == ("DMHC", "DHCS", "CMS")
    assert parse_contract(
        "columns: [A]\nreport_type: [QHP, HSD, TAR]\n"
    ).report_types == ("QHP", "HSD", "TAR")


def test_the_two_vocabularies_are_independent():
    # 'Internal' is the only term in both; everything else belongs to one list.
    contract = parse_contract("columns: [A]\nend_client: DMHC\nreport_type: [QHP, PAAS]\n")
    assert contract.end_clients == ("DMHC",)
    assert contract.report_types == ("QHP", "PAAS")

    with pytest.raises(ContractError, match="unknown end client 'QHP'"):
        parse_contract("columns: [A]\nend_client: QHP\n")
    with pytest.raises(ContractError, match="unknown report type 'DMHC'"):
        parse_contract("columns: [A]\nreport_type: DMHC\n")


@pytest.mark.parametrize(
    "key, written, canonical",
    [
        ("end_client", "dmhc", "DMHC"),
        ("end_client", "Dhcs", "DHCS"),
        ("end_client", "  cms  ", "CMS"),
        ("end_client", "INTERNAL", "Internal"),
        ("report_type", "paas", "PAAS"),
        ("report_type", "qhp", "QHP"),
        ("report_type", "  Tar  ", "TAR"),
        ("report_type", "INTERNAL", "Internal"),
    ],
)
def test_vocabulary_spelling_is_forgiven_and_folded(key, written, canonical):
    contract = parse_contract("columns: [A]\n{0}: '{1}'\n".format(key, written))
    assert getattr(contract, key + "s") == (canonical,)


def test_end_client_and_report_type_are_optional():
    bare = parse_contract("columns: [A]\n")
    assert bare.end_clients == () and bare.report_types == ()
    nulled = parse_contract("columns: [A]\nend_client: null\nreport_type: null\n")
    assert nulled.end_clients == () and nulled.report_types == ()


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("columns: [A]\nend_client: Medicare\n", "unknown end client 'Medicare'"),
        ("columns: [A]\nend_client: [DMHC, Nope]\n", "unknown end client 'Nope'"),
        ("columns: [A]\nend_client: [DMHC, dmhc]\n", "lists DMHC twice"),
        ("columns: [A]\nend_client: []\n", "omit the key"),
        ("columns: [A]\nend_client: 7\n", "must be a string or a list of strings, got int"),
        ("columns: [A]\nend_client: {x: 1}\n", "must be a string or a list of strings, got dict"),
        ("columns: [A]\nend_client: [DMHC, 7]\n", "every 'end_client' entry must be a string"),
        ("columns: [A]\nreport_type: Medicare\n", "unknown report type 'Medicare'"),
        ("columns: [A]\nreport_type: [QHP, qhp]\n", "lists QHP twice"),
        ("columns: [A]\nreport_type: []\n", "omit the key"),
        ("columns: [A]\nreport_type: [QHP, 7]\n", "every 'report_type' entry must be a string"),
    ],
)
def test_an_unusable_vocabulary_value_is_rejected_at_load_time(text, fragment):
    with pytest.raises(ContractError) as exc:
        parse_contract(text, source="t.yaml")
    assert fragment in str(exc.value)


def test_the_rejection_names_the_permitted_values():
    with pytest.raises(ContractError) as exc:
        parse_contract("columns: [A]\nend_client: Medicaid\n")
    for client in END_CLIENTS:
        assert client in str(exc.value)

    with pytest.raises(ContractError) as exc:
        parse_contract("columns: [A]\nreport_type: Medicaid\n")
    for report_type in REPORT_TYPES:
        assert report_type in str(exc.value)


def test_allowed_values_accepts_mixed_scalars_including_null():
    contract = parse_contract(
        "columns:\n"
        "  - name: A\n"
        "    allowed_values: [P, 'C', ' ', 3, true, null]\n"
    )
    assert contract.column("A").allowed_values == ("P", "C", " ", 3, True, None)


def test_a_blank_allowed_value_survives_parsing():
    # ' ' must stay a single space -- not stripped, and not folded into ''.
    contract = parse_contract("columns:\n  - name: A\n    allowed_values: [\"P\", \" \"]\n")
    assert contract.column("A").allowed_values == ("P", " ")


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
        ("columns:\n  - name: A\n    allowed_values: P\n", "must be a list of scalars"),
        ("columns:\n  - name: A\n    allowed_values: []\n", "empty list"),
        ("columns:\n  - name: A\n    allowed_values: [[P]]\n", "must be a scalar"),
        ("columns:\n  - name: A\n    allowed_values: [P, C, P]\n", "lists 'P' twice"),
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
        assert len(load_contract("telemedicine.yaml").columns) == 28


def test_missing_file_lists_what_is_available():
    with pytest.raises(ContractError) as exc:
        load_contract("/no/such/contract.yaml")
    assert "telemedicine.yaml" in str(exc.value)
