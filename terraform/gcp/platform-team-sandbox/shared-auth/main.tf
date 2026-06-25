# -----------------------------------------------------------------------------
# Shared Keycloak Infrastructure for Staging Environments
# 
# Deploys a single Keycloak instance at auth.ohe-staging.platform-team.all-hands.dev
# that can be shared across multiple branch deployments, enabling:
#   - Single SAML/OIDC configuration with identity providers (Google, etc.)
#   - Consistent authentication across all staging branches
#   - Simplified identity provider management
# -----------------------------------------------------------------------------

terraform {
  required_version = ">= 1.0"
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Provider Configuration - uses local kubeconfig
# -----------------------------------------------------------------------------

provider "kubernetes" {
  config_path    = "~/.kube/config"
  config_context = var.kube_context
}

provider "helm" {
  kubernetes = {
    config_path    = "~/.kube/config"
    config_context = var.kube_context
  }
}

# -----------------------------------------------------------------------------
# Namespace for shared authentication services
# -----------------------------------------------------------------------------

resource "kubernetes_namespace" "shared_auth" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "shared-auth"
      "app.kubernetes.io/managed-by" = "terraform"
      environment                    = "staging"
    }
  }
}

# -----------------------------------------------------------------------------
# Secrets - these need to be created before Keycloak deployment
# -----------------------------------------------------------------------------

resource "kubernetes_secret" "keycloak_admin" {
  metadata {
    name      = "keycloak-admin"
    namespace = kubernetes_namespace.shared_auth.metadata[0].name
  }

  data = {
    "admin-password" = var.keycloak_admin_password
  }

  type = "Opaque"
}

resource "kubernetes_secret" "postgres_password" {
  metadata {
    name      = "postgres-password"
    namespace = kubernetes_namespace.shared_auth.metadata[0].name
  }

  data = {
    username = var.postgres_username
    password = var.postgres_password
  }

  type = "Opaque"
}

# -----------------------------------------------------------------------------
# Secret for staging client credentials
# Branch deployments will reference this for their keycloak-realm secret
# -----------------------------------------------------------------------------

resource "kubernetes_secret" "staging_client" {
  metadata {
    name      = "keycloak-staging-client"
    namespace = kubernetes_namespace.shared_auth.metadata[0].name
  }

  data = {
    "realm-name"    = var.realm_name
    "client-id"     = var.client_id
    "client-secret" = var.client_secret
  }

  type = "Opaque"
}

# -----------------------------------------------------------------------------
# Shared Keycloak Helm Release (using codecentric/keycloakx chart)
# 
# Note: Using codecentric/keycloakx instead of Bitnami because Bitnami
# images are currently unavailable on Docker Hub. The codecentric chart
# uses official quay.io/keycloak/keycloak images.
# -----------------------------------------------------------------------------

resource "helm_release" "keycloak" {
  name       = "keycloak"
  namespace  = kubernetes_namespace.shared_auth.metadata[0].name
  repository = "https://codecentric.github.io/helm-charts"
  chart      = "keycloakx"
  version    = var.keycloak_chart_version

  values = [
    templatefile("${path.module}/values-keycloak.yaml", {
      hostname          = var.keycloak_hostname
      postgres_host     = var.postgres_host
      postgres_database = var.postgres_database
      tls_secret_name   = var.tls_secret_name
      ingress_class     = var.ingress_class
    })
  ]

  depends_on = [
    kubernetes_secret.keycloak_admin,
    kubernetes_secret.postgres_password
  ]
}

# -----------------------------------------------------------------------------
# ConfigMap for realm configuration template
# This can be used by branch deployments to configure their clients
# SAML SSO placeholders are substituted with terraform variables
# -----------------------------------------------------------------------------

resource "kubernetes_config_map" "realm_template" {
  metadata {
    name      = "keycloak-realm-template"
    namespace = kubernetes_namespace.shared_auth.metadata[0].name
  }

  data = {
    "realm-template.json" = replace(
      replace(
        file("${path.module}/realm-template.json"),
        "$${SAML_SSO_URL}",
        var.saml_sso_url
      ),
      "$${SAML_SIGNING_CERTIFICATE}",
      var.saml_signing_certificate
    )
  }
}

# -----------------------------------------------------------------------------
# ConfigMap for realm setup script
# -----------------------------------------------------------------------------

