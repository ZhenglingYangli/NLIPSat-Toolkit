#!/usr/bin/env python3

from pysat.formula import WCNF, IDPool
from pysat.card import CardEnc
from .onehot import _check_deadline


class EncodingDriverError(Exception):
    pass


def add_domain_constraints(problem, encoding, name2idx, vpool, wcnf, cfg=None):
    deadline = getattr(cfg, 'encoding_deadline', 0.0) if cfg else 0.0
    x_fn = lambda q, r: vpool.id(f'x_{q}@{r}')
    use_map = cfg is not None and getattr(cfg, 'use_mapping_shift', False)

    for var_name, meta in problem.get('variables', {}).items():
        _check_deadline(deadline)
        q = name2idx[var_name]
        lb = int(meta.get('lb', 0))
        ub = int(meta.get('ub', 0))
        ub_eff = (ub - lb) if use_map else ub

        if encoding == 'OH':
            _ensure_oh_structure(wcnf, x_fn, q, ub_eff, vpool, deadline)
        elif encoding == 'UNA':
            _ensure_una_structure(wcnf, x_fn, q, ub_eff, deadline)
        elif encoding == 'BIN':
            _ensure_bin_structure(wcnf, x_fn, q, ub_eff, deadline)
        else:
            raise EncodingDriverError(f"Unsupported encoding: {encoding}")


def encode_constraints(problem, encoding, name2idx, vpool, cfg=None):
    from .constraints import encode_all_constraints

    use_map = cfg is not None and getattr(cfg, 'use_mapping_shift', False)
    lb_map = {vn: int(info.get('lb', 0))
              for vn, info in problem.get('variables', {}).items()} if use_map else {}

    global_cache = getattr(cfg, '_global_and_cache', None) if cfg else None
    deadline = getattr(cfg, 'encoding_deadline', 0.0) if cfg else 0.0

    use_decomp = (
        encoding == 'BIN' and
        cfg is not None and
        getattr(cfg, 'use_decomposition', False)
    )
    decomp_threshold = getattr(cfg, 'decomp_threshold', 3) if cfg else 3
    decomp_strategy = getattr(cfg, 'decomp_strategy', 'sequential') if cfg else 'sequential'
    decomp_shared = getattr(cfg, 'decomp_shared', True) if cfg else True

    clauses = encode_all_constraints(
        problem, name2idx, vpool, encoding, lb_map,
        and_cache=global_cache, deadline=deadline,
        use_decomp=use_decomp,
        decomp_threshold=decomp_threshold,
        decomp_strategy=decomp_strategy,
        decomp_shared=decomp_shared
    )

    w = WCNF()
    for clause in clauses:
        w.append(clause)

    return w


def _is_all_binary(problem):
    """Check whether every variable has domain {0,1}."""
    for info in problem.get('variables', {}).values():
        if int(info.get('lb', 0)) != 0 or int(info.get('ub', 0)) != 1:
            return False
    return True


def _encode_objective_binary_linear(problem, name2idx, vpool, encoding='BIN', deadline=0):
    """Fast McCormick linearization for objectives over binary variables.
    Each quadratic term x_i*x_j is linearized via z <-> (x_i AND x_j).
    Works for BIN (x_q@0 = value), OH/UNA (x_q@1 = indicator for value 1)."""
    from pysat.formula import WCNF
    w = WCNF()
    terms = problem.get('objective', {}).get('terms', [])
    if not terms:
        return w

    bit_idx = 0 if encoding == 'BIN' else 1
    x_fn = lambda q: vpool.id(f'x_{q}@{bit_idx}')
    product_cache = {}
    neg_soft_total = 0

    for t in terms:
        _check_deadline(deadline)
        c_val = int(t.get('c', 0))
        if c_val == 0:
            continue
        vs = t.get('vars', {}) or {}
        active = [(vn, int(p)) for vn, p in vs.items() if int(p) > 0]

        if not active:
            continue

        if len(active) == 1:
            vn, _ = active[0]
            lit = x_fn(name2idx[vn])
            if c_val > 0:
                w.append([lit], weight=c_val)
            else:
                w.append([-lit], weight=-c_val)
                neg_soft_total += -c_val
            continue

        if len(active) == 2:
            (vn1, _), (vn2, _) = active
            x1, x2 = x_fn(name2idx[vn1]), x_fn(name2idx[vn2])
            cache_key = (min(x1, x2), max(x1, x2))
            if cache_key in product_cache:
                z = product_cache[cache_key]
            else:
                z = vpool.id(f'z_binlin_{name2idx[vn1]}_{name2idx[vn2]}')
                w.append([-z, x1])
                w.append([-z, x2])
                w.append([z, -x1, -x2])
                product_cache[cache_key] = z
            if c_val > 0:
                w.append([z], weight=c_val)
            else:
                w.append([-z], weight=-c_val)
                neg_soft_total += -c_val
            continue

        # Higher-degree: chain AND gates (x^k = x for binary, so degree <= #vars)
        var_lits = []
        for vn, _ in active:
            lit = x_fn(name2idx[vn])
            if lit not in var_lits:
                var_lits.append(lit)
        z = var_lits[0]
        for i in range(1, len(var_lits)):
            _check_deadline(deadline)
            pair_key = (min(z, var_lits[i]), max(z, var_lits[i]))
            if pair_key in product_cache:
                z = product_cache[pair_key]
            else:
                z_new = vpool.id(f'z_binlin_chain_{pair_key[0]}_{pair_key[1]}')
                w.append([-z_new, z])
                w.append([-z_new, var_lits[i]])
                w.append([z_new, -z, -var_lits[i]])
                product_cache[pair_key] = z_new
                z = z_new
        if c_val > 0:
            w.append([z], weight=c_val)
        else:
            w.append([-z], weight=-c_val)
            neg_soft_total += -c_val

    if neg_soft_total > 0:
        w._neg_soft_total = neg_soft_total
    return w


