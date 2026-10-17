#!/usr/bin/env python3
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict, deque

try:
    import z3
except ImportError:
    raise ImportError("z3-solver required. Install: pip install z3-solver")


@dataclass
class ParseStats:
    total_assertions: int = 0
    bound_constraints: int = 0
    polynomial_constraints: int = 0
    equality_definitions: int = 0
    and_constraints: int = 0
    or_constraints: int = 0
    skipped_constraints: int = 0
    skipped_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    max_degree: int = 0

    def summary(self) -> str:
        lines = [
            f"Total assertions: {self.total_assertions}",
            f"Bound constraints: {self.bound_constraints}",
            f"Polynomial constraints: {self.polynomial_constraints}",
            f"Equality definitions (var=poly): {self.equality_definitions}",
            f"AND constraints (expanded): {self.and_constraints}",
            f"OR constraints: {self.or_constraints}",
            f"Skipped constraints: {self.skipped_constraints}",
            f"Max polynomial degree: {self.max_degree}",
        ]
        if self.skipped_reasons:
            lines.append("Skipped reasons:")
            for reason, count in sorted(self.skipped_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"  - {reason}: {count}")
        return "\n".join(lines)


def parse_smt2_file(filepath: str, objective_mode: str = "sum",
                    verbose: bool = False, default_ub: int = 1000,
                    strict: bool = True) -> Dict[str, Any]:
    if objective_mode not in ('sum', 'first', 'zero'):
        raise ValueError(f'unsupported SMT objective mode: {objective_mode}')
    path = Path(filepath)
    stats = ParseStats()

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # 1. Extract variable declarations via regex
    variables = {}
    int_var_names = []
    bool_var_names = []

    decl_pattern = r'\(declare-(?:fun|const)\s+(\|[^|]*\||[^\s)]+)(?:\s*\(\))?\s+(Int|Bool)\)'
    for match in re.finditer(decl_pattern, content):
        name, var_type = match.groups()
        clean_name = name.strip('|')
        if var_type == 'Int':
            int_var_names.append(clean_name)
            variables[clean_name] = {'lb': None, 'ub': None}
        elif var_type == 'Bool':
            bool_var_names.append(clean_name)
            variables[clean_name] = {'lb': 0, 'ub': 1}

    # 2. Use Z3 to extract bounds and constraints
    constraints = []
    assertions = []
    try:
        assertions = z3.parse_smt2_file(filepath)
        stats.total_assertions = len(assertions)

        _extract_bounds_z3(assertions, variables, int_var_names, stats)
        all_constraints = _extract_all_constraints_z3(assertions, int_var_names, stats,
                                                       variables=variables)
        constraints.extend(all_constraints)

    except TimeoutError:
        raise
    except Exception as e:
        if strict:
            raise ValueError(f"Z3 could not parse {path.name}: {e}") from e
        stats.skipped_constraints += 1
        stats.skipped_reasons["SMT parse failure"] += 1
        if verbose:
            print(f"Warning: Z3 parse failed: {e}")

    if strict and stats.skipped_constraints:
        reasons = ", ".join(
            f"{reason}={count}" for reason, count in sorted(stats.skipped_reasons.items())
        )
        raise ValueError(
            f"incomplete SMT translation for {path.name}: "
            f"{stats.skipped_constraints} unsupported constraint(s) ({reasons})"
        )

    # 3. Set default bounds for variables without inferred bounds
    defaulted_bounds = []
    for name in int_var_names:
        if variables[name]['lb'] is None:
            defaulted_bounds.append(f"{name}:lb")
            variables[name]['lb'] = 0
        if variables[name]['ub'] is None:
            defaulted_bounds.append(f"{name}:ub")
            variables[name]['ub'] = default_ub

    if strict and defaulted_bounds:
        preview = ", ".join(defaulted_bounds[:8])
        if len(defaulted_bounds) > 8:
            preview += f", ... (+{len(defaulted_bounds) - 8})"
        raise ValueError(
            f"SMT variables require artificial bounds in {path.name}: {preview}"
        )

    # 4. Build objective function
    objective = _build_objective(int_var_names, objective_mode)

    # 5. Update max degree from objective terms
    for term in objective['terms']:
        degree = sum(term['vars'].values()) if term['vars'] else 0
        stats.max_degree = max(stats.max_degree, degree)

    # 6. Normalize variable names (SMT2 names may contain special chars)
    name_map = {}
    normalized_vars = {}
    for i, name in enumerate(sorted(variables.keys())):
        safe_name = f"x{i+1}"
        name_map[name] = safe_name
        normalized_vars[safe_name] = variables[name]

    def _normalize_atom(atom):
        new_terms = []
        for term in atom['terms']:
            new_vars = {name_map.get(v, v): exp for v, exp in term['vars'].items()}
            new_terms.append({'c': term['c'], 'vars': new_vars})
        return {'terms': new_terms, 'rel': atom['rel'], 'rhs': atom['rhs']}

    normalized_constraints = []
    for c in constraints:
        if c.get('type') == 'disjunction':
            normalized_constraints.append({
                'type': 'disjunction',
                'disjuncts': [
                    [_normalize_atom(atom) for atom in branch]
                    for branch in c.get('disjuncts', [])
                ]
            })
        else:
            normalized_constraints.append(_normalize_atom(c))

    normalized_obj_terms = []
    for term in objective['terms']:
        new_vars = {name_map.get(v, v): exp for v, exp in term['vars'].items()}
        normalized_obj_terms.append({'c': term['c'], 'vars': new_vars})

    problem = {
        'name': path.stem,
        'type': 'SMT2_NIA',
        'source': str(path),
        'variables': normalized_vars,
        'objective': {
            'sense': objective['sense'],
            'terms': normalized_obj_terms
        },
        'constraints': normalized_constraints,
        '_name_map': name_map,
        '_parse_stats': {
            'total_assertions': stats.total_assertions,
            'polynomial_constraints': stats.polynomial_constraints,
            'equality_definitions': stats.equality_definitions,
            'max_degree': stats.max_degree,
            'skipped': stats.skipped_constraints,
            'skipped_reasons': dict(stats.skipped_reasons),
            'defaulted_bounds': defaulted_bounds,
            'strict': strict
        }
    }

    if verbose:
        print(f"\n=== Parse Stats for {path.name} ===")
        print(stats.summary())

    problem["_smt_assertions"] = assertions
    return problem


