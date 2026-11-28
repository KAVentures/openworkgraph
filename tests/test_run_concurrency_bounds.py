from itertools import product
from random import Random
import pytest
from shared.run_concurrency_bounds import summarize


def test_random_exhaustive_completions():
    rng = Random(8801)
    for _ in range(250):
        w = rng.randint(1, 7)
        cap = rng.randint(1, 4)
        measured = [(f'm{i}', a, rng.randint(a+1, w)) for i, a in enumerate(rng.choices(range(w), k=rng.randrange(5)))]
        unresolved = [(f'u{i}', a) for i, a in enumerate(rng.choices(range(w), k=rng.randrange(4)))]
        trajectories = []
        for ends in product(*(range(a, w+1) for _, a in unresolved)):
            intervals = measured + [(key, a, b) for (key, a), b in zip(unresolved, ends)]
            trajectories.append([sum(a <= t < b for _, a, b in intervals) for t in range(w)])
        lower = [min(x[t] for x in trajectories) for t in range(w)]
        upper = [max(x[t] for x in trajectories) for t in range(w)]
        r = summarize(measured, unresolved, w, cap)
        assert [(x['lower_runs'], x['upper_runs']) for x in r['segments'] for _ in range(x['start_us'], x['end_us'])] == list(zip(lower, upper))
        s = r['summary']
        above = [sum(v > cap for v in x) for x in trajectories]
        assert s['measured_above_capacity_us'] == min(above)
        assert s['upper_above_capacity_us'] == max(above)
        assert s['additional_possible_above_capacity_us'] == max(above)-min(above)
        assert s['peak_lower_runs'] == min(map(max, trajectories))
        assert s['peak_upper_runs'] == max(map(max, trajectories))
        assert int(s['lower_run_us']) == min(map(sum, trajectories))
        assert int(s['upper_run_us']) == max(map(sum, trajectories))
        assert r == summarize(list(reversed(measured)), list(reversed(unresolved)), w, cap)


def test_boundaries_and_empty():
    r = summarize([('a', 0, 3), ('b', 3, 5)], [('c', 3)], 5, 1)
    assert r['segments'] == [
        {'start_us': 0, 'end_us': 3, 'lower_runs': 1, 'upper_runs': 1, 'capacity_state': 'not_above_in_envelope'},
        {'start_us': 3, 'end_us': 5, 'lower_runs': 1, 'upper_runs': 2, 'capacity_state': 'possibly_above'}]
    assert summarize([], [], 5, 1)['summary']['not_above_in_envelope_us'] == 5


def test_large_exact_decimal_totals():
    w = 366 * 86400 * 1000000
    r = summarize([], [(str(i), 0) for i in range(10000)], w, 5000)
    assert r['summary']['upper_run_us'] == str(10000*w)
    assert r['summary']['upper_above_capacity_us'] == w
    assert r['summary']['measured_above_capacity_us'] == 0


@pytest.mark.parametrize('measured,unresolved,w,cap', [
    ([('a',0,1)],[('a',0)],2,1), ([],[('a',2)],2,1),
    ([('a',1,1)],[],2,1), ([],[('a',True)],2,1),
    ([],[],0,1), ([],[],2,True), ([],[('a',-1)],2,1),
])
def test_invalid(measured, unresolved, w, cap):
    with pytest.raises(ValueError):
        summarize(measured, unresolved, w, cap)
