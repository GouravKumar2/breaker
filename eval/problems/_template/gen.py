# Prints ONE random valid input for seed = int(sys.argv[1]). Mix tiny sizes, tiny and huge values.
# Used only to prove that buggy variants really differ from the correct solution (kills "equivalent mutants").
import random, sys
r = random.Random(int(sys.argv[1]))
n = r.choice([1, 2, 3, 5, 8, 20])
lim = r.choice([3, 10, 10**9])
print(n)
print(*[r.randint(-lim, lim) for _ in range(n)])
