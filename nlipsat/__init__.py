"""
nlipsat – Python API for the NLIPSat research toolkit.

NLIPSat encodes bounded polynomial nonlinear integer programming (NLIP)
instances into weighted partial MaxSAT (WCNF) and solves them with modern
MaxSAT engines.  The package exposes five public entry points:

* :func:`load_problem` – read an instance from a supported file format.
* :class:`EncodingConfig` – configure encoding options (decomposition, etc.).
* :func:`build_wcnf` – build a PySAT WCNF formula from a problem dictionary.
* :func:`solve` – encode *and* solve in one call.
* :func:`verify_solution` – check the decoded solution against the original
  problem.

Quick start::

    from nlipsat import load_problem, EncodingConfig, \\
                        build_wcnf, solve, verify_solution

    problem = load_problem("instance.qplib")
    cfg = EncodingConfig(use_decomposition=True)

    wcnf, name2idx, vpool = build_wcnf(problem, "BIN", cfg)
    wcnf.to_file("output.wcnf")

    result = solve(problem, encoding="BIN", config=cfg, solver="RC2")
    ok, report = verify_solution(problem, result)
"""

from .api import EncodingConfig, build_wcnf, load_problem, solve, verify_solution

__all__ = [
    "EncodingConfig",
    "build_wcnf",
    "load_problem",
    "solve",
    "verify_solution",
]
