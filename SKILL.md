---
name: skill-publisher
description: Package an Agent Skill once for GitHub, Gitee, WorkBuddy and Qianwen, with optional Tencent SkillHub community submission; publish verified release assets or assist marketplace submission. Use when preparing or distributing a skill across these channels.
---

Use `scripts/publish.py` with Python 3.9 or newer. Resolve this script relative to this skill directory, not the user's current project. Basic use needs only the standard library; complex YAML frontmatter requires PyYAML.

Read [configuration](references/config.md) to prepare a source profile. Read [platform instructions](references/platforms.md) for the selected destinations. Keep account credentials in environment variables or the GitHub CLI login.

1. Create or inspect a profile. Confirm the source skill, version, public metadata, license, repository destinations and included files. Work archives never belong in release packages.
2. Run `build --config PROFILE`. Inspect `manifest.json`, package contents and generated submission JSON. The source allowlist is the only source of release files. Never push the source project's existing Git history.
3. Run `publish --release RELEASE` for a preview. Use `--platform NAME` to select a subset. The release works independently of its source directory after building.
4. When the user authorizes publication, run the same command with `--execute`. GitHub and Gitee create releases and verify assets. SkillHub submits through its documented community API and records the review receipt; it is distinct from the WorkBuddy market. WorkBuddy and Qianwen remain `prepared`; use the generated ZIP and submission JSON in their official portals. Use available browser tools to assist, and ask the user only for necessary login, identity verification or missing account details.
5. Run `status --release RELEASE`. After a marketplace confirms submission or publication, use `record` with its real HTTPS URL and concrete evidence. Upload completion alone does not mean market publication.

A failed GitHub or Gitee release can be retried. SkillHub submitting, submitted, published or uncertain states refuse a repeated POST for that version; check its receipt and portal first. The command compares remote assets before reusing them and refuses different content under the same filename. Change the version when package contents change. Do not overwrite remote assets to conceal a mismatch. Source license must be explicitly configured before public publication.
