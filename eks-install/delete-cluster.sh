#!/usr/bin/env bash
# Tear down the EKS test cluster in the RIGHT ORDER. An orphaned NLB and its
# ENIs/security groups block VPC (and therefore cluster) deletion, so the
# LoadBalancer service must go BEFORE eksctl delete cluster.
#
# Required env:
#   CLUSTER, REGION, HOSTED_ZONE_ID, BASE_DOMAIN
set -euo pipefail

: "${CLUSTER:?set CLUSTER}"
: "${REGION:?set REGION}"
: "${HOSTED_ZONE_ID:?set HOSTED_ZONE_ID}"
: "${BASE_DOMAIN:?set BASE_DOMAIN}"
CTX="${CLUSTER}.${REGION}.eksctl.io"

# Print the exact target and the keep-list before deleting anything — a shared
# account may hold other people's similarly named clusters.
echo "Clusters in $REGION:"
eksctl get cluster --region "$REGION" -o json 2>/dev/null \
  | python3 -c 'import json,sys,os
c=os.environ["CLUSTER"]
for x in json.load(sys.stdin):
    n=x.get("Name") or x.get("metadata",{}).get("name")
    print(("TARGET: " if n==c else "KEEP:   ")+str(n))' || true

# 1. Capture the NLB hostname, then delete the ingress + LoadBalancer so AWS
#    reclaims the NLB/ENIs before the VPC teardown.
LB_HOST="$(kubectl --context "$CTX" -n traefik get svc traefik \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
helm --kube-context "$CTX" uninstall openhands -n openhands 2>/dev/null || true
helm --kube-context "$CTX" uninstall traefik -n traefik 2>/dev/null || true
echo "Waiting for the NLB to be released..."
sleep 60

# 2. Remove the wildcard record (needs the current value to DELETE).
if [ -n "$LB_HOST" ]; then
  cat >/tmp/eks-wildcard-del.json <<JSON
{"Changes":[{"Action":"DELETE","ResourceRecordSet":{
  "Name":"*.${BASE_DOMAIN}","Type":"CNAME","TTL":300,
  "ResourceRecords":[{"Value":"${LB_HOST}"}]}}]}
JSON
  aws route53 change-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" \
    --change-batch file:///tmp/eks-wildcard-del.json || \
    echo "Wildcard record delete failed; remove *.${BASE_DOMAIN} by hand."
fi

# 3. Delete the cluster (VPC, nodegroup, IAM service accounts, addons).
eksctl delete cluster --name "$CLUSTER" --region "$REGION" --disable-nodegroup-eviction

# 4. EBS volumes from PVCs use reclaimPolicy Delete, so helm uninstall should
#    have removed them. Confirm none were orphaned (a straight cluster delete
#    without the uninstall above WILL leave them billing).
echo "Checking for orphaned EBS volumes tagged for this cluster..."
aws ec2 describe-volumes --region "$REGION" \
  --filters "Name=tag:kubernetes.io/cluster/${CLUSTER},Values=owned" \
  --query 'Volumes[].{id:VolumeId,size:Size,state:State}' --output table || true
echo "Delete any listed above by hand: aws ec2 delete-volume --region $REGION --volume-id <id>"
