#!/usr/bin/env python3

from typing import Dict, Any, List, Tuple
from pysat.formula import WCNF, IDPool
from pysat.pb import PBEnc

from .onehot import encode_polynomial_oh, _check_deadline
from .unary import encode_polynomial_una
from .binary import encode_polynomial_bin


def _bit_count_compat(x: int) -> int:
    if hasattr(int, "bit_count"):
        return int(x).bit_count()
    return bin(int(x)).count("1")


def _linearize_binary_quadratic_constraint(
    terms: List[Dict[str, Any]],
    variables: Dict[str, Dict[str, Any]],
    name2idx: Dict[str, int],
    vpool: IDPool,
    constr_idx: int,
    encoding: str = 'BIN',
    global_product_cache: Dict[Tuple[int, int], int] = None,
    deadline: float = 0.0
) -> Tuple[bool, List[int], List[int], List[List[int]]]:
    if not terms:
        return False, [], [], []

    all_binary = True
    for t in terms:
        _check_deadline(deadline)
        vs = t.get('vars', {}) or {}
        for vn, p in vs.items():
            p = int(p)
            if p == 0:
                continue
            info = variables.get(vn)
            if info is None:
                all_binary = False
                break
            lb = int(info.get('lb', 0))
            ub = int(info.get('ub', 0))
            if lb != 0 or ub != 1:
                all_binary = False
                break
        if not all_binary:
            break

    if not all_binary:
        return False, [], [], []

    # Decide whether the entire constraint supports this path before publishing
    # any product in the shared cache: a later fallback discards local clauses.
    for t in terms:
        _check_deadline(deadline)
        if sum(p > 0 for p in t.get('vars', {}).values()) > 2:
            return False, [], [], []

    # BIN encoding: x_q@0 (bit 0) = variable value for binary {0,1}
    # OH encoding:  x_q@1 (indicator for value 1) = variable value
    # UNA encoding: x_q@1 (indicator for value >= 1) = variable value
    bit_idx = 0 if encoding == 'BIN' else 1
    x_fn = lambda q: vpool.id(f'x_{q}@{bit_idx}')

    pb_lits = []
    pb_weights = []
    hard_clauses = []
    product_cache = global_product_cache if global_product_cache is not None else {}

    for t in terms:
        _check_deadline(deadline)
        c_val = int(t.get('c', 0))
        if c_val == 0:
            continue
        vs = t.get('vars', {}) or {}
        active_vars = [(vn, int(p)) for vn, p in vs.items() if int(p) > 0]

        if len(active_vars) == 0:
            continue
        elif len(active_vars) == 1:
            vn, p = active_vars[0]
            pb_lits.append(x_fn(name2idx[vn]))
            pb_weights.append(c_val)
        elif len(active_vars) == 2:
            (vn1, p1), (vn2, p2) = active_vars
            x1 = x_fn(name2idx[vn1])
            x2 = x_fn(name2idx[vn2])
            cache_key = (min(x1, x2), max(x1, x2))

            if cache_key in product_cache:
                z = product_cache[cache_key]
            else:
                # Use a stable name based on input literals so we can safely share
                # (x_i AND x_j) across constraints when global_product_cache is provided.
                z = vpool.id(f'z_lin_{cache_key[0]}_{cache_key[1]}')
                hard_clauses.append([-z, x1])
                hard_clauses.append([-z, x2])
                hard_clauses.append([z, -x1, -x2])
                product_cache[cache_key] = z

            pb_lits.append(z)
            pb_weights.append(c_val)
        else:
            return False, [], [], []

    return True, pb_lits, pb_weights, hard_clauses


