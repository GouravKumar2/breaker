# Breaker eval results

Model: `gemini-3.5-flash-lite`. Problems: 1. Runs: 7.

Buggy variants are only counted if a witness input proves they differ from the correct solution. ERROR runs are excluded from the rates (they are retried on the next run); INCONCLUSIVE counts as a miss.

## Strategy comparison

| Strategy | Bug catch rate | False-alarm rate (correct solutions) | Inconclusive | Avg tests run | Avg agent tool calls | Avg LLM calls | Avg wall time (s)* |
|---|---|---|---|---|---|---|---|
| boundary | 83% (5/6) | 0% (0/1) | 0 | 16.0 | 3.9 | 5.4 | 21.3 |
| ALL | 83% (5/6) | 0% (0/1) | 0 | 16.0 | 3.9 | 5.4 | 21.3 |

## Catch rate by bug type

| Bug type | boundary | ALL |
|---|---|---|
| int32_overflow | 0% (0/1) | 0% (0/1) |
| missing_edge_case | 100% (1/1) | 100% (1/1) |
| off_by_one | 100% (2/2) | 100% (2/2) |
| wrong_loop_bound | 100% (2/2) | 100% (2/2) |

## Per problem (all strategies)

| Problem | Buggy variants | Caught | False alarms / correct runs |
|---|---|---|---|
| max_subarray | 6 | 83% (5/6) | 0/1 |

\* Wall time = real execution time + the original LLM latency of cached responses (replayed), so reruns report comparable numbers. Test suites are shared by all variants of a problem (identical prompts hit the same cache entry), so strategies are compared on identical inputs.
