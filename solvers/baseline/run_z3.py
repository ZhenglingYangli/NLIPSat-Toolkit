#!/usr/bin/env python3
"""Z3 baseline solver for QPLIB and SMT2 instances.

Pipeline for QPLIB:
  QPLIB file  →  qplib_parser  →  Python dict (variables, objective, constraints)
  →  Z3 Int variables + quadratic/linear constraints + objective  →  Z3 solve

Two-phase solving strategy:
  Phase 1 – Optimize(): fast for small / linear problems, can prove optimality.
  Phase 2 – Solver() + iterative tightening: more robust for nonlinear integer
            arithmetic where Optimize cannot determine bounds.
"""

import argparse
import json
import sys
import os
import time
import traceback
from fractions import Fraction
from pathlib import Path

CODES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "codes")
sys.path.insert(0, CODES_DIR)

try:
    from z3 import Int, IntVal, Optimize, Solver, SolverFor, sat, unsat, unknown, RatVal, And, Or, is_true, simplify
    HAS_Z3 = True
except ImportError:
    HAS_Z3 = False


# ---------------------------------------------------------------------------
#  Parsing helpers
# ---------------------------------------------------------------------------

def load_instance(filepath, k=None):
    if filepath.endswith('.qplib'):
        from tools.qplib_parser import parse_qplib_file
        return parse_qplib_file(filepath)
    elif filepath.endswith('.smt2'):
        return None
    elif filepath.endswith('.cnf'):
        if k is not None and k >= 2:
            from tools.diversesat_parser import parse_diversesat
            return parse_diversesat(filepath, k=k)
        from tools.cnf_parser import parse_cnf_file
        return parse_cnf_file(filepath)
    else:
        with open(filepath) as f:
            return json.load(f, parse_float=Fraction)


# ---------------------------------------------------------------------------
#  Z3 expression building
# ---------------------------------------------------------------------------

def to_z3_coeff(c):
    """Convert a Python number to Z3-compatible int or RatVal."""
    f = Fraction(str(c))
    return f.numerator if f.denominator == 1 else RatVal(f.numerator, f.denominator)


def build_expr(terms, z3_vars):
    expr = IntVal(0)
    for term in terms:
        monomial = to_z3_coeff(term.get('c', 1))
        for name, power in term.get('vars', {}).items():
            if int(power) != power or power < 0:
                raise ValueError(f'invalid exponent: {power}')
            for _ in range(int(power)):
                monomial = monomial * z3_vars[name]
        expr += monomial
    return expr

def eval_objective(model, obj_terms, z3_vars):
    from tools.verify import evaluate_polynomial
    values = {name: model.eval(var, model_completion=True).as_long() for name, var in z3_vars.items()}
    return evaluate_polynomial(obj_terms, values)

def create_z3_vars(problem):
    """Create Z3 Int variables with domain bounds."""
    z3_vars = {}
    bounds = []
    for vname, info in problem.get('variables', {}).items():
        x = Int(vname)
        z3_vars[vname] = x
        bounds.append(x >= to_z3_coeff(info['lb']))
        bounds.append(x <= to_z3_coeff(info['ub']))
    return z3_vars, bounds


def add_problem_constraints(ctx, problem, z3_vars):
    def expression(c):
        if c.get('type') == 'disjunction':
            return Or([And([expression(a) for a in b]) for b in c['disjuncts']])
        lhs = build_expr(c.get('terms', c.get('lhs', [])), z3_vars)
        rhs = to_z3_coeff(c.get('rhs', 0))
        rel = c.get('rel', c.get('sense', '<='))
        return {'<=': lhs <= rhs, '>=': lhs >= rhs, '==': lhs == rhs,
                '=': lhs == rhs, '<': lhs < rhs, '>': lhs > rhs}[rel]
    for c in problem.get('constraints', []):
        ctx.add(expression(c))

def _handle_is_finite(handle):
    return is_true(simplify(handle.lower() == handle.upper())) and 'oo' not in str(handle.value())


