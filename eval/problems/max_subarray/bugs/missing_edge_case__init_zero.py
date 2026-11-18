n = int(input())
a = list(map(int, input().split()))
best = 0
cur = 0
for x in a:
    cur = max(0, cur + x)
    best = max(best, cur)
print(best)
