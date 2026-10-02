#!/usr/bin/env bash
set -euo pipefail

# Point AZURE_CONFIG_DIR at the authenticated profile before running.
# This script creates billable evaluation resources. Reuse the existing cluster
# rather than rerunning if it already exists.
: "${SUBSCRIPTION:?set SUBSCRIPTION}" "${RESOURCE_GROUP:?set RESOURCE_GROUP}"
: "${REGION:?set REGION}" "${CLUSTER:?set CLUSTER}" "${KUBECONFIG:?set KUBECONFIG}"
subscription=$SUBSCRIPTION
resource_group=$RESOURCE_GROUP
region=$REGION
cluster=$CLUSTER

for provider in Microsoft.ContainerService Microsoft.Network Microsoft.Storage Microsoft.ManagedIdentity; do
  az provider register --subscription "$subscription" --namespace "$provider" --wait
done

az group create --subscription "$subscription" --name "$resource_group" \
  --location "$region" --tags purpose=openhands-aks-evaluation

az aks create --subscription "$subscription" \
  --resource-group "$resource_group" --name "$cluster" --location "$region" \
  --kubernetes-version 1.35 --tier free \
  --nodepool-name platform --node-count 2 --node-vm-size Standard_D4s_v3 \
  --os-sku Ubuntu --node-osdisk-type Managed --node-osdisk-size 64 \
  --nodepool-labels workload=openhands-platform \
  --enable-managed-identity \
  --network-plugin azure --network-plugin-mode overlay --network-policy calico \
  --pod-cidr 10.244.0.0/16 --service-cidr 10.0.0.0/16 --dns-service-ip 10.0.0.10 \
  --generate-ssh-keys \
  --tags purpose=openhands-aks-evaluation --no-wait

az aks wait --subscription "$subscription" --resource-group "$resource_group" \
  --name "$cluster" --created --interval 20 --timeout 1800

az aks nodepool add --subscription "$subscription" \
  --resource-group "$resource_group" --cluster-name "$cluster" \
  --name sandbox --mode User --node-count 1 --node-vm-size Standard_D4s_v3 \
  --os-sku Ubuntu --node-osdisk-type Managed --node-osdisk-size 64 \
  --labels workload=openhands-sandbox sysbox-install=yes \
  --enable-cluster-autoscaler --min-count 1 --max-count 2

# The current Sysbox installer requires the documented per-node correction on
# this AKS image, including any node added by the autoscaler. See .agents/skills/aks-install.md.
az aks get-credentials --subscription "$subscription" \
  --resource-group "$resource_group" --name "$cluster" \
  --file "$KUBECONFIG"
