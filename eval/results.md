# Breaker eval results

Model: `gemini-3.5-flash-lite`. Problems: 1. Runs: 7. Reference validation: **on**.

Buggy variants are only counted if a witness input proves they differ from the correct solution. ERROR runs are excluded from the rates (they are retried on the next run); INCONCLUSIVE counts as a miss.

## Strategy comparison

| Strategy | Bug catch rate | False-alarm rate (correct solutions) | Inconclusive | Avg tests run | Avg agent tool calls | Avg LLM calls | Avg wall time (s)* |
|---|---|---|---|---|---|---|---|
| boundary | 100% (6/6) | 0% (0/1) | 0 | 15.6 | 4.0 | 5.4 | 42.1 |
| ALL | 100% (6/6) | 0% (0/1) | 0 | 15.6 | 4.0 | 5.4 | 42.1 |

## Catch rate by bug type

| Bug type | boundary | ALL |
|---|---|---|
| int32_overflow | 100% (1/1) | 100% (1/1) |
| missing_edge_case | 100% (1/1) | 100% (1/1) |
| off_by_one | 100% (2/2) | 100% (2/2) |
| wrong_loop_bound | 100% (2/2) | 100% (2/2) |

## Per problem (all strategies)

| Problem | Buggy variants | Caught | False alarms / correct runs |
|---|---|---|---|
| max_subarray | 6 | 100% (6/6) | 0/1 |

## Reference validation: before vs after (identical runs)

| Reference validation | Bug catch rate | False-alarm rate | Inconclusive (correct solutions) | Inconclusive (buggy) |
|---|---|---|---|---|
| off (before) | 83% (5/6) | 0% (0/1) | 0 | 0 |
| on (after) | 100% (6/6) | 0% (0/1) | 0 | 0 |

## Oracle confidence (reference validated against statement samples)

| Confidence | Runs | BROKEN verdicts |
|---|---|---|
| verified | 7 | 6 |

\* Wall time = real execution time + the original LLM latency of cached responses (replayed), so reruns report comparable numbers. Test suites are shared by all variants of a problem (identical prompts hit the same cache entry), so strategies are compared on identical inputs.
