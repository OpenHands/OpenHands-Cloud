"""Opt-in rendered-chart checks against disposable loopback Keycloak only.

OIDC_TEST_URL, OIDC_TEST_ISSUER, OIDC_TEST_ADMIN_USERNAME/PASSWORD and OIDC_TEST_CLIENT_ID/SECRET
are required. Never use a production target. The fixture deletes only its own
randomly named test realm. Configured issuer discovery is used; no user credentials.
"""
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
import uuid

import pytest

yaml=pytest.importorskip('yaml')
ROOT=Path(__file__).resolve().parents[1]
BASE=os.getenv('OIDC_TEST_URL','')
ISSUER=os.getenv('OIDC_TEST_ISSUER','')
pytestmark=pytest.mark.skipif(not BASE,reason='requires disposable local Keycloak')

def token():
 data=urllib.parse.urlencode({'client_id':'admin-cli','grant_type':'password','username':os.environ['OIDC_TEST_ADMIN_USERNAME'],'password':os.environ['OIDC_TEST_ADMIN_PASSWORD']}).encode()
 return json.load(urllib.request.urlopen(BASE+'/realms/master/protocol/openid-connect/token',data=data,timeout=20))['access_token']

def api(path,body=None,method=None):
 req=urllib.request.Request(BASE+'/admin/realms'+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+token(),'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as r:
  raw=r.read();return json.loads(raw) if raw else None

@pytest.fixture
def realm(tmp_path):
 assert urllib.parse.urlsplit(BASE).hostname in {'localhost','127.0.0.1'}
 assert urllib.parse.urlsplit(BASE).scheme=='http'
 assert ISSUER.startswith('https://'),'OIDC_TEST_ISSUER must be configured'
 name='oidc-chart-test-'+uuid.uuid4().hex[:12]
 # The chart's fallback bootstrap username is admin; create it only inside
 # the explicitly disposable local Keycloak fixture, never a deployed service.
 if not api('/master/users?username=admin&exact=true'):
  api('/master/users',{'username':'admin','enabled':True,'credentials':[{'type':'password','value':os.environ['OIDC_TEST_ADMIN_PASSWORD'],'temporary':False}]})
  user=api('/master/users?username=admin&exact=true')[0]
  api('/master/users/'+user['id']+'/role-mappings/realm',[api('/master/roles/admin')])
 yield name,tmp_path
 realms=api('')
 if any(r['realm']==name for r in realms):api('/'+name,method='DELETE')

def startup(realm,enabled=True):
 name,tmp=realm
 args=['helm','template','oidc-test',str(ROOT/'charts/openhands'),'--set','keycloak.enabled=true','--set','enterpriseSSO.protocol=oidc','--set','enterpriseSSO.enabled='+str(enabled).lower(),'--set','enterpriseSSO.oidc.issuerUrl='+ISSUER,'--set','enterpriseSSO.oidc.clientId=fixture','--set','enterpriseSSO.oidc.existingSecret=fixture']
 docs=list(yaml.safe_load_all(subprocess.check_output(args,text=True)))
 maps={d['metadata']['name']:d['data'] for d in docs if d and d['kind']=='ConfigMap'}
 template=maps['keycloak-realm-template']['allhands-realm-github-provider.json.tmpl']
 obj=json.loads(template);obj['attributes']['frontendUrl']=BASE;obj['sslRequired']='none'
 file=tmp/'realm.json';file.write_text(json.dumps(obj))
 namespace_file=tmp/'namespace-realm-ids.py';namespace_file.write_text(maps['keycloak-config-script']['namespace-realm-ids.py'])
 python_file=tmp/'enterprise-oidc.py';python_file.write_text(maps['keycloak-config-script']['enterprise-oidc.py'])
 script=maps['keycloak-config-script']['keycloak-config.sh'].replace('/realm-template/allhands-realm-github-provider.json.tmpl',str(file)).replace('/tmp/',str(tmp)+'/').replace('python /scripts/enterprise-oidc.py','"'+os.environ['OIDC_TEST_PYTHON']+'" '+str(python_file)).replace('python /scripts/namespace-realm-ids.py','"'+os.environ['OIDC_TEST_PYTHON']+'" '+str(namespace_file)).replace('/scripts/keycloak-config.sh',str(tmp/'startup.sh'))
 env=dict(os.environ,KEYCLOAK_SERVER_URL=BASE,KEYCLOAK_REALM_NAME=name,KEYCLOAK_CLIENT_ID='allhands',KEYCLOAK_CLIENT_SECRET='fixture-app-secret',KEYCLOAK_ADMIN_PASSWORD=os.environ['OIDC_TEST_ADMIN_PASSWORD'],KEYCLOAK_ADMIN_CLIENT_ID='',KEYCLOAK_ADMIN_CLIENT_SECRET='',KEYCLOAK_ADMIN_UI_PASSWORD='',WEB_HOST='app.example.test',AUTH_WEB_HOST='auth.example.test',LAMINAR_WEB_HOST='analytics.example.test',GITHUB_BASE_URL='https://github.com',ENTERPRISE_SSO_DISPLAY_NAME='Fixture OIDC',ENTERPRISE_SSO_OIDC_ISSUER=ISSUER,ENTERPRISE_SSO_OIDC_CLIENT_ID=os.environ['OIDC_TEST_CLIENT_ID'],ENTERPRISE_SSO_OIDC_CLIENT_SECRET=os.environ['OIDC_TEST_CLIENT_SECRET'])
 script_file=tmp/'startup.sh';script_file.write_text(script)
 result=subprocess.run(['sh',str(script_file)],text=True,capture_output=True,env=env,timeout=90)
 # Never surface raw startup output: inherited helpers may print error bodies.
 if result.returncode:
  for suffix,text in [('stdout',result.stdout),('stderr',result.stderr)]:
   output=tmp/('startup.'+suffix);output.write_text(text);output.chmod(0o600)
  raise AssertionError('Rendered startup failed; inspect private operator output')
 return result

def test_rendered_startup_reconcile_disable_and_reenable(realm):
 name,_=realm
 startup(realm)
 p=api('/'+name+'/identity-provider/instances/enterprise_sso')
 assert p['providerId']=='oidc' and p['enabled']
 original=p['internalId']
 for key in ['validateSignature','pkceEnabled']:
  assert p['config'][key]=='true'
 assert p['config']['pkceMethod']=='S256'
 mappers=api('/'+name+'/identity-provider/instances/enterprise_sso/mappers')
 assert len(mappers)==2
 assert next(m for m in mappers if m['name']=='identity-provider')['config']['attribute.value']=='enterprise_sso:oidc'
 assert p['config']['filteredByClaim']=='false'
 assert p['firstBrokerLoginFlowAlias']=='openhands-oidc-create-only-v1'
 assert p['postBrokerLoginFlowAlias']=='openhands-oidc-verified-email-v1'
 startup(realm)
 assert api('/'+name+'/identity-provider/instances/enterprise_sso')['internalId']==original
 assert len(api('/'+name+'/identity-provider/instances/enterprise_sso/mappers'))==2
 startup(realm,False)
 assert api('/'+name+'/identity-provider/instances/enterprise_sso')['enabled'] is False
 startup(realm)
 assert api('/'+name+'/identity-provider/instances/enterprise_sso')['enabled'] is True


@pytest.mark.parametrize('precreate',[False,True])
def test_concurrent_rendered_startups_preserve_owned_flows(realm,precreate):
 name,tmp=realm
 if precreate:startup(realm)
 def run(index):
  directory=tmp/str(index)
  directory.mkdir()
  return startup((name,directory))
 with ThreadPoolExecutor(max_workers=3) as pool:
  results=list(pool.map(run,range(3)))
 assert all(result.returncode==0 for result in results)
 first=api('/'+name+'/authentication/flows/openhands-oidc-create-only-v1/executions')
 assert len(first)==1 and first[0]['providerId']=='idp-create-user-if-unique'
 assert len(api('/'+name+'/identity-provider/instances/enterprise_sso/mappers'))==2
 assert api('/'+name+'/identity-provider/instances/enterprise_sso')['enabled'] is True
 assert api('/'+name+'/clients?clientId=openhands-oidc-provisioning-lock-v1')==[]
 lock_id='openhands-oidc-provisioning-lock-v1-'+hashlib.sha256(name.encode()).hexdigest()[:24]
 assert api('/master/clients?clientId='+lock_id)==[]
 profile=api('/'+name+'/users/profile')
 attribute=next(a for a in profile['attributes'] if a['name']=='openhands_oidc_email_verified')
 assert attribute['permissions']=={'view':['admin'],'edit':['admin']}