def _encode_polynomial_bin_mixed_decomp(
    terms: List[Dict[str, Any]],
    problem: Dict[str, Any],
    name2idx: Dict[str, int],
    vpool: IDPool,
    z_prefix: str,
    lb_map: Dict[str, int] = None,
    and_cache: Dict = None,
    decomp_threshold: int = 3,
    decomp_strategy: str = "sequential",
    decomp_cache=None,
    deadline: float = 0.0
) -> Tuple[Dict, List[List[int]]]:
    import math
    from .decomposition import encode_term_decomp
    from .binary import encode_term_bin

    all_z_mapping = {}
    all_hard = []

    for t_idx, term in enumerate(terms):
        tv = term.get('vars', {}) or {}
        degree = sum(int(p) for p in tv.values())

        use_decomp_for_term = degree >= decomp_threshold
        if not use_decomp_for_term and degree >= 2 and len(tv) >= 2:
            bit_product = 1
            for vn, p in tv.items():
                ub = int(problem['variables'][vn]['ub'])
                nb = max(1, ub.bit_length())
                bit_product *= nb ** int(p)
            if bit_product > 256:
                use_decomp_for_term = True

        if use_decomp_for_term:
            z_map, hard = encode_term_decomp(
                term, problem, name2idx, vpool,
                f"{z_prefix}_decomp_t{t_idx}", lb_map,
                sense=0, strategy=decomp_strategy, cache=decomp_cache, deadline=deadline
            )
        else:
            z_map, hard, _ = encode_term_bin(
                term, problem, name2idx, vpool,
                f"{z_prefix}_t{t_idx}", lb_map,
                for_objective=False, and_cache=and_cache, deadline=deadline
            )

        for key, val in z_map.items():
            all_z_mapping[(t_idx, key)] = val
        all_hard.extend(hard)

    return all_z_mapping, all_hard


def encode_constraint(
    constraint: Dict[str, Any],
    problem: Dict[str, Any],
    name2idx: Dict[str, int],
    vpool: IDPool,
    encoding: str,
    constr_idx: int,
    lb_map: Dict[str, int] = None,
    and_cache: Dict = None,
    deadline: float = 0.0,
    use_decomp: bool = False,
    decomp_threshold: int = 3,
    decomp_strategy: str = "sequential",
    decomp_cache=None
) -> List[List[int]]:
    _check_deadline(deadline)
    # Support both 'terms'/'lhs' and 'rel'/'sense' field names
    terms = constraint.get('terms', constraint.get('lhs', []))
    rel = constraint.get('rel', constraint.get('sense', '<='))
    rhs = int(constraint.get('rhs', 0))

    if not terms:
        # A constant constraint must not disappear.  This is especially
        # important for a false branch produced while lowering SMT Boolean
        # structure: an empty clause disables that branch (or the formula).
        if rel == '<=':
            holds = 0 <= rhs
        elif rel == '<':
            holds = 0 < rhs
        elif rel == '>=':
            holds = 0 >= rhs
        elif rel == '>':
            holds = 0 > rhs
        elif rel in ('=', '=='):
            holds = 0 == rhs
        else:
            raise ValueError(f"Unknown relation: {rel}")
        return [] if holds else [[]]

    # Try binary quadratic linearization first (much more efficient for QBQ)
    variables = problem.get('variables', {}) or {}
    lin_prod_cache = None
    if isinstance(and_cache, dict):
        lin_prod_cache = and_cache.setdefault('_lin_prod_cache', {})
    success, lin_lits, lin_weights, lin_hard = _linearize_binary_quadratic_constraint(
        terms, variables, name2idx, vpool, constr_idx, encoding=encoding,
        global_product_cache=lin_prod_cache, deadline=deadline
    )
    if success and lin_lits:
        hard_clauses = lin_hard
        pb_lits = lin_lits
        pb_weights = lin_weights
        return _encode_pb_constraint(pb_lits, pb_weights, rhs, rel, vpool, hard_clauses,
                                     force_adder=False, deadline=deadline)

    z_prefix = f"z_{encoding.lower()}_constr{constr_idx}"

    if encoding == 'OH':
        z_mapping, hard_clauses, _ = encode_polynomial_oh(
            terms, problem, name2idx, vpool, z_prefix, lb_map,
            deadline=deadline
        )
    elif encoding == 'UNA':
        z_mapping, hard_clauses, _ = encode_polynomial_una(
            terms, problem, name2idx, vpool, z_prefix, lb_map,
            deadline=deadline
        )
    elif encoding == 'BIN' and use_decomp:
        z_mapping, hard_clauses = _encode_polynomial_bin_mixed_decomp(
            terms, problem, name2idx, vpool, z_prefix, lb_map,
            and_cache=and_cache,
            decomp_threshold=decomp_threshold,
            decomp_strategy=decomp_strategy,
            decomp_cache=decomp_cache, deadline=deadline
        )
    elif encoding == 'BIN':
        z_mapping, hard_clauses, _ = encode_polynomial_bin(
            terms, problem, name2idx, vpool, z_prefix, lb_map,
            external_and_cache=and_cache, deadline=deadline
        )
    else:
        raise ValueError(f"Unknown encoding: {encoding}")


    pb_lits = []
    pb_weights = []

    for _, (z_var, value) in z_mapping.items():
        _check_deadline(deadline)
        val = int(value)
        if val != 0:
            pb_lits.append(z_var)
            pb_weights.append(val)


    return _encode_pb_constraint(pb_lits, pb_weights, rhs, rel, vpool, hard_clauses,
                                 deadline=deadline)


