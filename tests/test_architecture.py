import ast
import unittest
from pathlib import Path


PACKAGE = Path(__file__).parents[1] / "src" / "tg_forwarder"
SETUP_PACKAGE = Path(__file__).parents[1] / "src" / "tg_setup"
ALLOWED_DEPENDENCIES = {
    "__init__": set(),
    "__main__": {"cli"},
    "app": {"config", "core", "errors", "ports"},
    "bootstrap": {
        "app",
        "config",
        "errors",
        "reset_service",
        "state",
    },
    "cli": {"bootstrap", "config", "errors"},
    "config": set(),
    "core": {"models"},
    "errors": set(),
    "models": set(),
    "ports": {"core", "models"},
    "reset_service": {"config", "core", "ports"},
    "state": {"core", "models"},
}

SETUP_ALLOWED_DEPENDENCIES = {
    "__init__": set(),
    "__main__": {"cli"},
    "bot_setup": {"errors"},
    "cli": {"bot_setup", "config", "errors", "service"},
    "config": set(),
    "errors": set(),
    "models": set(),
    "service": {"config", "errors", "models"},
}


def internal_dependencies(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            dependencies.add(node.module.split(".", 1)[0])
    return dependencies


def local_call_graph(path: Path) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    graph: dict[str, set[str]] = {}

    module_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            graph[node.name] = {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in module_functions
            }

        if not isinstance(node, ast.ClassDef):
            continue
        methods = {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for child in node.body:
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            graph[f"{node.name}.{child.name}"] = {
                f"{node.name}.{call.func.attr}"
                for call in ast.walk(child)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in {"self", "cls"}
                and call.func.attr in methods
            }
    return graph


def assert_acyclic(
    testcase: unittest.TestCase,
    graph: dict[str, set[str]],
    label: str,
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            cycle_start = path.index(node)
            cycle = (*path[cycle_start:], node)
            testcase.fail(f"{label} cycle: {' -> '.join(cycle)}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency, (*path, node))
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, ())


class ArchitectureTests(unittest.TestCase):
    def test_internal_imports_follow_the_layer_tree(self) -> None:
        for package, allowed_dependencies in (
            (PACKAGE, ALLOWED_DEPENDENCIES),
            (SETUP_PACKAGE, SETUP_ALLOWED_DEPENDENCIES),
        ):
            modules = {path.stem: path for path in package.glob("*.py")}
            with self.subTest(package=package.name):
                self.assertEqual(set(modules), set(allowed_dependencies))
            for module, path in modules.items():
                with self.subTest(package=package.name, module=module):
                    self.assertEqual(
                        internal_dependencies(path),
                        allowed_dependencies[module],
                    )

    def test_internal_import_graph_has_no_cycles(self) -> None:
        for package in (PACKAGE, SETUP_PACKAGE):
            graph = {
                path.stem: internal_dependencies(path)
                for path in package.glob("*.py")
            }
            with self.subTest(package=package.name):
                assert_acyclic(self, graph, "Import")

    def test_local_function_and_method_calls_have_no_cycles(self) -> None:
        for package in (PACKAGE, SETUP_PACKAGE):
            for path in package.glob("*.py"):
                with self.subTest(package=package.name, module=path.stem):
                    assert_acyclic(self, local_call_graph(path), "Call")


if __name__ == "__main__":
    unittest.main()
