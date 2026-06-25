# Shared Keycloak for Staging Environments

This Terraform module deploys a shared Keycloak instance that can be used by multiple branch deployments in the staging environment.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Shared Infrastructure                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Keycloak (auth.ohe-staging.platform-team.all-hands.dev) │  │
│  │  - Single SAML config with Google Workspace              │  │
│  │  - allhands realm with identity providers                │  │
│  │  - Shared PostgreSQL database                            │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
            ▲                    ▲                    ▲
            │                    │                    │
┌───────────┴───┐    ┌───────────┴───┐    ┌───────────┴───┐
│ branch-a      │    │ branch-b      │    │ branch-c      │
│ deployment    │    │ deployment    │    │ deployment    │
│ (no keycloak) │    │ (no keycloak) │    │ (no keycloak) │
└───────────────┘    └───────────────┘    └───────────────┘
```

## Benefits

1. **Single Identity Provider Configuration**: Configure Google SAML (or other IdPs) once
2. **Consistent Authentication**: All branches share the same authentication setup
3. **Resource Efficiency**: One Keycloak instance instead of one per branch
4. **Simplified Management**: Single place to manage users, roles, and IdP settings

## Prerequisites

1. Kubernetes cluster with:
   - Traefik ingress controller
   - cert-manager with wildcard certificate
   - PostgreSQL (from openhands namespace)

2. Wildcard TLS certificate secret in the shared-auth namespace

## Deployment

### 1. Configure Variables

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

### 2. Copy TLS Secret (if needed)

The shared-auth namespace needs access to the wildcard TLS certificate:

```bash
# Copy from openhands namespace
kubectl get secret ohe-staging-wildcard-tls -n openhands -o yaml | \
  sed 's/namespace: openhands/namespace: shared-auth/' | \
  kubectl apply -f -
```

### 3. Create Keycloak Database

Create the database in the shared PostgreSQL:

```bash
kubectl exec -it openhands-postgresql-0 -n openhands -- \
  psql -U postgres -c "CREATE DATABASE shared_keycloak;"
```

### 4. Apply Terraform

```bash
terraform init
terraform plan
terraform apply
```

## Configuring Branch Deployments

To use the shared Keycloak, update your branch deployment's helm values:

```yaml
keycloak:
  enabled: false  # Disable embedded Keycloak

  # External Keycloak configuration
  external:
    enabled: true
    url: "https://auth.ohe-staging.platform-team.all-hands.dev"
    realm: "allhands"
```

## Adding a New Branch Client

Each branch deployment needs a client configured in Keycloak. This is handled automatically by the init container in the openhands chart.

The client will be created with:
- Client ID: `openhands-<branch-name>`
- Valid Redirect URIs: `https://<branch>.ohe-staging.platform-team.all-hands.dev/*`

## Configuring Google SAML (Manual)

If you need to configure Google SAML manually via the admin console:

1. Go to Keycloak Admin Console: https://auth.ohe-staging.platform-team.all-hands.dev/auth/admin
2. Select the `allhands` realm
3. Go to Identity Providers → Add Provider → SAML v2.0
4. Configure with your Google Workspace SAML settings:
   - Alias: `google`
   - Service Provider Entity ID: `https://auth.ohe-staging.platform-team.all-hands.dev/auth/realms/allhands`
   - Single Sign-On Service URL: (from Google)
   - NameID Policy Format: Email
   - Principal Type: Subject NameID

## Configuring Enterprise SSO via Terraform (Recommended)

The shared-auth module supports Google Workspace SAML authentication through Terraform variables, which is the recommended approach for reproducible deployments.

### Step 1: Create SAML App in Google Workspace

1. Go to [Google Workspace Admin Console](https://admin.google.com)
2. Navigate to **Apps** → **Web and mobile apps** → **Add App** → **Add custom SAML app**
3. Enter app name: "OpenHands Staging" (or your preferred name)
4. On the "Google Identity Provider details" page, copy:
   - **SSO URL**: This is your `saml_sso_url` value (format: `https://accounts.google.com/o/saml2/idp?idpid=...`)
   - **Certificate**: Download or copy the certificate content
5. Configure Service Provider details:
   - **ACS URL**: `https://auth.ohe-staging.platform-team.all-hands.dev/auth/realms/allhands/broker/enterprise_sso/endpoint`
   - **Entity ID**: `openhands-allhands`
   - **Name ID format**: `EMAIL`
   - **Name ID**: `Basic Information > Primary email`
6. Click through to complete the setup and enable the app for your users/groups

### Step 2: Prepare the Certificate

The SAML signing certificate must be formatted as a single line without headers:

```bash
# If you downloaded the certificate file:
cat google_certificate.pem | grep -v "BEGIN\|END" | tr -d '\n'
```

### Step 3: Configure Terraform Variables

In your `terraform.tfvars`:

```hcl
# SAML SSO URL from Google Workspace
saml_sso_url = "https://accounts.google.com/o/saml2/idp?idpid=C01234567"

# SAML signing certificate (base64 only, no BEGIN/END lines, single line)
saml_signing_certificate = "MIIDdDCCAlygAwIBAgIGAY..."
```

### Step 4: Apply Terraform Changes

```bash
terraform plan   # Review the changes
terraform apply  # Apply the configuration
```

The realm setup job will automatically run and configure the enterprise_sso identity provider.

### Step 5: Delete Old Realm Setup Job (if updating)

If you're updating an existing deployment, you may need to delete the old job first:

```bash
# Delete the completed job so terraform can recreate it
kubectl delete job keycloak-realm-setup -n shared-auth

# Re-apply terraform to recreate the job with new config
terraform apply
```

### Step 6: Test Login

Navigate to your deployment (e.g., `https://ohe-staging.platform-team.all-hands.dev`) and you should see the "Enterprise SSO (Google Workspace)" login option.

## Troubleshooting

### "At least one identity provider must be configured" Error

This error occurs when the Keycloak realm doesn't have any identity providers configured. Check:

1. Verify the realm was created with identity providers:
   ```bash
   kubectl logs -n shared-auth job/keycloak-realm-setup
   ```

2. Check if SAML variables are set in terraform.tfvars:
   ```bash
   grep saml_sso_url terraform.tfvars
   grep saml_signing_certificate terraform.tfvars
   ```

3. If the realm already exists but needs identity providers added, you may need to:
   - Delete the realm via Keycloak admin console and re-run terraform apply
   - Or manually add the identity provider via the admin console

### Realm Setup Job Fails

Check the job logs:
```bash
kubectl logs -n shared-auth job/keycloak-realm-setup
```

Common issues:
- Keycloak not ready yet (job will retry automatically)
- Database connectivity issues
- Invalid SAML certificate format (must be base64 without headers)

### Certificate Format Issues

The SAML signing certificate must be:
- Base64 encoded (standard PEM certificate content)
- Single line (no line breaks)
- Without `-----BEGIN CERTIFICATE-----` and `-----END CERTIFICATE-----` headers

Example conversion:
```bash
# From a downloaded .pem file
cat certificate.pem | grep -v "BEGIN\|END" | tr -d '\n' > certificate_single_line.txt
```

## Outputs

After deployment, Terraform provides these outputs:

- `keycloak_url`: Public URL for Keycloak
- `keycloak_admin_console`: Admin console URL
- `keycloak_internal_url`: Internal cluster URL for branch deployments
- `branch_deployment_config`: Configuration values to use in branch helm values
