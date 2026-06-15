# inventree-agent-skills

A collection of agent skills for working with [InvenTree](https://inventree.org/) — open-source inventory management for makers.

Each skill is a self-contained folder with a `SKILL.md` (the procedure an agent follows) and optional `scripts/`.

## Skills

| Skill | Description |
|---|---|
| [inventree-print-tracking](./inventree-print-tracking) | Track 3D-printed parts: create build → allocate stock → consume materials → finish. |
| [inventree-build-export](./inventree-build-export) | Export all Build Orders to XLSX (one row per BO × build_line × consumed child): produced IPN + qty, consumed IPN + qty, source PO/BO. |

## Setup

All skills in this collection share the same two environment variables:

```bash
export INV_TOKEN="<your-inventree-api-token>"   # InvenTree REST API token
export INV_URL="https://inventree.example.com"   # base URL of your InvenTree instance
```

Get the API token from your InvenTree instance under **Settings → API Tokens**.

The `INV_URL` is also used as the `Referer` header for REST calls (the InvenTree server validates it).

## Adding a skill

1. Create a new directory: `mkdir my-new-skill/`
2. Add a `SKILL.md` with the required YAML frontmatter (`name`, `description`)
3. Add `scripts/` for any helper code
4. Update the skill table above
5. Open a PR

Keep skill descriptions short and trigger-phrase-rich so the right skill fires at the right time.

## Conventions

- `INV_TOKEN` and `INV_URL` are the only required env vars. Use them, don't hardcode.
- Skill names use lowercase-hyphen, prefixed with `inventree-` (e.g. `inventree-supplier-import`).
- Reference InvenTree endpoints by path, not full URL.

## License

MIT — see [LICENSE](./LICENSE).
