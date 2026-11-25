import ast
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


def _module_name(path: Path, src_root: Path = SRC_ROOT) -> str:
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "__root__"


def _resolve_relative_import(
    pkg_parts: list[str], level: int, module: str | None, alias_names: list[str]
) -> set[str]:
    ascend = level - 1
    base_parts = pkg_parts[: max(0, len(pkg_parts) - ascend)]
    prefix = ".".join(base_parts)
    resolved: set[str] = set()
    if module:
        resolved.add(f"{prefix}.{module}" if prefix else module)
    else:
        for name in alias_names:
            if name == "*":
                if prefix:
                    resolved.add(prefix)
            else:
                resolved.add(f"{prefix}.{name}" if prefix else name)
    return resolved


def _imports_for(path: Path, src_root: Path = SRC_ROOT) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg_parts = list(path.relative_to(src_root).parent.parts)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                imports.update(
                    _resolve_relative_import(
                        pkg_parts,
                        node.level,
                        node.module,
                        [alias.name for alias in node.names],
                    )
                )
            elif node.module:
                imports.add(node.module)
    return imports


def _python_modules(src_root: Path = SRC_ROOT) -> dict[str, Path]:
    return {
        _module_name(path, src_root): path
        for path in sorted(src_root.rglob("*.py"))
    }


def _is_core_module(module: str) -> bool:
    return module == "core" or module.startswith("core.")


def _is_gui_module(module: str) -> bool:
    return module == "gui" or module.startswith("gui.")


def test_core_does_not_import_gui():
    violations = {
        module: sorted(
            import_name
            for import_name in _imports_for(path)
            if import_name == "gui" or import_name.startswith("gui.")
        )
        for module, path in _python_modules().items()
        if _is_core_module(module)
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
        module: sorted(
            import_name
            for import_name in _imports_for(path)
            if import_name == "gui.app_controller"
            or import_name.startswith("gui.app_controller.")
        )
        for module, path in _python_modules().items()
        if module == "gui.components" or module.startswith("gui.components.")
    }
    violations = {module: set(imports) for module, imports in violations.items() if imports}

    assert violations == known_debt


def test_no_new_gui_to_low_level_moodle_ws_imports():
    known_debt = {}
    violations = {
        module: sorted(
            import_name
            for import_name in _imports_for(path)
            if import_name == "core.ws_functions"
            or import_name.startswith("core.ws_functions.")
            or import_name == "core"
        )
        for module, path in _python_modules().items()
        if _is_gui_module(module)
    }
    violations = {module: set(imports) for module, imports in violations.items() if imports}

    assert violations == known_debt


def test_low_level_moodle_ws_is_hidden_behind_moodle_service():
    allowed_modules = {"core.moodle_service", "core.ws_functions"}
    violations = {
        module: sorted(
            import_name
            for import_name in _imports_for(path)
            if import_name == "core.ws_functions"
            or import_name.startswith("core.ws_functions.")
            or import_name == "core"
        )
        for module, path in _python_modules().items()
        if _is_core_module(module) and module not in allowed_modules
    }
    violations = {module: set(imports) for module, imports in violations.items() if imports}

    assert violations == {}


def test_models_config_dependency_is_explicit_debt_only():
    imports = _imports_for(SRC_ROOT / "models.py")

    assert {
        imp for imp in imports if imp == "config" or imp.startswith("config.")
    } == set()


def test_no_new_core_to_config_or_platform_utils_imports():
    """Lock Phase 0 / Milestone 5 boundary debt so new core modules stay pure."""
    allowed_core_to_config = {
        "core.academic_radar.service",
        "core.client",
        "core.course_grade_monitor",
        "core.courses.cache_store",
        "core.courses.preview_cache",
        "core.data_cache",
        "core.data_orchestrator",
        "core.grade_monitor",
        "core.notification_history",
        "core.quiz_web_session",
        "core.today_schedule",
        "core.url_validation",
        "core.ws_functions",
    }
    allowed_core_to_platform_utils = {
        "core.autostart",
        "core.distribution",
        "core.update_checker",
        "core.update_coordinator",
    }

    config_couplings = {
        module
        for module, path in _python_modules().items()
        if _is_core_module(module)
        and any(
            imp == "config" or imp.startswith("config.")
            for imp in _imports_for(path)
        )
    }
    platform_couplings = {
        module
        for module, path in _python_modules().items()
        if _is_core_module(module)
        and any(
            imp == "platform_utils" or imp.startswith("platform_utils.")
            for imp in _imports_for(path)
        )
    }

    unexpected_config = sorted(config_couplings - allowed_core_to_config)
    unexpected_platform = sorted(platform_couplings - allowed_core_to_platform_utils)

    assert unexpected_config == [], (
        f"New core -> config coupling introduced: {unexpected_config}"
    )
    assert unexpected_platform == [], (
        f"New core -> platform_utils coupling introduced: {unexpected_platform}"
    )