def _checked_model(problem, model, z3_vars):
    from tools.verify import evaluate_polynomial
    values = {name: model.eval(var, model_completion=True).as_long() for name, var in z3_vars.items()}
    objective = evaluate_polynomial(problem.get('objective', {}).get('terms', []), values)
    return objective, values



def _phase1_optimize(problem, z3_vars, bounds, obj_expr, obj_terms, is_max,
                     timeout):
    """Try Z3 Optimize; returns result dict or None if inconclusive."""
    opt = Optimize()
    opt.set("timeout", int(timeout * 1000))

    for b in bounds:
        opt.add(b)
    add_problem_constraints(opt, problem, z3_vars)

    handle = opt.maximize(obj_expr) if is_max else opt.minimize(obj_expr)
    result = opt.check()

    if result == sat and _handle_is_finite(handle):
        obj_val, values = _checked_model(problem, opt.model(), z3_vars)
        if not is_true(simplify(handle.value() == to_z3_coeff(obj_val))):
            raise ValueError('Z3 optimum bound does not match its model')
        # Nonlinear Optimize bounds require confirmation by the tightening phase.
        if all(sum(term.get('vars', {}).values()) <= 1 for term in obj_terms):
            return {'status': 'OPTIMAL', 'objective': obj_val, 'proven': True, 'witness': values}

    if result == unsat:
        return {'status': 'UNSAT', 'objective': None, 'proven': True}

    best_val = None
    best_z3_obj = None
    values = None
    if result == sat:
        m = opt.model()
        best_val, values = _checked_model(problem, m, z3_vars)
        best_z3_obj = m.eval(obj_expr, model_completion=True)

    return {'status': None, 'best_val': best_val, 'best_z3_obj': best_z3_obj, 'witness': values}


# ---------------------------------------------------------------------------
#  Phase 2 – Solver + iterative tightening
# ---------------------------------------------------------------------------

def _phase2_iterative(problem, z3_vars, bounds, obj_expr, obj_terms, is_max,
                      timeout_deadline, initial_val=None, initial_z3_obj=None, initial_witness=None):
    """Iteratively improve the objective with Z3 Solver.

    Returns (proven_optimal: bool, best_val, best_z3_obj).
    """
    solver = Solver()
    for b in bounds:
        solver.add(b)
    add_problem_constraints(solver, problem, z3_vars)

    witness = initial_witness
    infeasible = False
    best_val = initial_val
    best_z3_obj = initial_z3_obj
    proven = False

    while True:
        remaining_ms = int((timeout_deadline - time.perf_counter()) * 1000)
        if remaining_ms < 500:
            break

        solver.set("timeout", remaining_ms)
        solver.push()

        if best_z3_obj is not None:
            if is_max:
                solver.add(obj_expr > best_z3_obj)
            else:
                solver.add(obj_expr < best_z3_obj)

        r = solver.check()

        if r == sat:
            m = solver.model()
            new_val, values = _checked_model(problem, m, z3_vars)
            new_z3_obj = m.eval(obj_expr, model_completion=True)
            improved = (best_val is None
                        or (is_max and new_val > best_val)
                        or (not is_max and new_val < best_val))
            if improved:
                best_val = new_val
                best_z3_obj = new_z3_obj
                witness = values
            solver.pop()
        elif r == unsat:
            solver.pop()
            if best_val is not None:
                proven = True
            else:
                infeasible = True
            break
        else:
            solver.pop()
            break

    return proven, best_val, best_z3_obj, witness, infeasible


# ---------------------------------------------------------------------------
#  Main QPLIB solver entry
# ---------------------------------------------------------------------------

