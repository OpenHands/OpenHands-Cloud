"""Exercise discovery, ownership, idempotence and safe request behavior."""
import copy
from contextlib import nullcontext
import shutil
import subprocess
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('oidc',ROOT/'charts/openhands/files/enterprise-oidc.py')
oidc=importlib.util.module_from_spec(spec);spec.loader.exec_module(oidc)
ISSUER='https://tenant.okta.com'
DISC={'issuer':ISSUER,'authorization_endpoint':ISSUER+'/oauth2/v1/authorize','token_endpoint':ISSUER+'/oauth2/v1/token','userinfo_endpoint':ISSUER+'/oauth2/v1/userinfo','jwks_uri':ISSUER+'/oauth2/v1/keys','token_endpoint_auth_methods_supported':['client_secret_basic'],'code_challenge_methods_supported':['S256']}
ENV={'ENTERPRISE_SSO_OIDC_ISSUER':ISSUER,'ENTERPRISE_SSO_OIDC_CLIENT_ID':'client','ENTERPRISE_SSO_OIDC_CLIENT_SECRET':'special\n"secret','ENTERPRISE_SSO_DISPLAY_NAME':'Company OIDC','KEYCLOAK_SERVER_URL':'http://localhost:18089','KEYCLOAK_REALM_NAME':'test','ACCESS_TOKEN':'test-admin-token'}

@pytest.fixture(autouse=True)
def separately_tested_flow_setup(monkeypatch):
 # Real flow behavior and idempotence are covered against Keycloak26.3 by
 # test_oidc_claim_flows.py and rendered-chart integration, not an API mock.
 monkeypatch.setattr(oidc, 'security_flows', lambda *args: None)
 monkeypatch.setattr(oidc, 'provisioning_lock', lambda *args: nullcontext())


