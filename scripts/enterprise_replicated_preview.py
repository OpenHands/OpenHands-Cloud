#!/usr/bin/env python3
"""Helpers for Enterprise PR Replicated preview environments.

The GitHub Actions workflow keeps credentials and side effects in YAML. This
module owns deterministic naming, chart patching, and generated input files so
those pieces are testable without talking to GitHub, AWS, or Replicated.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)(?P<suffix>[-+].*)?$"
)
SAFE_DOMAIN_LABEL_RE = re.compile(r"[^a-z0-9-]")


@dataclass(frozen=True)
class PreviewMetadata:
    enterprise_pr_number: int
    enterprise_sha: str
    enterprise_short_sha: str
    enterprise_image_tag: str
    enterprise_image_ref: str
    preview_channel: str
    preview_customer: str
    preview_release_version: str
    preview_chart_version: str
    preview_instance_name: str
    preview_base_domain: str | None


def validate_pr_number(value: str | int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("enterprise PR number must be an integer") from exc
    if number <= 0:
        raise ValueError("enterprise PR number must be positive")
    return number


def normalize_sha(value: str) -> str:
    sha = (value or "").strip().lower()
    if not SHA_RE.fullmatch(sha):
        raise ValueError("enterprise SHA must be 7 to 40 hexadecimal characters")
    return sha


def normalize_domain_label(value: str, *, max_length: int = 63) -> str:
    label = SAFE_DOMAIN_LABEL_RE.sub("-", value.lower()).strip("-")
    label = re.sub(r"-+", "-", label)
    if not label:
        raise ValueError("domain label cannot be empty after normalization")
    return label[:max_length].rstrip("-")


def next_preview_chart_version(
    base_version: str, pr_number: int, run_number: int
) -> str:
    match = SEMVER_RE.fullmatch(base_version.strip())
    if not match:
        raise ValueError(f"unsupported chart version {base_version!r}; expected semver")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch")) + 1
    return f"{major}.{minor}.{patch}-enterprise-pr.{pr_number}.{run_number}"


def read_chart_version(chart_file: Path) -> str:
    for line in chart_file.read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise ValueError(f"could not find top-level version in {chart_file}")


def build_metadata(
    *,
    pr_number: str | int,
    sha: str,
    chart_version: str,
    run_number: int,
    image_tag: str | None = None,
    image_repository: str = "ghcr.io/openhands/enterprise-server",
    domain_suffix: str | None = None,
) -> PreviewMetadata:
    pr = validate_pr_number(pr_number)
    full_sha = normalize_sha(sha)
    short_sha = full_sha[:7]
    tag = (image_tag or f"sha-{short_sha}").strip()
    if not tag:
        raise ValueError("enterprise image tag cannot be empty")

    channel = f"enterprise-pr-{pr}"
    release_version = f"enterprise-pr-{pr}-{short_sha}"
    chart_preview_version = next_preview_chart_version(chart_version, pr, run_number)
    instance_name = normalize_domain_label(f"oh-ent-pr-{pr}-{short_sha}", max_length=50)
    base_domain = None
    if domain_suffix:
        suffix = domain_suffix.strip().strip(".")
        if not suffix:
            raise ValueError("domain suffix cannot be blank")
        base_domain = f"pr-{pr}.{suffix}"

    return PreviewMetadata(
        enterprise_pr_number=pr,
        enterprise_sha=full_sha,
        enterprise_short_sha=short_sha,
        enterprise_image_tag=tag,
        enterprise_image_ref=f"{image_repository}:{tag}",
        preview_channel=channel,
        preview_customer=f"enterprise-pr-{pr}-{short_sha}",
        preview_release_version=release_version,
        preview_chart_version=chart_preview_version,
        preview_instance_name=instance_name,
        preview_base_domain=base_domain,
    )


def patch_chart_file(chart_file: Path, new_version: str) -> None:
    lines = chart_file.read_text().splitlines(keepends=True)
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith("version:"):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"version: {new_version}{newline}"
            replaced = True
            break
    if not replaced:
        raise ValueError(f"could not find top-level version in {chart_file}")
    chart_file.write_text("".join(lines))


def patch_values_image_tag(values_file: Path, image_tag: str) -> None:
    lines = values_file.read_text().splitlines(keepends=True)
    in_image_block = False
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if line.startswith("image:"):
            in_image_block = True
            continue
        if in_image_block and line and not line.startswith((" ", "\t", "#")):
            break
        if in_image_block and stripped.startswith("tag:"):
            newline = "\n" if line.endswith("\n") else ""
            indent = line[: len(line) - len(line.lstrip())]
            lines[index] = f'{indent}tag: "{image_tag}"{newline}'
            replaced = True
            break
    if not replaced:
        raise ValueError(f"could not find image.tag in {values_file}")
    values_file.write_text("".join(lines))


def _yaml_scalar(value: str) -> str:
    return json.dumps(value)


def _yaml_block(value: str, indent: int) -> str:
    pad = " " * indent
    text = value.rstrip("\n")
    if not text:
        return f"{pad}''\n"
    return "".join(f"{pad}{line}\n" for line in text.splitlines())


def _file_value(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def render_config_values(
    *,
    base_domain: str,
    tls_certificate: Path,
    tls_private_key: Path,
    tls_ca_certificate: Path | None = None,
    hostname_layout: str = "legacy",
    llm_provider: str = "custom",
    custom_base_url: str = "http://localhost:4000/v1",
    custom_models: str = "openai/preview-smoke-test-model",
    custom_api_key: str | None = None,
    github_client_id: str | None = None,
    github_client_secret: str | None = None,
    github_app_id: str | None = None,
    github_app_slug: str | None = None,
    github_webhook_secret: str | None = None,
    github_private_key: Path | None = None,
    automations_enabled: bool = False,
    agent_canvas_enabled: bool = True,
    analytics_enabled: bool = False,
) -> str:
    if hostname_layout not in {"legacy", "flat"}:
        raise ValueError("hostname layout must be legacy or flat")

    values: list[tuple[str, str, str]] = [
        ("hostname_mode", "value", "derive"),
        ("base_domain", "value", base_domain),
        ("tls_certificate", "value", _file_value(tls_certificate)),
        ("tls_private_key", "value", _file_value(tls_private_key)),
        ("llm_provider", "value", llm_provider),
        ("automations_enabled", "value", "1" if automations_enabled else "0"),
        ("agent_canvas_enabled", "value", "1" if agent_canvas_enabled else "0"),
        ("analytics_enabled", "value", "1" if analytics_enabled else "0"),
    ]
    if hostname_layout == "flat":
        values = [
            ("hostname_mode", "value", "custom"),
            ("app_hostname", "value", f"app-{base_domain}"),
            ("analytics_hostname", "value", f"analytics-{base_domain}"),
            ("auth_hostname", "value", f"auth-{base_domain}"),
            ("llm_proxy_hostname", "value", f"llm-proxy-{base_domain}"),
            ("runtime_api_hostname", "value", f"runtime-api-{base_domain}"),
            ("runtime_base_hostname", "value", f"runtime-{base_domain}"),
            ("runtime_routing_mode", "value", "path"),
            *values[2:],
        ]
    if tls_ca_certificate:
        values.append(("tls_ca_certificate", "value", _file_value(tls_ca_certificate)))
    if llm_provider == "custom":
        values.extend(
            [
                ("custom_base_url", "value", custom_base_url),
                ("custom_models", "value", custom_models),
            ]
        )
        if custom_api_key:
            values.append(("custom_api_key", "valuePlaintext", custom_api_key))

    github_values = [
        ("github_oauth_client_id", "value", github_client_id),
        ("github_oauth_client_secret", "valuePlaintext", github_client_secret),
        ("github_app_id", "value", github_app_id),
        ("github_app_slug", "value", github_app_slug),
        ("github_app_webhook_secret", "valuePlaintext", github_webhook_secret),
    ]
    if any(value for _, _, value in github_values) or github_private_key:
        if not all(value for _, _, value in github_values) or not github_private_key:
            raise ValueError(
                "all GitHub App credentials are required when GitHub auth is enabled"
            )
        values.append(("github_auth_enabled", "value", "1"))
        values.extend(github_values)
        values.append(
            ("github_app_private_key", "value", _file_value(github_private_key))
        )

    rendered = [
        "apiVersion: kots.io/v1beta1\n",
        "kind: ConfigValues\n",
        "spec:\n",
        "  values:\n",
    ]
    for name, key, value in values:
        rendered.append(f"    {name}:\n")
        if "\n" in value:
            rendered.append(f"      {key}: |\n")
            rendered.append(_yaml_block(value, 8))
        else:
            rendered.append(f"      {key}: {_yaml_scalar(value)}\n")
    return "".join(rendered)


def render_tfvars(
    *,
    instance_name: str,
    base_domain: str,
    aws_region: str,
    route53_zone_id: str,
    acme_email: str,
    vpc_id: str = "",
    subnet_id: str = "",
    allowed_cidrs: Sequence[str] = (),
    default_tags: dict[str, str] | None = None,
) -> str:
    tags = default_tags or {}
    cidrs = list(allowed_cidrs) or ["0.0.0.0/0"]
    lines = [
        f"aws_region = {_yaml_scalar(aws_region)}\n",
        f"instance_name = {_yaml_scalar(instance_name)}\n",
        f"base_domain = {_yaml_scalar(base_domain)}\n",
        f"route53_zone_id = {_yaml_scalar(route53_zone_id)}\n",
        f"acme_email = {_yaml_scalar(acme_email)}\n",
        'hostname_mode = "legacy"\n',
        "provision_cert = true\n",
        f"allowed_cidrs = {json.dumps(cidrs)}\n",
    ]
    if vpc_id:
        lines.append(f"vpc_id = {_yaml_scalar(vpc_id)}\n")
    if subnet_id:
        lines.append(f"subnet_id = {_yaml_scalar(subnet_id)}\n")
    if tags:
        lines.append("default_tags = {\n")
        for key in sorted(tags):
            lines.append(f"  {json.dumps(key)} = {json.dumps(tags[key])}\n")
        lines.append("}\n")
    return "".join(lines)


def render_gcp_tfvars(
    *,
    instance_name: str,
    base_domain: str,
    project_id: str,
    region: str,
    zone: str,
    network: str,
    subnetwork: str,
    dns_managed_zone: str,
    machine_type: str = "c3d-standard-8",
    boot_disk_size_gb: int = 200,
    allowed_admin_cidrs: Sequence[str] = (),
    labels: dict[str, str] | None = None,
) -> str:
    label_values = labels or {}
    cidrs = list(allowed_admin_cidrs) or ["0.0.0.0/0"]
    lines = [
        f"project_id = {_yaml_scalar(project_id)}\n",
        f"region = {_yaml_scalar(region)}\n",
        f"zone = {_yaml_scalar(zone)}\n",
        f"instance_name = {_yaml_scalar(instance_name)}\n",
        f"base_domain = {_yaml_scalar(base_domain)}\n",
        f"network = {_yaml_scalar(network)}\n",
        f"subnetwork = {_yaml_scalar(subnetwork)}\n",
        f"dns_managed_zone = {_yaml_scalar(dns_managed_zone)}\n",
        f"machine_type = {_yaml_scalar(machine_type)}\n",
        f"boot_disk_size_gb = {boot_disk_size_gb}\n",
        f"allowed_admin_cidrs = {json.dumps(cidrs)}\n",
    ]
    if label_values:
        lines.append("labels = {\n")
        for key in sorted(label_values):
            lines.append(f"  {json.dumps(key)} = {json.dumps(label_values[key])}\n")
        lines.append("}\n")
    return "".join(lines)


def command_metadata(args: argparse.Namespace) -> int:
    chart_version = args.chart_version or read_chart_version(Path(args.chart_file))
    metadata = build_metadata(
        pr_number=args.pr,
        sha=args.sha,
        chart_version=chart_version,
        run_number=args.run_number,
        image_tag=args.image_tag,
        image_repository=args.image_repository,
        domain_suffix=args.domain_suffix,
    )
    if args.format == "json":
        print(json.dumps(asdict(metadata), indent=2, sort_keys=True))
    else:
        for key, value in asdict(metadata).items():
            if value is not None:
                print(f"{key.upper()}={value}")
    return 0


def command_patch_chart(args: argparse.Namespace) -> int:
    chart_file = Path(args.chart_file)
    values_file = Path(args.values_file)
    chart_version = args.chart_version or read_chart_version(chart_file)
    metadata = build_metadata(
        pr_number=args.pr,
        sha=args.sha,
        chart_version=chart_version,
        run_number=args.run_number,
        image_tag=args.image_tag,
    )
    patch_chart_file(chart_file, metadata.preview_chart_version)
    patch_values_image_tag(values_file, metadata.enterprise_image_tag)
    print(
        f"Patched {chart_file} to {metadata.preview_chart_version} and "
        f"{values_file} image.tag to {metadata.enterprise_image_tag}"
    )
    return 0


def command_write_config_values(args: argparse.Namespace) -> int:
    output = Path(args.output)
    output.write_text(
        render_config_values(
            base_domain=args.base_domain,
            tls_certificate=Path(args.tls_certificate),
            tls_private_key=Path(args.tls_private_key),
            tls_ca_certificate=Path(args.tls_ca_certificate)
            if args.tls_ca_certificate
            else None,
            hostname_layout=args.hostname_layout,
            llm_provider=args.llm_provider,
            custom_base_url=args.custom_base_url,
            custom_models=args.custom_models,
            custom_api_key=args.custom_api_key,
            github_client_id=args.github_client_id,
            github_client_secret=args.github_client_secret,
            github_app_id=args.github_app_id,
            github_app_slug=args.github_app_slug,
            github_webhook_secret=args.github_webhook_secret,
            github_private_key=Path(args.github_private_key)
            if args.github_private_key
            else None,
            automations_enabled=args.automations_enabled,
            agent_canvas_enabled=not args.disable_agent_canvas,
            analytics_enabled=args.analytics_enabled,
        )
    )
    print(f"Wrote {output}")
    return 0


def command_write_tfvars(args: argparse.Namespace) -> int:
    tags = {
        "Environment": "preview",
        "PreviewKind": "enterprise-replicated",
        "EnterprisePR": str(args.pr),
        "EnterpriseSHA": args.sha,
    }
    output = Path(args.output)
    output.write_text(
        render_tfvars(
            instance_name=args.instance_name,
            base_domain=args.base_domain,
            aws_region=args.aws_region,
            route53_zone_id=args.route53_zone_id,
            acme_email=args.acme_email,
            vpc_id=args.vpc_id,
            subnet_id=args.subnet_id,
            allowed_cidrs=args.allowed_cidr,
            default_tags=tags,
        )
    )
    print(f"Wrote {output}")
    return 0


def command_write_gcp_tfvars(args: argparse.Namespace) -> int:
    labels = {
        "environment": "preview",
        "preview-kind": "enterprise-replicated",
        "enterprise-pr": str(validate_pr_number(args.pr)),
        "enterprise-sha": normalize_sha(args.sha)[:40],
    }
    output = Path(args.output)
    output.write_text(
        render_gcp_tfvars(
            instance_name=args.instance_name,
            base_domain=args.base_domain,
            project_id=args.project_id,
            region=args.region,
            zone=args.zone,
            network=args.network,
            subnetwork=args.subnetwork,
            dns_managed_zone=args.dns_managed_zone,
            machine_type=args.machine_type,
            boot_disk_size_gb=args.boot_disk_size_gb,
            allowed_admin_cidrs=args.allowed_admin_cidr,
            labels=labels,
        )
    )
    print(f"Wrote {output}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_metadata_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--pr", required=True, help="Enterprise PR number")
        p.add_argument("--sha", required=True, help="Enterprise PR head SHA")
        p.add_argument("--image-tag", default="", help="Enterprise image tag")
        p.add_argument(
            "--image-repository",
            default="ghcr.io/openhands/enterprise-server",
            help="Enterprise image repository",
        )
        p.add_argument("--run-number", type=int, required=True)
        p.add_argument("--chart-file", default="charts/openhands/Chart.yaml")
        p.add_argument("--chart-version", default="")
        p.add_argument("--domain-suffix", default="")

    metadata = subparsers.add_parser("metadata")
    add_metadata_args(metadata)
    metadata.add_argument("--format", choices=("env", "json"), default="env")
    metadata.set_defaults(func=command_metadata)

    patch = subparsers.add_parser("patch-chart")
    add_metadata_args(patch)
    patch.add_argument("--values-file", default="charts/openhands/values.yaml")
    patch.set_defaults(func=command_patch_chart)

    config = subparsers.add_parser("write-config-values")
    config.add_argument("--output", required=True)
    config.add_argument("--base-domain", required=True)
    config.add_argument("--tls-certificate", required=True)
    config.add_argument("--tls-private-key", required=True)
    config.add_argument("--tls-ca-certificate", default="")
    config.add_argument(
        "--hostname-layout", choices=("legacy", "flat"), default="legacy"
    )
    config.add_argument("--llm-provider", default="custom")
    config.add_argument("--custom-base-url", default="http://localhost:4000/v1")
    config.add_argument("--custom-models", default="openai/preview-smoke-test-model")
    config.add_argument("--custom-api-key", default="")
    config.add_argument("--github-client-id", default="")
    config.add_argument("--github-client-secret", default="")
    config.add_argument("--github-app-id", default="")
    config.add_argument("--github-app-slug", default="")
    config.add_argument("--github-webhook-secret", default="")
    config.add_argument("--github-private-key", default="")
    config.add_argument("--automations-enabled", action="store_true")
    config.add_argument("--analytics-enabled", action="store_true")
    config.add_argument("--disable-agent-canvas", action="store_true")
    config.set_defaults(func=command_write_config_values)

    tfvars = subparsers.add_parser("write-tfvars")
    tfvars.add_argument("--output", required=True)
    tfvars.add_argument("--pr", required=True)
    tfvars.add_argument("--sha", required=True)
    tfvars.add_argument("--instance-name", required=True)
    tfvars.add_argument("--base-domain", required=True)
    tfvars.add_argument("--aws-region", required=True)
    tfvars.add_argument("--route53-zone-id", required=True)
    tfvars.add_argument("--acme-email", required=True)
    tfvars.add_argument("--vpc-id", default="")
    tfvars.add_argument("--subnet-id", default="")
    tfvars.add_argument("--allowed-cidr", action="append", default=[])
    tfvars.set_defaults(func=command_write_tfvars)

    gcp_tfvars = subparsers.add_parser("write-gcp-tfvars")
    gcp_tfvars.add_argument("--output", required=True)
    gcp_tfvars.add_argument("--pr", required=True)
    gcp_tfvars.add_argument("--sha", required=True)
    gcp_tfvars.add_argument("--instance-name", required=True)
    gcp_tfvars.add_argument("--base-domain", required=True)
    gcp_tfvars.add_argument("--project-id", required=True)
    gcp_tfvars.add_argument("--region", required=True)
    gcp_tfvars.add_argument("--zone", required=True)
    gcp_tfvars.add_argument("--network", required=True)
    gcp_tfvars.add_argument("--subnetwork", required=True)
    gcp_tfvars.add_argument("--dns-managed-zone", required=True)
    gcp_tfvars.add_argument("--machine-type", default="c3d-standard-8")
    gcp_tfvars.add_argument("--boot-disk-size-gb", type=int, default=200)
    gcp_tfvars.add_argument("--allowed-admin-cidr", action="append", default=[])
    gcp_tfvars.set_defaults(func=command_write_gcp_tfvars)


    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