def _extract_bounds_z3(assertions, variables: Dict, int_var_names: List[str], stats: ParseStats):
    queue = deque(assertions)

    while queue:
        expr = queue.popleft()

        if z3.is_and(expr):
            queue.extend(expr.children())
            continue

        if not z3.is_app(expr) or len(expr.children()) != 2:
            continue

        kind = expr.decl().kind()
        lhs, rhs = (z3.simplify(a) for a in expr.children())

        bound_extracted = False

        # Form: var rel const
        if _is_int_var(lhs) and z3.is_int_value(rhs):
            var_name = _get_var_name(lhs)
            if var_name in variables:
                _update_bound(variables, var_name, kind, rhs.as_long(), is_rhs_const=True)
                bound_extracted = True

        # Form: const rel var
        elif z3.is_int_value(lhs) and _is_int_var(rhs):
            var_name = _get_var_name(rhs)
            if var_name in variables:
                _update_bound(variables, var_name, kind, lhs.as_long(), is_rhs_const=False)
                bound_extracted = True

        if bound_extracted:
            stats.bound_constraints += 1


def _get_var_name(expr) -> str:
    name = expr.decl().name()
    return name.strip('|')


def _is_int_var(expr) -> bool:
    return (z3.is_const(expr) and
            expr.decl().kind() == z3.Z3_OP_UNINTERPRETED and
            expr.sort().kind() == z3.Z3_INT_SORT)


def _update_bound(variables: Dict, var_name: str, kind: int, val: int, is_rhs_const: bool):
    lb = variables[var_name]['lb']
    ub = variables[var_name]['ub']

    if kind == z3.Z3_OP_EQ:
        lb = max(lb, val) if lb is not None else val
        ub = min(ub, val) if ub is not None else val
    elif kind == z3.Z3_OP_LE:
        if is_rhs_const:
            ub = min(ub, val) if ub is not None else val
        else:
            lb = max(lb, val) if lb is not None else val
    elif kind == z3.Z3_OP_GE:
        if is_rhs_const:
            lb = max(lb, val) if lb is not None else val
        else:
            ub = min(ub, val) if ub is not None else val
    elif kind == z3.Z3_OP_LT:
        if is_rhs_const:
            ub = min(ub, val - 1) if ub is not None else val - 1
        else:
            lb = max(lb, val + 1) if lb is not None else val + 1
    elif kind == z3.Z3_OP_GT:
        if is_rhs_const:
            lb = max(lb, val + 1) if lb is not None else val + 1
        else:
            ub = min(ub, val - 1) if ub is not None else val - 1

    variables[var_name]['lb'] = lb
    variables[var_name]['ub'] = ub


