# AKS installation helpers

Use these helpers with Enterprise chart 0.74.0 / OpenHands 1.67.0 and Runtime API
0.10.0. The installation sequence is in `.agents/skills/aks-install.md`. Adapt
network ranges and capacity to the target environment. Set `AKS_VERSION` and
`NODE_VM_SIZE` before cluster creation to override the defaults after checking
regional support and quota. The namespaces are fixed to `openhands` and
`openhands-runtimes`.

## Optional Azure Disk checks

These helper checks still need validation on a fresh runc installation. Use the
dedicated `KUBECONFIG` from the installation sequence. Run the following block as
a Bash script so a timeout stops the check. Temporarily raise the
sandbox pool minimum to two nodes, wait for both to become Ready, then choose a
second node in the same disk-compatible topology as the writer.

```bash
az aks nodepool update --subscription "$SUBSCRIPTION" \
  --resource-group "$RESOURCE_GROUP" --cluster-name "$CLUSTER" --name sandbox \
  --update-cluster-autoscaler --min-count 2 --max-count 2
kubectl get nodes -l workload=openhands-sandbox -o wide
# Wait for the second node to join before checking readiness.
joined=false
for attempt in $(seq 1 90); do
  count=$(kubectl get nodes -l workload=openhands-sandbox -o name | wc -l)
  if [ "$count" -ge 2 ]; then joined=true; break; fi
  sleep 10
done
[ "$joined" = true ] || { echo 'Second sandbox node did not join' >&2; exit 1; }
kubectl wait node -l workload=openhands-sandbox --for=condition=Ready --timeout=15m
kubectl apply -f aks-install/runc-storage-smoke.yaml
kubectl -n openhands-runtimes wait pod/runc-storage-smoke \
  --for=jsonpath='{.status.phase}'=Succeeded --timeout=10m
kubectl -n openhands-runtimes logs runc-storage-smoke
kubectl -n openhands-runtimes get pod runc-storage-smoke -o wide
# Select a Ready node different from the writer node shown above.
export SECOND_SANDBOX_NODE=<other-ready-sandbox-node>
kubectl -n openhands-runtimes delete pod runc-storage-smoke --wait=true

envsubst '${SECOND_SANDBOX_NODE}' < aks-install/runc-storage-reschedule-smoke.yaml.tmpl > /tmp/aks-storage-reader.yaml
kubectl apply -f /tmp/aks-storage-reader.yaml
kubectl -n openhands-runtimes wait pod/runc-storage-reschedule-smoke \
  --for=jsonpath='{.status.phase}'=Succeeded --timeout=10m
kubectl -n openhands-runtimes logs runc-storage-reschedule-smoke
kubectl -n openhands-runtimes delete pod runc-storage-reschedule-smoke --wait=true

envsubst '${SECOND_SANDBOX_NODE}' < aks-install/runc-storage-expand-smoke.yaml.tmpl > /tmp/aks-storage-expand.yaml
kubectl apply -f /tmp/aks-storage-expand.yaml
kubectl -n openhands-runtimes wait pod/runc-storage-expand-smoke \
  --for=jsonpath='{.status.phase}'=Succeeded --timeout=15m
kubectl -n openhands-runtimes logs runc-storage-expand-smoke
kubectl -n openhands-runtimes get pvc runc-storage-smoke
kubectl -n openhands-runtimes delete pod runc-storage-expand-smoke --wait=true
kubectl -n openhands-runtimes delete pvc runc-storage-smoke
az aks nodepool update --subscription "$SUBSCRIPTION" \
  --resource-group "$RESOURCE_GROUP" --cluster-name "$CLUSTER" --name sandbox \
  --update-cluster-autoscaler --min-count 1 --max-count 2
```

Do not reapply the original 1Gi PVC manifest after expansion. If a check times out,
inspect Pod events and PVC conditions (including `FileSystemResizePending`)
before retrying. Remove each reader/writer
before attaching the RWO disk elsewhere. Restore the original pool minimum after
cleanup; do not bypass disruption budgets to force node removal.
