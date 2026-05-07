"""Core implementation of the ``nlipsat`` public API.

All public symbols are re-exported from :mod:`nlipsat.__init__`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CODES_DIR = _REPO_ROOT / "codes"

if str(_CODES_DIR) not in sys.path:
    sys.path.insert(0, str(_CODES_DIR))

from solver.config import EncodingConfig  # pyright: ignore[reportMissingImports] # noqa: E402
from solver.solve import build_and_solve as _build_and_solve  # pyright: ignore[reportMissingImports] # noqa: E402
from solver.solve import build_wcnf as _build_wcnf  # pyright: ignore[reportMissingImports] # noqa: E402
from tools.verify import verify_solution as _verify_solution  # pyright: ignore[reportMissingImports] # noqa: E402


def load_problem(path: str | Path, *, k: Optional[int] = None) -> Dict[str, Any]:
    """Load an NLIP instance from a supported file format.

    Parameters
    ----------
    path : str or Path
        Path to the input file.  Accepted extensions:

        * ``.json``  – native NLIPSat JSON format
        * ``.qplib`` – QPLIB format
        * ``.smt2``  – SMT-LIB 2 (QF_NIA) format
        * ``.cnf``   – DIMACS CNF (interpreted as Diverse SAT when *k* ≥ 2)
    k : int, optional
        Number of diverse models for Diverse SAT instances (*k* ≥ 2).
        Ignored for non-CNF inputs.

    Returns
    -------
    dict
        A problem dictionary with keys ``"variables"``, ``"objective"``,
        and ``"constraints"``.

        * ``variables`` maps each variable name (str) to
          ``{"lb": int, "ub": int}``.
        * ``objective`` has ``"sense"`` (``"max"`` or ``"min"``) and
          ``"terms"`` (list of ``{"c": number, "vars": {name: degree}}``).
        * ``constraints`` is a list of
          ``{"terms": [...], "sense": str, "rhs": number}``.
    """
    main_module = _load_cli_main()
    return main_module.load_problem(str(path), k=k)


def build_wcnf(
    problem: Dict[str, Any],
    encoding: str = "BIN",
    config: Optional[EncodingConfig] = None,
    *,
    verbose: bool = False,
):
    """Build a PySAT WCNF formula from an NLIP problem dictionary.

    Parameters
    ----------
    problem : dict
        Problem dictionary as returned by :func:`load_problem` or
        constructed manually.
    encoding : ``{"OH", "UNA", "BIN"}``, default ``"BIN"``
        Integer-to-Boolean encoding strategy:

        * ``"OH"``  – One-Hot
        * ``"UNA"`` – Unary
        * ``"BIN"`` – Binary (supports order decomposition)
    config : EncodingConfig, optional
        Encoding configuration.  Controls order decomposition
        (``use_decomposition``), weight normalization
        (``weight_gcd_normalize``), preprocessing, and more.  Defaults to
        ``EncodingConfig()`` with all default settings.
    verbose : bool, default False
        If *True*, internal log messages are printed to stdout.

    Returns
    -------
    wcnf : pysat.formula.WCNF
        The weighted partial MaxSAT formula.  Key fields:

        * ``hard``  – list of hard clauses (list of list of int)
        * ``soft``  – list of soft clauses
        * ``wght``  – list of soft-clause weights
        * ``topw``  – top weight (1 + sum of all soft weights)
    name2idx : dict[str, int]
        Maps each integer variable name to its 1-based index used in the
        encoding.
    vpool : pysat.formula.IDPool
        Identifier pool that tracks Boolean variable assignments, useful
        for decoding solver results back to integer values.
    """
    cfg = config or EncodingConfig()
    with _maybe_silence_stdout(verbose):
        return _build_wcnf(problem, encoding, cfg)


def solve(
    problem: Dict[str, Any],
    *,
    encoding: str = "BIN",
    config: Optional[EncodingConfig] = None,
    solver: str = "RC2",
    verbose: bool = False,
) -> Dict[str, Any]:
    """Encode and solve an NLIP problem with a MaxSAT backend.

    This is a convenience wrapper that calls :func:`build_wcnf` followed
    by the selected solver.

    Parameters
    ----------
    problem : dict
        Problem dictionary (see :func:`load_problem`).
    encoding : ``{"OH", "UNA", "BIN"}``, default ``"BIN"``
        Encoding strategy.
    config : EncodingConfig, optional
        Encoding configuration (see :func:`build_wcnf`).
    solver : ``{"RC2", "MAXHS", "WMAXCDCL", "OPENWBO"}``, default ``"RC2"``
        MaxSAT solver backend.  ``"RC2"`` uses the PySAT built-in solver;
        the others require the corresponding binary on the system.
    verbose : bool, default False
        If *True*, print internal log messages.

    Returns
    -------
    dict
        Result dictionary with keys:

        * ``"encoding"``         – encoding name used
        * ``"solver"``           – solver name used
        * ``"solver_status"``    – ``"OPTIMAL"``, ``"FEASIBLE"``, or
          ``"TIMEOUT"``
        * ``"objective_value"``  – decoded NLIP objective value (int or float)
        * ``"maxsat_cost"``      – raw MaxSAT cost
        * ``"assignment"``       – list of signed Boolean literals
        * ``"name2idx"``         – variable-name map
        * ``"vpool"``            – identifier pool
        * ``"num_variables"``    – number of Boolean variables
        * ``"num_hard_clauses"`` – number of hard clauses
        * ``"num_soft_clauses"`` – number of soft clauses
        * ``"top_weight"``       – WCNF top weight
        * ``"timings"``          – dict of runtime breakdowns (seconds)
    """
    cfg = config or EncodingConfig()
    with _maybe_silence_stdout(verbose):
        return _build_and_solve(problem, encoding, cfg, solver=solver)


def verify_solution(
    problem: Dict[str, Any],
    result: Dict[str, Any],
    *,
    encoding: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Verify a decoded solver result against the original NLIP problem.

    Checks that every hard constraint is satisfied and that the reported
    objective value matches the recomputed value.

    Parameters
    ----------
    problem : dict
        Original problem dictionary.
    result : dict
        Result dictionary as returned by :func:`solve`.
    encoding : str, optional
        Encoding name.  If omitted, taken from ``result["encoding"]``.

    Returns
    -------
    ok : bool
        *True* if all constraints are satisfied and the objective is
        consistent.
    report : dict
        Detailed verification report with keys ``"valid"``, ``"errors"``,
        ``"warnings"``, ``"var_values"``, ``"constraint_checks"``,
        ``"computed_objective"``, and ``"reported_objective"``.
    """
    enc = encoding or result.get("encoding")
    if not enc:
        raise ValueError("encoding must be provided or present in result")
    return _verify_solution(problem, result, enc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_cli_main():
    main_path = _CODES_DIR / "main.py"
    spec = importlib.util.spec_from_file_location("_nlipsat_cli_main", main_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load NLIPSat main module from {main_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _maybe_silence_stdout(verbose: bool):
    if verbose:
        return contextlib.nullcontext()
    return contextlib.redirect_stdout(io.StringIO())