def _extract_all_constraints_z3(assertions, int_var_names: List[str], stats: ParseStats,
                                 variables: Dict = None) -> List[Dict]:
    """Translate the top-level conjunction into polynomial atoms and exact DNF.

    Boolean structure is preserved.  In particular, no unselected branch is
    allowed to constrain the integer variables, which was the semantic defect
    in the selector-based implementation being replaced here.
    """
    constraints = []
    queue = deque(assertions)

    while queue:
        expr = queue.popleft()
        if z3.is_and(expr):
            queue.extend(expr.children())
            stats.and_constraints += 1
            continue

        if z3.is_or(expr):
            stats.or_constraints += 1

        if z3.is_bool(expr):
            dnf = _boolean_to_dnf(expr, int_var_names, stats)
            if dnf is None:
                continue
            if not dnf:
                constraints.append({'terms': [], 'rel': '==', 'rhs': 1})
            elif len(dnf) == 1:
                constraints.extend(dnf[0])
            else:
                constraints.append({'type': 'disjunction', 'disjuncts': dnf})
            _record_dnf_degree(dnf, stats)
            continue

        parsed = _parse_polynomial_constraint_general(expr, int_var_names, stats)
        if parsed is not None:
            constraints.append(parsed)
            _record_atom_degree(parsed, stats)

    return constraints


_MAX_DNF_BRANCHES = 4096


def _record_atom_degree(atom: Dict, stats: ParseStats) -> None:
    for term in atom.get('terms', []):
        degree = sum(term['vars'].values()) if term.get('vars') else 0
        stats.max_degree = max(stats.max_degree, degree)


def _record_dnf_degree(dnf: List[List[Dict]], stats: ParseStats) -> None:
    for branch in dnf:
        for atom in branch:
            _record_atom_degree(atom, stats)


def _negate_constraint(constraint: Dict) -> List[List[Dict]]:
    """Return exact integer DNF for the negation of one polynomial atom."""
    terms = constraint['terms']
    rel = constraint['rel']
    rhs = constraint['rhs']

    if rel == '<=':
        return [[{'terms': terms, 'rel': '>=', 'rhs': rhs + 1}]]
    if rel == '<':
        return [[{'terms': terms, 'rel': '>=', 'rhs': rhs}]]
    if rel == '>=':
        return [[{'terms': terms, 'rel': '<=', 'rhs': rhs - 1}]]
    if rel == '>':
        return [[{'terms': terms, 'rel': '<=', 'rhs': rhs}]]
    if rel in ('=', '=='):
        return [
            [{'terms': terms, 'rel': '<=', 'rhs': rhs - 1}],
            [{'terms': terms, 'rel': '>=', 'rhs': rhs + 1}],
        ]
    raise ValueError(f"cannot negate relation {rel}")


def _dnf_and(left: List[List[Dict]], right: List[List[Dict]],
             stats: ParseStats) -> Optional[List[List[Dict]]]:
    if not left or not right:
        return []
    if len(left) * len(right) > _MAX_DNF_BRANCHES:
        stats.skipped_reasons["Boolean DNF exceeds branch limit"] += 1
        stats.skipped_constraints += 1
        return None
    return [a + b for a in left for b in right]


