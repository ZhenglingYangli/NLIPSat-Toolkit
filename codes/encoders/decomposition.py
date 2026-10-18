#!/usr/bin/env python3

from typing import Dict, Any, List, Tuple, Optional
from pysat.formula import IDPool
from .onehot import _check_deadline


def mul(
    a_bits: List[int],
    b_bits: List[int],
    vpool: IDPool,
    prefix: str,
    ub_a: int,
    ub_b: int,
    sense: int = 0,
    deadline: float = 0.0
) -> Tuple[List[int], List[List[int]]]:
    _check_deadline(deadline)
    if sense not in (-1, 0, 1):
        raise ValueError(f'unsupported multiplication sense: {sense}')
    vpool._multiplication_count = getattr(vpool, '_multiplication_count', 0) + 1
    num_bits_a = len(a_bits)
    num_bits_b = len(b_bits)

    hard_clauses = []

    z_lits = []
    z_weights = []

    for r_a in range(num_bits_a):
        _check_deadline(deadline)
        for r_b in range(num_bits_b):
            z = vpool.id(f'{prefix}_z_{r_a}_{r_b}')

            # Mul-1: z <-> (a AND b)
            if sense == 0:
                hard_clauses.append([-z, a_bits[r_a]])
                hard_clauses.append([-z, b_bits[r_b]])
                hard_clauses.append([z, -a_bits[r_a], -b_bits[r_b]])
            elif sense == 1:
                hard_clauses.append([-z, a_bits[r_a]])
                hard_clauses.append([-z, b_bits[r_b]])
            elif sense == -1:
                hard_clauses.append([z, -a_bits[r_a], -b_bits[r_b]])

            z_lits.append(z)
            z_weights.append(2 ** (r_a + r_b))

    ub_product = ub_a * ub_b
    num_bits_y = max(1, ub_product.bit_length())

    y_bits = [vpool.id(f'{prefix}_y_{r}') for r in range(num_bits_y)]

    y_lits = y_bits[:]
    y_weights = [2 ** r for r in range(num_bits_y)]

    # Reuse the exact PB encoder, including its large-integer adder path.
    from .constraints import _encode_pb_constraint
    if sense == 1:
        leq_lits = y_lits + [-z for z in z_lits]
        leq_weights = y_weights + z_weights
        leq_bound = sum(z_weights)
        clauses = _encode_pb_constraint(leq_lits, leq_weights, leq_bound, '<=', vpool, [], deadline=deadline)
    elif sense == -1:
        geq_lits = y_lits + [-z for z in z_lits]
        geq_weights = y_weights + z_weights
        geq_bound = sum(z_weights)
        clauses = _encode_pb_constraint(geq_lits, geq_weights, geq_bound, '>=', vpool, [], deadline=deadline)
    else:
        all_lits = z_lits + [-y for y in y_lits]
        all_weights = z_weights + y_weights
        bound = sum(y_weights)
        clauses = _encode_pb_constraint(all_lits, all_weights, bound, '==', vpool, [], deadline=deadline)

    hard_clauses.extend(clauses)

    return y_bits, hard_clauses

class MulCache:

    def __init__(self):
        self._cache: Dict[tuple, Tuple[List[int], int]] = {}
        self.hits = 0

    def get(self, key: tuple) -> Optional[Tuple[List[int], int]]:
        value = self._cache.get(key)
        if value is not None:
            self.hits += 1
        return value

    def put(self, key: tuple, y_bits: List[int], ub_product: int):
        self._cache[key] = (y_bits, ub_product)

    @staticmethod
    def make_key(a_bits: List[int], b_bits: List[int], sense: int) -> tuple:
        key_a = tuple(a_bits)
        key_b = tuple(b_bits)
        return (min(key_a, key_b), max(key_a, key_b), sense)

    def __len__(self):
        return len(self._cache)


def mul_cached(
    a_bits: List[int],
    b_bits: List[int],
    vpool: IDPool,
    prefix: str,
    ub_a: int,
    ub_b: int,
    sense: int = 0,
    cache: Optional[MulCache] = None,
    deadline: float = 0.0
) -> Tuple[List[int], List[List[int]], int]:
    if cache is not None:
        key = MulCache.make_key(a_bits, b_bits, sense)
        cached = cache.get(key)
        if cached is not None:
            vpool._multiplication_cache_hits = getattr(vpool, '_multiplication_cache_hits', 0) + 1
            return cached[0], [], cached[1]  # Reuse bits, no new clauses

    y_bits, hard_clauses = mul(a_bits, b_bits, vpool, prefix, ub_a, ub_b, sense, deadline=deadline)
    ub_product = ub_a * ub_b

    if cache is not None:
        key = MulCache.make_key(a_bits, b_bits, sense)
        cache.put(key, y_bits, ub_product)

    return y_bits, hard_clauses, ub_product

