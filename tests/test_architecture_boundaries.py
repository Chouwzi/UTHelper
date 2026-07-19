import ast
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC_ROOT).with_suffix("").parts)


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _python_modules() -> dict[str, Path]:
    return {
        _module_name(path): path
        for path in SRC_ROOT.rglob("*.py")
        if path.name != "__init__.py"
    }


def test_core_does_not_import_gui():
    violations = {
        module: sorted(import_name for import_name in _imports_for(path) if import_name == "gui" or import_name.startswith("gui."))
        for module, path in _python_modules().items()
        if module.startswith("core.")
    }
    violations = {module: imports for module, imports in violations.items() if imports}

    assert violations == {}


def test_no_new_gui_component_to_app_controller_imports():
    known_debt = {
        "gui.components.calendar_view": {"gui.app_controller"},
        "gui.components.detail_view": {"gui.app_controller"},
        "gui.components.grade_overview_view": {"gui.app_controller"},
        "gui.components.settings_view": {"gui.app_controller"},
    }
    violations = {
        module: sorted(import_name for import_name in _imports_for(path) if import_name == "gui.app_controller")
        for module, path in _python_modules().items()
        if module.startswith("gui.components.")
    }
    violations = {module: set(imports) for module, imports in violations.items() if imports}

    assert violations == known_debt


def test_no_new_gui_to_low_level_moodle_ws_imports():
    known_debt = {}
    violations = {
        module: sorted(
            import_name
            for import_name in _imports_for(path)
            if import_name == "core.ws_functions" or import_name == "core"
        )
        for module, path in _python_modules().items()
        if module.startswith("gui.")
    }
    violations = {module: set(imports) for module, imports in violations.items() if imports}

    assert violations == known_debt


def test_low_level_moodle_ws_is_hidden_behind_moodle_service():
    allowed_modules = {"core.moodle_service", "core.ws_functions"}
    violations = {
        module: sorted(
            import_name
            for import_name in _imports_for(path)
            if import_name == "core.ws_functions" or import_name == "core"
        )
        for module, path in _python_modules().items()
        if module.startswith("core.") and module not in allowed_modules
    }
    violations = {module: set(imports) for module, imports in violations.items() if imports}

    assert violations == {}


def test_models_config_dependency_is_explicit_debt_only():
    imports = _imports_for(SRC_ROOT / "models.py")

    assert {"config"} & imports == {"config"}
