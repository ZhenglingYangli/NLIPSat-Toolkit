#!/usr/bin/env python3
import os
from fractions import Fraction
from math import ceil, floor
from typing import Dict, Any, List


def _strip_comment(line: str) -> str:
    return line.split('#')[0].strip()


def parse_qplib_file(filepath: str) -> Dict[str, Any]:

    with open(filepath, 'r') as f:
        raw_lines = [line.rstrip('\n') for line in f]

    pos = [0]

    def next_line() -> str:
        while pos[0] < len(raw_lines):
            line = _strip_comment(raw_lines[pos[0]])
            pos[0] += 1
            if line:
                return line
        return ''

    def next_val() -> str:
        return _strip_comment(next_line())

    def next_int() -> int:
        value = Fraction(next_val())
        if value.denominator != 1:
            raise ValueError('expected integer in QPLIB header/count')
        return int(value)

    def next_float() -> Fraction:
        return Fraction(next_val())

    # ── 1. Header ──────────────────────────────────────────────
    problem_name = next_val()
    problem_type = next_val()
    obj_sense_raw = next_val()
    num_vars = next_int()

    obj_type = problem_type[0]   # Q / L / D / C / N
    var_type = problem_type[1]   # B / I / C / M / G
    con_type = problem_type[2]   # B / L / Q / N

    if var_type not in ('B', 'I'):
        raise ValueError(f'QPLIB variable type {var_type} is not supported: bounded pure integer problems required')
    if obj_type not in ('L', 'Q', 'D', 'C') or con_type not in ('N', 'B', 'L', 'Q', 'D', 'C'):
        raise ValueError(f'unsupported QPLIB problem type: {problem_type}')
    if obj_sense_raw not in ('maximize', 'minimize'):
        raise ValueError(f'unsupported QPLIB objective sense: {obj_sense_raw}')
    has_constraints = (con_type not in ('N', 'B'))
    has_obj_quad = (obj_type in ('Q', 'D', 'C'))
    has_con_quad = (con_type in ('Q', 'D', 'C'))
    is_binary = (var_type == 'B')

    if con_type == 'N' and not is_binary:
        raise ValueError('unbounded QPLIB integer variables are unsupported')
    num_constraints = next_int() if has_constraints else 0

    problem = {
        'name': problem_name,
        'type': problem_type,
        'source': os.path.abspath(filepath),
        'variables': {},
        'objective': {
            'sense': 'max' if obj_sense_raw == 'maximize' else 'min',
            'terms': []
        },
        'constraints': []
    }

    # ── 2. Objective quadratic terms ───────────────────────────
    if has_obj_quad:
        num_quad = next_int()
        for _ in range(num_quad):
            parts = next_line().split()
            i, j = int(parts[0]), int(parts[1])
            val = Fraction(parts[2])
            if val == 0:
                continue
            if i == j:
                # Hessian convention: coeff of x_i^2 = Q[i][i] / 2
                problem['objective']['terms'].append({
                    'c': val / 2,
                    'vars': {f'x{i}': 2}
                })
            else:
                # QPLIB stores one triangle of Q in (1/2) x^T Q x.
                # Off-diagonal entries also carry the factor 1/2.
                problem['objective']['terms'].append({
                    'c': val / 2,
                    'vars': {f'x{i}': 1, f'x{j}': 1}
                })

    # ── 3. Objective linear terms (default + non-default) ─────
    default_linear = next_float()
    num_nd_linear = next_int()
    linear_overrides = {}
    for _ in range(num_nd_linear):
        parts = next_line().split()
        linear_overrides[int(parts[0])] = Fraction(parts[1])

    for vi in range(1, num_vars + 1):
        c = linear_overrides.get(vi, default_linear)
        if c != 0:
            problem['objective']['terms'].append({
                'c': c,
                'vars': {f'x{vi}': 1}
            })

    # ── 4. Objective constant ─────────────────────────────────
    obj_constant = next_float()
    if obj_constant != 0:
        problem['objective']['terms'].append({
            'c': obj_constant,
            'vars': {}
        })

    # ── 5. Constraint quadratic terms (Hessians, if Q/N) ──────
    con_quad = {}
    if has_constraints and has_con_quad:
        num_cq = next_int()
        for _ in range(num_cq):
            parts = next_line().split()
            ci, i, j = int(parts[0]), int(parts[1]), int(parts[2])
            val = Fraction(parts[3])
            if val == 0:
                continue
            con_quad.setdefault(ci, [])
            if i == j:
                con_quad[ci].append({'c': val / 2, 'vars': {f'x{i}': 2}})
            else:
                con_quad[ci].append({'c': val / 2, 'vars': {f'x{i}': 1, f'x{j}': 1}})

    # ── 6. Constraint linear terms (Jacobian) ─────────────────
    con_linear = {}
    if has_constraints:
        num_jac = next_int()
        for _ in range(num_jac):
            parts = next_line().split()
            ci, vi = int(parts[0]), int(parts[1])
            val = Fraction(parts[2])
            if val == 0:
                continue
            con_linear.setdefault(ci, [])
            con_linear[ci].append({'c': val, 'vars': {f'x{vi}': 1}})

    # ── 7. Bounds: infinity, LHS, RHS ─────────────────────────
    infinity_val = next_float()

    if has_constraints:
        default_lhs = next_float()
        num_nd_lhs = next_int()
        lhs_map = {}
        for _ in range(num_nd_lhs):
            parts = next_line().split()
            lhs_map[int(parts[0])] = Fraction(parts[1])

        default_rhs = next_float()
        num_nd_rhs = next_int()
        rhs_map = {}
        for _ in range(num_nd_rhs):
            parts = next_line().split()
            rhs_map[int(parts[0])] = Fraction(parts[1])

        # Build constraint list
        for ci in range(1, num_constraints + 1):
            terms = con_quad.get(ci, []) + con_linear.get(ci, [])
            lhs = lhs_map.get(ci, default_lhs)
            rhs = rhs_map.get(ci, default_rhs)

            inf_lhs = (lhs <= -infinity_val)
            inf_rhs = (rhs >= infinity_val)

            if inf_lhs and inf_rhs:
                continue
            elif inf_lhs:
                problem['constraints'].append({
                    'terms': terms, 'rel': '<=', 'rhs': rhs
                })
            elif inf_rhs:
                problem['constraints'].append({
                    'terms': terms, 'rel': '>=', 'rhs': lhs
                })
            elif lhs == rhs:
                problem['constraints'].append({
                    'terms': terms, 'rel': '==', 'rhs': rhs
                })
            else:
                problem['constraints'].append({
                    'terms': list(terms), 'rel': '>=', 'rhs': lhs
                })
                import copy
                problem['constraints'].append({
                    'terms': copy.deepcopy(terms), 'rel': '<=', 'rhs': rhs
                })

    # ── 8. Variable bounds ────────────────────────────────────
    if is_binary:
        for vi in range(1, num_vars + 1):
            problem['variables'][f'x{vi}'] = {'lb': 0, 'ub': 1}
    else:
        default_lb = next_float()
        num_nd_lb = next_int()
        lb_map = {}
        for _ in range(num_nd_lb):
            parts = next_line().split()
            lb_map[int(parts[0])] = Fraction(parts[1])

        default_ub = next_float()
        num_nd_ub = next_int()
        ub_map = {}
        for _ in range(num_nd_ub):
            parts = next_line().split()
            ub_map[int(parts[0])] = Fraction(parts[1])

        for vi in range(1, num_vars + 1):
            lb = lb_map.get(vi, default_lb)
            ub = ub_map.get(vi, default_ub)
            if lb <= -infinity_val or ub >= infinity_val:
                raise ValueError(f'x{vi} has no finite QPLIB bounds; refusing artificial bounds')
            problem['variables'][f'x{vi}'] = {
                'lb': ceil(lb), 'ub': floor(ub)
            }

    print(f"Parsed {problem_name} ({problem_type}): "
          f"{num_vars} vars, {num_constraints} constrs, "
          f"{len(problem['objective']['terms'])} obj terms")

    return problem
