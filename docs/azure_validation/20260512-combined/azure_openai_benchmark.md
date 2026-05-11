# Azure OpenAI Geond Benchmark

Temporary Azure OpenAI validation created an S0 account in Korea Central and deployed `text-embedding-3-small` as a `GlobalStandard` deployment with capacity `7`.

Observed deployment limits from Azure:

| Limit | Count | Renewal |
| --- | ---: | --- |
| request | 7 | 10 seconds |
| token | 7000 | 60 seconds |

Geond benchmark evidence:

| Check | Result |
| --- | --- |
| Messages embedded through Azure OpenAI | 10 |
| `embed-messages` provider | `azure-openai` |
| Benchmark mode | `hybrid` |
| Successful retrieval query | `app_context` |
| Results | 1 |
| Min latency | 351.788 ms |
| Avg latency | 1349.138 ms |
| Max latency | 2346.489 ms |

Notes:

- The first capacity-1 deployment hit Azure OpenAI request rate limiting (`1 request / 10 seconds`), which validated that the code path was reaching the real service. The script now provisions capacity `7` for the smoke run.
- The Korean CLI query row from the Windows PowerShell path was omitted from this public summary because the local console encoding mangled the displayed query text. Multilingual retrieval was separately validated on the B2s VM with MiniLM in `slm_vm_benchmark.json`.
