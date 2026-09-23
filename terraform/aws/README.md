# AWS Terraform for Replicated OpenHands VM

This Terraform module provisions a single AWS EC2 instance with all supporting infrastructure needed to install OpenHands via Replicated's embedded cluster.

<!---
TODO: Update the link to our docs once the replicated installation guide is live.
-->
Once your instance is up, you can follow the instructions on our [docs site](https://docs.openhands.dev/enterprise) to install the Replicated embedded cluster and deploy OpenHands.

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.0
- AWS CLI configured with credentials (`aws configure` or environment variables)
- A registered domain name

## Quick Start (Recommended)

In our recommended setup, Terraform will provision a VPC and EC2 instance, set up DNS records in Route 53, and automatically provision TLS certificates with Let's Encrypt.

To follow the quick start, you will need the following in addition to the prerequisites:
- An AWS hosted zone in Route 53 for your domain
- An email address for ACME registration (for TLS certificates)

If you want to configure your own VPC, DNS provider, or TLS certificates, there are additional instructions after the quick start guide:
- [Manual VPC instructions](#manual-vpc-configuration)
- [Manual DNS instructions](#manual-dns-configuration)
- [Manual TLS instructions](#manual-tls-certificate-provisioning)

---

1. **Clone the repo and navigate here:**

   ```bash
   cd terraform/aws
   ```

2. **Copy the example variables file:**

   ```bash
   cp example.tfvars terraform.tfvars
   ```

3. **Edit `terraform.tfvars` with your values:**
   - `instance_name` — a name for your instance
   - `base_domain` — the base domain for your instance (e.g. `openhands.example.com`)
   - `route53_zone_id` — your Route 53 hosted zone ID
   - `acme_email` — for Let's Encrypt registration

4. **Initialize Terraform:**

   ```bash
   make init
   ```

5. **Preview the plan:**

   ```bash
   make plan
   ```

6. **Apply:**

   ```bash
   make apply
   ```
   
7. **Copy your certificates to the instance:**

   ```bash
   make copy-files
   ```

8. **SSH into the instance:**

   ```bash
   make ssh
   ```
   
9. **Install OpenHands:**

   Follow the instructions on our [docs site](https://docs.openhands.dev/enterprise) to install the Replicated embedded cluster and deploy OpenHands.
   
## Manual VPC Configuration

To deploy into an existing VPC instead, set `vpc_id` and `subnet_id` in your `terraform.tfvars`:

```hcl
vpc_id    = "vpc-0123456789abcdef0"
subnet_id = "subnet-0123456789abcdef0"
```

The provided subnet must be public (i.e. it has an internet gateway and a route to `0.0.0.0/0`). When these are set, `vpc_cidr` and `subnet_cidr` are ignored.

## Manual DNS Configuration

To provision your own DNS records instead of having Terraform manage them for you. 

Simply omit `route53_zone_id` from your `terraform.tfvars`.

> **Note:** Automatic TLS certificate provisioning uses Route 53 DNS challenges. If you are not using Route 53, you will also need to [bring your own TLS certificates](#manual-tls-certificate-provisioning).

Create A records pointing to the instance's Elastic IP. With the installer's
default Simple mode, every hostname is a single label under your base domain,
so one record covers the whole install, the admin console at
`admin.<your-domain>:30000` included:
- `*.<your-domain>`

If you set `hostname_mode = "legacy"` to match an install on the installer's
Legacy mode, create these instead:
- `<your-domain>`
- `app.<your-domain>`
- `analytics.app.<your-domain>`
- `auth.app.<your-domain>`
- `llm-proxy.<your-domain>`
- `runtime-api.<your-domain>`
- `*.runtime.<your-domain>`

You can retrieve your instance's public IP with:

```bash
make ip
```

## Manual TLS Certificate Provisioning

To bring your own certificates, set `provision_cert = false` in your `terraform.tfvars` and provide paths to your certificate files:

```hcl
provision_cert        = false
user_cert_path        = "/path/to/certificate.pem"
user_private_key_path = "/path/to/private-key.pem"
user_ca_path          = "/path/to/ca.pem"
```

With the default Simple mode, the certificate needs one wildcard name and
nothing else:
- `*.<your-domain>`

If you set `hostname_mode = "legacy"`, the SANs must cover each name
individually, including a second wildcard for sandbox runtimes:
- `<your-domain>`
- `app.<your-domain>`
- `analytics.app.<your-domain>`
- `auth.app.<your-domain>`
- `llm-proxy.<your-domain>`
- `runtime-api.<your-domain>`
- `*.runtime.<your-domain>`

## Cleanup

You can destroy all resources created by Terraform with:

```bash
make destroy
```
