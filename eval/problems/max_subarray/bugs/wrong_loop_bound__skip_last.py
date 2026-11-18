n = int(input())
a = list(map(int, input().split()))
best = a[0]
cur = a[0]
for i in range(1, n - 1):
    cur = max(a[i], cur + a[i])
    best = max(best, cur)
print(best)