resource "kubernetes_config_map" "realm_setup_script" {
  metadata {
    name      = "keycloak-realm-setup"
    namespace = kubernetes_namespace.shared_auth.metadata[0].name
  }

  data = {
    "setup-realm.sh" = <<-EOF
      #!/bin/sh
      set -e
      
      # Install jq if not present (alpine/curl image should have it, but just in case)
      if ! command -v jq &> /dev/null; then
        echo "Installing jq..."
        apk add --no-cache jq
      fi
      
      echo "============================================"
      echo "Keycloak Realm Setup"
      echo "============================================"
      echo "KEYCLOAK_URL: $KEYCLOAK_URL"
      echo "REALM_NAME: $REALM_NAME"
      echo "CLIENT_ID: $CLIENT_ID"
      echo "============================================"
      
      echo ""
      echo "Waiting for Keycloak to be ready..."
      RETRY_COUNT=0
      MAX_RETRIES=60
      until curl --output /dev/null --silent --head --fail "$KEYCLOAK_URL/realms/master"; do
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
          echo "ERROR: Keycloak not ready after $MAX_RETRIES attempts"
          exit 1
        fi
        echo "Keycloak not ready (attempt $RETRY_COUNT/$MAX_RETRIES), waiting 10s..."
        sleep 10
      done
      echo "Keycloak is ready!"
      
      # Get admin token
      echo ""
      echo "Getting admin access token..."
      TOKEN_RESPONSE=$(curl -s -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli" \
        -d "grant_type=password" \
        -d "username=admin" \
        -d "password=$KEYCLOAK_ADMIN_PASSWORD")
      
      ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
      
      if [ "$ACCESS_TOKEN" = "null" ] || [ -z "$ACCESS_TOKEN" ]; then
        echo "ERROR: Failed to get admin token"
        echo "Response: $TOKEN_RESPONSE"
        exit 1
      fi
      echo "Got access token successfully"
      
      # Check if realm exists
      echo ""
      echo "Checking if realm '$REALM_NAME' exists..."
      REALM_EXISTS=$(curl -s -o /dev/null -w "%%{http_code}" \
        "$KEYCLOAK_URL/admin/realms/$REALM_NAME" \
        -H "Authorization: Bearer $ACCESS_TOKEN")
      
      # Prepare realm template with client secret (needed for both create and sync)
      jq --arg secret "$CLIENT_SECRET" \
         '.clients[] | select(.clientId == "allhands") | .secret = $secret' \
         /config/realm-template.json > /dev/null
      
      # Create full realm.json with proper client secret for allhands client
      jq --arg secret "$CLIENT_SECRET" \
         '(.clients[] | select(.clientId == "allhands")).secret = $secret' \
         /config/realm-template.json > /tmp/realm.json
      
      if [ "$REALM_EXISTS" = "404" ]; then
        echo "Realm does not exist, creating: $REALM_NAME"
        
        echo "Realm configuration:"
        jq '.realm, .clients[].clientId' /tmp/realm.json
        
        RESPONSE=$(curl -s -w "\n%%{http_code}" -X POST "$KEYCLOAK_URL/admin/realms" \
          -H "Authorization: Bearer $ACCESS_TOKEN" \
          -H "Content-Type: application/json" \
          --data "@/tmp/realm.json")
        
        HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
        BODY=$(echo "$RESPONSE" | sed '$d')
        
        if [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "204" ]; then
          echo "Realm '$REALM_NAME' created successfully!"
        else
          echo "ERROR: Failed to create realm (HTTP $HTTP_CODE)"
          echo "Response: $BODY"
          exit 1
        fi
      else
        echo "Realm '$REALM_NAME' already exists (HTTP $REALM_EXISTS)"
        echo "Syncing configuration from template..."
        
        # =====================================================================
        # 1. Sync ALL clients from template (broker and allhands)
        # =====================================================================
        echo ""
        echo "--- Syncing client configurations ---"
        
        for template_client_id in $(jq -r '.clients[].clientId' /tmp/realm.json); do
          echo ""
          echo "Processing client: $template_client_id"
          
          # Get client UUID from Keycloak
          CLIENT_RESPONSE=$(curl -s "$KEYCLOAK_URL/admin/realms/$REALM_NAME/clients?clientId=$template_client_id" \
            -H "Authorization: Bearer $ACCESS_TOKEN")
          
          CLIENT_UUID=$(echo "$CLIENT_RESPONSE" | jq -r '.[0].id')
          
          # Get client config from template (with secret for allhands)
          if [ "$template_client_id" = "allhands" ]; then
            TEMPLATE_CLIENT=$(jq --arg secret "$CLIENT_SECRET" --arg cid "$template_client_id" \
              '.clients[] | select(.clientId == $cid) | .secret = $secret' /tmp/realm.json)
          else
            TEMPLATE_CLIENT=$(jq --arg cid "$template_client_id" \
              '.clients[] | select(.clientId == $cid)' /tmp/realm.json)
          fi
          
          if [ "$CLIENT_UUID" != "null" ] && [ -n "$CLIENT_UUID" ]; then
            echo "  Found client '$template_client_id' with UUID: $CLIENT_UUID"
            echo "  Updating client configuration..."
            
            UPDATE_RESPONSE=$(curl -s -w "\n%%{http_code}" -X PUT \
              "$KEYCLOAK_URL/admin/realms/$REALM_NAME/clients/$CLIENT_UUID" \
              -H "Authorization: Bearer $ACCESS_TOKEN" \
              -H "Content-Type: application/json" \
              --data "$TEMPLATE_CLIENT")
            
            HTTP_CODE=$(echo "$UPDATE_RESPONSE" | tail -n1)
            
            if [ "$HTTP_CODE" = "204" ] || [ "$HTTP_CODE" = "200" ]; then
              echo "  Client '$template_client_id' synced successfully!"
            else
              BODY=$(echo "$UPDATE_RESPONSE" | sed '$d')
              echo "  WARNING: Failed to sync client '$template_client_id' (HTTP $HTTP_CODE): $BODY"
            fi
          else
            echo "  Client '$template_client_id' not found, creating..."
            
            CREATE_RESPONSE=$(curl -s -w "\n%%{http_code}" -X POST \
              "$KEYCLOAK_URL/admin/realms/$REALM_NAME/clients" \
              -H "Authorization: Bearer $ACCESS_TOKEN" \
              -H "Content-Type: application/json" \
              --data "$TEMPLATE_CLIENT")
            
            HTTP_CODE=$(echo "$CREATE_RESPONSE" | tail -n1)
            
            if [ "$HTTP_CODE" = "201" ]; then
              echo "  Client '$template_client_id' created successfully!"
            else
              BODY=$(echo "$CREATE_RESPONSE" | sed '$d')
              echo "  WARNING: Failed to create client '$template_client_id' (HTTP $HTTP_CODE): $BODY"
            fi
          fi
        done
        
        # =====================================================================
        # 1.5 Sync Realm Roles and Default Role Configuration
        # =====================================================================
        echo ""
        echo "--- Syncing realm roles and default role configuration ---"
        
        # Create/update broker client role: read-token
        BROKER_CLIENT_RESPONSE=$(curl -s "$KEYCLOAK_URL/admin/realms/$REALM_NAME/clients?clientId=broker" \
          -H "Authorization: Bearer $ACCESS_TOKEN")
        BROKER_UUID=$(echo "$BROKER_CLIENT_RESPONSE" | jq -r '.[0].id')
        
        if [ "$BROKER_UUID" != "null" ] && [ -n "$BROKER_UUID" ]; then
          echo "Found broker client with UUID: $BROKER_UUID"
          
          # Check if read-token role exists
          EXISTING_ROLES=$(curl -s "$KEYCLOAK_URL/admin/realms/$REALM_NAME/clients/$BROKER_UUID/roles" \
            -H "Authorization: Bearer $ACCESS_TOKEN")
          
          READ_TOKEN_EXISTS=$(echo "$EXISTING_ROLES" | jq -r '.[] | select(.name == "read-token") | .name')
          
          if [ "$READ_TOKEN_EXISTS" != "read-token" ]; then
            echo "Creating broker:read-token role..."
            ROLE_CONFIG='{"name":"read-token","description":"$${role_read-token}","composite":false,"clientRole":true}'
            ROLE_RESPONSE=$(curl -s -w "\n%%{http_code}" -X POST \
              "$KEYCLOAK_URL/admin/realms/$REALM_NAME/clients/$BROKER_UUID/roles" \
              -H "Authorization: Bearer $ACCESS_TOKEN" \
              -H "Content-Type: application/json" \
              --data "$ROLE_CONFIG")
            
            HTTP_CODE=$(echo "$ROLE_RESPONSE" | tail -n1)
            if [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "204" ]; then
              echo "  broker:read-token role created successfully!"
            else
              BODY=$(echo "$ROLE_RESPONSE" | sed '$d')
              echo "  WARNING: Failed to create broker:read-token role (HTTP $HTTP_CODE): $BODY"
            fi
          else
            echo "  broker:read-token role already exists"
          fi
          
          # Get the broker:read-token role representation for assignment
          READ_TOKEN_ROLE=$(curl -s "$KEYCLOAK_URL/admin/realms/$REALM_NAME/clients/$BROKER_UUID/roles/read-token" \
            -H "Authorization: Bearer $ACCESS_TOKEN")
          
          # Get default-roles-allhands role ID
          DEFAULT_ROLE_RESPONSE=$(curl -s "$KEYCLOAK_URL/admin/realms/$REALM_NAME/roles/default-roles-allhands" \
            -H "Authorization: Bearer $ACCESS_TOKEN")
          DEFAULT_ROLE_ID=$(echo "$DEFAULT_ROLE_RESPONSE" | jq -r '.id')
          
          if [ "$DEFAULT_ROLE_ID" != "null" ] && [ -n "$DEFAULT_ROLE_ID" ]; then
            echo "Found default-roles-allhands with ID: $DEFAULT_ROLE_ID"
            echo "Adding broker:read-token to default-roles-allhands composites..."
            
            # Add broker:read-token to the composites of default-roles
            COMPOSITE_RESPONSE=$(curl -s -w "\n%%{http_code}" -X POST \
              "$KEYCLOAK_URL/admin/realms/$REALM_NAME/roles-by-id/$DEFAULT_ROLE_ID/composites" \
              -H "Authorization: Bearer $ACCESS_TOKEN" \
              -H "Content-Type: application/json" \
              --data "[$READ_TOKEN_ROLE]")
            
            HTTP_CODE=$(echo "$COMPOSITE_RESPONSE" | tail -n1)
            if [ "$HTTP_CODE" = "204" ] || [ "$HTTP_CODE" = "200" ]; then
              echo "  broker:read-token added to default roles!"
            else
              BODY=$(echo "$COMPOSITE_RESPONSE" | sed '$d')
              echo "  INFO: broker:read-token composite (HTTP $HTTP_CODE - may already exist): $BODY"
            fi
          else
            echo "  WARNING: default-roles-allhands not found"
          fi
        else
          echo "WARNING: broker client not found - cannot configure read-token role"
        fi
        
        # =====================================================================
        # 2. Sync Identity Providers
        # =====================================================================
        echo ""
        echo "--- Syncing identity providers ---"
        
        EXISTING_IDPS=$(curl -s "$KEYCLOAK_URL/admin/realms/$REALM_NAME/identity-provider/instances" \
          -H "Authorization: Bearer $ACCESS_TOKEN" | jq -r '.[].alias')
        
        for alias in $(jq -r '.identityProviders[]?.alias // empty' /tmp/realm.json); do
          IDP_CONFIG=$(jq --arg a "$alias" '.identityProviders[] | select(.alias == $a)' /tmp/realm.json)
          
          if echo "$EXISTING_IDPS" | grep -qx "$alias"; then
            echo "Updating identity provider: $alias"
            IDP_RESPONSE=$(curl -s -w "\n%%{http_code}" -X PUT \
              "$KEYCLOAK_URL/admin/realms/$REALM_NAME/identity-provider/instances/$alias" \
              -H "Authorization: Bearer $ACCESS_TOKEN" \
              -H "Content-Type: application/json" \
              --data "$IDP_CONFIG")
          else
            echo "Creating identity provider: $alias"
            IDP_RESPONSE=$(curl -s -w "\n%%{http_code}" -X POST \
              "$KEYCLOAK_URL/admin/realms/$REALM_NAME/identity-provider/instances" \
              -H "Authorization: Bearer $ACCESS_TOKEN" \
              -H "Content-Type: application/json" \
              --data "$IDP_CONFIG")
          fi
          
          HTTP_CODE=$(echo "$IDP_RESPONSE" | tail -n1)
          if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "204" ]; then
            echo "  Identity provider '$alias' synced successfully"
          else
            BODY=$(echo "$IDP_RESPONSE" | sed '$d')
            echo "  WARNING: Failed to sync identity provider '$alias' (HTTP $HTTP_CODE): $BODY"
          fi
        done
        
        # =====================================================================
        # 3. Sync Identity Provider Mappers
        # =====================================================================
        echo ""
        echo "--- Syncing identity provider mappers ---"
        
        for alias in $(jq -r '.identityProviders[]?.alias // empty' /tmp/realm.json); do
          EXISTING_MAPPERS=$(curl -s "$KEYCLOAK_URL/admin/realms/$REALM_NAME/identity-provider/instances/$alias/mappers" \
            -H "Authorization: Bearer $ACCESS_TOKEN")
          
          for mapper_name in $(jq -r --arg a "$alias" \
            '.identityProviderMappers[]? | select(.identityProviderAlias == $a) | .name // empty' /tmp/realm.json); do
            
            MAPPER_CONFIG=$(jq --arg a "$alias" --arg n "$mapper_name" \
              '.identityProviderMappers[] | select(.identityProviderAlias == $a and .name == $n)' /tmp/realm.json)
            
            EXISTING_ID=$(echo "$EXISTING_MAPPERS" | jq -r --arg n "$mapper_name" \
              '.[] | select(.name == $n) | .id')
            
            if [ -n "$EXISTING_ID" ] && [ "$EXISTING_ID" != "null" ]; then
              echo "Updating mapper: $alias/$mapper_name"
              MAPPER_WITH_ID=$(echo "$MAPPER_CONFIG" | jq --arg id "$EXISTING_ID" '. + {id: $id}')
              MAPPER_RESPONSE=$(curl -s -w "\n%%{http_code}" -X PUT \
                "$KEYCLOAK_URL/admin/realms/$REALM_NAME/identity-provider/instances/$alias/mappers/$EXISTING_ID" \
                -H "Authorization: Bearer $ACCESS_TOKEN" \
                -H "Content-Type: application/json" \
                --data "$MAPPER_WITH_ID")
            else
              echo "Creating mapper: $alias/$mapper_name"
              MAPPER_RESPONSE=$(curl -s -w "\n%%{http_code}" -X POST \
                "$KEYCLOAK_URL/admin/realms/$REALM_NAME/identity-provider/instances/$alias/mappers" \
                -H "Authorization: Bearer $ACCESS_TOKEN" \
                -H "Content-Type: application/json" \
                --data "$MAPPER_CONFIG")
            fi
            
            HTTP_CODE=$(echo "$MAPPER_RESPONSE" | tail -n1)
            if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "204" ]; then
              echo "  Mapper '$mapper_name' synced successfully"
            else
              BODY=$(echo "$MAPPER_RESPONSE" | sed '$d')
              echo "  WARNING: Failed to sync mapper '$mapper_name' (HTTP $HTTP_CODE): $BODY"
            fi
          done
        done
        
        echo ""
        echo "Configuration sync complete!"
      fi
      
      echo ""
      echo "============================================"
      echo "Realm setup complete!"
      echo "============================================"
    EOF
  }
}

