from pathlib import Path
from typing import Any

import pytest
import yaml
from linkml_runtime.utils.schemaview import SchemaView

from pxos_kernel.ontology import (
    BASE_SCHEMA,
    ActionType,
    InvalidPackage,
    Kind,
    Rule,
    load_package,
)
from pxos_kernel.ontology.loader import class_kind, is_compatible

RETAIL = Path(__file__).parent / "retail_fixture"


def retail() -> tuple[dict[str, Any], dict[str, Any]]:
    schema = yaml.safe_load((RETAIL / "ontology.yaml").read_text(encoding="utf-8"))
    manifest = yaml.safe_load((RETAIL / "manifest.yaml").read_text(encoding="utf-8"))
    return schema, manifest


def write_package(tmp_path: Path, schema: dict[str, Any], manifest: dict[str, Any]) -> Path:
    (tmp_path / "ontology.yaml").write_text(
        yaml.safe_dump(schema, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return tmp_path


def test_base_has_minimal_types_with_kind_and_portuguese_labels() -> None:
    base = SchemaView(str(BASE_SCHEMA))
    assert set(base.all_classes()) == {"Organization", "Actor", "Role", "Event", "Evidence"}
    assert base.schema.version
    for name in base.all_classes():
        assert class_kind(base, name, "pxos_base") in Kind
        assert base.get_class(name).title


def test_fixture_package_extends_base() -> None:
    package = load_package(RETAIL)

    assert package.name == "retail"
    assert package.version == "0.1.0"
    assert package.kinds == {"RestockOrder": Kind.CANONICAL, "ShelfObservation": Kind.CONTEXT}
    assert package.readers == {"RestockOrder": ("store-staff", "store-manager")}
    assert package.actions == (
        ActionType(
            name="create_restock_order",
            creates="RestockOrder",
            approver="store-manager",
            reversible=True,
            rule_parameters=("quantity",),
            rules=(Rule(parameter="quantity", operator="max", value=10),),
        ),
        ActionType(
            name="confirm_restock_order",
            creates="RestockOrder",
            approver="store-manager",
            reversible=False,
            rule_parameters=("quantity",),
            rules=(Rule(parameter="quantity", operator="max", value=10),),
        ),
    )
    assert package.action("create_restock_order").approver == "store-manager"
    assert package.references == {"RestockOrder": {"assignee": "Actor"}}
    assert "occurred_at" in package.schema.class_slots("RestockOrder")


def test_digest_covers_manifest_and_ontology(tmp_path: Path) -> None:
    schema, manifest = retail()
    original = load_package(write_package(tmp_path, schema, manifest)).digest
    assert load_package(RETAIL).digest == original

    manifest["actions"][0]["reversible"] = False
    assert load_package(write_package(tmp_path, schema, manifest)).digest != original

    schema, manifest = retail()
    schema["classes"]["RestockOrder"]["attributes"]["quantity"]["minimum_value"] = 2
    assert load_package(write_package(tmp_path, schema, manifest)).digest != original


def test_package_must_import_base(tmp_path: Path) -> None:
    schema, manifest = retail()
    schema["imports"] = ["linkml:types"]
    schema["classes"]["RestockOrder"].pop("is_a")
    with pytest.raises(InvalidPackage, match="must import pxos_base"):
        load_package(write_package(tmp_path, schema, manifest))


def test_package_must_declare_kind_for_every_type(tmp_path: Path) -> None:
    schema, manifest = retail()
    del schema["classes"]["ShelfObservation"]["annotations"]
    with pytest.raises(InvalidPackage, match="ShelfObservation must declare pxos.kind"):
        load_package(write_package(tmp_path, schema, manifest))


def test_package_cannot_redefine_base_type(tmp_path: Path) -> None:
    schema, manifest = retail()
    schema["classes"]["Role"] = {"annotations": {"pxos.kind": "canonical"}}
    manifest["types"].append({"name": "Role", "readers": ["store-staff"]})
    with pytest.raises(InvalidPackage, match="redefines base types"):
        load_package(write_package(tmp_path, schema, manifest))


def test_package_built_for_other_base_version_is_rejected(tmp_path: Path) -> None:
    schema, manifest = retail()
    schema["annotations"]["pxos.base_version"] = "0.2.0"
    with pytest.raises(InvalidPackage, match="built for base 0.2.0"):
        load_package(write_package(tmp_path, schema, manifest))


@pytest.mark.parametrize(
    ("declared", "available", "compatible"),
    [
        ("0.1.0", "0.1.3", True),
        ("0.1.0", "0.2.0", False),
        ("1.0.0", "1.4.0", True),
        ("1.0.0", "2.0.0", False),
    ],
)
def test_layer_version_compatibility(declared: str, available: str, compatible: bool) -> None:
    assert is_compatible(declared, available) is compatible


def test_malformed_version_is_an_invalid_package() -> None:
    with pytest.raises(InvalidPackage, match="MAJOR.MINOR.PATCH"):
        is_compatible("0.1", "0.1.0")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"version": "0.2.0"}, "must match the LinkML schema"),
        ({"name": "other"}, "must match the LinkML schema"),
        ({"version": "1"}, "invalid manifest"),
        ({"owner": "someone"}, "invalid manifest"),
        ({"types": [{"name": "RestockOrder", "readers": ["store-manager"]}]}, "exactly"),
    ],
)
def test_manifest_is_validated(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    schema, manifest = retail()
    with pytest.raises(InvalidPackage, match=message):
        load_package(write_package(tmp_path, schema, {**manifest, **change}))


def test_canonical_types_need_readers_and_context_types_have_none(tmp_path: Path) -> None:
    schema, manifest = retail()
    manifest["types"][0].pop("readers")
    with pytest.raises(InvalidPackage, match="must declare readers"):
        load_package(write_package(tmp_path, schema, manifest))

    schema, manifest = retail()
    manifest["types"][1]["readers"] = ["store-staff"]
    with pytest.raises(InvalidPackage, match="cannot declare readers"):
        load_package(write_package(tmp_path, schema, manifest))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"approver": None}, "invalid manifest"),
        ({"reversible": "maybe"}, "invalid manifest"),
        ({"autonomy": "full"}, "invalid manifest"),
        ({"creates": "ShelfObservation"}, "must create a canonical type"),
        ({"creates": "Role"}, "must create a canonical type"),
        ({"approver": "cashier"}, "approver of create_restock_order must read RestockOrder"),
        ({"rule_parameters": ["price"]}, r"rule parameters \['price'\] unknown"),
    ],
)
def test_action_type_must_follow_metamodel(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    schema, manifest = retail()
    action = {**manifest["actions"][0], **change}
    manifest["actions"] = [{key: value for key, value in action.items() if value is not None}]
    with pytest.raises(InvalidPackage, match=message):
        load_package(write_package(tmp_path, schema, manifest))


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ([{"parameter": "sku", "max": 1}], r"rule on sku is not a rule parameter"),
        ([{"parameter": "quantity"}], "rule on quantity needs exactly one of max, within_days"),
        (
            [{"parameter": "quantity", "max": 1, "within_days": 2}],
            "rule on quantity needs exactly one of max, within_days",
        ),
        ([{"parameter": "quantity", "within_days": 2}], "within_days needs a date"),
        ([{"parameter": "quantity", "max": -1}], "invalid manifest"),
        ([{"parameter": "quantity", "max": 1, "limit": 2}], "invalid manifest"),
        ([{"parameter": "quantity", "max": 1}] * 2, "duplicated rule on quantity"),
    ],
)
def test_action_rules_limit_declared_parameters(
    tmp_path: Path, rules: list[dict[str, Any]], message: str
) -> None:
    schema, manifest = retail()
    manifest["actions"][0]["rules"] = rules
    with pytest.raises(InvalidPackage, match=message):
        load_package(write_package(tmp_path, schema, manifest))


