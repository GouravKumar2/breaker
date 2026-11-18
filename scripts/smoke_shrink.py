from app.shrink import shrink

# Fake bug: "fails" whenever the array contains a number >= 100 and n matches the count.
def still_fails(inp: str) -> bool:
    lines = inp.strip().splitlines()
    if len(lines) != 2:
        return False
    n, arr = int(lines[0]), lines[1].split()
    return len(arr) == n and any(int(x) >= 100 for x in arr)

# This "bug" is reproducible, but shrinking tokens breaks the n == len(arr) rule,
# so the shrinker must keep n and the array consistent via the predicate.
big = "8\n5 7 -3 250 9 1000000 4 2\n"
print("before:", repr(big))
print("after: ", repr(shrink(big, still_fails)))