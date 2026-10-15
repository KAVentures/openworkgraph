"""MCP tool guidance stays aligned with implemented, consent-gated source readers."""
from mcp_server.record_graph_tools import register_record_graph_tools


def test_local_mcp_record_graph_explains_stripe_jira_and_github_limits():
    handlers = []
    class FakeMCP:
        def tool(self, **kwargs):
            def register(fn):
                handlers.append(fn)
                return fn
            return register
    server = FakeMCP()
    register_record_graph_tools(server, object())
    assert len(handlers) == 1
    description = handlers[0].__doc__ or ""
    for expected in ("GitHub", "Jira", "Stripe", "default-off",
                     "bank settlement", "unknown"):
        assert expected in description
    assert "Other source systems and proven human work acceptance are not supported" not in description