def test_max_rule_needs_a_number_and_within_days_a_date(tmp_path: Path) -> None:
    schema, manifest = retail()
    schema["classes"]["RestockOrder"]["attributes"]["deliver_by"] = {"range": "date"}
    manifest["actions"][0]["rule_parameters"] = ["quantity", "deliver_by"]
    manifest["actions"][0]["rules"] = [
        {"parameter": "quantity", "max": 10},
        {"parameter": "deliver_by", "within_days": 7},
    ]
    action = load_package(write_package(tmp_path, schema, manifest)).action("create_restock_order")
    assert action.rules == (
        Rule(parameter="quantity", operator="max", value=10),
        Rule(parameter="deliver_by", operator="within_days", value=7),
    )

    manifest["actions"][0]["rules"] = [{"parameter": "deliver_by", "max": 7}]
    with pytest.raises(InvalidPackage, match="max needs a number"):
        load_package(write_package(tmp_path, schema, manifest))


def test_action_type_names_are_unique(tmp_path: Path) -> None:
    schema, manifest = retail()
    manifest["actions"] = manifest["actions"] * 2
    with pytest.raises(InvalidPackage, match="duplicated action type names"):
        load_package(write_package(tmp_path, schema, manifest))


def test_a_reference_can_only_point_to_an_actor(tmp_path: Path) -> None:
    schema, manifest = retail()
    assignee = schema["classes"]["RestockOrder"]["attributes"]["assignee"]
    assignee["annotations"]["pxos.refers_to"] = "Shelf"
    with pytest.raises(InvalidPackage, match="assignee can only refer to Actor"):
        load_package(write_package(tmp_path, schema, manifest))


def test_package_must_declare_the_base_version_it_was_built_for(tmp_path: Path) -> None:
    schema, manifest = retail()
    del schema["annotations"]["pxos.base_version"]
    with pytest.raises(InvalidPackage, match="must declare pxos.base_version"):
        load_package(write_package(tmp_path, schema, manifest))


def test_an_unknown_action_is_refused() -> None:
    with pytest.raises(InvalidPackage, match="unknown action restock"):
        load_package(RETAIL).action("restock")
