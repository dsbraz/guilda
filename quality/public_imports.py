"""Disambiguate reexported symbols from same-named modules in Grimp's import graph."""

import ast
from pathlib import Path


def _symbols(directory: Path, module: str) -> set[str]:
    result = set()
    for node in ast.walk(ast.parse((directory / "__init__.py").read_text())):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            name = alias.asname or alias.name
            source = directory / f"{name}.py"
            if node.module != f"{module}.{name}" or not source.exists():
                continue
            definitions = ast.parse(source.read_text()).body
            if any(
                isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and d.name == alias.name
                for d in definitions
            ):
                result.add(name)
    return result


def _used_symbols(file: Path, module: str, exported: set[str]) -> set[str]:
    public: set[str] = set()
    direct: set[str] = set()
    for node in ast.walk(ast.parse(file.read_text())):
        if isinstance(node, ast.ImportFrom):
            if node.module == module:
                public.update(alias.name for alias in node.names)
            elif node.module and node.module.startswith(module + "."):
                direct.add(node.module[len(module) + 1 :].split(".")[0])
        elif isinstance(node, ast.Import):
            direct.update(
                alias.name[len(module) + 1 :].split(".")[0]
                for alias in node.names
                if alias.name.startswith(module + ".")
            )
    return public & exported - direct


def symbol_edges(packages: list[Path], directory: Path) -> list[str]:
    module = f"{directory.parent.name}.{directory.name}"
    exported = _symbols(directory, module)
    if not exported:
        return []
    edges: list[str] = []
    for package in packages:
        for file in package.rglob("*.py"):
            relative = file.relative_to(package.parent).with_suffix("")
            caller = ".".join(relative.parts).removesuffix(".__init__")
            edges.extend(
                f"{caller} -> {module}.{name}" for name in _used_symbols(file, module, exported)
            )
    return sorted(edges)
