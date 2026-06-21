Configuration files in this directory are safe examples.

- `external_services.json`: public service URLs and optional integration endpoints.
- `portal_catalog.json`: example models, bundles, custom pricing and optional LiteLLM team metadata.

For production:

1. Replace all localhost and example URLs.
2. Replace model ids with model names configured in your LiteLLM proxy.
3. If you use LiteLLM teams, set `team.team_id` to your real team id.
4. Keep secrets in `.env`, not in JSON files.

