# Azure validation run: 20260512-combined

This directory contains sanitized evidence from temporary Azure smoke runs for `geond-agent-protocol`.

![Geond Azure validation](geond_azure_validation.gif)

## What was validated

| Area | Result |
| --- | --- |
| Azure OpenAI | Created an S0 account and `text-embedding-3-small` `GlobalStandard` deployment in Korea Central. Geond embedded 10 messages through the `azure-openai` provider and ran a hybrid benchmark. |
| APIM gateway | Created an APIM Consumption instance, 3 backends, and a `geond-openai` API with subscriptions disabled for smoke validation. Policy application is now opt-in because the first REST policy attempt exceeded the terminal timeout. |
| VM local SLM | Created a temporary `Standard_B2s` Ubuntu VM and ran `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` over Korean/English retrieval probes. MRR was `0.8333`. |
| Cleanup | All `rg-geond-validate-*` resource groups were deleted. Final `az group list` check returned `[]`. |

## Cost inputs

| Signal | Value |
| --- | --- |
| VM SKU | `Standard_B2s` |
| VM retail signal | `0.052 USD/hour` for Linux Korea Central |
| Observed VM runtime | about `11.8` minutes |
| VM compute estimate | about `0.0102 USD` before storage/network/tax adjustments |
| Azure OpenAI SKU | `S0` |
| Embedding deployment | `GlobalStandard`, capacity `7` |
| APIM SKU | `Consumption` |

These values are for later cost modeling, not an invoice. Exact charges should be checked in Azure Cost Management.

## Artifacts

- [summary.json](summary.json)
- [azure_openai_benchmark.md](azure_openai_benchmark.md)
- [slm_vm_benchmark.json](slm_vm_benchmark.json)
- [geond_azure_validation.gif](geond_azure_validation.gif)