def decompose_sequential(
    omega: List[Tuple[str, int, int, List[int]]],
    vpool: IDPool,
    prefix: str,
    sense: int = 0,
    cache: Optional[MulCache] = None,
    deadline: float = 0.0
) -> Tuple[List[int], List[List[int]], int]:
    if len(omega) == 0:
        return [], [], 1

    if len(omega) == 1:
        _, _, ub, bits = omega[0]
        return bits, [], ub

    hard_clauses = []

    _, _, ub1, bits1 = omega[0]
    _, _, ub2, bits2 = omega[1]

    acc_bits, clauses, acc_ub = mul_cached(
        bits1, bits2, vpool, f'{prefix}_mul_1_2', ub1, ub2, sense, cache, deadline=deadline
    )
    hard_clauses.extend(clauses)

    for k in range(2, len(omega)):
        _, _, ub_k, bits_k = omega[k]
        acc_bits, clauses, acc_ub = mul_cached(
            acc_bits, bits_k, vpool,
            f'{prefix}_mul_1to{k}_and_{k+1}',
            acc_ub, ub_k, sense, cache, deadline=deadline
        )
        hard_clauses.extend(clauses)

    return acc_bits, hard_clauses, acc_ub

def decompose_binary_tree(
    omega: List[Tuple[str, int, int, List[int]]],
    vpool: IDPool,
    prefix: str,
    sense: int = 0,
    cache: Optional[MulCache] = None,
    deadline: float = 0.0
) -> Tuple[List[int], List[List[int]], int]:
    if len(omega) == 0:
        return [], [], 1

    if len(omega) == 1:
        _, _, ub, bits = omega[0]
        return bits, [], ub

    mid = len(omega) // 2
    left = omega[:mid]
    right = omega[mid:]

    left_bits, left_clauses, left_ub = decompose_binary_tree(
        left, vpool, f'{prefix}_L', sense, cache, deadline=deadline
    )
    right_bits, right_clauses, right_ub = decompose_binary_tree(
        right, vpool, f'{prefix}_R', sense, cache, deadline=deadline
    )

    result_bits, mul_clauses, result_ub = mul_cached(
        left_bits, right_bits, vpool, f'{prefix}_mul',
        left_ub, right_ub, sense, cache, deadline=deadline
    )

    all_clauses = left_clauses + right_clauses + mul_clauses
    return result_bits, all_clauses, result_ub

def encode_term_decomp(
    term: Dict[str, Any],
    problem: Dict[str, Any],
    name2idx: Dict[str, int],
    vpool: IDPool,
    prefix: str,
    lb_map: Dict[str, int] = None,
    sense: int = 0,
    strategy: str = "sequential",
    cache: Optional[MulCache] = None,
    deadline: float = 0.0
) -> Tuple[Dict[int, Tuple[int, int]], List[List[int]]]:

    _check_deadline(deadline)
    if strategy not in ('sequential', 'binary_tree'):
        raise ValueError(f'unsupported decomposition strategy: {strategy}')
    term_vars = term.get('vars', {}) or {}
    c = int(term.get('c', 1))

    if not term_vars or c == 0:
        return {}, []

    omega = []
    for var_name, power in sorted(term_vars.items()):
        q = name2idx[var_name]
        ub = int(problem['variables'][var_name]['ub'])
        num_bits = max(1, ub.bit_length())
        bits = [vpool.id(f'x_{q}@{r}') for r in range(num_bits)]

        for _ in range(int(power)):
            omega.append((var_name, q, ub, bits))

    if len(omega) == 0:
        return {}, []

    if strategy == "binary_tree":
        result_bits, hard_clauses, _ = decompose_binary_tree(
            omega, vpool, prefix, sense, cache, deadline=deadline
        )
    else:
        result_bits, hard_clauses, _ = decompose_sequential(
            omega, vpool, prefix, sense, cache, deadline=deadline
        )

    z_mapping = {}
    for r, y_var in enumerate(result_bits):
        weight = c * (2 ** r)
        z_mapping[r] = (y_var, weight)

    return z_mapping, hard_clauses
