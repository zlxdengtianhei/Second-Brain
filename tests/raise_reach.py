"""Which ``raise`` statements the registered checks can reach, by a call graph read from the package's source.

The graph follows calls by name: a function of the same module, a name imported from another module of the package
(at the top of a module or inside a function), and ``module.function`` for a module of the package imported under
any name. It does not follow a call through a variable or a method; the roots are the registered checks
(``jobs.SUBMISSION_VALIDATORS``) and the maintenance kinds' checks (``runner.KindSpec``). Used by
``test_answer_errors``.
"""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import secondbrain

PACKAGE = Path(secondbrain.__file__).parent


def _module_name(path: Path) -> str:
    rel = path.relative_to(PACKAGE.parent).with_suffix('')
    return '.'.join(rel.parts[:-1] if rel.name == '__init__' else rel.parts)


def _resolve(module: str, level: int, name: str | None) -> str:
    if not level:
        return name or ''
    base = module.split('.')
    pkg = base[:-1] if not (PACKAGE / Path(*base[1:]) / '__init__.py').exists() else base
    pkg = pkg[:len(pkg) - (level - 1)] if level > 1 else pkg
    return '.'.join(pkg + ([name] if name else []))


class Module:
    def __init__(self, path: Path):
        self.name = _module_name(path)
        self.tree = ast.parse(path.read_text(encoding='utf-8'))
        self.functions = {n.name: n for n in self.tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.modules: dict = {}   # local name -> module
        self.names: dict = {}     # local name -> (module, name)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                source = _resolve(self.name, node.level, node.module)
                for alias in node.names:
                    target = f'{source}.{alias.name}'
                    if (PACKAGE.parent / Path(*target.split('.'))).with_suffix('.py').exists() or \
                            (PACKAGE.parent / Path(*target.split('.')) / '__init__.py').exists():
                        self.modules[alias.asname or alias.name] = target
                    else:
                        self.names[alias.asname or alias.name] = (source, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('secondbrain'):
                        self.modules[alias.asname or alias.name] = alias.name


def modules() -> dict:
    return {m.name: m for m in (Module(p) for p in PACKAGE.rglob('*.py'))}


def callees(mods: dict, module: str, fn: ast.AST) -> set:
    mod, out = mods[module], set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            if f.id in mod.functions:
                out.add((module, f.id))
            elif f.id in mod.names:
                out.add(mod.names[f.id])
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in mod.modules:
            out.add((mod.modules[f.value.id], f.attr))
    return {(m, n) for m, n in out if m in mods and n in mods[m].functions}


def roots() -> set:
    from secondbrain import jobs, runner
    out = set()
    for ref in jobs.SUBMISSION_VALIDATORS.values():
        module, name = ref.split(':')
        out.add((module, name))
    for kind in ('classification', 'curation', 'relocation', 'domcreate'):
        spec = runner._spec(kind)
        for fn in (spec.check, spec.review_check):
            fn = inspect.unwrap(fn)
            if fn.__name__ == '<lambda>':  # the classification check: it calls the kind's validate
                out.add((fn.__module__, 'validate'))
            else:
                out.add((fn.__module__, fn.__name__))
    return out


def reachable() -> tuple[dict, set]:
    mods = modules()
    seen, todo = set(), list(roots())
    while todo:
        key = todo.pop()
        if key in seen or key[0] not in mods or key[1] not in mods[key[0]].functions:
            continue
        seen.add(key)
        todo += list(callees(mods, key[0], mods[key[0]].functions[key[1]]))
    return mods, seen


def raises():
    """Every ``raise X(...)`` in a reachable function: ``(module, function, line, class, text)``."""
    mods, seen = reachable()
    out = []
    for module, name in sorted(seen):
        namespace = vars(importlib.import_module(module))
        fn = mods[module].functions[name]
        for node in ast.walk(fn):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                try:
                    cls = eval(ast.unparse(node.exc.func), dict(namespace))  # noqa: S307 - a name of the module
                except Exception:  # noqa: BLE001 - a class built inside the function: skipped
                    continue
                if isinstance(cls, type) and issubclass(cls, BaseException):
                    out.append((module, name, node.lineno, cls, ast.unparse(node.exc)[:100]))
    return out
