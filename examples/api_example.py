from nlipsat import load_problem, EncodingConfig, build_wcnf, solve, verify_solution

problem = load_problem("examples/example4.json")  # .qplib .smt2 .cnf also accepted
cfg = EncodingConfig()  # use_decomposition=True for order decomp

wcnf, name2idx, vpool = build_wcnf(problem, "BIN", cfg)
print(len(wcnf.hard), len(wcnf.soft), wcnf.wght, wcnf.topw)

wcnf.to_file("example.wcnf")
result = solve(problem, encoding="BIN", config=cfg, solver="RC2")
ok, report = verify_solution(problem, result)
print(result["objective_value"], ok)
