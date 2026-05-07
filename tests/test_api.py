"""Comprehensive tests for the nlipsat public API."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlipsat import EncodingConfig, build_wcnf, load_problem, solve, verify_solution

EXAMPLE_JSON = os.path.join(os.path.dirname(__file__), "..", "examples", "example4.json")

HAND_BUILT_PROBLEM = {
    "variables": {
        "X1": {"lb": 0, "ub": 2},
        "X2": {"lb": 0, "ub": 2},
    },
    "objective": {
        "sense": "max",
        "terms": [{"c": 1, "vars": {"X1": 1, "X2": 1}}],
    },
    "constraints": [
        {
            "terms": [
                {"c": 1, "vars": {"X1": 1}},
                {"c": 1, "vars": {"X2": 1}},
            ],
            "sense": "<=",
            "rhs": 3,
        }
    ],
}

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


# ── Test 1: load_problem from JSON ─────────────────────────────────────────
print("\n[1] load_problem (JSON)")
problem = load_problem(EXAMPLE_JSON)
check("returns dict", isinstance(problem, dict))
check("has 'variables'", "variables" in problem)
check("has 'objective'", "objective" in problem)
check("has 'constraints'", "constraints" in problem)
check("X1 present", "X1" in problem["variables"])
check("X1 bounds", problem["variables"]["X1"] == {"lb": 0, "ub": 2})

# ── Test 2: build_wcnf with all three encodings ───────────────────────────
for enc in ("OH", "UNA", "BIN"):
    print(f"\n[2-{enc}] build_wcnf encoding={enc}")
    cfg = EncodingConfig()
    wcnf, name2idx, vpool = build_wcnf(problem, enc, cfg)
    check("wcnf has hard", hasattr(wcnf, "hard") and len(wcnf.hard) > 0)
    check("wcnf has soft", hasattr(wcnf, "soft") and len(wcnf.soft) > 0)
    check("wcnf has wght", hasattr(wcnf, "wght") and len(wcnf.wght) > 0)
    check("wcnf has topw", hasattr(wcnf, "topw") and wcnf.topw > 0)
    check("name2idx is dict", isinstance(name2idx, dict))
    check("name2idx has X1", "X1" in name2idx)
    check("vpool exists", vpool is not None)

# ── Test 3: BIN specific values (paper reference) ─────────────────────────
print("\n[3] build_wcnf BIN – paper reference values")
cfg = EncodingConfig()
wcnf, name2idx, vpool = build_wcnf(problem, "BIN", cfg)
check("18 hard clauses", len(wcnf.hard) == 18, f"got {len(wcnf.hard)}")
check("4 soft clauses", len(wcnf.soft) == 4, f"got {len(wcnf.soft)}")
check("wght = [1,2,2,4]", wcnf.wght == [1, 2, 2, 4], f"got {wcnf.wght}")
check("topw = 10", wcnf.topw == 10, f"got {wcnf.topw}")

# ── Test 4: to_file export ─────────────────────────────────────────────────
print("\n[4] WCNF to_file export")
with tempfile.NamedTemporaryFile(suffix=".wcnf", delete=False) as f:
    tmp_path = f.name
wcnf.to_file(tmp_path)
check("file created", os.path.exists(tmp_path))
check("file non-empty", os.path.getsize(tmp_path) > 0)
with open(tmp_path) as f:
    first_line = f.readline()
check("starts with p wcnf", first_line.startswith("p wcnf"))
os.unlink(tmp_path)

# ── Test 5: solve with RC2 ─────────────────────────────────────────────────
print("\n[5] solve (RC2, BIN)")
result = solve(problem, encoding="BIN", config=EncodingConfig(), solver="RC2")
check("result is dict", isinstance(result, dict))
check("has objective_value", "objective_value" in result)
check("objective_value = 2", result["objective_value"] == 2, f"got {result['objective_value']}")
check("has assignment", "assignment" in result and len(result["assignment"]) > 0)
check("has encoding", result.get("encoding") == "BIN")
check("has solver", result.get("solver") == "RC2")
check("has num_hard_clauses", "num_hard_clauses" in result)
check("has num_soft_clauses", "num_soft_clauses" in result)
check("has timings", "timings" in result)

# ── Test 6: solve with all three encodings ─────────────────────────────────
for enc in ("OH", "UNA", "BIN"):
    print(f"\n[6-{enc}] solve encoding={enc}")
    r = solve(problem, encoding=enc, config=EncodingConfig(), solver="RC2")
    check(f"objective = 2", r["objective_value"] == 2, f"got {r['objective_value']}")

# ── Test 7: verify_solution ───────────────────────────────────────────────
print("\n[7] verify_solution")
ok, report = verify_solution(problem, result)
check("verification passed", ok)
check("report is dict", isinstance(report, dict))
check("report valid", report.get("valid") is True)
check("no errors", len(report.get("errors", [])) == 0)

# ── Test 8: EncodingConfig with decomposition ─────────────────────────────
print("\n[8] EncodingConfig with decomposition")
cfg_decomp = EncodingConfig(use_decomposition=True, decomp_threshold=2)
wcnf_d, _, _ = build_wcnf(problem, "BIN", cfg_decomp)
check("decomp wcnf has hard", len(wcnf_d.hard) > 0)
check("decomp wcnf has soft", len(wcnf_d.soft) > 0)
r_decomp = solve(problem, encoding="BIN", config=cfg_decomp, solver="RC2")
check("decomp objective = 2", r_decomp["objective_value"] == 2, f"got {r_decomp['objective_value']}")

# ── Test 9: hand-built problem (no file) ──────────────────────────────────
print("\n[9] hand-built problem dict (no load_problem)")
wcnf_h, _, _ = build_wcnf(HAND_BUILT_PROBLEM, "BIN", EncodingConfig())
check("hand-built: 18 hard", len(wcnf_h.hard) == 18, f"got {len(wcnf_h.hard)}")
r_h = solve(HAND_BUILT_PROBLEM, encoding="BIN", solver="RC2")
check("hand-built: obj = 2", r_h["objective_value"] == 2, f"got {r_h['objective_value']}")

# ── Test 10: verbose mode ─────────────────────────────────────────────────
print("\n[10] verbose mode (should not crash)")
import io as _io
import contextlib
buf = _io.StringIO()
with contextlib.redirect_stdout(buf):
    build_wcnf(problem, "BIN", EncodingConfig(), verbose=True)
check("verbose produced output", len(buf.getvalue()) > 0)

# ── Summary ────────────────────────────────────────────────────────────────
total = passed + failed
print(f"\n{'='*50}")
print(f"Results: {passed}/{total} passed, {failed} failed")
if failed:
    sys.exit(1)
else:
    print("All tests passed!")
