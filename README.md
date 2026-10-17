# NLIPSat: Satisfiability-Based Nonlinear Integer Programming Encoding Toolkit

NLIPSat is a research prototype for encoding nonlinear integer programming (NLIP) instances into weighted MaxSAT and solving them with modern MaxSAT engines. The toolkit supports multiple encoding strategies, optional order decomposition for high-degree terms, lightweight preprocessing, and solution verification. It also includes benchmark collections used in our experiments.

## Repository Structure

```text
.
├── benchmarks/
│   ├── diverse_sat/
│   ├── qplib_fully_passed/
│   └── smt_0_10/
└── codes/
    ├── encoders/
    ├── solver/
    ├── tools/
    └── main.py
```

## Benchmark Collections

The repository currently includes the following benchmark groups:

- `benchmarks/diverse_sat`: 108 CNF instances
- `benchmarks/qplib_fully_passed`: 137 QPLIB instances
- `benchmarks/smt_0_10`: 150 SMT-LIB2 instances

## Requirements

- Python 3.10 or newer (tested on Python 3.10–3.12)
- [PySAT](https://pysathq.github.io/) with PB support (`python-sat[pblib]`)
- Z3 Python bindings (`z3-solver`) for SMT-LIB2 parsing

## Quick Start

Run from the repository root:

```bash
python3 codes/main.py path/to/problem.json
```

Examples:

```bash
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib -e BIN
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib -e BIN --decomp
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib -s RC2 --verify
python3 codes/main.py benchmarks/diverse_sat/ais/ais10.cnf --k 2 -e BIN
```

## Python API

NLIPSat also exposes a small Python API for constructing and inspecting the
generated WCNF formula object before solving:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python3 examples/api_example.py
```

```python
from nlipsat import load_problem, EncodingConfig, \
                    build_wcnf, solve, verify_solution

problem = load_problem("examples/example4.json")  # .qplib .smt2 .cnf also accepted
cfg = EncodingConfig()  # use_decomposition=True for order decomp

wcnf, name2idx, vpool = build_wcnf(problem, "BIN", cfg)
print(len(wcnf.hard), len(wcnf.soft), wcnf.wght, wcnf.topw)

wcnf.to_file("example.wcnf")
result = solve(problem, encoding="BIN", config=cfg, solver="RC2")
ok, report = verify_solution(problem, result)
print(result["objective_value"], ok)
# 2 True
```

### Generate WCNF only (no solving)

If you only want to build the weighted CNF encoding (for inspection or for running a third-party MaxSAT solver manually), use solver `NONE`:

```bash
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib -e BIN -s NONE -o out.wcnf
python3 codes/main.py benchmarks/smt_0_10/calypto/problem-005612.cvc.1.smt2 -e BIN --decomp -s NONE -o out.wcnf
```

## Supported Input Formats

### JSON

Native NLIP instances can be provided in JSON format.

### QPLIB

QPLIB instances are parsed through `codes/tools/qplib_parser.py`.

### SMT-LIB2

SMT2 benchmark files are parsed through `codes/tools/smt2_parser.py`.
This parser uses `z3-solver` for extracting bounds and constraints, and rejects unsupported assertions and variables without finite integer bounds.

### CNF for Diverse-SAT

When the input file is a CNF instance and `--k` is set to a value of at least `2`, the toolkit interprets the instance as a Diverse-SAT problem and builds the corresponding quadratic objective.

## Main Command-Line Options

```text
usage: python3 codes/main.py input [options]
```

Common options:

- `-e, --encoding {OH,UNA,BIN}`: choose the encoding strategy
- `-s, --solver {RC2,MAXHS,WMAXCDCL,OPENWBO,NONE}`: choose the solver backend (`NONE` = build WCNF only)
- `--decomp`: enable order decomposition for high-degree terms in binary encoding
- `--decomp-threshold N`: minimum degree that triggers decomposition
- `--decomp-strategy {sequential,binary_tree}`: choose the decomposition strategy
- `--decomp-relaxed`: use relaxed multiplication semantics
- `--no-decomp-shared`: disable shared substructure caching
- `--no-weight-gcd`: disable soft-weight normalization by GCD
- `--no-preprocess`: disable optional bound propagation and variable fixing; exact normalization and variable shifts still run
- `--verify`: display the original-problem verification report (successful solutions are always verified)
- `--timeout T`: set a timeout in seconds
- `--k K`: number of diverse models for CNF input (`K >= 2`)
- `-o, --output FILE`: save the generated WCNF to a file
- `-v, --verbose`: print detailed statistics

## Example Workflows

### Solve a QPLIB instance with the default encoding

```bash
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib
```

### Solve with binary encoding and decomposition

```bash
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib -e BIN --decomp
```

### Solve an SMT-LIB2 instance and verify the result

```bash
python3 codes/main.py benchmarks/smt_0_10/calypto/problem-005612.cvc.1.smt2 --verify
```

### Build a WCNF file without further processing

```bash
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib -s NONE -o out.wcnf
```

## Output

For each run, the tool prints a compact summary line containing:

- benchmark name
- encoding
- solver
- status
- objective value
- MaxSAT cost
- variable and clause statistics
- read, encoding, solving, and total runtime

With `--verbose`, the tool prints additional encoding and timing details. Every successful solution is checked against the original variable domains, constraints, and objective; SMT witnesses are also checked against the original assertions. `--verify` displays this report. A failed check produces `INVALID` and a nonzero exit code. For optimization results, `OPTExact` and the API field `objective_value_exact` preserve the exact integer or rational objective value.

## Correctness and regression tests

QPLIB quadratic coefficients follow its stored-triangle convention: every stored entry contributes `q_ij*x_i*x_j/2`, including off-diagonal entries. Decimal coefficients and objective constants are kept as exact rational numbers. Preprocessing preserves nonlinear constraints and fixes binary variables only when the full objective proves global monotonicity. SMT Boolean combinations retain their conjunction/disjunction structure; unsupported assertions and integer variables without finite bounds are rejected.

Run the included public API checks in the toolkit environment:

```bash
python3 tests/test_api.py
```

Additional development-time checks, maintained outside this published test set, included independent finite-domain enumeration, comparison with original Z3 formulas, external-solver output parsing, both WCNF export paths, and the published QPLIB_3562 feasible point (objective 15, 63 variable domains and 42 constraints). This published point checks feasibility and objective recovery; it is not an optimality certificate. Historical results affected by the parsing or preprocessing defects require a new run with corrected code.

## References

If you use NLIPSat, please cite the following papers:

- Zhengling Yangli, Zhifei Zheng, Sami Cherif, Rui Sá Shibasaki, and Chu-Min Li. *NLIPSat: Satisfiability-Based Nonlinear Integer Programming Encoding Toolkit*. In Proceedings of the 29th International Conference on Theory and Applications of Satisfiability Testing (SAT 2026), LIPIcs, to appear.
- Zhifei Zheng, Sami Cherif, Rui Sá Shibasaki, Chu-Min Li, and Jialu Zhang. *Maximum Satisfiability Formulations for Nonlinear Integer Programming*. In Proceedings of the 19th European Conference on Logics in Artificial Intelligence (JELIA 2025), LNCS 16094, pp. 190--206, Springer, 2025. DOI: [10.1007/978-3-032-04590-4_14](https://doi.org/10.1007/978-3-032-04590-4_14).

## Implementation details and local validation

The implementation review covers all 22 runtime/interface Python files, the
example, package configuration, and test entry points. The toolkit retains its
original encodings, decomposition options, and solver choices.

| Files | Implemented behavior and local evidence |
|---|---|
| `codes/tools/qplib_parser.py` | Exact stored-triangle coefficients, finite integer domains, original infinity sentinel and rational bounds; checked with QPLIB's published solution point. |
| `codes/tools/smt2_parser.py` | Exact Boolean lowering, Boolean variables and Boolean equivalence, integer polynomial constraints, strict rejection of unsupported assertions or artificial bounds. Diagnostic parse failures cannot be compiled as an empty problem. |
| `codes/tools/cnf_parser.py`, `diversesat_parser.py` | Clause truth and pairwise Hamming-distance objective; zero-variable CNF retains zero variables. Diverse models are tuples and may repeat, as in the original formulation. |
| `codes/tools/preprocessing.py` | Exact normalization, safe linear propagation, polynomial variable shifts, monotone unconstrained binary fixing, and objective recovery metadata. Fixing preserves the optimum and may remove nonoptimal feasible points. |
| `codes/encoders/onehot.py`, `unary.py` | Exactly-one and prefix domains; exact hard-constraint definitions and signed objective handling. The mixed finite difference of a monomial is evaluated as a product of one-dimensional differences. |
| `codes/encoders/binary.py`, `encoder_driver.py` | Integer bit lengths, compact upper-bound comparison, repeated-factor weight contributions, and consistent ordinary AND caching in direct and decomposition-fallback paths. |
| `codes/encoders/decomposition.py` | Sequential and binary-tree multiplication; exact and directional objective definitions; hard constraints always use exact products. Large-integer relations use the existing PB adder instead of overflowing PBLib. |
| `codes/encoders/constraints.py` | Constant contradictions, signed PB relations, one shared exact-sum circuit for equality, and branch-local definitions for disjunctions. |
| `codes/solver/solve.py` | Hard-clause contradiction preservation, exact objective restoration, required witness checks, backend model/cost/status consistency, classic WCNF serialization across supported PySAT versions, and solver cleanup on interruption. |
| `codes/tools/verify.py` | Original domains, constraints and rational objective; original SMT assertions; runtime timeouts propagate instead of becoming mathematical failures. |
| `codes/main.py` | Both export paths, canonical error statuses, exact-objective display and wall-clock timeout cleanup of the external solver. |
| `solvers/baseline/run_nlip_sat.py`, `run_z3.py` | Original SAT and Z3 backends; original-problem witness checks, consistent objective directions and nonzero exit status for invalid/error results. |
| `nlipsat/api.py`, `__init__.py`, package initializers and `pyproject.toml` | Original public entry points; installed wheel imports its own execution modules and solves an exact rational example outside the source directory. |
| `codes/solver/config.py` | Existing options remain available. Legacy `encoding` and `save_wcnf` fields are metadata; choose the encoding through the API/CLI argument and export through an explicit output path. Domain shifting always runs in the solve pipeline. |

`use_decomposition` records the requested setting. `decomposition_effective`,
`num_multiplications`, and `multiplication_cache_hits` report the actual
arithmetic work. Pure binary objectives use the existing exact-AND fast path;
requesting decomposition alone does not establish that arithmetic multiplication
was used. `decomp_shared` controls multiplication reuse, while ordinary AND
caching is kept consistent in both configurations. The existing adaptive
quadratic bit-product trigger remains unchanged.

Development-time validation passed 72 unittest cases, including execution of
the original public API script and its 56 checks. The extended test scripts
remain local; the published repository includes the API script. Validation
used both existing dependency environments:
PySAT 1.8.dev27 / Z3 4.15.4 and PySAT 1.9.dev15 / Z3 5.1.0. No dependency upgrade
is required for the original runtime. Additional checks cover 32 combinations of
decomposition strategy, exactness, sharing, preprocessing and weight GCD;
240 seeded finite-domain optimization comparisons; 2,880 PB-assignment
comparisons; exhaustive small multiplication; and Boolean SMT truth tables.

All four original MaxSAT backends pass 16 actual optimization/UNSAT checks in
each dependency environment. For the real `flat30-7.cnf` input, OH, UNA and BIN
return objective 60; the two returned models satisfy every one of its 300 raw
clauses and their directly computed Hamming distance is 60.

The `--solver NONE` path requires `--output`. CLI exports and files passed to
the bundled external solvers use classic `p wcnf` format. A returned native
PySAT WCNF object's own `to_file` method follows that PySAT version's defaults.

### Measured local encoding changes

- BIN domain `0..65536`: 65,535 upper-bound clauses become 16 clauses; small-domain exhaustive checks confirm the same allowed integers.
- Eight-variable UNA example: construction time was 0.1717 seconds before and 0.0395 seconds after; both formulas have 280 variables, 2064 hard clauses and 256 soft clauses.
- For `x^2` on `0..3`, enabling decomposition without triggering multiplication now reuses the same ordinary cache as BIN and generates the same formula size.

The preprocessing/runtime comparisons below use three alternating before/after
runs on the same Ubuntu host and report median wall time. The baseline is the
repaired toolkit immediately before these local optimizations.

| Operation and input | Before | After |
| --- | ---: | ---: |
| Bound propagation, one 600-term linear constraint | 1.7558 s | 0.0148 s |
| Subsumption, 6,000 binary clauses sharing one literal | 2.1034 s | 0.0204 s |
| Binary objective probing, 1,000 degree-10 terms | 0.0661 s | 0.0263 s |
| Bounded RC2, 100,000 hard clauses | 0.3466 s | 0.2665 s |
| CPMpy cumulative model, 20 activities, domains 0..50, complete build | 1.2849 s | 1.2210 s |

Linear bound propagation maintains exact minimum/maximum sums and updates them
after each tightening, reducing a sweep from quadratic to linear work in the
number of terms. Short-clause subsumption indexes candidates by size and literal;
its subset-comparison budget is four times the number of literals in the unique
hard clauses. After that budget is consumed, remaining clauses are retained.
Formulas with at least 50,000 hard clauses skip subsumption. Binary probing
shares each term's upper-bound product among candidate variables and exits when
no unconstrained binary candidate remains. Timed RC2 transfers the formula
fields directly, avoiding a second clause-copying and maximum-variable scan.

The cumulative comparison produces identical formulas: 54,980 variables and
88,864 hard clauses. A small rational polynomial example has the same
independently enumerated optimum under OH, UNA and BIN; complete solve times
remain approximately 0.085-0.088 seconds in both versions.


These are local formula/construction measurements, not full-benchmark search
speedups.

### CPMpy integration and runtime contracts

`cpmpy-pr1127-adaptation.patch` adapts the draft CPMpy interface to the repaired
toolkit: it stops installing the old internal workarounds, uses bounded RC2,
and forwards fractional external-solver time budgets without rounding them up.
The patch applies to the original PR branch and has been tested locally.

`EncodingConfig(encoding_deadline=time.time() + seconds)` controls encoding.
Checks cover preprocessing expansion/propagation, integerization, binary-power
reduction, variable probing/substitution, domain generation, objectives, binary
constraint linearization, constraint branches, PB dispatch and PB adder
construction, and WCNF cleanup.
They are cooperative Python checkpoints; individual native-library calls are
not forcibly preempted. Expired PB calls fail before native encoding; near the
deadline, the existing Python adder path is selected.

`EncodingConfig(rc2_timeout=seconds)` uses a child process for bounded RC2;
zero keeps the original unlimited in-process mode. Serialization consumes the
budget; original variable IDs and formula weights are preserved. Timed-out
children are killed and reaped; partial incumbents are not recovered. `solve_rc2` returns `(-1, None)` for timeout and `(-1, [])` for UNSAT.
External solver budgets also include WCNF serialization. An early UNKNOWN is
reported as UNKNOWN, independent of whether a limit was configured.

The local development suite passed 72 tests in both dependency environments.
Independent checks include 576 fixed-assignment feasibility/cost comparisons
with fractional objectives and disjunctions, and 48 CPMpy optimization/witness
comparisons against direct enumeration. Repeated solve/add, solveAll, all three
external backends, and fractional adapter budgets pass.
Eight focused optimization tests cover retained feasible assignments, exact
weighted costs, nonlinear-constraint exclusion, probing optima, subsumption
budget exhaustion, helper deadlines and timed RC2 transfer. Additional paired
checks confirm identical bound updates in 160 cases and identical probing and
integerization results in another 160 cases.

A normally installed wheel is verified from a separate working directory with its actual import path
checked. The current wheel also passes bounded RC2 and configured MaxHS in an
isolated environment without AIGER packages. The unused AIGER extra has been
removed; the declared runtime dependencies are python-sat[pblib] and z3-solver.
The earlier installed SMT loading/verification check remains valid.

CPMpy integration checks were run locally against the adapted PR branch.
The included patch targets CPMpy PR #1127 and must be applied in that project;
installing this toolkit alone does not install the CPMpy solver adapter.

### Input coverage and remaining scope

All 150 bundled SMT inputs pass the final strict parsing scan, with zero errors
and zero timeouts under its 20-second per-file diagnostic budget (two workers).
The two previously unresolved inputs now parse: the AProVE instance took about
6 seconds and the Calypto instance about 9 seconds in separate measurements.
For Calypto, a constructed feasible assignment satisfies all 151 translated
constraints and the original SMT assertions. Shared-expression caching and
key-based polynomial merging eliminate repeated DAG traversal and quadratic
monomial matching. The existing 137 QPLIB and 108 CNF parsing checks remain valid;
their parsers were unchanged during this final pass.

The review covers the implementation and the stated independent local checks.
The full 395-instance performance matrix has not been rerun. PBLib remains
required; when its wheel is unavailable, installation needs Python headers and
a compiler. Historical formal results remain unchanged; results affected by correctness fixes require
recomputation. The included CPMpy adaptation is supplied for review in the CPMpy project.
