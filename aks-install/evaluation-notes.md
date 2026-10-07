# AKS helper compatibility

These templates target Enterprise Helm chart 0.74.0 / OpenHands 1.67.0 and Runtime API 0.10.0, with Azure Disk CSI, Traefik and standard runc sandboxes. Adapt resource sizes, network ranges and supported AKS versions to the target environment.

The runc deployment was evaluated on AKS 1.35.8 / Ubuntu 24.04.5 / containerd 2.3.3-2. Ordinary conversations and workspace persistence worked without node runtime repairs. Automations and PVC expansion have not been validated on the runc rebuild; their helpers are optional checks, not acceptance claims. The generalized package has not been rerun as a fresh installation.

Sysbox remains a separate unresolved AKS integration issue. This package does not install it or include manual node repairs. Detailed evaluation records are kept outside the installation skill.
