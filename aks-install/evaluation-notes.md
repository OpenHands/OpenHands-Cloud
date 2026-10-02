# AKS evaluation reference

The source evaluation on October 2, 2026 used:

| Component | Version or choice |
| --- | --- |
| Region / VM | eastus2, Standard_D4s_v3, regional placement |
| AKS / Ubuntu | 1.35.8 / 24.04.5 LTS |
| Kernel / containerd | 6.8.0-1067-azure / 2.3.3-2 |
| Pools | Two platform nodes; sandbox autoscaler 1–2 |
| Network | Azure CNI overlay, Calico, managed VNet |
| Storage | managed-csi, Azure Disk CSI |
| Ingress | Traefik chart 41.6.0 / image 3.7.13 |
| OpenHands | Helm chart 0.74.0 / application 1.67.0 |
| Agent server / automation | 1.49.6-python / 1.15.1 |
| Database / object store | Embedded PostgreSQL / bundled RustFS |

Login, a command-executing conversation, automation completion with callback,
and Azure Disk read/write, cross-node persistence and expansion passed. Both
sandbox nodes required the manual Sysbox registration correction. Future nodes
still need a maintained solution. TLS was manually provisioned.

External production PostgreSQL, Azure Blob, backup/restore, cross-zone recovery,
node reimage and unattended certificate renewal were not validated. The
generalized companion package has not been rerun as a fresh installation.
