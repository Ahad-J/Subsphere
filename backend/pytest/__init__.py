"""Self-contained pytest-compatible test runner for offline and CI execution."""
from __future__ import annotations

import importlib.util
import inspect
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from typing import Any, Callable


class SkipException(Exception):
    pass


class FailedException(AssertionError):
    pass


def skip(reason: str = "") -> None:
    raise SkipException(reason)


def fail(msg: str = "") -> None:
    raise FailedException(msg)


@contextmanager
def raises(expected_exc: type[BaseException] | tuple[type[BaseException], ...], match: str | None = None):
    try:
        yield
    except expected_exc as exc:
        if match and not re.search(match, str(exc)):
            raise AssertionError(f"Regex {match!r} did not match {str(exc)!r}")
        return
    raise AssertionError(f"Did not raise {expected_exc}")


def fixture(func: Callable[..., Any] | None = None, *, scope: str = "function", autouse: bool = False):
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._pytest_fixture = {"scope": scope, "autouse": autouse}
        return fn
    if func is not None:
        return deco(func)
    return deco


class _Mark:
    def __getattr__(self, name: str):
        def marker(*args: Any, **kwargs: Any):
            def deco(fn: Callable[..., Any]):
                marks = getattr(fn, "_pytest_marks", [])
                marks.append((name, args, kwargs))
                fn._pytest_marks = marks
                return fn
            return deco
        return marker

    def parametrize(self, argnames: str | list[str], argvalues: list[Any]):
        names = [a.strip() for a in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
        def deco(fn: Callable[..., Any]):
            params = getattr(fn, "_pytest_parametrize", [])
            params.append((names, list(argvalues)))
            fn._pytest_parametrize = params
            return fn
        return deco


mark = _Mark()


class MonkeyPatch:
    def __init__(self):
        self._undo: list[Callable[[], None]] = []

    def setenv(self, name: str, value: str) -> None:
        had = name in os.environ
        old = os.environ.get(name)
        os.environ[name] = value
        def undo():
            if had and old is not None:
                os.environ[name] = old
            else:
                os.environ.pop(name, None)
        self._undo.append(undo)

    def delenv(self, name: str, raising: bool = True) -> None:
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            return
        old = os.environ[name]
        del os.environ[name]
        self._undo.append(lambda: os.environ.__setitem__(name, old))

    def setattr(self, target: Any, name: str, value: Any) -> None:
        old = getattr(target, name)
        setattr(target, name, value)
        self._undo.append(lambda: setattr(target, name, old))

    def undo(self) -> None:
        while self._undo:
            fn = self._undo.pop()
            fn()


def _load_module(path: pathlib.Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _collect_fixtures(mod: Any, conftests: list[Any]) -> dict[str, Callable[..., Any]]:
    fixtures: dict[str, Callable[..., Any]] = {}
    for cmod in conftests:
        for attr in dir(cmod):
            val = getattr(cmod, attr)
            if callable(val) and hasattr(val, "_pytest_fixture"):
                fixtures[attr] = val
    for attr in dir(mod):
        val = getattr(mod, attr)
        if callable(val) and hasattr(val, "_pytest_fixture"):
            fixtures[attr] = val
    return fixtures


def _resolve_fixture(
    name: str,
    fixtures: dict[str, Callable[..., Any]],
    cache: dict[str, Any],
    teardowns: list[Any],
    cleanups: list[Callable[[], None]],
) -> Any:
    if name in cache:
        return cache[name]
    if name == "monkeypatch":
        mp = MonkeyPatch()
        cleanups.append(mp.undo)
        cache[name] = mp
        return mp
    if name == "tmp_path":
        d = pathlib.Path(tempfile.mkdtemp(prefix="pytest_tmp_"))
        cleanups.append(lambda: shutil.rmtree(d, ignore_errors=True))
        cache[name] = d
        return d
    if name not in fixtures:
        raise KeyError(f"Fixture {name!r} not found")
    fn = fixtures[name]
    sig = inspect.signature(fn)
    kwargs = {}
    for pname in sig.parameters:
        kwargs[pname] = _resolve_fixture(pname, fixtures, cache, teardowns, cleanups)
    if inspect.isgeneratorfunction(fn):
        gen = fn(**kwargs)
        val = next(gen)
        teardowns.append(gen)
    else:
        val = fn(**kwargs)
    cache[name] = val
    return val


def main(args: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if args is None else args)
    quiet = "-q" in argv
    verbose = "-v" in argv
    k_filter = None
    targets: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-q", "-v", "-s"):
            i += 1
        elif a == "-k" and i + 1 < len(argv):
            k_filter = argv[i + 1]
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            targets.append(a)
            i += 1

    root = pathlib.Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    test_files: list[pathlib.Path] = []
    search_roots = [pathlib.Path(t) for t in targets] if targets else [root / "tests" if (root / "tests").exists() else root]
    for sr in search_roots:
        if sr.is_file():
            test_files.append(sr)
        elif sr.is_dir():
            for p in sorted(sr.rglob("test_*.py")):
                test_files.append(p)

    passed = 0
    failed = 0
    skipped = 0
    failures: list[tuple[str, str]] = []
    start_ts = time.perf_counter()

    conftest_cache: dict[pathlib.Path, Any] = {}

    def get_conftests(test_path: pathlib.Path) -> list[Any]:
        out = []
        for parent in reversed(test_path.parents):
            cf = parent / "conftest.py"
            if cf.exists():
                if cf not in conftest_cache:
                    conftest_cache[cf] = _load_module(cf, f"conftest_{abs(hash(str(cf)))}")
                out.append(conftest_cache[cf])
        return out

    for idx, tf in enumerate(test_files):
        conftests = get_conftests(tf.resolve())
        mod = _load_module(tf, f"_test_mod_{idx}_{tf.stem}")
        fixtures = _collect_fixtures(mod, conftests)

        test_funcs: list[tuple[str, Callable[..., Any]]] = []
        for attr_name in dir(mod):
            if attr_name.startswith("test_"):
                fn = getattr(mod, attr_name)
                if callable(fn):
                    test_funcs.append((attr_name, fn))
        test_funcs.sort(key=lambda item: getattr(item[1], "__code__", None).co_firstlineno if hasattr(item[1], "__code__") else 0)

        for tname, fn in test_funcs:
            param_sets = getattr(fn, "_pytest_parametrize", None)
            cases: list[tuple[str, dict[str, Any]]] = []
            if param_sets:
                names, vals = param_sets[0]
                for v_idx, v in enumerate(vals):
                    if len(names) == 1:
                        cases.append((f"{tname}[{v_idx}]", {names[0]: v}))
                    else:
                        cases.append((f"{tname}[{v_idx}]", dict(zip(names, v))))
            else:
                cases.append((tname, {}))

            for case_label, extra_kwargs in cases:
                full_name = f"{tf}::{case_label}"
                if k_filter and k_filter not in full_name:
                    continue

                cache: dict[str, Any] = {}
                teardowns: list[Any] = []
                cleanups: list[Callable[[], None]] = []
                try:
                    sig = inspect.signature(fn)
                    call_kwargs = dict(extra_kwargs)
                    for fname, fval in fixtures.items():
                        if getattr(fval, "_pytest_fixture", {}).get("autouse"):
                            _resolve_fixture(fname, fixtures, cache, teardowns, cleanups)
                    for pname in sig.parameters:
                        if pname not in call_kwargs:
                            call_kwargs[pname] = _resolve_fixture(pname, fixtures, cache, teardowns, cleanups)
                    fn(**call_kwargs)
                    passed += 1
                    if verbose:
                        print(f"{full_name} PASSED")
                    elif not quiet:
                        print(".", end="", flush=True)
                except SkipException:
                    skipped += 1
                    if verbose:
                        print(f"{full_name} SKIPPED")
                    elif not quiet:
                        print("s", end="", flush=True)
                except BaseException:
                    failed += 1
                    tb = traceback.format_exc()
                    failures.append((full_name, tb))
                    if verbose:
                        print(f"{full_name} FAILED")
                    elif not quiet:
                        print("F", end="", flush=True)
                finally:
                    for gen in reversed(teardowns):
                        try:
                            next(gen, None)
                        except Exception:
                            pass
                    for cln in reversed(cleanups):
                        try:
                            cln()
                        except Exception:
                            pass

    elapsed = time.perf_counter() - start_ts
    if not verbose and not quiet:
        print()
    for name, tb in failures:
        print(f"\n=== FAILURES: {name} ===\n{tb}")
    print(f"{passed} passed, {failed} failed, {skipped} skipped in {elapsed:.2f}s")
    return 1 if failed else 0


__all__ = ["fail", "fixture", "main", "mark", "raises", "skip"]
