# Enterprise policy defaults

The chart sets these product policy defaults in `env`. An explicit `env` value
in your overlay replaces each default without creating a duplicate entry.

| Setting | Default | To restore the previous behaviour |
| --- | --- | --- |
| Personal LLM key editing | hidden: `OH_ALLOW_USER_LLM_CONFIGURATION: "false"` | `"true"` |
| Billing page | hidden: `HIDE_BILLING_PAGE: "true"` | `"false"` |
| Email changes | disabled: `EMAIL_CHANGE_ENABLED` and `OH_WEB_CLIENT_EMAIL_CHANGE_ENABLED` are `"false"` | set both to `"true"` |
| Personal workspaces | hidden once a user belongs to an organization: `HIDE_PERSONAL_WORKSPACES: "true"` | `"false"` |
| Automations page | shown only when the app has a service URL (`automationService.url`, `env.AUTOMATION_SERVICE_URL`, or the bundled service's URL when `automation.enabled` is true) | `env.ENABLE_AUTOMATIONS: "true"` |

## What the flags do

- `OH_ALLOW_USER_LLM_CONFIGURATION` hides the key and custom model inputs in
  the settings UI. Keys that users saved before stay in use. The same flag
  also hides the custom key inputs and the provider connection selector on
  the organization form.
- When the legacy LiteLLM proxy is off (`ENABLE_LEGACY_LITELLM: "false"`),
  the app shows key editing anyway, because a user key is then the only model
  source.
- `HIDE_BILLING_PAGE` changes only page visibility. It does not enable or
  disable billing.
- With `EMAIL_CHANGE_ENABLED: "false"`, the email change API returns 403.
- `HIDE_PERSONAL_WORKSPACES` also changes the server. The organization list
  marks a personal workspace hidden for a user who belongs to a team
  organization, and sign-in moves a member of the default organization out of
  their personal workspace and into it. The server reads the plain variable in both
  flag modes.
- While personal workspaces are hidden, the app does not switch a user into
  their personal workspace. Conversations stored there cannot be opened from
  the UI, and a link to one opens the launcher instead. Nothing is deleted:
  with `HIDE_PERSONAL_WORKSPACES: "false"` they open again.

## Structured feature flags

The app reads web client feature flags in one of two modes. If any
`OH_WEB_CLIENT_FEATURE_FLAGS_*` variable is set, the app ignores the plain
flags `OH_ALLOW_USER_LLM_CONFIGURATION`, `HIDE_BILLING_PAGE` and
`ENABLE_AUTOMATIONS`, and uses its own defaults for every structured flag that
is not set. An install in that mode must set these instead:

- `OH_WEB_CLIENT_FEATURE_FLAGS_ALLOW_USER_LLM_CONFIGURATION`
- `OH_WEB_CLIENT_FEATURE_FLAGS_HIDE_BILLING_PAGE`
- `OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_AUTOMATIONS`
- `OH_WEB_CLIENT_FEATURE_FLAGS_HIDE_PERSONAL_WORKSPACES`, together with the
  plain `HIDE_PERSONAL_WORKSPACES` that the server reads

The two email variables work in both modes.

## Upgrading

An install that omits these values takes the new defaults on upgrade. Before
you upgrade, set each value whose previous behaviour you want to keep.
[examples/cloud-overrides.yaml](examples/cloud-overrides.yaml) restores all
of them.
