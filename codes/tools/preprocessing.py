#!/usr/bin/env python3
from typing import Dict, Any, List, Tuple, Optional
from fractions import Fraction
from math import gcd
from functools import reduce
import copy
import time


class EncodingTimeoutError(TimeoutError, RuntimeError):
    pass


def _check_deadline(deadline):
    if deadline > 0 and time.time() >= deadline:
        raise EncodingTimeoutError("Encoding deadline exceeded during preprocessing")


def _safe_fraction(val: Any, denom_cap: int = 10**6) -> Fraction:
    """Preserve the supplied integer or decimal value exactly."""
    return val if isinstance(val, Fraction) else Fraction(str(val))

def _lcm(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


def _lcm_many(nums: List[int]) -> int:
    if not nums:
        return 1
    return reduce(_lcm, nums, 1)


def _gcd_many(nums: List[int]) -> int:
    nums = [abs(n) for n in nums if n != 0]
    if not nums:
        return 1
    return reduce(gcd, nums)


def _binom(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    if k == 0 or k == n:
        return 1
    k = min(k, n - k)
    num = 1
    den = 1
    for i in range(1, k + 1):
        num *= (n - (k - i))
        den *= i
    return num // den


def _expand_term_by_shifts(term: Dict[str, Any], shift_map: Dict[str, int], deadline=0) -> Tuple[List[Tuple[Fraction, Dict[str, int]]], Fraction]:
    c = _safe_fraction(term.get('c', 1), 10**6)
    var_pows: Dict[str, int] = {vn: int(ep) for vn, ep in term.get('vars', {}).items()}

    # Incremental expansion
    partial: List[Tuple[Fraction, Dict[str, int]]] = [(c, {})]
    _check_deadline(deadline)
    for v, k in var_pows.items():
        d = int(shift_map.get(v, 0))
        new_partial: List[Tuple[Fraction, Dict[str, int]]] = []
        for coeff, vp in partial:
            # (Y + d)^k = sum_{j=0}^{k} C(k,j) * Y^j * d^(k-j)
            for j in ([k] if d == 0 else range(k + 1)):
                _check_deadline(deadline)
                coef2 = coeff * Fraction(_binom(k, j), 1) * (Fraction(d, 1) ** (k - j))
                vp2 = dict(vp)
                if j > 0:
                    vp2[v] = vp2.get(v, 0) + j
                new_partial.append((coef2, vp2))
        partial = new_partial

    # Merge like terms
    acc: Dict[Tuple[Tuple[str, int], ...], Fraction] = {}
    const_sum = Fraction(0, 1)
    for coeff, vp in partial:
        _check_deadline(deadline)
        if not vp:
            const_sum += coeff
        else:
            key = tuple(sorted((vn, exp) for vn, exp in vp.items() if exp != 0))
            if key:
                acc[key] = acc.get(key, Fraction(0, 1)) + coeff
            else:
                const_sum += coeff

    items: List[Tuple[Fraction, Dict[str, int]]] = []
    for key, coef in acc.items():
        if coef == 0:
            continue
        vp = {vn: exp for vn, exp in key}
        items.append((coef, vp))
    return items, const_sum


def _canonicalize_polynomial(terms: List[Dict[str, Any]], shift_map: Dict[str, int], deadline=0) -> Tuple[List[Dict[str, Any]], Fraction]:
    items_total: Dict[Tuple[Tuple[str, int], ...], Fraction] = {}
    const_total = Fraction(0, 1)
    for t in terms or []:
        _check_deadline(deadline)
        items, const_sum = _expand_term_by_shifts(t, shift_map, deadline)
        const_total += const_sum
        for coef, vp in items:
            key = tuple(sorted(vp.items()))
            items_total[key] = items_total.get(key, Fraction(0, 1)) + coef
    new_terms: List[Dict[str, Any]] = []
    for key, coef in items_total.items():
        if coef == 0:
            continue
        new_terms.append({'c': coef, 'vars': dict(key)})
    return new_terms, const_total


def _reduce_binary_powers(terms: List[Dict[str, Any]], variables: Dict[str, Dict[str, Any]], deadline=0) -> List[Dict[str, Any]]:

    acc: Dict[Tuple[Tuple[str, int], ...], Fraction] = {}
    for t in terms or []:
        _check_deadline(deadline)
        coeff = _safe_fraction(t.get('c', 0), 10**6)
        if coeff == 0:
            continue

        raw_vars = t.get('vars', {}) or {}
        reduced_vars: Dict[str, int] = {}
        for vn, power in raw_vars.items():
            _check_deadline(deadline)
            p = int(power)
            if p <= 0:
                continue
            info = variables.get(vn, {}) or {}
            lb = int(info.get('lb', 0))
            ub = int(info.get('ub', 0))
            # x in {0,1} => x^k == x for all k>=1
            reduced_vars[vn] = 1 if (lb == 0 and ub == 1) else p

        key = tuple(sorted((vn, exp) for vn, exp in reduced_vars.items() if exp != 0))
        acc[key] = acc.get(key, Fraction(0, 1)) + coeff

    merged: List[Dict[str, Any]] = []
    for key, coef in acc.items():
        if coef == 0:
            continue
        merged.append({'c': coef, 'vars': dict(key)})
    return merged


def _integerize_polynomial(terms, rhs, denom_cap, scale_limit, deadline=0):
    values = []
    factor = 1
    for t in terms:
        _check_deadline(deadline)
        value = _safe_fraction(t.get('c', 0), denom_cap)
        values.append(value)
        factor = _lcm(factor, value.denominator)
    if rhs is not None:
        value = _safe_fraction(rhs, denom_cap)
        values.append(value)
        factor = _lcm(factor, value.denominator)
    if factor > scale_limit:
        raise ValueError(f'exact integerization requires scale {factor}, limit is {scale_limit}')
    scaled, divisor = [], 0
    for value in values:
        _check_deadline(deadline)
        integer = int(value * factor)
        scaled.append(integer)
        divisor = gcd(divisor, abs(integer))
    divisor = divisor or 1
    result = []
    for t, integer in zip(terms, scaled):
        _check_deadline(deadline)
        if integer:
            result.append({'c': integer // divisor, 'vars': dict(t.get('vars', {}))})
    int_rhs = scaled[-1] // divisor if rhs is not None else None
    return result, int_rhs, (factor, divisor)


def _iter_constraint_atoms(constraints: List[Dict[str, Any]]):
    """Yield atoms for dependency checks without treating OR branches as AND."""
    for cons in constraints or []:
        if cons.get('type') == 'disjunction':
            for branch in cons.get('disjuncts', []) or []:
                for atom in branch:
                    yield atom
        else:
            yield cons


def _probe_binary_variables(terms, variables, constraints, deadline=0):
    """Fix only unconstrained binary variables with a globally monotone objective."""
    slopes = {}
    for v, info in variables.items():
        _check_deadline(deadline)
        if info['lb'] == 0 and info['ub'] == 1:
            slopes[v] = [0, 0]
    if not slopes:
        return {}
    for a in _iter_constraint_atoms(constraints):
        _check_deadline(deadline)
        for t in a.get('terms', []):
            _check_deadline(deadline)
            for v in t.get('vars', {}):
                slopes.pop(v, None)
        if not slopes:
            return {}
    for t in terms:
        _check_deadline(deadline)
        powers = t.get('vars', {})
        candidates = powers.keys() & slopes.keys()
        if not candidates:
            continue
        c = _safe_fraction(t.get('c', 0))
        # Every candidate is binary with upper bound 1, so its removal
        # leaves the same upper product for all candidates in this term.
        upper_product = 1
        for v, power in powers.items():
            _check_deadline(deadline)
            upper_product *= variables[v]['ub'] ** power
        lo, hi = (c, c) if len(powers) == 1 else (min(0, c * upper_product), max(0, c * upper_product))
        for v in candidates:
            _check_deadline(deadline)
            slopes[v][0] += lo
            slopes[v][1] += hi
    return {v: (1 if lo > 0 else 0) for v, (lo, hi) in slopes.items() if lo > 0 or hi < 0}


def _substitute_fixed_variables(terms, fixed, deadline=0):
    acc = {}
    constant = Fraction(0)
    for t in terms:
        _check_deadline(deadline)
        coeff = _safe_fraction(t.get('c', 0))
        remaining = {}
        for v, power in t.get('vars', {}).items():
            _check_deadline(deadline)
            if v in fixed:
                coeff *= fixed[v] ** power
            else:
                remaining[v] = power
        if remaining:
            key = tuple(sorted(remaining.items()))
            acc[key] = acc.get(key, Fraction(0)) + coeff
        else:
            constant += coeff
    return [{'c': c, 'vars': dict(k)} for k, c in acc.items() if c], constant

def _bound_tightening(constraints, variables, max_rounds=10, deadline=0):
    """Propagate exact rational bounds from complete linear constraints."""
    from math import floor, ceil
    _check_deadline(deadline)
    linear = []
    for con in constraints:
        _check_deadline(deadline)
        if con.get('type') == 'disjunction':
            continue
        terms, constant = _canonicalize_polynomial(con.get('terms', []), {}, deadline)
        if any(sum(t['vars'].values()) != 1 for t in terms):
            continue
        coeffs = {next(iter(t['vars'])): t['c'] for t in terms}
        linear.append((coeffs, con.get('rel', con.get('sense', '<=')),
                       _safe_fraction(con.get('rhs', 0)) - constant))
    total = 0
    for _ in range(max_rounds):
        changed = False
        for coeffs, rel, rhs in linear:
            _check_deadline(deadline)
            # Maintain the interval sum as each variable is tightened.
            # A full sweep takes linear work in the number of terms.
            sum_min = sum_max = Fraction(0)
            for v, c in coeffs.items():
                _check_deadline(deadline)
                lo, hi = variables[v]['lb'], variables[v]['ub']
                if lo > hi:
                    return total
                sum_min += c * (lo if c > 0 else hi)
                sum_max += c * (hi if c > 0 else lo)
            for v, c in coeffs.items():
                _check_deadline(deadline)
                info = variables[v]
                lo, hi = info['lb'], info['ub']
                own_min = c * (lo if c > 0 else hi)
                own_max = c * (hi if c > 0 else lo)
                other_min, other_max = sum_min - own_min, sum_max - own_max
                new_lo, new_hi = lo, hi
                if rel in ('<=', '<', '=', '=='):
                    bound = (rhs - other_min) / c
                    if c > 0:
                        new_hi = min(new_hi, ceil(bound) - 1 if rel == '<' else floor(bound))
                    else:
                        new_lo = max(new_lo, floor(bound) + 1 if rel == '<' else ceil(bound))
                if rel in ('>=', '>', '=', '=='):
                    bound = (rhs - other_max) / c
                    if c > 0:
                        new_lo = max(new_lo, floor(bound) + 1 if rel == '>' else ceil(bound))
                    else:
                        new_hi = min(new_hi, ceil(bound) - 1 if rel == '>' else floor(bound))
                if (new_lo, new_hi) != (lo, hi):
                    total += new_lo - lo + hi - new_hi
                    info.update(lb=new_lo, ub=new_hi)
                    changed = True
                    if new_lo > new_hi:
                        return total
                    sum_min += c * (new_lo if c > 0 else new_hi) - own_min
                    sum_max += c * (new_hi if c > 0 else new_lo) - own_max
        if not changed:
            break
    return total

def preprocess_problem(problem, denom_cap=10**6, scale_limit=10**6,
                       enable_min_to_max=True, enable_integerize=True,
                       enable_optimizations=True, deadline=0):
    """Normalize to nonnegative integer variables and a maximization objective.

    Normalization preserves mathematical meaning even when optional bound
    propagation and objective probing are disabled.
    """
    from math import ceil, floor
    _check_deadline(deadline)
    prob = copy.deepcopy({k: v for k, v in problem.items() if k != '_smt_assertions'})
    variables = prob.setdefault('variables', {})
    obj = prob.setdefault('objective', {'sense': 'max', 'terms': []})
    constraints = prob.setdefault('constraints', [])
    for v, info in variables.items():
        _check_deadline(deadline)
        info['lb'] = ceil(_safe_fraction(info['lb']))
        info['ub'] = floor(_safe_fraction(info['ub']))
    for a in _iter_constraint_atoms(constraints):
        _check_deadline(deadline)
        a['terms'] = a.get('terms', a.get('lhs', []))
        rel = a.get('rel', a.get('sense', '<='))
        if rel not in ('<=', '<', '>=', '>', '=', '=='):
            raise ValueError(f'unsupported relation: {rel}')
    all_terms = list(obj.get('terms', [])) + [t for a in _iter_constraint_atoms(constraints) for t in a['terms']]
    for t in all_terms:
        _check_deadline(deadline)
        for v, power in list(t.get('vars', {}).items()):
            if v not in variables or int(power) != power or power < 0:
                raise ValueError(f'invalid polynomial factor: {v}^{power}')
            if power == 0:
                del t['vars'][v]
    if enable_optimizations and all(v['lb'] <= v['ub'] for v in variables.values()):
        _bound_tightening(constraints, variables, deadline=deadline)
    infeasible = any(v['lb'] > v['ub'] for v in variables.values())
    if infeasible:
        constraints.append({'terms': [], 'rel': '==', 'rhs': 1})
        for v in variables.values():
            v['ub'] = max(v['ub'], v['lb'])
    shifts = {v: i['lb'] for v, i in variables.items() if i['lb']}
    for i in variables.values():
        i['ub'] -= i['lb']
        i['lb'] = 0
    terms, constant = _canonicalize_polynomial(obj.get('terms', []), shifts, deadline)
    terms = _reduce_binary_powers(terms, variables, deadline)
    sense = str(obj.get('sense', 'max')).lower()
    if sense not in ('max', 'maximize', 'maximum', 'min', 'minimize', 'minimum'):
        raise ValueError(f'unsupported objective sense: {sense}')
    flip = sense.startswith('min')
    if flip:
        terms = [{'c': -t['c'], 'vars': t['vars']} for t in terms]
        constant = -constant
    if not enable_integerize and any(_safe_fraction(t['c']).denominator != 1 for t in terms):
        raise ValueError('fractional coefficients require integerization')
    terms, _, (lcm, divisor) = _integerize_polynomial(terms, None, denom_cap, scale_limit * 100, deadline)
    probed = _probe_binary_variables(terms, variables, constraints, deadline) if enable_optimizations and not infeasible else {}
    if probed:
        terms, added = _substitute_fixed_variables(terms, probed, deadline)
        constant += added * Fraction(divisor, lcm)

    def transform_atom(a):
        ts, const = _canonicalize_polynomial(a['terms'], shifts, deadline)
        ts = _reduce_binary_powers(ts, variables, deadline)
        if probed:
            ts, added = _substitute_fixed_variables(ts, probed, deadline)
            const += added
        rhs = _safe_fraction(a.get('rhs', 0)) - const
        if not enable_integerize and (rhs.denominator != 1 or any(t['c'].denominator != 1 for t in ts)):
            raise ValueError('fractional constraints require integerization')
        ts, rhs, _ = _integerize_polynomial(ts, rhs, denom_cap, scale_limit, deadline)
        return {'terms': ts, 'rel': a.get('rel', a.get('sense', '<=')), 'rhs': rhs}

    prob['constraints'] = [
        {'type': 'disjunction', 'disjuncts': [[transform_atom(a) for a in b] for b in c['disjuncts']]}
        if c.get('type') == 'disjunction' else transform_atom(c) for c in constraints]
    for v in probed:
        del variables[v]
    prob['objective'] = {'sense': 'max', 'terms': [{'c': int(t['c']), 'vars': t['vars']} for t in terms]}
    meta = {'variable_shifts': shifts, 'constraints': [], 'objective': {
        'sense_flip': flip, 'scale_lcm': lcm, 'scale_gcd': divisor,
        'scale': Fraction(lcm, divisor), 'constant_shift': constant,
        'probed_variables': probed}}
    return prob, meta
