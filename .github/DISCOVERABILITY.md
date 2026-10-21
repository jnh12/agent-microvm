# Discoverability rollout

The README and `docs/` guides introduce the project using its purpose: AI agent
sandboxes and isolated code execution in the user's AWS account. Keep `asbox`
(package), `agent_sandbox` (import), and `asb` (CLI) identified together.

## Repository settings after review

These are repository-wide settings, not branch content. Apply them when the
documentation change is accepted.

Suggested About description:

> Run AI agents and untrusted code in isolated AWS microVM sandboxes. Python SDK, CLI, and MCP server with suspend/resume.

Suggested topics (preserve any other relevant existing topics):

```text
ai-agents sandbox code-execution microvm aws aws-lambda python mcp mcp-server model-context-protocol
```

See [GitHub's topics guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics).

## Publishing and distribution

- Release through the existing package publishing process after merge to expose
  the new description, keywords, and project URLs on PyPI. This branch does not
  bump the package version or publish a release.
- Use the four guides in `docs/` as the initial content for a documentation site.
  Their front matter includes titles and descriptions. Once a site is built and
  verified, add its URL to About and package metadata and connect Search Console.
  Do not advertise a site URL before it is live.
- Consider a relevant submission to
  [Awesome Agent Sandbox](https://github.com/yanmxa/awesome-agent-sandbox), following
  that repository's contribution rules. Directory submissions are separate
  outreach, not performed by this branch.

## Search intent and measurement

Initial targets: `AI agent sandbox`, `run untrusted Python code`, `AWS agent
sandbox`, `MCP sandbox server`, and `E2B alternative`. These are hypotheses based
on public search results and discussions, not measured monthly search volumes.

Examples of the underlying questions:

- [Self-hostable alternatives to E2B](https://www.reddit.com/r/LocalLLaMA/comments/1fy48b4/is_there_any_selfhostable_alternative_to_e2bs/)
- [Managing AWS access for AI agents](https://www.reddit.com/r/devops/comments/1wkvk6c/how_are_people_currently_managing_aws_access_for/)

Record a baseline and review visitors, clones, referring sites, and popular
content weekly using [GitHub Traffic](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-traffic-to-a-repository).
Its rolling window is 14 days. Once a documentation site is live, use Search
Console impressions, queries, clicks, and click-through rates to revise page
titles and prioritize new guides. GitHub traffic alone does not provide a
keyword-volume report.
