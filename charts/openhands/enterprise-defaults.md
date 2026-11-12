# Enterprise policy defaults

The chart sets these product policy defaults in `env`. An explicit `env` value
in your overlay replaces each default without creating a duplicate entry.

| Setting | Default | To restore the previous behaviour |
| --- | --- | --- |
| Personal LLM key editing | hidden: `OH_ALLOW_USER_LLM_CONFIGURATION: "false"` | `"true"` |
| Billing page | hidden: `HIDE_BILLING_PAGE: "true"` | `"false"` |
| Email changes | disabled: `EMAIL_CHANGE_ENABLED` and `OH_WEB_CLIENT_EMAIL_CHANGE_ENABLED` are `"false"` | set both to `"true"` |
| Super Admin dashboard | shown at `/super-admin`: `ENABLE_SUPER_ADMIN: "true"` | `"false"` |
| Automations page | shown only when the app has a service URL (`automationService.url` or `env.AUTOMATION_SERVICE_URL`) | `env.ENABLE_AUTOMATIONS: "true"` |

Enabling the bundled `automation` subchart (`automation.enabled: true`) does not by itself show the Automations page, because the chart does not derive the service URL. Set `automationService.url` as well.

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

- `ENABLE_SUPER_ADMIN` mounts the Super Admin API and shows the dashboard. On a
  fresh install, the first user to sign in becomes the first Super Admin and
  goes through the first-install setup. An upgraded install that already has
  users does not run the setup again.

## Structured feature flags

The app reads web client feature flags in one of two modes. If any
`OH_WEB_CLIENT_FEATURE_FLAGS_*` variable is set, the app ignores the plain
flags `OH_ALLOW_USER_LLM_CONFIGURATION`, `HIDE_BILLING_PAGE` and
`ENABLE_AUTOMATIONS`, and uses its own defaults for every structured flag that
is not set. An install in that mode must set these instead:

- `OH_WEB_CLIENT_FEATURE_FLAGS_ALLOW_USER_LLM_CONFIGURATION`
- `OH_WEB_CLIENT_FEATURE_FLAGS_HIDE_BILLING_PAGE`
- `OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_AUTOMATIONS`

The two email variables work in both modes.

The chart sets `OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_SUPER_ADMIN`, so the app is
in this mode by default. To keep the plain flags above working, the chart copies
the effective value of each one (`OH_ALLOW_USER_LLM_CONFIGURATION`,
`HIDE_BILLING_PAGE`, `ENABLE_AUTOMATIONS`, `ENABLE_SUPER_ADMIN`) into its
structured flag. Set the plain flag as before, or set the structured flag
explicitly to override the copy.

## Upgrading

An install that omits these values takes the new defaults on upgrade. Before
you upgrade, set each value whose previous behaviour you want to keep.
[examples/cloud-overrides.yaml](examples/cloud-overrides.yaml) restores all
of them.
