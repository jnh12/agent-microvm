from typer.testing import CliRunner
from agent_sandbox.cli.main import app


def test_machine_webhook_cli(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setattr(
        "agent_sandbox.cli.machine_webhooks.service_url", lambda: "https://api.example.com"
    )
    row = {
        "id": "a" * 32,
        "name": "control",
        "computer": "dev",
        "trigger_path": "/v1/machine-webhooks/" + "a" * 32 + "/trigger",
    }
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        return {"items": [row]} if method == "GET" else row

    monkeypatch.setattr("agent_sandbox.cli.machine_webhooks.api", api)
    r = CliRunner()
    for args in [
        ["create", "control", "--computer", "dev", "--action", "wake", "--action", "shutdown"],
        ["list"],
        ["deliveries", "control"],
        ["rotate", "control"],
        ["revoke", "control"],
    ]:
        result = r.invoke(app, ["machine-webhook", *args, "--json"])
        assert result.exit_code == 0, result.output
    assert calls[0][2]["actions"] == ["wake", "shutdown"]
    assert (
        r.invoke(app, ["machine-webhook", "trigger", "control", "--request-key", "test"]).exit_code
        != 0
    )
    monkeypatch.setenv("VMNEST_WEBHOOK_SECRET", "test-secret")

    def send(self, method, path, **kwargs):
        assert kwargs["token"] == "test-secret" and kwargs["request_key"] == "test"
        assert kwargs["body"]["input"] == {"ticket": 42}
        return {"id": "delivery", "status": "pending"}

    monkeypatch.setattr("agent_sandbox.cli.machine_webhooks.AccountClient.request", send)
    result = r.invoke(
        app,
        [
            "machine-webhook",
            "trigger",
            "control",
            "--prompt",
            "Review",
            "--input-json",
            '{"ticket":42}',
            "--request-key",
            "test",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "test-secret" not in result.output


def test_both_webhook_delete_commands(monkeypatch):
    monkeypatch.setenv('VMNEST_MODE','hosted')
    calls=[]
    def api(method,path,body=None):
        calls.append((method,path))
        return {'items':[{'id':'a'*32,'name':'control'}]} if method=='GET' else {'status':'deleted'}
    monkeypatch.setattr('agent_sandbox.cli.webhooks.api',api)
    monkeypatch.setattr('agent_sandbox.cli.machine_webhooks.api',api)
    for command,root in [('webhook','webhooks'),('machine-webhook','machine-webhooks')]:
        result=CliRunner().invoke(app,[command,'delete','control','--json'])
        assert result.exit_code==0,result.output
        assert calls[-1]==('DELETE','/v1/'+root+'/'+'a'*32+'?permanent=true')
