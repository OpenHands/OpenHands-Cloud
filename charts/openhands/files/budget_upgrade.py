"""Stop legacy writers before migration; resume verified maintenance after rollout."""

import json
import os
import time

from kubernetes import client, config
from kubernetes.client.rest import ApiException


def containers(spec):
    return (spec.containers or []) + (spec.init_containers or [])


def enterprise_image(image):
    return image.split("@")[0].rsplit("/", 1)[-1].split(":")[0] == "enterprise-server"


def writer(spec):
    return any(enterprise_image(c.image) for c in containers(spec))


class Upgrade:
    def __init__(self, core, apps, batch, namespace, release, image, native_image):
        self.core, self.apps, self.batch = core, apps, batch
        self.namespace, self.release = namespace, release
        self.image, self.native_image = image, native_image
        self.name = f"{release}-budget-upgrade-state"

    def deployments(self):
        return [
            d
            for d in self.apps.list_namespaced_deployment(self.namespace).items
            if writer(d.spec.template.spec)
            or (self.native_image and d.metadata.name == f"{self.release}-litellm")
        ]

    def cronjobs(self):
        return [
            c
            for c in self.batch.list_namespaced_cron_job(self.namespace).items
            if writer(c.spec.job_template.spec.template.spec)
        ]

    def read_state(self):
        try:
            return json.loads(
                self.core.read_namespaced_config_map(self.name, self.namespace).data[
                    "state"
                ]
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            return None

    def save_state(self, state, create=False):
        body = {"metadata": {"name": self.name}, "data": {"state": json.dumps(state)}}
        if create:
            self.core.create_namespaced_config_map(self.namespace, body)
        else:
            self.core.patch_namespaced_config_map(self.name, self.namespace, body)

    def quiesce(self):
        state = self.read_state()
        if not state or state["phase"] == "complete":
            previous = state
            state = {
                "phase": "quiescing",
                "deployments": {
                    d.metadata.name: d.spec.replicas for d in self.deployments()
                },
                "cronjobs": {
                    c.metadata.name: bool(c.spec.suspend) for c in self.cronjobs()
                },
            }
            self.save_state(state, create=previous is None)
        for cron in self.cronjobs():
            self.batch.patch_namespaced_cron_job(
                cron.metadata.name, self.namespace, {"spec": {"suspend": True}}
            )
        for deploy in self.deployments():
            self.apps.patch_namespaced_deployment_scale(
                deploy.metadata.name, self.namespace, {"spec": {"replicas": 0}}
            )
        for job in self.batch.list_namespaced_job(self.namespace).items:
            if writer(job.spec.template.spec) and not self.is_hook(job):
                if not any(
                    c.type in {"Complete", "Failed"} and c.status == "True"
                    for c in (job.status.conditions or [])
                ):
                    self.batch.delete_namespaced_job(
                        job.metadata.name,
                        self.namespace,
                        propagation_policy="Foreground",
                    )
        deadline = time.monotonic() + 240
        while True:
            active = [
                p.metadata.name
                for p in self.core.list_namespaced_pod(self.namespace).items
                if not self.is_hook(p)
                and p.status.phase not in {"Succeeded", "Failed"}
                and (
                    writer(p.spec)
                    or (
                        self.native_image
                        and any(
                            c.image == self.native_image or "litellm" in c.image
                            for c in containers(p.spec)
                        )
                    )
                )
            ]
            if not active:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Budget upgrade still has active writers: {active}")
            time.sleep(2)
        state["phase"] = "quiesced"
        self.save_state(state)
        print("Old budget writers stopped; migration may proceed.", flush=True)

    @staticmethod
    def is_hook(resource):
        return (resource.metadata.labels or {}).get(
            "app.kubernetes.io/component"
        ) == "budget-upgrade"

    def verify_spec(self, spec, name):
        for container in containers(spec):
            if enterprise_image(container.image) and container.image != self.image:
                raise RuntimeError(
                    f"Old Enterprise image remains in {name}/{container.name}"
                )

    def resume(self):
        state = self.read_state() or {
            "phase": "installed",
            "cronjobs": {},
            "deployments": {},
        }
        for deploy in self.deployments():
            self.verify_spec(deploy.spec.template.spec, deploy.metadata.name)
            if deploy.metadata.name == f"{self.release}-litellm":
                if any(
                    c.image != self.native_image
                    for c in deploy.spec.template.spec.containers
                ):
                    raise RuntimeError(
                        "LiteLLM image does not match the budget release"
                    )
        cronjobs = self.cronjobs()
        for cron in cronjobs:
            self.verify_spec(
                cron.spec.job_template.spec.template.spec, cron.metadata.name
            )
        for pod in self.core.list_namespaced_pod(self.namespace).items:
            if pod.status.phase not in {"Succeeded", "Failed"}:
                self.verify_spec(pod.spec, pod.metadata.name)
        for cron in cronjobs:
            suspended = state["cronjobs"].get(cron.metadata.name, False)
            if cron.metadata.name == f"{self.release}-budget-maintenance":
                suspended = False
            self.batch.patch_namespaced_cron_job(
                cron.metadata.name, self.namespace, {"spec": {"suspend": suspended}}
            )
        state["phase"] = "complete"
        self.save_state(state, create=self.read_state() is None)
        print("Verified new writers; budget maintenance resumed.", flush=True)


if __name__ == "__main__":
    config.load_incluster_config()
    upgrade = Upgrade(
        client.CoreV1Api(),
        client.AppsV1Api(),
        client.BatchV1Api(),
        os.environ["POD_NAMESPACE"],
        os.environ["RELEASE_NAME"],
        os.environ["ENTERPRISE_IMAGE"],
        os.environ.get("LITELLM_IMAGE", ""),
    )
    if os.environ["UPGRADE_PHASE"] == "pre":
        upgrade.quiesce()
    else:
        upgrade.resume()
