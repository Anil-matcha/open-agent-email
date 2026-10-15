from open_agent_email import AgentEmailClient
from tests.conftest import SECRET_KEY


def test_page_method_forwards_cursor_and_filters(server):
    server.route("GET", "/v1/agents/page", {"items": [{"id": "a"}], "next_cursor": "c1"})
    client = AgentEmailClient(server.url, SECRET_KEY)
    page = client.get_agents_page(include_deleted=True, limit=10, cursor="c0")
    assert page["items"][0]["id"] == "a"
    assert page["next_cursor"] == "c1"
    assert server.last["path"] == "/v1/agents/page"
    assert server.last["query"] == {"include_deleted": "True", "limit": "10", "cursor": "c0"}


def test_iterator_walks_all_pages_and_preserves_filters(server):
    client = AgentEmailClient(server.url, SECRET_KEY)
    responses = iter([
        {"items": [{"id": "a"}], "next_cursor": "c1"},
        {"items": [{"id": "b"}], "next_cursor": None},
    ])
    seen_queries = []
    def fake_request(method, path, query=None, **kwargs):
        seen_queries.append(query)
        return next(responses)
    client._request = fake_request
    assert [row["id"] for row in client.iter_engagements("example.test", due=True, limit=1)] == ["a", "b"]
    assert seen_queries == [
        {"due": True, "limit": 1, "cursor": None},
        {"due": True, "limit": 1, "cursor": "c1"},
    ]
