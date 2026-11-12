#!/usr/bin/env python3
"""In-pod deterministic probe skeleton for KinD control-vs-treatment testing.

Copy to /tmp/probe.py, cp it into the app pod (pin the kind context), then run it
against both the control and treatment images:

    KCTX=kind-<cluster>
    kubectl --context "$KCTX" cp /tmp/probe.py <ns>/<app-pod>:/tmp/probe.py -c openhands
    kubectl --context "$KCTX" exec -n <ns> <app-pod> -c openhands -- python /tmp/probe.py <USER_ID> <ORG_ID>

Why in-pod: SaaS secret columns are encrypted and the effective LLM config is a
merge of org defaults + member diff + active profile. Reading via the app's stores
yields decrypted, resolved values. Adapt `report()` to the PR under test, and reset
any shared state to a known baseline before each run so control and treatment start
identically.
"""

import asyncio
import sys
import uuid

from storage.saas_settings_store import SaasSettingsStore


async def report(user_id: str, org_id: str) -> None:
    store = await SaasSettingsStore.get_instance(
        user_id, effective_org_id=uuid.UUID(org_id)
    )
    settings = await store.load(resolve_agent_profile=True)
    llm = settings.agent_settings.llm
    api_key = (llm.api_key.get_secret_value() if llm.api_key else '') or ''
    print('resolved llm.model   :', llm.model)
    print('resolved llm.base_url:', llm.base_url)
    print(
        'resolved llm.api_key :',
        (api_key[:4] + f'… (len {len(api_key)})') if api_key else '(none)',
    )

    # --- Adapt below per PR --------------------------------------------------
    # Concurrency/rotation example (run N concurrent forced rotations and count
    # failures + surviving keys):
    #   import asyncio
    #   async def one(i):
    #       s = await SaasSettingsStore.get_instance(user_id, effective_org_id=uuid.UUID(org_id))
    #       try:
    #           r = await s.rotate_managed_llm_key()
    #           return f'rot{i}: OK {r.status}'
    #       except Exception as e:  # noqa: BLE001
    #           return f'rot{i}: EXC {type(e).__name__}'
    #   print(*await asyncio.gather(*[one(i) for i in range(4)]), sep='\n')


def main() -> None:
    if len(sys.argv) < 3:
        print('usage: probe.py <USER_ID> <ORG_ID>')
        sys.exit(2)
    asyncio.run(report(sys.argv[1], sys.argv[2]))


if __name__ == '__main__':
    main()
