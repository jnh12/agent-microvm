# Hosted MCP and local MCP

Hosted endpoint: https://api.vmnest.cc/mcp (Streamable HTTP). Set up OAuth or a scoped bearer token at https://vmnest.cc/dashboard/mcp. Use the MCP client's normal authorization mechanism; do not paste tokens into an agent conversation. Revoke unused connections. A read-only connection lists read tools and cannot wake machines, run commands or mutate settings.

Start with vmnest_guide and tools/list. Tool inputs follow generated API schemas: many mutations take a body object, path parameters such as name are separate, and queries are separate properties. Tool results use {ok,data} or {ok,error}; check isError and the returned error code. computers_bulk returns per-item outcomes, so HTTP/MCP success alone is not batch success. Use stable request_key for supported operations and inspect after uncertain outcomes.

Workflow mapping: computers_list/computer_inspect -> discover; computer_create/computer_wake -> start; computer_exec -> shell; agent_send -> prompt; agent_run_inspect/agent_run_events -> outcome and reply; file_read/file_write -> base64 transfer; computer_shutdown -> stop; computer_delete with permanent=true -> irreversible deletion. computer_delete without permanent is shutdown, unlike CLI delete. profiles_*/profile_*, defaults_*, webhooks, machine webhooks and event endpoints cover account automation. Consult tool schemas for exact names and fields.

The hosted MCP cannot open a local terminal, read a laptop file, discover local provider credentials, or create a localhost port-forward on the caller's device. Use the CLI for local file transfers, provider credential discovery and forwarding. VM file content must be supplied explicitly to MCP. Billing purchases and interactive sign-in belong in the website.

`vmnest-mcp` installed with the mcp extra is the separate local/self-hosted stdio server. It uses local AWS/runtime configuration and sandbox_* tools. Do not present it as a proxy for the hosted account. It also exposes vmnest_guide and documentation resources, but hosted workflow tool names apply only to the hosted server.