def _encode_pb_constraint(
    pb_lits: List[int],
    pb_weights: List[int],
    rhs: int,
    rel: str,
    vpool: IDPool,
    hard_clauses: List[List[int]],
    force_adder: bool = False,
    deadline: float = 0.0
) -> List[List[int]]:
    """Encode a pseudo-Boolean constraint into SAT clauses."""
    _check_deadline(deadline)
    from math import gcd
    from functools import reduce

    if rel == '<=':
        bound = rhs
    elif rel == '<':
        bound = rhs - 1
    elif rel == '>=':
        bound = rhs
    elif rel == '>':
        bound = rhs + 1
    elif rel in ('=', '=='):
        bound = rhs
    else:
        raise ValueError(f"Unknown relation: {rel}")

    if bound < 0:
        all_nonneg = all(w >= 0 for w in pb_weights)
        if rel in ('<=', '<'):
            if all_nonneg:
                hard_clauses.append([])
                return hard_clauses
        elif rel in ('>=', '>'):
            if all_nonneg:
                return hard_clauses
        elif rel in ('=', '=='):
            if all_nonneg:
                hard_clauses.append([])
                return hard_clauses

    min_sum = sum(w for w in pb_weights if w < 0)
    max_sum = sum(w for w in pb_weights if w > 0)

    if rel in ('<=', '<'):
        if bound >= max_sum:
            return hard_clauses
        if bound < min_sum:
            hard_clauses.append([])
            return hard_clauses
    elif rel in ('>=', '>'):
        if bound <= min_sum:
            return hard_clauses
        if bound > max_sum:
            hard_clauses.append([])
            return hard_clauses
    elif rel in ('=', '=='):
        if bound < min_sum or bound > max_sum:
            hard_clauses.append([])
            return hard_clauses

    norm_lits = list(pb_lits)
    norm_weights = list(pb_weights)
    norm_bound = bound
    has_negative = any(w < 0 for w in norm_weights)
    if has_negative:
        for i in range(len(norm_weights)):
            if norm_weights[i] < 0:
                norm_bound -= norm_weights[i]
                norm_weights[i] = -norm_weights[i]
                norm_lits[i] = -norm_lits[i]

    if norm_bound < 0:
        if rel in ('<=', '<'):
            hard_clauses.append([])
            return hard_clauses
        elif rel in ('>=', '>'):
            return hard_clauses
        elif rel in ('=', '=='):
            hard_clauses.append([])
            return hard_clauses

    # GCD normalization to reduce PB encoding complexity
    all_vals = [abs(w) for w in norm_weights if w != 0]
    if all_vals and abs(norm_bound) > 0:
        all_vals.append(abs(norm_bound))
    if all_vals:
        g = reduce(gcd, all_vals)
        if g > 1:
            norm_weights = [w // g for w in norm_weights]
            if rel in ('<=', '<'):
                norm_bound = norm_bound // g
            elif rel in ('>=', '>'):
                import math
                norm_bound = -(-norm_bound // g)
            elif rel in ('=', '=='):
                if norm_bound % g != 0:
                    hard_clauses.append([])
                    return hard_clauses
                norm_bound = norm_bound // g

    # Use adder-based encoding for large PB constraints to avoid BDD blowup.
    # PBEnc runs in C and cannot be interrupted by Python signal handlers,
    # so we proactively use the adder for any large PB constraint or when
    # approaching the encoding deadline.
    import time as _time
    max_sum = sum(norm_weights)
    if max_sum < 0:
        max_sum = 0
    # Proxy for PBEnc (BDD) complexity: depends on (n, bound) but also worsens
    # when weights make the reachable sum space wide. A robust cheap proxy is
    # n * min(bound, max_sum-bound), plus a separate guard for huge weight bit-width.
    slack = norm_bound
    if max_sum >= 0:
        slack = min(norm_bound, max_sum - norm_bound) if max_sum >= norm_bound else norm_bound
    bdd_proxy = len(norm_lits) * max(0, slack)
    # Proxy for adder size: roughly proportional to (sum popcount(weights) + n) * num_bits.
    num_bits = (max_sum.bit_length() if max_sum > 0 else 1)
    popcount_sum = 0
    for w in norm_weights:
        if w > 0:
            popcount_sum += _bit_count_compat(int(w))
    adder_proxy = (popcount_sum + len(norm_lits)) * num_bits
    use_adder = False
    if any(w > 2**63 - 1 for w in norm_weights) or max_sum > 2**63 - 1:
        use_adder = True
    elif force_adder:
        use_adder = True
    elif deadline > 0 and deadline - _time.time() < 1.0:
        use_adder = True
    elif len(norm_lits) > 10:
        # Prefer adder when PBEnc is likely to blow up, or when weights are huge
        # (BDD tends to get unstable and is non-interruptible in C).
        if bdd_proxy > 500_000:
            use_adder = True
        elif num_bits > 32 and adder_proxy > 200_000:
            use_adder = True
    if use_adder:
        adder_clauses = _encode_pb_adder(norm_lits, norm_weights, norm_bound, rel, vpool, deadline=deadline)
        if adder_clauses is not None:
            hard_clauses.extend(adder_clauses)
            return hard_clauses

    if rel in ('<=', '<'):
        cnf = PBEnc.leq(lits=norm_lits, weights=norm_weights, bound=norm_bound, vpool=vpool)
    elif rel in ('>=', '>'):
        cnf = PBEnc.geq(lits=norm_lits, weights=norm_weights, bound=norm_bound, vpool=vpool)
    elif rel in ('=', '=='):
        cnf = PBEnc.equals(lits=norm_lits, weights=norm_weights, bound=norm_bound, vpool=vpool)

    hard_clauses.extend(cnf.clauses)

    return hard_clauses


def _encode_pb_adder(
    lits: List[int],
    weights: List[int],
    bound: int,
    rel: str,
    vpool: IDPool,
    deadline: float = 0.0
) -> List[List[int]]:

    _check_deadline(deadline)
    if rel in ('<=', '<'):
        return _encode_pb_adder_leq(lits, weights, bound, vpool, deadline=deadline)
    elif rel in ('>=', '>'):
        flipped_lits = [-l for l in lits]
        new_bound = sum(weights) - bound
        if new_bound < 0:
            return [[]]
        return _encode_pb_adder_leq(flipped_lits, weights, new_bound, vpool, deadline=deadline)
    elif rel in ('=', '=='):
        # Share one exact sum and fix its output bits. Two independent
        # inequality circuits hide low-bit equalities from unit propagation.
        return _encode_pb_adder_leq(lits, weights, bound, vpool, exact=True, deadline=deadline)
    return None


def _encode_pb_adder_leq(
    lits: List[int],
    weights: List[int],
    bound: int,
    vpool: IDPool,
    exact: bool = False,
    deadline: float = 0.0
) -> List[List[int]]:
    """Encode a weighted sum <= bound, or = bound when exact is true."""
    _check_deadline(deadline)
    import math

    n = len(lits)
    if bound < 0:
        return [[]]
    if n == 0:
        return [] if not exact or bound == 0 else [[]]

    max_sum = sum(weights)
    if exact and bound > max_sum:
        return [[]]
    num_bits = max(1, max_sum.bit_length())
    clauses = []

    def make_bit_repr(lit, weight):
        bits = []
        for b in range(num_bits):
            if (weight >> b) & 1:
                bits.append(lit)
            else:
                bits.append(None)
        return bits

    def full_adder(a, b, cin, vpool):
        cls = []
        inputs = [x for x in [a, b, cin] if x is not None]

        if len(inputs) == 0:
            return None, None, []
        if len(inputs) == 1:
            return inputs[0], None, []
        if len(inputs) == 2:
            s = vpool.id(f'_fa_s_{vpool.top}')
            c = vpool.id(f'_fa_c_{vpool.top}')
            x, y = inputs
            # s = x XOR y
            cls.append([-x, -y, -s])
            cls.append([x, y, -s])
            cls.append([-x, y, s])
            cls.append([x, -y, s])
            # c = x AND y
            cls.append([-c, x])
            cls.append([-c, y])
            cls.append([c, -x, -y])
            return s, c, cls

        x, y, z = a, b, cin
        s = vpool.id(f'_fa_s_{vpool.top}')
        c = vpool.id(f'_fa_c_{vpool.top}')
        # s = x XOR y XOR z
        cls.append([x, y, z, -s])
        cls.append([x, -y, -z, -s])
        cls.append([-x, y, -z, -s])
        cls.append([-x, -y, z, -s])
        cls.append([-x, -y, -z, s])
        cls.append([-x, y, z, s])
        cls.append([x, -y, z, s])
        cls.append([x, y, -z, s])
        # c = majority(x, y, z)
        cls.append([-c, x, y])
        cls.append([-c, x, z])
        cls.append([-c, y, z])
        cls.append([c, -x, -y])
        cls.append([c, -x, -z])
        cls.append([c, -y, -z])
        return s, c, cls

    # Create bit representations for each weighted literal
    bit_columns = [[] for _ in range(num_bits + n)]

    for i, (lit, w) in enumerate(zip(lits, weights)):
        _check_deadline(deadline)
        for b in range(num_bits):
            if (w >> b) & 1:
                bit_columns[b].append(lit)

    # Reduce columns using 3:2 compressors (Wallace tree)
    result_bits = [None] * (num_bits + n)
    max_iters = n * 2

    for iteration in range(max_iters):
        any_reduced = False
        for b in range(len(bit_columns)):
            _check_deadline(deadline)
            while len(bit_columns[b]) >= 3:
                _check_deadline(deadline)
                a = bit_columns[b].pop()
                bb = bit_columns[b].pop()
                c = bit_columns[b].pop()
                s, cout, cls = full_adder(a, bb, c, vpool)
                clauses.extend(cls)
                if s is not None:
                    bit_columns[b].append(s)
                if cout is not None:
                    if b + 1 >= len(bit_columns):
                        bit_columns.extend([[] for _ in range(b + 2 - len(bit_columns))])
                    bit_columns[b + 1].append(cout)
                any_reduced = True

            while len(bit_columns[b]) == 2:
                _check_deadline(deadline)
                a = bit_columns[b].pop()
                bb = bit_columns[b].pop()
                s, cout, cls = full_adder(a, bb, None, vpool)
                clauses.extend(cls)
                if s is not None:
                    bit_columns[b].append(s)
                if cout is not None:
                    if b + 1 >= len(bit_columns):
                        bit_columns.extend([[] for _ in range(b + 2 - len(bit_columns))])
                    bit_columns[b + 1].append(cout)
                any_reduced = True

        if not any_reduced:
            break

    # Extract final sum bits
    sum_bits = []
    for b in range(len(bit_columns)):
        if bit_columns[b]:
            sum_bits.append((b, bit_columns[b][0]))
        else:
            sum_bits.append((b, None))

    if exact:
        for b, literal in sum_bits:
            target = (bound >> b) & 1
            if literal is None:
                if target:
                    return [[]]
            else:
                clauses.append([literal if target else -literal])
        return clauses

    # Binary comparator: enforce sum <= bound
    # active[b] = "sum[MSB..b+1] equals bound[MSB..b+1]" (still potentially equal)
    # When active and bound_b=0: sum_b must be 0
    # When active and bound_b=1: if sum_b=0, we go strictly lt (deactivate)
    #                             if sum_b=1, we stay active (equal so far)
    # When NOT active: we're already strictly lt, all bits are free

    sum_bit_map = {b: lit for b, lit in sum_bits if lit is not None}
    max_sum_bit = max((b for b, lit in sum_bits if lit is not None), default=-1)

    # Overflow bits must be 0
    for b, s_lit in sum_bits:
        if s_lit is not None and b > num_bits + 2:
            clauses.append([-s_lit])

    bound_msb = bound.bit_length() - 1 if bound > 0 else -1
    max_bit = max(max_sum_bit, bound_msb)

    if max_bit < 0:
        return clauses

    # active[MSB+1] = true (implicit)
    active = None  # None means "always active" (true)

    for b in range(max_bit, -1, -1):
        _check_deadline(deadline)
        bound_bit = (bound >> b) & 1
        s_lit = sum_bit_map.get(b)

        if s_lit is None:
            if bound_bit == 1:
                # sum_b=0 < bound_b=1: go strictly lt -> deactivate
                active = None  # We are now guaranteed sum <= bound from here
                break  # No need to check lower bits
            # bound_bit=0, sum_bit=0: still equal, active unchanged
        else:
            if bound_bit == 0:
                # When active: sum_b must be 0
                if active is None:
                    clauses.append([-s_lit])
                else:
                    clauses.append([-s_lit, -active])
                # active remains the same (if sum_b forced to 0, still equal)
            else:
                # bound_bit=1: sum_b can be 0 or 1
                # new_active = active AND sum_b
                if active is None:
                    active = s_lit  # active = sum_b (if sum_b=1, still equal; if 0, lt)
                else:
                    new_active = vpool.id(f'_cmp_a_{vpool.top}_{b}')
                    # new_active = active AND sum_b
                    clauses.append([-new_active, active])
                    clauses.append([-new_active, s_lit])
                    clauses.append([new_active, -active, -s_lit])
                    active = new_active

    return clauses


def encode_all_constraints(
    problem: Dict[str, Any],
    name2idx: Dict[str, int],
    vpool: IDPool,
    encoding: str,
    lb_map: Dict[str, int] = None,
    and_cache: Dict = None,
    deadline: float = 0.0,
    use_decomp: bool = False,
    decomp_threshold: int = 3,
    decomp_strategy: str = "sequential",
    decomp_shared: bool = True
) -> List[List[int]]:
    all_clauses = []

    decomp_cache = None
    if use_decomp and decomp_shared:
        from .decomposition import MulCache
        decomp_cache = MulCache()

    for idx, constraint in enumerate(problem.get('constraints', [])):
        _check_deadline(deadline)
        if constraint.get('type') == 'disjunction':
            clauses = _encode_disjunction(
                constraint, problem, name2idx, vpool, encoding, idx, lb_map,
                and_cache=and_cache, deadline=deadline,
                use_decomp=use_decomp,
                decomp_threshold=decomp_threshold,
                decomp_strategy=decomp_strategy,
                decomp_cache=decomp_cache
            )
        else:
            clauses = encode_constraint(
                constraint, problem, name2idx, vpool, encoding, idx, lb_map,
                and_cache=and_cache, deadline=deadline,
                use_decomp=use_decomp,
                decomp_threshold=decomp_threshold,
                decomp_strategy=decomp_strategy,
                decomp_cache=decomp_cache
            )
        all_clauses.extend(clauses)

    return all_clauses


def _encode_disjunction(
    constraint: Dict[str, Any],
    problem: Dict[str, Any],
    name2idx: Dict[str, int],
    vpool: IDPool,
    encoding: str,
    constr_idx: int,
    lb_map: Dict[str, int] = None,
    and_cache: Dict = None,
    deadline: float = 0.0,
    use_decomp: bool = False,
    decomp_threshold: int = 3,
    decomp_strategy: str = "sequential",
    decomp_cache=None
) -> List[List[int]]:
    """Encode a disjunction of conjunctive polynomial branches.

    ``disjuncts`` has the exact DNF shape ``[[atom, ...], [atom, ...]]``.
    One indicator is introduced per branch.  If an indicator is selected,
    every clause of every atom in that branch is enforced; at least one
    branch indicator must be selected.  Reverse implications are unnecessary
    because the indicators are existential auxiliaries.
    """
    branches = constraint.get('disjuncts', []) or []
    if not branches:
        return [[]]

    all_clauses: List[List[int]] = []
    indicator_lits: List[int] = []

    for branch_idx, branch in enumerate(branches):
        _check_deadline(deadline)
        indicator = vpool.id(f'_disj_{constr_idx}_{branch_idx}')
        indicator_lits.append(indicator)

        # All definitions used by a branch must be active with that branch.
        branch_and_cache = {}
        branch_decomp_cache = None
        if decomp_cache is not None:
            from .decomposition import MulCache
            branch_decomp_cache = MulCache()
        for atom_idx, atom in enumerate(branch):
            if atom.get('type') == 'disjunction':
                raise ValueError("nested disjunction must be lowered before encoding")
            sub_clauses = encode_constraint(
                atom, problem, name2idx, vpool, encoding,
                f'd{constr_idx}_b{branch_idx}_a{atom_idx}', lb_map,
                and_cache=branch_and_cache, deadline=deadline,
                use_decomp=use_decomp,
                decomp_threshold=decomp_threshold,
                decomp_strategy=decomp_strategy,
                decomp_cache=branch_decomp_cache
            )
            for clause in sub_clauses:
                all_clauses.append([-indicator] + clause)

    all_clauses.append(indicator_lits)
    return all_clauses
