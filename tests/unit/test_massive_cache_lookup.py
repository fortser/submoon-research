"""Поиск сегмента MassiveCache: эквивалентность прежней семантике searchsorted.

Не требует REBOUND: сегменты задаются напрямую. Проверяет, что курсор и bisect
(W2-T008) выбирают тот же сегмент и отказывают в тех же случаях, что и прежняя
реализация np.searchsorted(ends, t, side='right').
"""
import math

import numpy as np
import pytest

from submoon_research.dynamics.dense_segments import PowerSegment
from submoon_research.dynamics.native_ias15 import MassiveCache


def reference_index(ends, lefts, t):
    """Прежняя реализация segment_at, возвращающая индекс или None при отказе."""
    index = int(np.searchsorted(ends, t, side='right'))
    if index == len(ends) and ends and t == ends[-1]:
        index -= 1
    if index >= len(ends) or t < lefts[index]:
        return None
    return index


def make_cache(bounds):
    cache = MassiveCache(np.zeros((1, 6)), [1.0], [])
    for left, right in zip(bounds[:-1], bounds[1:]):
        cache.segments.append(PowerSegment(float(left), float(right), np.zeros((10, 6))))
        cache.ends.append(float(right))
    return cache


def lookup(cache, t):
    try:
        segment = cache.segment_at(t)
    except ValueError:
        return None
    return cache.segments.index(segment)


def probe_times(bounds, rng):
    inner = rng.uniform(bounds[0], bounds[-1], 400)
    edges = np.concatenate([bounds, np.nextafter(bounds, -np.inf), np.nextafter(bounds, np.inf)])
    outside = [bounds[0]-1.0, bounds[-1]+1.0, -math.inf, math.inf, math.nan]
    return [*inner, *edges, *outside]


@pytest.mark.parametrize('seed', range(5))
def test_lookup_matches_previous_searchsorted_in_any_order(seed):
    rng = np.random.default_rng(seed)
    bounds = np.concatenate([[0.0], np.cumsum(rng.uniform(1e-4, 0.3, 257))])
    cache = make_cache(bounds)
    lefts = [s.left for s in cache.segments]
    times = probe_times(bounds, rng)
    for order in (sorted(t for t in times if not math.isnan(t)), list(rng.permutation(times))):
        for t in order:
            assert lookup(cache, t) == reference_index(cache.ends, lefts, t), t


def test_sequential_and_backward_queries_inside_and_across_segments():
    bounds = np.array([0.0, 0.5, 0.75, 2.0, 2.5])
    cache = make_cache(bounds)
    lefts = [s.left for s in cache.segments]
    forward = np.linspace(0.0, 2.5, 101)
    for t in [*forward, *forward[::-1], 0.6, 0.4, 0.6, 2.5, 0.0, 2.5]:
        assert lookup(cache, t) == reference_index(cache.ends, lefts, t), t


def test_end_of_coverage_and_outside_raise_as_before():
    cache = make_cache(np.array([0.0, 1.0, 2.0]))
    assert cache.segment_at(2.0) is cache.segments[-1]
    assert cache.segment_at(1.0) is cache.segments[1]
    for t in (-1e-300, 2.0+1e-12, math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            cache.segment_at(t)
    empty = MassiveCache(np.zeros((1, 6)), [1.0], [])
    with pytest.raises(ValueError):
        empty.segment_at(0.0)


def test_cursor_stays_valid_after_extension_and_replacement():
    cache = make_cache(np.array([0.0, 1.0, 2.0]))
    assert cache.segment_at(1.5) is cache.segments[1]
    # Расширение (как в extend): курсор указывает на прежний сегмент, новые доступны.
    cache.segments.append(PowerSegment(2.0, 3.0, np.zeros((10, 6))))
    cache.ends.append(3.0)
    assert cache.segment_at(2.0) is cache.segments[2]
    assert cache.segment_at(1.5) is cache.segments[1]
    # Замена набора сегментов меньшим (как при load): устаревший курсор не используется.
    cache.segment_at(2.5)
    cache.segments, cache.ends = cache.segments[:1], cache.ends[:1]
    assert cache.segment_at(0.5) is cache.segments[0]
    with pytest.raises(ValueError):
        cache.segment_at(1.5)


def test_saved_and_loaded_cache_resets_cursor(tmp_path):
    from submoon_research.workflows.smoke import sha256
    cache = make_cache(np.array([0.0, 0.25, 1.0, 4.0]))
    cache.segment_at(3.0)
    path = tmp_path/'cache.npz'
    cache.save(path)
    loaded = MassiveCache(np.zeros((1, 6)), [1.0], [])
    loaded._cursor = 99
    loaded.load(path, sha256(path))
    assert loaded._cursor == 0
    for t in (0.0, 0.1, 0.25, 3.9, 4.0):
        assert loaded.segment_at(t).left == cache.segment_at(t).left
