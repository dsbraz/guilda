from pathlib import Path

from quality.public_imports import symbol_edges


def test_public_symbol_with_same_name_as_module_is_not_an_internal_import(tmp_path: Path) -> None:
    package = tmp_path / "kernel"
    child = package / "identity"
    child.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (child / "__init__.py").write_text("from kernel.identity.session import session\n")
    (child / "session.py").write_text("def session():\n    pass\n")
    caller = package / "caller.py"
    caller.write_text("from kernel.identity import session\n")
    assert symbol_edges([package], child) == ["kernel.caller -> kernel.identity.session"]
    caller.write_text("from kernel.identity import session\nimport kernel.identity.session\n")
    assert symbol_edges([package], child) == []


def test_module_reexport_does_not_exempt_internal_access(tmp_path: Path) -> None:
    package = tmp_path / "kernel"
    child = package / "identity"
    child.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (child / "__init__.py").write_text("from kernel.identity import session\n")
    (child / "session.py").write_text("x = 1\n")
    (package / "caller.py").write_text("import kernel.identity.session\n")
    assert symbol_edges([package], child) == []
