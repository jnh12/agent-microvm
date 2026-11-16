from agent_sandbox import accounts, remote


def test_clean_install_uses_production_without_url(monkeypatch):
    for name in ('VMNEST_MODE', 'VMNEST_API_URL', 'VMNEST_URL'):
        monkeypatch.delenv(name, raising=False)
    assert accounts.mode() == 'hosted'
    assert accounts.service_url() == 'https://api.vmnest.cc'
    assert remote.remote_url() == 'https://api.vmnest.cc'


def test_explicit_configuration_is_preserved(monkeypatch):
    for name in ('VMNEST_MODE', 'VMNEST_API_URL', 'VMNEST_URL'):
        monkeypatch.delenv(name, raising=False)
    accounts.save_config({'mode': 'self-hosted', 'url': 'https://custom.example'})
    assert remote.remote_url() == ''
    assert accounts.service_url() == 'https://custom.example'
    assert accounts.service_url('https://override.example') == 'https://override.example'
