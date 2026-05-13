# Continue LSP Reference Workflow

Use this workflow when Continue or another MCP-capable editor needs to enrich
Geond with language-server references before asking `get_symbol_context`.

1. Make sure a stdio language server is available for the target language.

   ```bash
   pyright-langserver --version
   typescript-language-server --version
   ```

2. Collect references with Geond's built-in profile selection.

   ```bash
   uv run geond collect-lsp-references examples/python_service/service.py \
     --line 4 \
     --character 5 \
     --workspace-root examples/python_service \
     --server-profile auto \
     --target-qualified-name service.build_answer \
     --output references.json
   ```

3. Import the result into the workspace used by the MCP client.

   ```bash
   uv run geond import-lsp-references <workspace-id-or-uri> references.json
   ```

For a one-step flow, replace the output/import pair with
`--import-workspace-id <workspace-id-or-uri>`. Continue can keep using the same
Geond MCP server configuration from [continue_config.yaml](continue_config.yaml);
this workflow only adds a pre-query enrichment command.