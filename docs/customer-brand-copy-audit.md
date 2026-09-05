# Customer Brand Copy Audit

Branch-only audit. No production change, merge or deploy is authorised by this file.

## Verified behaviour

Saved Content Creator brand profiles are backend-owned and filtered by the authenticated `user_id`. Owner-brand seeding is owner-tier only.

## Customer-facing copy issue

`studio-v2.html` currently contains owner-specific examples and instructions including Zyia Creations, Feed the Feed and Spew Crew. The text itself is static UI copy, so customers can see those names even though they cannot access the owner's saved brand records.

## Repair target

Replace owner-specific examples with neutral wording, for example:

- Brand-name placeholder: `Your business or project name`
- Instruction notice: `Save each business or project as its own brand profile. The production engine follows the selected brand's voice, palette, logo and references.`

Do not remove the backend account isolation or owner-only seed capability.

## Acceptance criteria

- Normal customer UI contains no references to Emma/ADG brands in the brand-profile instructions.
- Customer sees only their own backend profiles.
- Owner account retains owner-only preset capability.
- Public marketing/footer references can be reviewed separately; this repair is scoped to private/workspace brand instructions.
