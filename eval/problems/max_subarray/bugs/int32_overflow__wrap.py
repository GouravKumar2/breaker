def i32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v

n = int(input())
a = list(map(int, input().split()))
best = a[0]
cur = a[0]
for x in a[1:]:
    cur = max(x, i32(cur + x))
    best = max(best, cur)
print(best)
