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
- `benchmarks/smt_0_10`: 150SMT-LIB2 instances

## Requirements

- Python 3.10 or newer (tested on Python 3.10–3.12)
- [PySAT](https://pysathq.github.io/) with PB support (`python-sat[pblib,aiger]`)
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
# 18 4 [1, 2, 2, 4] 10

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
python3 codes/main.py benchmarks/smt_0_10/calypto/problem-005596.cvc.1.smt2 -e BIN --decomp -s NONE -o out.wcnf
```

## Supported Input Formats

### JSON

Native NLIP instances can be provided in JSON format.

### QPLIB

QPLIB instances are parsed through `codes/tools/qplib_parser.py`.

### SMT-LIB2

SMT2 benchmark files are parsed through `codes/tools/smt2_parser.py`.
This parser uses `z3-solver` for extracting bounds and constraints, and may skip unsupported assertions (see `--verbose` for parse stats).

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
- `--no-preprocess`: disable preprocessing
- `--verify`: verify the decoded solution
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
python3 codes/main.py benchmarks/smt_0_10/calypto/problem-005596.cvc.1.smt2 --verify
```

### Build a WCNF file without further processing

```bash
python3 codes/main.py benchmarks/qplib_fully_passed/QPLIB_0067.qplib -o out.wcnf
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

With `--verbose`, the tool prints additional encoding and timing details. With `--verify`, it also checks whether the decoded assignment satisfies the original constraints and whether the reported objective is consistent.

## References

If you use NLIPSat, please cite the following papers:

- Zhengling Yangli, Zhifei Zheng, Sami Cherif, Rui Sá Shibasaki, and Chu-Min Li. *NLIPSat: Satisfiability-Based Nonlinear Integer Programming Encoding Toolkit*. In Proceedings of the 29th International Conference on Theory and Applications of Satisfiability Testing (SAT 2026), LIPIcs, to appear.
- Zhifei Zheng, Sami Cherif, Rui Sá Shibasaki, Chu-Min Li, and Jialu Zhang. *Maximum Satisfiability Formulations for Nonlinear Integer Programming*. In Proceedings of the 19th European Conference on Logics in Artificial Intelligence (JELIA 2025), LNCS 16094, pp. 190--206, Springer, 2025. DOI: [10.1007/978-3-032-04590-4_14](https://doi.org/10.1007/978-3-032-04590-4_14).

