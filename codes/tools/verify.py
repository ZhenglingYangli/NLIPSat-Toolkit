"""Check decoded witnesses in the original, unpreprocessed problem."""
from fractions import Fraction


def number(value):
    return value if isinstance(value, Fraction) else Fraction(str(value))


def decode_assignment(assignment, encoding, name2idx, problem, vpool=None):
    if vpool is None:
        raise ValueError('variable pool is required to decode a SAT model')
    if assignment is None:
        raise ValueError('no SAT model returned')
    signed = set(assignment)
    if any(-lit in signed for lit in signed):
        raise ValueError('contradictory SAT literals')
    values = {}
    for name, info in problem.get('variables', {}).items():
        q = name2idx[name]
        lb, ub = int(info['lb']), int(info['ub'])
        width = ub - lb
        def selected(r):
            return vpool.obj2id.get(f'x_{q}@{r}') in signed
        if encoding == 'OH':
            choices = [r for r in range(width + 1) if selected(r)]
            if len(choices) != 1:
                raise ValueError(f'{name}: expected exactly one selected value')
            value = choices[0]
        elif encoding == 'UNA':
            bits = [selected(r) for r in range(1, width + 1)]
            value = sum(bits)
            if bits != [r < value for r in range(width)]:
                raise ValueError(f'{name}: non-monotone unary model')
        elif encoding == 'BIN':
            value = sum(1 << r for r in range(max(1, width.bit_length())) if selected(r))
        else:
            raise ValueError(f'unsupported encoding: {encoding}')
        if not 0 <= value <= width:
            raise ValueError(f'{name}: encoded value outside its domain')
        values[name] = lb + value
    return values


decode_assignment_with_vpool = decode_assignment


def evaluate_term(term, values):
    result = number(term.get('c', 1))
    for name, power in term.get('vars', {}).items():
        if power:
            result *= values[name] ** int(power)
    return result


def evaluate_polynomial(terms, values):
    return sum((evaluate_term(t, values) for t in terms), Fraction(0))


def check_constraint(constraint, values):
    if constraint.get('type') == 'disjunction':
        branches = [all(check_constraint(a, values)[0] for a in branch)
                    for branch in constraint.get('disjuncts', [])]
        return any(branches), f'disjunction: {branches}'
    lhs = evaluate_polynomial(constraint.get('terms', constraint.get('lhs', [])), values)
    rhs = number(constraint.get('rhs', 0))
    rel = constraint.get('rel', constraint.get('sense', '<='))
    outcomes = {'<=': lhs <= rhs, '<': lhs < rhs, '>=': lhs >= rhs,
                '>': lhs > rhs, '=': lhs == rhs, '==': lhs == rhs}
    if rel not in outcomes:
        raise ValueError(f'unsupported relation: {rel}')
    return outcomes[rel], f'{lhs} {rel} {rhs}'


def check_original_smt(problem, values):
    """Substitute the witness into original Z3 ASTs, independently of the IR."""
    if '_smt_assertions' not in problem:
        return None
    import z3
    substitutions = []
    for original, normalized in problem['_name_map'].items():
        value = values[normalized]
        substitutions.append((z3.Int(original), z3.IntVal(value)))
        substitutions.append((z3.Bool(original), z3.BoolVal(bool(value))))
    for index, assertion in enumerate(problem['_smt_assertions']):
        if not z3.is_true(z3.simplify(z3.substitute(assertion, *substitutions))):
            raise ValueError(f'original SMT assertion {index} not satisfied')
    return True


def verify_values(problem, values):
    """Shared witness check for SAT decoding and the Z3 baseline."""
    if set(values) != set(problem.get('variables', {})):
        raise ValueError('witness does not cover exactly the original variables')
    for name, value in values.items():
        info = problem['variables'][name]
        if not isinstance(value, int) or not number(info['lb']) <= value <= number(info['ub']):
            raise ValueError(f'{name}={value} outside original integer domain')
    checks = []
    for index, constraint in enumerate(problem.get('constraints', [])):
        valid, desc = check_constraint(constraint, values)
        checks.append({'index': index, 'satisfied': valid, 'description': desc})
        if not valid:
            raise ValueError(f'constraint {index} violated: {desc}')
    source_valid = check_original_smt(problem, values)
    objective = evaluate_polynomial(problem.get('objective', {}).get('terms', []), values)
    return objective, checks, source_valid


def verify_solution(problem, result, encoding, vpool=None):
    report = {'valid': False, 'errors': [], 'warnings': [], 'var_values': {},
              'constraint_checks': [], 'computed_objective': None,
              'reported_objective': result.get('objective_value')}
    try:
        processed = result.get('processed_problem', problem)
        values = decode_assignment(result.get('assignment'), encoding,
                                   result.get('name2idx', {}), processed,
                                   vpool if vpool is not None else result.get('vpool'))
        meta = result.get('preprocess_meta', {})
        values.update(meta.get('objective', {}).get('probed_variables', {}))
        for name, shift in meta.get('variable_shifts', {}).items():
            values[name] += shift
        report['var_values'] = values
        computed, checks, source_valid = verify_values(problem, values)
        report['constraint_checks'] = checks
        report['source_smt_verified'] = source_valid
        report['computed_objective_exact'] = str(computed)
        report['computed_objective'] = int(computed) if computed.denominator == 1 else float(computed)
        exact = result.get('objective_value_exact')
        reported = result.get('objective_value')
        if exact is not None and number(exact) != computed:
            raise ValueError(f'objective mismatch: computed={computed}, reported={exact}')
        if reported is not None:
            if exact is None:
                matches = number(reported) == computed
            else:
                matches = reported == report['computed_objective']
            if not matches:
                raise ValueError(f'objective mismatch: computed={computed}, reported={reported}')
        report['valid'] = True
    except TimeoutError:
        raise
    except Exception as exc:
        report['errors'].append(str(exc))
    return report['valid'], report


def format_verification_report(report):
    lines = ['Verification PASSED' if report['valid'] else 'Verification FAILED']
    if report['var_values']:
        lines.append('  Variables: ' + ', '.join(f'{k}={v}' for k, v in sorted(report['var_values'].items())))
    if report.get('source_smt_verified'):
        lines.append('  Original SMT assertions: passed')
    if report['computed_objective'] is not None:
        lines.append(f"  Computed objective: {report.get('computed_objective_exact', report['computed_objective'])}")
    lines.extend(f'  ERROR: {error}' for error in report['errors'])
    return '\n'.join(lines)
