# -----------------------------------------------------------------------------
# AWS
# -----------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "instance_name" {
  description = "Name tag for the EC2 instance and related resources"
  type        = string
}

# -----------------------------------------------------------------------------
# EC2
# -----------------------------------------------------------------------------

variable "instance_type" {
  description = "EC2 instance type (16 vCPU / 64 GB recommended)"
  type        = string
  default     = "m6i.4xlarge"
}

variable "root_volume_size" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 200
}

variable "ami_id" {
  description = "Optional AMI override. If empty, latest Ubuntu 24.04 LTS is used."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# SSH
# -----------------------------------------------------------------------------

variable "allowed_cidrs" {
  description = "CIDR blocks allowed for SSH and admin ports. Recommend restricting to your IP."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# -----------------------------------------------------------------------------
# Network
# -----------------------------------------------------------------------------

variable "vpc_id" {
  description = "ID of an existing VPC. If empty, a new VPC is created. Must be set together with subnet_id."
  type        = string
  default     = ""

  validation {
    condition     = var.vpc_id == "" || var.subnet_id != ""
    error_message = "subnet_id is required when vpc_id is set."
  }
}

variable "subnet_id" {
  description = "ID of an existing public subnet. Required when vpc_id is set."
  type        = string
  default     = ""
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC (ignored when vpc_id is set)"
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for the public subnet (ignored when subnet_id is set)"
  type        = string
  default     = "10.0.1.0/24"
}

# -----------------------------------------------------------------------------
# DNS
# -----------------------------------------------------------------------------

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID for DNS records. If empty, no DNS records are created."
  type        = string
  default     = ""
}

variable "base_domain" {
  description = "Base domain for OpenHands (e.g. openhands.example.com)"
  type        = string
}

variable "hostname_mode" {
  description = <<-EOT
    Which hostname layout to provision DNS records and a certificate for.
    Mirrors the installer's Hostname Configuration Mode setting: "wildcard" is
    the mode shown there as Simple, and "legacy" the one shown as Legacy.
    "wildcard" keeps every hostname one label below base_domain, so a single
    *.base_domain record and wildcard certificate serve the whole install.
    "legacy" is the original layout, which puts Keycloak at auth.app.<base>,
    analytics at analytics.app.<base>, and sandboxes under *.runtime.<base>.
    New installs default to wildcard; set this to legacy only to match an
    install whose Hostname Configuration Mode is Legacy.
    The installer's third mode, Manual, has no equivalent here -- Terraform
    cannot know hostnames you enter by hand. For that, omit route53_zone_id and
    set provision_cert = false, then supply your own records and certificate.
  EOT
  type        = string
  default     = "wildcard"

  validation {
    condition     = contains(["wildcard", "legacy"], var.hostname_mode)
    error_message = "hostname_mode must be either \"wildcard\" or \"legacy\"."
  }
}

variable "dns_ttl" {
  description = "TTL in seconds for DNS A records"
  type        = number
  default     = 300
}

# -----------------------------------------------------------------------------
# Certificate
# -----------------------------------------------------------------------------

variable "provision_cert" {
  description = "If true, provision a Let's Encrypt certificate via ACME. If false, supply your own."
  type        = bool
  default     = true
}

variable "acme_email" {
  description = "Email address for the ACME account (required when provision_cert = true)"
  type        = string
  default     = ""
}

variable "acme_server" {
  description = "ACME directory URL"
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"
}

variable "user_cert_path" {
  description = "Path to user-provided TLS certificate (when provision_cert = false)"
  type        = string
  default     = ""
}

variable "user_private_key_path" {
  description = "Path to user-provided TLS private key (when provision_cert = false)"
  type        = string
  default     = ""
}

variable "user_ca_path" {
  description = "Path to user-provided CA certificate (when provision_cert = false)"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Tags
# -----------------------------------------------------------------------------

variable "default_tags" {
  description = "Default tags applied to all AWS resources"
  type        = map(string)
  default     = {}
}
