# These helpers run only when enterpriseSSO.enforceOnly is enabled. The flow has
# no cookie/password fallback: each client authorization reauthenticates at SSO.
ensure_sso_only_flow() {
  SSO_FLOW_ALIAS=openhands-enterprise-sso-only
  keycloak_get_optional "$KC_REALM/authentication/flows" || return 1
  SSO_FLOW_ID=$(printf '%s\n' "$RESPONSE" | jq -er --arg alias "$SSO_FLOW_ALIAS" \
    '[.[] | select(.alias == $alias)] | if length == 0 then "" elif length == 1 and .[0].description == "Managed by OpenHands enterpriseSSO.enforceOnly" then .[0].id else error("SSO flow ownership conflict") end') || return 1
  if [ -z "$SSO_FLOW_ID" ]; then
    jq -n --arg alias "$SSO_FLOW_ALIAS" '{alias:$alias,description:"Managed by OpenHands enterpriseSSO.enforceOnly",providerId:"basic-flow",topLevel:true,builtIn:false}' > /tmp/sso-only-flow.json || return 1
    keycloak_write POST "$KC_REALM/authentication/flows" /tmp/sso-only-flow.json || return 1
    keycloak_get_optional "$KC_REALM/authentication/flows" || return 1
    SSO_FLOW_ID=$(printf '%s\n' "$RESPONSE" | jq -er --arg alias "$SSO_FLOW_ALIAS" '.[] | select(.alias == $alias) | .id') || return 1
  fi
  keycloak_get_optional "$KC_REALM/authentication/flows/$SSO_FLOW_ALIAS/executions" || return 1
  SSO_EXEC_COUNT=$(printf '%s\n' "$RESPONSE" | jq -er 'length') || return 1
  if [ "$SSO_EXEC_COUNT" = 0 ]; then
    printf '%s\n' '{"provider":"identity-provider-redirector"}' > /tmp/sso-only-execution.json
    keycloak_write POST "$KC_REALM/authentication/flows/$SSO_FLOW_ALIAS/executions/execution" /tmp/sso-only-execution.json || return 1
    keycloak_get_optional "$KC_REALM/authentication/flows/$SSO_FLOW_ALIAS/executions" || return 1
  fi
  # Never adopt a flow with extra steps (for example a password/cookie fallback).
  printf '%s\n' "$RESPONSE" | jq -e 'length == 1 and .[0].providerId == "identity-provider-redirector"' >/dev/null || return 1
  SSO_EXEC_ID=$(printf '%s\n' "$RESPONSE" | jq -er '.[0].id') || return 1
  SSO_CONFIG_ID=$(printf '%s\n' "$RESPONSE" | jq -r '.[0].authenticationConfig // empty') || return 1
  printf '%s\n' "$RESPONSE" | jq '.[0] | .requirement = "REQUIRED"' > /tmp/sso-only-execution.json || return 1
  keycloak_write PUT "$KC_REALM/authentication/flows/$SSO_FLOW_ALIAS/executions" /tmp/sso-only-execution.json || return 1
  printf '%s\n' '{"alias":"openhands-enterprise-sso-only-redirect","config":{"defaultProvider":"enterprise_sso"}}' > /tmp/sso-only-config.json
  if [ -n "$SSO_CONFIG_ID" ]; then
    keycloak_write PUT "$KC_REALM/authentication/config/$SSO_CONFIG_ID" /tmp/sso-only-config.json || return 1
  else
    keycloak_write POST "$KC_REALM/authentication/executions/$SSO_EXEC_ID/config" /tmp/sso-only-config.json || return 1
  fi
}

apply_sso_only_client() {
  keycloak_get_optional "$KC_REALM/identity-provider/instances" || return 1
  # Unknown enabled login brokers must be restricted explicitly by their owner.
  # Do not overwrite their credentials or silently adopt customer configuration.
  printf '%s\n' "$RESPONSE" | jq -e '
    any(.[]; .alias == "enterprise_sso" and .enabled == true and .providerId == "saml")
    and all(.[]; .alias == "enterprise_sso" or .enabled == false or .linkOnly == true)' >/dev/null || return 1
  ensure_sso_only_flow || return 1
  CLIENT_QUERY=$(printf '%s' "$KEYCLOAK_CLIENT_ID" | jq -sRr @uri) || return 1
  keycloak_get_optional "$KC_REALM/clients?clientId=$CLIENT_QUERY" || return 1
  SSO_CLIENT_ID=$(printf '%s\n' "$RESPONSE" | jq -er 'if length == 1 then .[0].id else error("Expected one application client") end') || return 1
  keycloak_get_optional "$KC_REALM/clients/$SSO_CLIENT_ID" || return 1
  printf '%s\n' "$RESPONSE" | jq --arg flow "$SSO_FLOW_ID" \
    '.enabled = true | .directAccessGrantsEnabled = false | .authenticationFlowBindingOverrides.browser = $flow' > /tmp/sso-only-client.json || return 1
  keycloak_write PUT "$KC_REALM/clients/$SSO_CLIENT_ID" /tmp/sso-only-client.json || return 1
}
