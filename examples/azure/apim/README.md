# APIM AI Gateway Sample

This sample shows a practical Azure API Management policy for putting Geond
embedding and model calls behind an AI gateway.

## Backends

Create these APIM backends before applying
`geond-openai-gateway-policy.xml`:

- `geond-foundry-openai`: Azure OpenAI or Foundry Models endpoint.
- `geond-embeddings`: embeddings deployment used by semantic cache lookup.
- `geond-content-safety`: Azure AI Content Safety endpoint.

Grant the APIM managed identity `Cognitive Services User` on the Azure OpenAI
or Foundry account and the embeddings resource.

## Geond Provider Config

Use the gateway as an OpenAI-compatible provider:

```bash
GEOND_EMBEDDING_PROVIDER=gateway
GEOND_EMBEDDING_BASE_URL=https://<apim-name>.azure-api.net/openai/v1
GEOND_EMBEDDING_API_KEY=<apim-subscription-key>
GEOND_EMBEDDING_MODEL=text-embedding-3-small
```

For fully local privacy tests, keep `GEOND_PRIVACY_MODE=local-only`; Geond will
block the gateway provider before any network call.

## Policy Notes

The policy combines managed identity auth, semantic cache lookup/store, token
limits, request rate limiting, content safety, backend routing, and token
metrics. It follows the APIM AI gateway policy order used in Microsoft guidance.

Official references:

- <https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities>
- <https://learn.microsoft.com/azure/api-management/azure-openai-token-limit-policy>
- <https://learn.microsoft.com/azure/api-management/azure-openai-enable-semantic-caching>
