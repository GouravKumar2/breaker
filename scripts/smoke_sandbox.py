from app.sandbox import Program, outputs_match

GOOD = "n=int(input()); a=list(map(int,input().split())); print(sum(a))"
LOOP = "while True: pass"
BOOM = "print(1/0)"
HOG = "x=[0]*(10**9); print(len(x))"

for name, code in [("good", GOOD), ("infinite loop", LOOP), ("crash", BOOM), ("memory hog", HOG)]:
    p = Program(code, "python")
    r = p.run("3\n1 2 3\n", timeout=2)
    print(f"{name:14} -> {r.status:14} {r.runtime_ms:7.0f}ms stdout={r.stdout!r}")
    p.cleanup()

a = Program(GOOD, "python"); b = Program("n=int(input()); a=input().split(); print(sum(map(int,a)))  \n", "python")
print("outputs_match:", outputs_match(a.run("3\n1 2 3\n").stdout, b.run("3\n1 2 3\n").stdout))
a.cleanup(); b.cleanup()