def test_security_and_secret_roundtrip():
 p=oidc.provider(DISC,ISSUER,'client',ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET'],'Company')
 assert p['config']['clientSecret']==ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET']
 assert p['config']['pkceMethod']=='S256' and p['config']['validateSignature']=='true'
 assert p['config']['disableNonce']=='false' and p['config']['useJwksUrl']=='true'
 assert p['trustEmail'] is True and p['storeToken'] is False
 assert p['config']['filteredByClaim']=='false'
 assert p['config']['syncMode']=='FORCE'
 assert p['firstBrokerLoginFlowAlias']==oidc.FIRST_FLOW
 assert p['postBrokerLoginFlowAlias']==oidc.POST_FLOW

@pytest.mark.parametrize('mutation',[{'issuer':ISSUER+'/'},{'token_endpoint':'http://tenant.okta.com/token'},{'jwks_uri':'https://evil.example/keys'},{'authorization_endpoint':'https://user@tenant.okta.com/authorize'},{'userinfo_endpoint':'https://tenant.okta.com/userinfo?token=x'},{'code_challenge_methods_supported':['plain']},{'token_endpoint_auth_methods_supported':['none']},{'jwks_uri':'https://tenant.okta.com/keys#fragment'}])
def test_reject_unsafe_discovery(mutation):
 with pytest.raises(ValueError): oidc.provider({**DISC,**mutation},ISSUER,'client','secret','Company')

@pytest.mark.parametrize('current',[{'providerId':'saml','config':{'openhandsManaged':'true'}},{'providerId':'oidc','config':{}},{'providerId':'oidc','config':{'openhandsProtocol':'oidc'}}])
def test_never_overwrite_other_owner_or_protocol(current):
 calls=[]
 def api(url,**kw):
  calls.append(kw)
  return DISC if '.well-known' in url else current
 with pytest.raises(ValueError,match='conflict'): oidc.reconcile(ENV,api)
 assert not any('body' in c for c in calls)

def test_create_and_repeat_reconcile():
 current=None;writes=[];mappers=[]
 def api(url,**kw):
  nonlocal current
  if '.well-known' in url:return DISC
  if url.endswith('/mappers'):
   if 'body' not in kw:return mappers
   mappers.append({**kw['body'],'id':'mapper-test'});return None
  if '/mappers/' in url:return None
  if 'body' not in kw:return current
  writes.append(kw['method']);current=copy.deepcopy(kw['body'])
 oidc.reconcile(ENV,api);oidc.reconcile(ENV,api)
 assert writes==['POST','PUT','PUT','PUT']
 assert len(mappers)==2
 assert current['enabled'] is True
 assert current['providerId']=='oidc'

@pytest.mark.parametrize('value',['http://tenant.okta.com','https://tenant.okta.com#fragment','https://x:secret@tenant.okta.com','https://tenant.okta.com?query=x','https://tenant.okta.com/ white'])
def test_unsafe_issuer(value):
 with pytest.raises(ValueError):oidc.https_url(value)

def test_no_redirect():
 assert oidc.NoRedirect().redirect_request(None,None,302,'redirect',{},'https://evil.example') is None

@pytest.mark.parametrize('field,value',[('issuer','https://other.okta.com'),('clientId','different-client')])
def test_immutable_identity(field,value):
 current=oidc.provider(DISC,ISSUER,'client','secret','Company')
 current['config'][field]=value
 writes=[]
 def api(url,**kw):
  if 'body' in kw:writes.append(kw)
  return DISC if '.well-known' in url else current
 with pytest.raises(ValueError,match='immutable'):oidc.reconcile(ENV,api)
 assert not writes

def test_discovery_outage_retains_only_matching_secure_configuration(capsys):
 current=oidc.provider(DISC,ISSUER,ENV['ENTERPRISE_SSO_OIDC_CLIENT_ID'],ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET'],ENV['ENTERPRISE_SSO_DISPLAY_NAME'])
 def api(url,**kw):
  if '.well-known' in url:raise oidc.TemporaryFailure
  if url.endswith('/mappers'):return []
  if 'body' not in kw:return current
 oidc.reconcile(ENV,api)
 assert 'retaining matching' in capsys.readouterr().err


def test_discovery_outage_can_resume_secure_broker_closed_for_startup():
 current=oidc.provider(DISC,ISSUER,ENV['ENTERPRISE_SSO_OIDC_CLIENT_ID'],ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET'],ENV['ENTERPRISE_SSO_DISPLAY_NAME'])
 current['enabled']=False
 writes=[]
 def api(url,**kwargs):
  if '.well-known' in url:raise oidc.TemporaryFailure
  if url.endswith('/mappers'):return []
  if 'body' in kwargs:writes.append(copy.deepcopy(kwargs['body']));return None
  return current
 oidc.reconcile(ENV,api)
 assert writes[-1]['enabled'] is True

@pytest.mark.parametrize('mutation',[None,{'validateSignature':'false'},{'disableNonce':'true'}])
def test_discovery_outage_never_accepts_missing_or_insecure_configuration(mutation):
 current=oidc.provider(DISC,ISSUER,ENV['ENTERPRISE_SSO_OIDC_CLIENT_ID'],ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET'],ENV['ENTERPRISE_SSO_DISPLAY_NAME']) if mutation is not None else None
 if current:current['config'].update(mutation)
 writes=[]
 def api(url,**kw):
  if '.well-known' in url:raise oidc.TemporaryFailure
  if 'body' in kw:writes.append(kw)
  return current
 with pytest.raises(oidc.ConfigurationError):oidc.reconcile(ENV,api)
 assert not writes

def test_provider_and_mapper_concurrent_creation():
 current=oidc.provider(DISC,ISSUER,ENV['ENTERPRISE_SSO_OIDC_CLIENT_ID'],ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET'],ENV['ENTERPRISE_SSO_DISPLAY_NAME'])
 provider_reads=0;methods=[];mappers=[]
 def api(url,**kw):
  nonlocal provider_reads
  if '.well-known' in url:return DISC
  if kw.get('method')=='POST':
   if url.endswith('/mappers'):
    mappers.append({**kw['body'],'id':'racing-'+str(len(mappers))})
   raise oidc.Conflict
  if kw.get('method')=='PUT':methods.append(kw);return None
  if url.endswith('/mappers'):return mappers
  provider_reads+=1;return None if provider_reads==1 else current
 oidc.reconcile(ENV,api)
 assert len(methods)==4
 assert methods[0]['body']['enabled'] is False
 assert methods[-1]['body']['enabled'] is True


def test_mapper_ownership_conflict_is_rejected():
 def api(url,**kw):
  if '.well-known' in url:return DISC
  if url.endswith('/mappers'):
   return [{'id':'custom','name':'identity-provider','identityProviderMapper':'custom','config':{}}]
  if 'body' not in kw:return None
 with pytest.raises(oidc.ConfigurationError,match='ownership conflict'):oidc.reconcile(ENV,api)


def test_realm_template_uuid_isolation():
 spec=importlib.util.spec_from_file_location('realm_ids',ROOT/'charts/openhands/files/namespace-realm-ids.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 original='{"id":"b99bc06b-072d-48aa-ab15-8b71aecd58de","reference":"b99bc06b-072d-48aa-ab15-8b71aecd58de","secret":"b99bc06b-072d-48aa-ab15-8b71aecd58de","clientId":"12345678-1234-1234-1234-123456789abc"}'
 assert module.namespace_ids(original,'allhands')==original
 isolated=module.namespace_ids(original,'allhands-oidc')
 assert isolated!=original and isolated==module.namespace_ids(original,'allhands-oidc')
 import json
 assert json.loads(isolated)['id']==json.loads(isolated)['reference']
 assert json.loads(isolated)['secret']==json.loads(original)['secret']
 assert json.loads(isolated)['clientId']==json.loads(original)['clientId']
 assert isolated!=module.namespace_ids(original,'another-realm')


def test_discovery_outage_reapplies_configured_secret_despite_keycloak_mask():
 current=oidc.provider(DISC,ISSUER,ENV['ENTERPRISE_SSO_OIDC_CLIENT_ID'],'**********',ENV['ENTERPRISE_SSO_DISPLAY_NAME'])
 writes=[]
 def api(url,**kw):
  if '.well-known' in url:raise oidc.TemporaryFailure
  if url.endswith('/mappers'):return []
  if 'body' in kw:writes.append(kw['body']);return None
  return current
 oidc.reconcile(ENV,api)
 assert writes[0]['config']['clientSecret']==ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET']


def test_mapper_failure_leaves_provider_disabled():
 current = oidc.provider(DISC, ISSUER, ENV['ENTERPRISE_SSO_OIDC_CLIENT_ID'], ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET'], 'Company')
 writes = []
 def api(url, **kw):
  if '.well-known' in url:return DISC
  if url.endswith('/mappers'):
   raise oidc.ConfigurationError('simulated mapper failure')
  if 'body' in kw:
   writes.append(copy.deepcopy(kw['body']))
   return None
  return current
 with pytest.raises(oidc.ConfigurationError,match='simulated mapper failure'):
  oidc.reconcile(ENV,api)
 assert len(writes)==1 and writes[0]['enabled'] is False


def test_flow_failure_leaves_existing_provider_disabled(monkeypatch):
 current=oidc.provider(DISC,ISSUER,ENV['ENTERPRISE_SSO_OIDC_CLIENT_ID'],ENV['ENTERPRISE_SSO_OIDC_CLIENT_SECRET'],'Company')
 writes=[]
 def fail(*args):raise oidc.ConfigurationError('simulated flow conflict')
 monkeypatch.setattr(oidc,'security_flows',fail)
 def api(url,**kw):
  if '.well-known' in url:return DISC
  if 'body' in kw:writes.append(copy.deepcopy(kw['body']));return None
  return current
 with pytest.raises(oidc.ConfigurationError,match='simulated flow conflict'):oidc.reconcile(ENV,api)
 assert len(writes)==1 and writes[0]['enabled'] is False


@pytest.mark.skipif(not shutil.which('helm'), reason='requires Helm')
def test_oidc_does_not_silently_enable_saml_only_policy():
 args=['helm','template','fixture',str(ROOT/'charts/openhands'),
       '--set','keycloak.enabled=true','--set','enterpriseSSO.enabled=true',
       '--set','enterpriseSSO.protocol=oidc','--set','enterpriseSSO.enforceOnly=true',
       '--set','enterpriseSSO.oidc.issuerUrl=https://oidc.example.test',
       '--set','enterpriseSSO.oidc.clientId=fixture','--set','enterpriseSSO.oidc.existingSecret=fixture']
 result=subprocess.run(args,capture_output=True,text=True)
 assert result.returncode!=0 and 'SAML only' in result.stderr


@pytest.mark.skipif(not shutil.which('helm'), reason='requires Helm')
def test_saml_startup_does_not_take_oidc_lock():
 result=subprocess.run(['helm','template','fixture',str(ROOT/'charts/openhands'),
                        '--set','keycloak.enabled=true','--set','enterpriseSSO.enabled=true',
                        '--set','enterpriseSSO.protocol=saml',
                        '--set','enterpriseSSO.idpMetadataUrl=https://saml.example.test/metadata'],
                       capture_output=True,text=True)
 assert result.returncode==0
 assert 'python /scripts/enterprise-oidc.py --startup-script /scripts/keycloak-config.sh' not in result.stdout
