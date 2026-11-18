n = int(input())
a = list(map(int, input().split()))
best = 1
cur = 1
for i in range(1, n):
    if a[i] > a[i - 1]:
        cur += 1
    else:
        best = max(best, cur)
        cur = 1
print(best)
