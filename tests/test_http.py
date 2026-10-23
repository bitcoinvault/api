import api

from blockchain import blockchain_analyzer


def test_status_http_contract_and_cache(chain_data, address_aggregations, monkeypatch):
    monkeypatch.setattr(blockchain_analyzer, 'get_hashrate', lambda: 123)
    with api.app.test_client() as client:
        for _ in range(2):
            response = client.get('/status')
            assert response.status_code == 200
            assert response.get_json() == {
                'height': 10, 'hashrate': 123, 'total_coin_supply': 7.0,
            }
    assert len(address_aggregations) == 1


def test_nonempty_count_http_contract_and_cache(chain_data, address_aggregations):
    with api.app.test_client() as client:
        for _ in range(2):
            response = client.get('/nonemptywalletsnumber')
            assert response.status_code == 200
            assert response.get_json() == 1
    assert len(address_aggregations) == 1