def solve_qplib(problem, timeout):
    result = _solve_qplib(problem, timeout)
    if result['status'] in ('OPTIMAL', 'FEASIBLE'):
        from tools.verify import verify_values
        start = time.perf_counter()
        try:
            actual, _, _ = verify_values(problem, result['witness'])
            if actual != result['objective']:
                raise ValueError('objective does not match original witness')
            result['verified'] = True
        except Exception as exc:
            result.update(status='INVALID', verified=False, error_msg=str(exc))
        result['time_verify'] = time.perf_counter() - start
    return result


def _solve_qplib(problem, timeout):
    """Solve a parsed QPLIB instance with Z3 (two-phase strategy)."""
    wall_start = time.perf_counter()
    deadline = wall_start + timeout

    obj_terms = problem.get('objective', {}).get('terms', [])
    sense = problem.get('objective', {}).get('sense', 'min')
    sense = str(sense).lower()
    if sense not in ('max', 'maximize', 'maximum', 'min', 'minimize', 'minimum'):
        raise ValueError(f'unsupported objective sense: {sense}')
    is_max = sense.startswith('max')

    z3_vars, bounds = create_z3_vars(problem)
    obj_expr = build_expr(obj_terms, z3_vars)
    if isinstance(obj_expr, int):
        obj_expr = IntVal(obj_expr)

    # ── Phase 1: Optimize() ──────────────────────────────────────
    # Cap Phase 1 time so Phase 2 always gets a fair share.
    phase1_budget = min(timeout * 0.4, 600)
    p1 = _phase1_optimize(problem, z3_vars, bounds, obj_expr, obj_terms,
                          is_max, phase1_budget)

    if p1.get('proven'):
        p1['time'] = time.perf_counter() - wall_start
        return p1

    best_val = p1.get('best_val')
    best_z3_obj = p1.get('best_z3_obj')

    # ── Phase 2: Solver + iterative tightening ───────────────────
    remaining = deadline - time.perf_counter()
    if remaining < 2:
        elapsed = time.perf_counter() - wall_start
        if best_val is not None:
            return {'status': 'FEASIBLE', 'objective': best_val, 'witness': p1.get('witness'),
                    'time': elapsed}
        return {'status': 'TIMEOUT' if elapsed >= timeout * 0.95 else 'UNKNOWN',
                'objective': None, 'time': elapsed}

    proven, best_val, _, witness, infeasible = _phase2_iterative(
        problem, z3_vars, bounds, obj_expr, obj_terms, is_max,
        deadline, best_val, best_z3_obj, p1.get('witness'))

    elapsed = time.perf_counter() - wall_start

    if infeasible:
        return {'status': 'UNSAT', 'objective': None, 'time': elapsed}
    if proven:
        return {'status': 'OPTIMAL', 'objective': best_val, 'time': elapsed, 'witness': witness}
    if best_val is not None:
        return {'status': 'FEASIBLE', 'objective': best_val, 'time': elapsed, 'witness': witness}
    if elapsed >= timeout * 0.95:
        return {'status': 'TIMEOUT', 'objective': None, 'time': elapsed}
    return {'status': 'UNKNOWN', 'objective': None, 'time': elapsed}


# ---------------------------------------------------------------------------
#  SMT2 solver (satisfiability only) — multi-strategy portfolio
# ---------------------------------------------------------------------------

def _try_solver(solver, filepath, timeout_ms):
    """Run a single Z3 solver attempt. Returns z3 result enum."""
    solver.set("timeout", int(timeout_ms))
    solver.from_file(filepath)
    return solver.check()


