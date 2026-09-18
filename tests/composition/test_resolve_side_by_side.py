# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""SR-0044: declared sources resolve side by side, bind in declared order, and a
shared cache directory is never written by two resolutions at once."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from throughline.model import Project

from throughline import composition as cli
from throughline import resolvers as spi
from throughline.sources import Source


class _Slow(spi.Resolver):
    """A resolver that takes a fixed time and records when each source ran."""

    def __init__(self, delay: float, fail: str | None = None):
        self.delay, self.fail = delay, fail
        self.spans: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def handles(self, source: Source) -> bool:
        return True

    def resolve(self, source: Source, consumer_root: Path):
        start = time.perf_counter()
        time.sleep(self.delay)
        if source.namespace == self.fail:
            raise spi.ResolverError(f"source '{source.namespace}' broke")
        with self.lock:
            self.spans[source.namespace] = (start, time.perf_counter())
        # A leaf: declares nothing, so the transitive walk (SR-0045) finds no closure.
        project = Project(path=str(consumer_root),
                          config={"project": {"name": source.namespace}})
        return spi.ResolvedSource(project=project, fingerprint=f"fp-{source.namespace}")


def _src(ns: str, url: str = None, ref: str = "v1", path: str = None) -> Source:
    return Source(namespace=ns, url=url, ref=ref if url else None, path=path)


@pytest.fixture
def slow(monkeypatch):
    resolver = _Slow(0.4)
    monkeypatch.setattr(cli, "resolver_for", lambda s: resolver)
    return resolver


def test_sources_resolve_at_the_same_time_and_bind_in_declared_order(slow, tmp_path):
    sources = [_src("a", "https://x/a"), _src("b", "https://x/b"), _src("c", "https://x/c")]
    t = time.perf_counter()
    out = cli.resolve_sources(sources, tmp_path)
    elapsed = time.perf_counter() - t
    assert elapsed < 0.9, f"three 0.4s resolutions took {elapsed:.2f}s — not side by side"
    assert list(out.resolved) == ["a", "b", "c"]
    assert [out.resolved[ns].project.config["project"]["name"]
            for ns in ("a", "b", "c")] == ["a", "b", "c"]


def test_a_shared_url_and_ref_is_never_resolved_twice_at_once(slow, tmp_path):
    sources = [_src("one", "https://x/same", "v2"), _src("two", "https://x/same", "v2", ), _src("other", "https://x/o")]
    sources[1] = Source(namespace="two", url="https://x/same", ref="v2", path=None, subdir="sub")
    cli.resolve_sources(sources, tmp_path)
    a0, a1 = slow.spans["one"]
    b0, b1 = slow.spans["two"]
    assert b0 >= a1, "the second source sharing the cache directory started before the first finished"
    c0, _ = slow.spans["other"]
    assert c0 < a1, "a source with its own cache directory should have run beside the first"


def test_the_first_failure_in_declared_order_is_the_one_reported(monkeypatch, tmp_path):
    resolver = _Slow(0.1, fail="b")
    monkeypatch.setattr(cli, "resolver_for", lambda s: resolver)
    sources = [_src("a", "https://x/a"), _src("b", "https://x/b"), _src("c", "https://x/c")]
    with pytest.raises(spi.ResolverError, match="source 'b' broke"):
        cli.resolve_sources(sources, tmp_path)


def test_without_threads_the_sources_resolve_in_order(slow, tmp_path, monkeypatch):
    """Pyodide cannot start a thread: the editor's worker must still compose."""
    import concurrent.futures
    import sys

    monkeypatch.setattr(sys, "platform", "emscripten")

    def refuse(*a, **k):
        raise AssertionError("a thread pool must not be built where threads cannot start")

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", refuse)
    sources = [_src("a", "https://x/a"), _src("b", "https://x/b")]
    out = cli.resolve_sources(sources, tmp_path)
    assert list(out.resolved) == ["a", "b"]
    a0, a1 = slow.spans["a"]
    b0, _ = slow.spans["b"]
    assert b0 >= a1, "without threads the second source must start after the first finishes"


def test_a_thread_that_cannot_start_falls_back_to_order(slow, tmp_path, monkeypatch):
    import concurrent.futures

    class NoThreads:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, *a, **k):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", NoThreads)
    sources = [_src("a", "https://x/a"), _src("b", "https://x/b")]
    out = cli.resolve_sources(sources, tmp_path)
    assert list(out.resolved) == ["a", "b"] and {"a", "b"} <= set(slow.spans)
