import random, sys
r = random.Random(int(sys.argv[1]))
n = r.choice([1, 2, 3, 4, 6, 10, 25])
lim = r.choice([2, 5, 100, 10**9])
mode = r.choice(["rand", "rand", "inc", "dup", "dec"])
if mode == "rand":
    a = [r.randint(-lim, lim) for _ in range(n)]
elif mode == "inc":
    a = sorted(r.sample(range(-lim, lim + 1), min(n, 2 * lim + 1)))
    n = len(a)
elif mode == "dup":
    v = r.randint(-lim, lim); a = [v] * n
else:
    a = sorted([r.randint(-lim, lim) for _ in range(n)], reverse=True)
print(n)
print(*a)