# -----------------------------------------------------------------------------
# Job to configure the realm after Keycloak is deployed
# -----------------------------------------------------------------------------

resource "kubernetes_job" "realm_setup" {
  metadata {
    name      = "keycloak-realm-setup"
    namespace = kubernetes_namespace.shared_auth.metadata[0].name
  }

  spec {
    ttl_seconds_after_finished = 300
    backoff_limit              = 10

    template {
      metadata {
        labels = {
          app = "keycloak-realm-setup"
        }
      }

      spec {
        restart_policy = "OnFailure"

        container {
          name  = "setup"
          image = "alpine/curl:8.5.0"  # Has both curl and jq

          command = ["/bin/sh", "/scripts/setup-realm.sh"]

          env {
            name  = "KEYCLOAK_URL"
            value = "http://keycloak-http:80"
          }

          env {
            name  = "REALM_NAME"
            value = var.realm_name
          }

          env {
            name  = "CLIENT_ID"
            value = var.client_id
          }

          env {
            name = "CLIENT_SECRET"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.staging_client.metadata[0].name
                key  = "client-secret"
              }
            }
          }

          env {
            name = "KEYCLOAK_ADMIN_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.keycloak_admin.metadata[0].name
                key  = "admin-password"
              }
            }
          }

          volume_mount {
            name       = "scripts"
            mount_path = "/scripts"
          }

          volume_mount {
            name       = "config"
            mount_path = "/config"
          }
        }

        volume {
          name = "scripts"
          config_map {
            name         = kubernetes_config_map.realm_setup_script.metadata[0].name
            default_mode = "0755"
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.realm_template.metadata[0].name
          }
        }
      }
    }
  }

  depends_on = [helm_release.keycloak]
}
