import json
import pytest
from typer.testing import CliRunner
from agent_sandbox.cli.agent_connections import app

@pytest.fixture
def catalog(monkeypatch):
    calls=[]
    def api(method,path,body=None):
        calls.append((method,path,body))
        if path.endswith('/models'):
            return {'provider':'codex','defaults':{'model':None,'effort':None},'items':[{'id':'a','name':'Model A','default':True,'efforts':['low','high']}]}
        return {'provider':'codex',**body}
    monkeypatch.setattr('agent_sandbox.cli.agent_models.api',api)
    return calls

def test_interactive_model_and_effort_picker(catalog):
    result=CliRunner().invoke(app,['model','--provider','codex'],input='a\nhigh\n')
    assert result.exit_code==0,result.output
    assert 'Model A' in result.output and 'low, high' in result.output
    assert catalog[-1][2]=={'model':'a','effort':'high'}

def test_explicit_json_and_invalid_effort(catalog):
    runner=CliRunner()
    result=runner.invoke(app,['model','a','--provider','codex','--effort','low','--json'])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['effort']=='low'
    result=runner.invoke(app,['model','a','--provider','codex','--effort','max'])
    assert result.exit_code!=0 and 'low, high' in result.output
    assert runner.invoke(app,['models','--provider','codex','--json']).exit_code==0
