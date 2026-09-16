# Proposal: collapse / validate web-client feature-flag env wiring

Status: draft / follow-up. Not for immediate merge — captures a hardening we
want after the surgical fix ships.

## Problem

A web-client feature flag can be set through **two** env mechanisms:

- the bare name (e.g. `OH_ALLOW_USER_LLM_CONFIGURATION`), read by the OSS
  `DefaultWebClientConfigInjector._get_feature_flags()` default factory; and
- the `OH_WEB_CLIENT_FEATURE_FLAGS_<FIELD>` prefix, read by the enterprise path
  that actually produces the config on OHE installs.

The chart therefore has to set **both** names for a flag to take effect
everywhere — and it does for some (`HIDE_PERSONAL_WORKSPACES` sets both). But
it's easy to set one and silently default the other: there's no error, the flag
just falls back to its model default.

This bit BYOK: `allow_user_llm_configuration` shipped with only the bare
`OH_ALLOW_USER_LLM_CONFIGURATION`, so on OHE (which reads the prefixed name) the
flag stayed at its `True` default — BYOK-off never reached the frontend and the
"managed models only" restriction had no effect. Fixed surgically in `#707` by
adding the `OH_WEB_CLIENT_FEATURE_FLAGS_ALLOW_USER_LLM_CONFIGURATION` env.

## Proposed change (pick one or both)

1. **Single source of truth (app):** have the enterprise path and the OSS
   injector read the *same* env mechanism, so a flag is wired exactly one way.
   (Touches the injector + every flag's wiring across app + chart.)
2. **CI guard (chart) — cheaper, recommended first:** a test that renders the
   KOTS chart and asserts every `WebClientFeatureFlags` field that the chart
   intends to control has a corresponding `OH_WEB_CLIENT_FEATURE_FLAGS_<FIELD>`
   env present. Catches the "set the env, it silently defaults because the wrong
   path reads it" class at CI instead of in a customer's Admin Console.

## Why it's deferred

(1) is a cross-repo refactor; (2) is the lower-risk preventive but still bigger
than the one-line surgical env add. Neither blocks the multi-model rollout.

## Acceptance

- A feature flag set in the KOTS config provably reaches `feature_flags.*` in the
  web-client config (asserted in CI), or
- there is a single env mechanism for feature flags (no dual-name footgun).
