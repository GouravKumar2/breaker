n = int(input())
a = list(map(int, input().split()))
best = a[0]
cur = a[0]
for x in a[1:]:
    cur = max(x, cur + x)
    best = max(best, cur)
print(best)
