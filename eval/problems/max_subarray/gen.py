import random, sys
r = random.Random(int(sys.argv[1]))
n = r.choice([1, 2, 3, 5, 8, 20])
lim = r.choice([3, 10, 1000, 10**9])
mode = r.choice(["mixed", "neg", "pos", "mixed"])
lo, hi = {"mixed": (-lim, lim), "neg": (-lim, -1), "pos": (1, lim)}[mode]
print(n)
print(*[r.randint(lo, hi) for _ in range(n)])