def solve_smt2(filepath, timeout):
    """Solve an SMT2 file with a multi-strategy portfolio.

    Strategy order (budget split across strategies):
      1. SolverFor('QF_LIA') — DPLL(T) with LIA theory; empirically solves
         many bounded QF_NIA instances that the default nlsat-based strategy
         cannot handle within reasonable time.
      2. Default Solver() — falls back to Z3's auto-configured strategy.
    """
    wall_start = time.perf_counter()
    deadline = wall_start + timeout

    strategies = [
        ("QF_LIA", lambda: SolverFor('QF_LIA')),
        ("default", Solver),
    ]
    n = len(strategies)

    try:
        for idx, (tag, make_solver) in enumerate(strategies):
            remaining = deadline - time.perf_counter()
            if remaining < 1:
                break

            is_last = (idx == n - 1)
            budget_ms = remaining * 1000 if is_last else (remaining / (n - idx)) * 1000

            solver = make_solver()
            r = _try_solver(solver, filepath, budget_ms)

            if r == sat:
                elapsed = time.perf_counter() - wall_start
                check_start = time.perf_counter()
                model = solver.model()
                if any(not is_true(simplify(model.eval(a, model_completion=True))) for a in solver.assertions()):
                    raise ValueError('original SMT witness verification failed')
                return {'status': 'SAT', 'objective': 'N/A', 'time': elapsed, 'verified': True,
                        'time_verify': time.perf_counter() - check_start}
            if r == unsat:
                elapsed = time.perf_counter() - wall_start
                return {'status': 'UNSAT', 'objective': 'N/A', 'time': elapsed}

        elapsed = time.perf_counter() - wall_start
        if elapsed >= timeout * 0.95:
            return {'status': 'TIMEOUT', 'objective': 'N/A', 'time': elapsed}
        return {'status': 'UNKNOWN', 'objective': 'N/A', 'time': elapsed}

    except Exception as e:
        return {'status': 'ERROR', 'objective': 'N/A',
                'time': time.perf_counter() - wall_start,
                'error_msg': f'{type(e).__name__}: {e}'}


# ---------------------------------------------------------------------------
#  CLI entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Z3 baseline solver")
    ap.add_argument("instance", help="Input file (QPLIB / SMT2 / CNF / JSON)")
    ap.add_argument("--timeout", type=int, default=7200)
    ap.add_argument("--k", type=int, default=None,
                    help="Diverse-SAT: number of diverse models (k >= 2). "
                         "Only for .cnf inputs.")
    args = ap.parse_args()

    name = Path(args.instance).name

    if not HAS_Z3:
        print(f">>> Benchmark {name} Solver Z3 "
              f"Status ERROR OPT N/A TimeTotal 0.000")
        print("Z3 Python bindings not installed", file=sys.stderr)
        return 1

    start_wall = time.perf_counter()
    try:
        if args.instance.endswith('.smt2'):
            result = solve_smt2(args.instance, args.timeout)
        else:
            problem = load_instance(args.instance, k=args.k)
            time_read = time.perf_counter() - start_wall
            result = solve_qplib(problem, max(0.001, args.timeout - time_read))
            result['time'] += time_read
    except Exception as e:
        elapsed = time.perf_counter() - start_wall
        msg = f'{type(e).__name__}: {e}'
        # Z3 may raise "canceled" near timeout in nonlinear optimization loops.
        # Treat this as TIMEOUT instead of ERROR to avoid inflating error counts.
        if "canceled" in str(e).lower():
            result = {'status': 'TIMEOUT', 'objective': None, 'time': elapsed,
                      'error_msg': msg}
        else:
            result = {'status': 'ERROR', 'objective': None, 'time': elapsed,
                      'error_msg': msg}
        traceback.print_exc(file=sys.stderr)

    obj = result.get('objective')
    if isinstance(obj, Fraction):
        print(f'ExactObjective {obj}')
        obj = int(obj) if obj.denominator == 1 else float(obj)
    obj_str = str(obj) if obj is not None else 'N/A'
    status = result['status']

    print(f">>> Benchmark {name} Solver Z3 "
          f"Status {status} OPT {obj_str} TimeTotal {result.get('time', 0.0):.3f} "
          f"TimeVerify {result.get('time_verify', 0.0):.3f}")

    if result.get('error_msg'):
        print(f"Error: {result['error_msg']}", file=sys.stderr)

    return 1 if status in ('ERROR', 'INVALID', 'UNSUPPORTED') else 0


if __name__ == "__main__":
    sys.exit(main())
