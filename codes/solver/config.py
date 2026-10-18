import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SOLVER_DIR = str(_PROJECT_ROOT / "solvers" / "maxsat")


@dataclass
class EncodingConfig:
    """Configuration for the NLIPSat encoding and solving pipeline.

    All fields have sensible defaults and can be overridden at construction
    time, e.g. ``EncodingConfig(use_decomposition=True, decomp_threshold=4)``.

    Attributes
    ----------
    encoding : str
        Legacy metadata. Select the encoding with the API or CLI argument.
    use_decomposition : bool
        Enable order decomposition for high-degree terms in BIN encoding.
    decomp_threshold : int
        Minimum polynomial degree that triggers decomposition.
    decomp_strategy : str
        Decomposition strategy (``"sequential"`` or ``"binary_tree"``).
    decomp_exact : bool
        Use exact multiplication semantics (*False* = relaxed).
    decomp_shared : bool
        Enable shared substructure caching across terms.
    weight_gcd_normalize : bool
        Divide all soft weights by their GCD (improves core-guided solving).
    save_wcnf : bool
        Legacy metadata. Export is controlled by the explicit output path.
    workdir : str
        Working directory for temporary files (default: system temp dir,
        override via ``NLIP_WORKDIR`` env var).
    maxhs_path, wmaxcdcl_path, openwbo_path : str
        Paths to external MaxSAT solver binaries (override via
        ``NLIP_MAXHS``, ``NLIP_WMAXCDCL``, ``NLIP_OPENWBO`` env vars).
    rc2_timeout : float
        RC2 subprocess time limit in seconds, including startup (0 = unlimited).
    external_solver_timeout : float
        Timeout in seconds for external solvers (0 = no timeout).
    enable_preprocess : bool
        Enable optional bound propagation and monotone variable fixing.
        Exact normalization and variable shifts always run.
    preprocess_integerize : bool
        Convert fractional coefficients to integers during preprocessing.
    preprocess_scale_limit : int
        Maximum scaling factor allowed during integerization.
    use_mapping_shift : bool
        Mapping option for direct encoder calls. The solve pipeline always
        normalizes variable lower bounds before encoding.
    encoding_deadline : float
        Absolute ``time.time()`` deadline for the encoding phase
        (0 = no deadline).
    """

    encoding: str = "OH"

    use_decomposition: bool = False
    decomp_threshold: int = 3
    decomp_strategy: str = "sequential"
    decomp_exact: bool = True
    decomp_shared: bool = True

    weight_gcd_normalize: bool = True

    save_wcnf: bool = True
    workdir: str = os.environ.get("NLIP_WORKDIR", tempfile.gettempdir())

    maxhs_path: str = os.environ.get("NLIP_MAXHS", os.path.join(_SOLVER_DIR, "maxhs"))
    wmaxcdcl_path: str = os.environ.get("NLIP_WMAXCDCL", os.path.join(_SOLVER_DIR, "wmaxcdcl"))
    openwbo_path: str = os.environ.get("NLIP_OPENWBO", os.path.join(_SOLVER_DIR, "openwbo"))
    external_solver_timeout: float = 0.0
    rc2_timeout: float = 0.0

    enable_preprocess: bool = True
    preprocess_integerize: bool = True
    preprocess_scale_limit: int = 1000000

    use_mapping_shift: bool = False

    encoding_deadline: float = 0.0