def _boolean_to_dnf(expr, int_var_names: List[str], stats: ParseStats,
                    negated: bool = False) -> Optional[List[List[Dict]]]:
    """Convert supported Boolean structure to exact DNF over polynomial atoms."""
    if z3.is_true(expr):
        return [] if negated else [[]]
    if z3.is_false(expr):
        return [[]] if negated else []
    if z3.is_const(expr) and expr.decl().kind() == z3.Z3_OP_UNINTERPRETED and z3.is_bool(expr):
        return [[{'terms': [{'c': 1, 'vars': {_get_var_name(expr): 1}}],
                  'rel': '==', 'rhs': 0 if negated else 1}]]
    if z3.is_eq(expr) and all(z3.is_bool(child) for child in expr.children()):
        left, right = expr.children()
        equivalent = z3.Or(z3.And(left, right), z3.And(z3.Not(left), z3.Not(right)))
        return _boolean_to_dnf(equivalent, int_var_names, stats, negated)
    if z3.is_not(expr):
        return _boolean_to_dnf(expr.children()[0], int_var_names, stats, not negated)

    if z3.is_implies(expr):
        lhs, rhs = expr.children()
        if negated:
            left = _boolean_to_dnf(lhs, int_var_names, stats, False)
            right = _boolean_to_dnf(rhs, int_var_names, stats, True)
            if left is None or right is None:
                return None
            return _dnf_and(left, right, stats)
        left = _boolean_to_dnf(lhs, int_var_names, stats, True)
        right = _boolean_to_dnf(rhs, int_var_names, stats, False)
        if left is None or right is None:
            return None
        result = left + right
        if len(result) > _MAX_DNF_BRANCHES:
            stats.skipped_reasons["Boolean DNF exceeds branch limit"] += 1
            stats.skipped_constraints += 1
            return None
        return result

    if z3.is_and(expr) or z3.is_or(expr):
        is_and = z3.is_and(expr)
        effective_and = is_and != negated
        children = list(expr.children())
        if effective_and:
            result: List[List[Dict]] = [[]]
            for child in children:
                child_dnf = _boolean_to_dnf(child, int_var_names, stats, negated)
                if child_dnf is None:
                    return None
                result = _dnf_and(result, child_dnf, stats)
                if result is None:
                    return None
            return result

        result = []
        for child in children:
            child_dnf = _boolean_to_dnf(child, int_var_names, stats, negated)
            if child_dnf is None:
                return None
            result.extend(child_dnf)
            if len(result) > _MAX_DNF_BRANCHES:
                stats.skipped_reasons["Boolean DNF exceeds branch limit"] += 1
                stats.skipped_constraints += 1
                return None
        return result

    atom = _parse_polynomial_constraint_general(expr, int_var_names, stats)
    if atom is None:
        return None
    return _negate_constraint(atom) if negated else [[atom]]


def _parse_polynomial_constraint_general(expr, int_var_names: List[str], stats: ParseStats) -> Optional[Dict]:
    if not z3.is_app(expr):
        stats.skipped_constraints += 1
        stats.skipped_reasons["unsupported non-atomic assertion"] += 1
        return None

    kind = expr.decl().kind()

    if kind == z3.Z3_OP_NOT:
        stats.skipped_reasons["NOT constraint (atomic)"] += 1
        stats.skipped_constraints += 1
        return None

    if kind == z3.Z3_OP_IMPLIES:
        stats.skipped_reasons["IMPLIES constraint"] += 1
        stats.skipped_constraints += 1
        return None

    if kind not in [z3.Z3_OP_LE, z3.Z3_OP_GE, z3.Z3_OP_LT, z3.Z3_OP_GT, z3.Z3_OP_EQ]:
        stats.skipped_reasons[f"Unknown op kind {kind}"] += 1
        stats.skipped_constraints += 1
        return None

    if len(expr.children()) != 2:
        stats.skipped_reasons["Non-binary constraint"] += 1
        stats.skipped_constraints += 1
        return None

    lhs, rhs = expr.children()

    polynomial_cache = {}
    lhs_terms = _parse_polynomial_expr(lhs, int_var_names, polynomial_cache)
    rhs_terms = _parse_polynomial_expr(rhs, int_var_names, polynomial_cache)

    if lhs_terms is None:
        stats.skipped_reasons["LHS parse failed"] += 1
        stats.skipped_constraints += 1
        return None

    if rhs_terms is None:
        stats.skipped_reasons["RHS parse failed"] += 1
        stats.skipped_constraints += 1
        return None

    # Merge lhs - rhs by monomial key. A linear search for every RHS term
    # becomes quadratic on the bundled Calypto expanded polynomial.
    combined = _merge_like_terms(lhs_terms + [
        {'c': -term['c'], 'vars': term['vars']} for term in rhs_terms])
    rhs_constant = -sum(term['c'] for term in combined if not term['vars'])
    combined_terms = [term for term in combined if term['vars'] and term['c']]

    rel_map = {
        z3.Z3_OP_LE: '<=',
        z3.Z3_OP_GE: '>=',
        z3.Z3_OP_LT: '<',
        z3.Z3_OP_GT: '>',
        z3.Z3_OP_EQ: '=='
    }
    rel = rel_map.get(kind, '<=')

    if kind == z3.Z3_OP_EQ and _is_int_var(lhs):
        stats.equality_definitions += 1

    stats.polynomial_constraints += 1

    return {
        'terms': combined_terms,
        'rel': rel,
        'rhs': rhs_constant
    }


