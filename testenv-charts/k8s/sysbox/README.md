# Sysbox Installation for OpenHands Runtimes

This directory contains manifests to install [sysbox](https://github.com/nestybox/sysbox) on GKE nodes for OpenHands runtime containers.

## Version Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| sysbox-deploy-k8s | v0.6.7+ | Required for Ubuntu 24.04 (Noble) support |
| Kubernetes | 1.29 - 1.32 | K8s v1.32 supported in v0.6.7+ |
| Ubuntu | 22.04 or 24.04 | Container-Optimized OS not supported |
| Kernel | 5.15+ (22.04), 6.8+ (24.04) | See [distro-compat.md](https://github.com/nestybox/sysbox/blob/master/docs/distro-compat.md) |

**Important**: Sysbox v0.6.4 does NOT support Ubuntu 24.04. Use v0.6.7+ for Noble Numbat.

## Why Sysbox?

OpenHands runtime containers need to run Docker-in-Docker for executing user code in isolated environments. Sysbox provides:

- **Rootless containers**: Runtime containers run without privileged mode
- **Strong isolation**: Each runtime has its own container namespace
- **Docker-in-Docker**: Full Docker functionality inside runtime containers

## Prerequisites

1. **Node Pool Configuration**: Runtime nodes must be:
   - Labeled with `sysbox-install=yes`
   - Tainted with `sysbox-runtime=true:NoSchedule`
   - Running Ubuntu-based images (Container-Optimized OS not supported)

2. **Terraform Setup**: Use the `create_runtime_node_pool = true` option:
   ```hcl
   module "gke_cluster" {
     # ... other config ...
     create_runtime_node_pool        = true
     runtime_node_machine_type       = "e2-standard-8"
     runtime_node_disk_size_gb       = 200
     runtime_node_pool_min_count     = 1
     runtime_node_pool_max_count     = 10
   }
   ```

## Installation

```bash
# Apply sysbox installation manifests
kubectl apply -f sysbox-install.yaml

# Verify sysbox pods are running on runtime nodes
kubectl get pods -n sysbox -o wide

# Verify RuntimeClass is available
kubectl get runtimeclass sysbox-runc
```

## Verification

Check that sysbox is installed correctly:

```bash
# Check sysbox installer status
kubectl logs -n sysbox -l app.kubernetes.io/name=sysbox-installer -c sysbox-installer

# Test sysbox with a simple pod
kubectl run sysbox-test \
  --image=alpine \
  --restart=Never \
  --rm -it \
  --overrides='{"spec":{"runtimeClassName":"sysbox-runc","tolerations":[{"key":"sysbox-runtime","operator":"Equal","value":"true","effect":"NoSchedule"}],"nodeSelector":{"sysbox-install":"yes"}}}' \
  -- sh -c "echo 'Sysbox is working!'"
```

## How It Works

1. **DaemonSet**: `sysbox-installer` runs on nodes with `sysbox-install=yes` label
2. **Init Container**: Installs sysbox binaries and configures containerd
3. **RuntimeClass**: `sysbox-runc` is registered for pods to use
4. **Taints**: `sysbox-runtime=true:NoSchedule` prevents non-runtime workloads

## Node Labels and Taints

| Component | NodeSelector | Tolerations |
|-----------|--------------|-------------|
| sysbox-installer | `sysbox-install=yes` | `sysbox-runtime=true:NoSchedule` |
| image-loader | `sysbox-install=yes` | `sysbox-runtime=true:NoSchedule` |
| runtime pods | `sysbox-install=yes` | `sysbox-runtime=true:NoSchedule` |
| warm-runtimes | `sysbox-install=yes` | `sysbox-runtime=true:NoSchedule` |
| openhands app | (none - default nodes) | (none) |

## Troubleshooting

### Sysbox pods not starting
```bash
# Check node labels
kubectl get nodes --show-labels | grep sysbox

# Check for taint issues
kubectl describe pod -n sysbox <pod-name>
```

### RuntimeClass not found
```bash
# Verify RuntimeClass exists
kubectl get runtimeclass

# Check sysbox installer logs
kubectl logs -n sysbox -l app.kubernetes.io/name=sysbox-installer -c sysbox-installer
```

### Runtime pods failing to start
```bash
# Check if pods are scheduling to sysbox nodes
kubectl get pods -o wide | grep runtime

# Verify node has sysbox installed
kubectl exec -n sysbox <installer-pod> -- cat /mnt/host/etc/containerd/config.toml | grep sysbox
```

## Uninstallation

```bash
# Remove sysbox components
kubectl delete -f sysbox-install.yaml

# Note: Sysbox binaries remain on nodes until node replacement
```