def encode_objective(problem, encoding, name2idx, vpool, cfg=None):
    use_map = cfg is not None and getattr(cfg, 'use_mapping_shift', False)
    lb_map = {vn: int(info.get('lb', 0))
              for vn, info in problem.get('variables', {}).items()} if use_map else {}

    deadline = getattr(cfg, 'encoding_deadline', 0.0) if cfg else 0.0
    from .onehot import _check_deadline
    _check_deadline(deadline)
    if _is_all_binary(problem):
        return _encode_objective_binary_linear(problem, name2idx, vpool, encoding=encoding, deadline=deadline)

    effective_encoding = encoding

    # Check if decomposition should be used (BIN only)
    use_decomp = (
        effective_encoding == 'BIN' and
        cfg is not None and
        getattr(cfg, 'use_decomposition', False)
    )
    decomp_threshold = getattr(cfg, 'decomp_threshold', 3) if cfg else 3

    if effective_encoding == 'OH':
        from .onehot import encode_objective_oh
        return encode_objective_oh(problem, name2idx, vpool, lb_map=lb_map, deadline=deadline)

    elif effective_encoding == 'UNA':
        from .unary import encode_objective_una
        return encode_objective_una(problem, name2idx, vpool, lb_map=lb_map, deadline=deadline)

    elif effective_encoding == 'BIN':
        if use_decomp:
            from .binary import encode_objective_bin_with_decomposition
            return encode_objective_bin_with_decomposition(
                problem, name2idx, vpool,
                lb_map=lb_map,
                decomp_threshold=decomp_threshold,
                decomp_strategy=getattr(cfg, 'decomp_strategy', 'sequential'),
                decomp_exact=getattr(cfg, 'decomp_exact', True),
                decomp_shared=getattr(cfg, 'decomp_shared', True),
                external_and_cache=getattr(cfg, '_global_and_cache', None),
                deadline=deadline
            )
        else:
            from .binary import encode_objective_bin
            global_cache = getattr(cfg, '_global_and_cache', None) if cfg else None
            return encode_objective_bin(problem, name2idx, vpool, lb_map=lb_map,
                                        external_and_cache=global_cache, deadline=deadline)

    raise EncodingDriverError(f"Unsupported encoding: {encoding}")


def _ensure_oh_structure(w, x_fn, q, ub, vpool, deadline=0):
    lits = []
    for r in range(ub + 1):
        _check_deadline(deadline)
        lits.append(x_fn(q, r))
    for clause in CardEnc.equals(lits=lits, bound=1, vpool=vpool):
        _check_deadline(deadline)
        w.append(clause)


def _ensure_una_structure(w, x_fn, q, ub, deadline=0):
    w.append([x_fn(q, 0)])
    for r in range(1, ub + 1):
        _check_deadline(deadline)
        w.append([-x_fn(q, r), x_fn(q, r - 1)])


def _ensure_bin_structure(w, x_fn, q, ub, deadline=0):
    num_bits = max(1, ub.bit_length())
    bits = [x_fn(q, r) for r in range(num_bits)]
    # A zero in ub forbids a larger bit when all higher one-bits match ub.
    for r in range(num_bits):
        _check_deadline(deadline)
        if not ((ub >> r) & 1):
            w.append([-bits[r]] + [-bits[k] for k in range(r + 1, num_bits) if (ub >> k) & 1])