def _parse_polynomial_expr(expr, int_var_names: List[str], cache=None) -> Optional[List[Dict]]:
    # Z3 expressions are DAGs: SMT let bindings can reuse a subtree many times.
    # Cache immutable polynomials per atom; callers receive fresh mutable terms.
    if cache is None:
        cache = {}
    key = expr.get_id()
    if key not in cache:
        result = _parse_polynomial_expr_uncached(expr, int_var_names, cache)
        cache[key] = None if result is None else tuple(
            (term['c'], tuple(term['vars'].items())) for term in result)
    stored = cache[key]
    return None if stored is None else [{'c': c, 'vars': dict(powers)} for c, powers in stored]


def _parse_polynomial_expr_uncached(expr, int_var_names: List[str], cache) -> Optional[List[Dict]]:
    # Integer constant
    if z3.is_int_value(expr):
        val = expr.as_long()
        return [{'c': val, 'vars': {}}]

    # Integer variable
    if _is_int_var(expr):
        name = _get_var_name(expr)
        return [{'c': 1, 'vars': {name: 1}}]

    if not z3.is_app(expr):
        return None

    kind = expr.decl().kind()
    children = expr.children()

    # Addition
    if kind == z3.Z3_OP_ADD:
        result = []
        for child in children:
            child_terms = _parse_polynomial_expr(child, int_var_names, cache)
            if child_terms is None:
                return None
            result.extend(child_terms)
        return _merge_like_terms(result)

    # Subtraction
    if kind == z3.Z3_OP_SUB:
        if len(children) == 1:
            terms = _parse_polynomial_expr(children[0], int_var_names, cache)
            if terms is None:
                return None
            for t in terms:
                t['c'] = -t['c']
            return terms
        elif len(children) == 2:
            left_terms = _parse_polynomial_expr(children[0], int_var_names, cache)
            right_terms = _parse_polynomial_expr(children[1], int_var_names, cache)
            if left_terms is None or right_terms is None:
                return None
            for t in right_terms:
                t['c'] = -t['c']
            return _merge_like_terms(left_terms + right_terms)
        return None

    # Unary negation
    if kind == z3.Z3_OP_UMINUS:
        if len(children) != 1:
            return None
        terms = _parse_polynomial_expr(children[0], int_var_names, cache)
        if terms is None:
            return None
        for t in terms:
            t['c'] = -t['c']
        return terms

    # Multiplication
    if kind == z3.Z3_OP_MUL:
        return _parse_multiplication(children, int_var_names, cache)

    # ITE (if-then-else) - not supported
    if kind == z3.Z3_OP_ITE:
        return None

    return None


def _parse_multiplication(children, int_var_names: List[str], cache=None) -> Optional[List[Dict]]:
    coeff = 1
    combined_vars = {}
    need_distribution = False

    for child in children:
        if z3.is_int_value(child):
            coeff *= child.as_long()
        elif _is_int_var(child):
            name = _get_var_name(child)
            combined_vars[name] = combined_vars.get(name, 0) + 1
        else:
            need_distribution = True
            break

    if not need_distribution:
        return [{'c': coeff, 'vars': combined_vars}]

    # Distributive expansion: (a+b)*(c+d) = a*c + a*d + b*c + b*d
    result_terms = [{'c': 1, 'vars': {}}]

    for child in children:
        child_terms = _parse_polynomial_expr(child, int_var_names, cache)
        if child_terms is None:
            return None

        new_result = []
        for rt in result_terms:
            for ct in child_terms:
                new_term = {
                    'c': rt['c'] * ct['c'],
                    'vars': rt['vars'].copy()
                }
                for v, e in ct['vars'].items():
                    new_term['vars'][v] = new_term['vars'].get(v, 0) + e
                new_result.append(new_term)
        result_terms = _merge_like_terms(new_result)

    return _merge_like_terms(result_terms)


def _merge_like_terms(terms: List[Dict]) -> List[Dict]:
    merged = {}

    for term in terms:
        key = tuple(sorted(term['vars'].items()))
        if key in merged:
            merged[key]['c'] += term['c']
        else:
            merged[key] = {'c': term['c'], 'vars': dict(term['vars'])}

    result = [t for t in merged.values() if t['c'] != 0]
    return result if result else [{'c': 0, 'vars': {}}]


def _build_objective(int_var_names: List[str], mode: str) -> Dict:
    if mode == "zero":
        return {'sense': 'min', 'terms': []}

    if mode == "first" and int_var_names:
        return {
            'sense': 'max',
            'terms': [{'c': 1, 'vars': {int_var_names[0]: 1}}]
        }

    # Default: maximize sum of all variables
    terms = [{'c': 1, 'vars': {name: 1}} for name in int_var_names]
    return {'sense': 'max', 'terms': terms}
