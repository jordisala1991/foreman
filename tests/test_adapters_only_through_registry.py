"""Outside providers/, only the registry hands out an adapter (DESIGN, Architecture): no other module imports one, in any
form (absolute, relative, `from foreman.providers import github`), and goes around the port."""

import ast
from pathlib import Path

from helpers import PLUGIN

# The modules of providers/ that are not adapters: the registry and the ports.
PORTS = {"registry", "repo", "runner", "backlog"}


def adapters(providers: Path) -> set[str]:
    """Every package and module under `providers`, but the ports: a new adapter counts without editing this test."""
    packages = {d.name for d in providers.iterdir() if d.is_dir() and d.name != "__pycache__"}
    return packages | ({f.stem for f in providers.glob("*.py")} - PORTS - {"__init__"})


ADAPTERS = adapters(PLUGIN / "foreman" / "providers")
# The two health checks that read the Claude login and Orca's state, each for its own adapter; the allowance only shrinks.
ALLOWED = {"steps/health/checks.py": {"claude"}, "steps/health/orca.py": {"orca"}}


def adapter_imports(path: str, source: str, known: set[str] = ADAPTERS) -> set[str]:
    """The adapters a module imports; `path` is its place under foreman/ (`services/agents/p6.py`)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        modules: list[tuple[list[str], list[str]]]  # each imported module's parts, and the names taken from it
        if isinstance(node, ast.Import):
            modules = [(alias.name.split("."), []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            package = path.split("/")[:-1]
            base = ["foreman", *package[: len(package) - node.level + 1]] if node.level else []
            modules = [(base + (node.module.split(".") if node.module else []), [a.name for a in node.names])]
        else:
            continue
        for parts, names in modules:
            if parts[:2] == ["foreman", "providers"]:
                found |= {parts[2]} & known if len(parts) > 2 else set(names) & known
    return found


def test_only_the_registry_imports_an_adapter_from_outside_providers():
    root = PLUGIN / "foreman"
    paths = (f.relative_to(root).as_posix() for f in root.rglob("*.py"))
    found = {p: adapter_imports(p, (root / p).read_text()) for p in paths if not p.startswith("providers/")}
    assert {p: a for p, a in found.items() if a} == ALLOWED


def test_every_form_of_an_adapter_import_is_found():
    assert adapter_imports("services/agents/p6.py", "from foreman.providers.github import provider") == {"github"}
    assert adapter_imports("services/agents/p6.py", "from ...providers.github import provider") == {"github"}
    assert adapter_imports("loop/p.py", "from ..providers.local import Local") == {"local"}
    assert adapter_imports("services/agents/p.py", "from foreman.providers import github") == {"github"}
    assert adapter_imports("services/agents/p.py", "import foreman.providers.orca.cli") == {"orca"}


def test_the_port_and_the_registry_are_not_adapters():
    assert adapter_imports("services/agents/p.py", "from ...providers import registry, Provider") == set()
    assert adapter_imports("services/agents/p.py", "from ...providers.runner import Runner") == set()


def fake_providers(tmp_path: Path) -> Path:
    providers = tmp_path / "providers"
    (providers / "newone").mkdir(parents=True)
    (providers / "__pycache__").mkdir()
    for name in ("newone/__init__.py", "newmod.py", "registry.py", "runner.py", "__init__.py"):
        (providers / name).touch()
    return providers


def test_a_new_adapter_directory_or_module_is_found(tmp_path: Path):
    assert adapters(fake_providers(tmp_path)) == {"newone", "newmod"}


def test_an_import_of_a_new_adapter_is_caught(tmp_path: Path):
    source = "from foreman.providers.newone import x"
    assert adapter_imports("services/agents/p13.py", source, adapters(fake_providers(tmp_path))) == {"newone"}
    assert adapter_imports("services/agents/p13.py", source) == set()
