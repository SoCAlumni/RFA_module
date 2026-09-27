import asyncio
import socket
import threading
import time

import httpx
import pytest
from fake_github import FakeGithub
from mcp_channels.github import GithubClient
from mcp_channels.server import DESK_PATH, MCP_PATH, create_app
from rfa_hostserve.__main__ import Bind, build_servers, plan_binds, serve_all

TLS = {"RFA_TLS_CERT": "certs/host.pem", "RFA_TLS_KEY": "certs/host.key"}


def test_loopback_only_without_sandbox_host():
    assert plan_binds("review", 8790, {}) == [Bind("127.0.0.1", 8790)]
    assert plan_binds("review", 8790, {"RFA_SANDBOX_HOST": " "}) == [Bind("127.0.0.1", 8790)]
    assert plan_binds("review", 8790, {"RFA_SANDBOX_HOST": "127.0.0.1"}) == [
        Bind("127.0.0.1", 8790)
    ]


@pytest.mark.parametrize("target", ["review", "knowledge"])
def test_plain_http_services_add_sandbox_bind(target):
    env = {"RFA_SANDBOX_HOST": "172.18.0.1", **TLS}
    assert plan_binds(target, 8790, env) == [Bind("127.0.0.1", 8790), Bind("172.18.0.1", 8790)]


def test_github_mcp_sandbox_bind_is_tls_only():
    binds = plan_binds("github-mcp", 8792, {"RFA_SANDBOX_HOST": "172.18.0.1", **TLS})
    assert binds == [
        Bind("127.0.0.1", 8792),
        Bind("172.18.0.1", 8792, "certs/host.pem", "certs/host.key"),
    ]


def test_github_mcp_sandbox_bind_requires_cert():
    with pytest.raises(SystemExit, match="RFA_TLS_CERT"):
        plan_binds("github-mcp", 8792, {"RFA_SANDBOX_HOST": "172.18.0.1"})
    # 샌드박스 바인드가 없으면 인증서도 필요 없다
    assert plan_binds("github-mcp", 8792, {}) == [Bind("127.0.0.1", 8792)]


def free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def test_one_app_serves_every_bind(tmp_path):
    """lifespan 은 첫 서버만 돌지만 두 번째 주소에서도 MCP 가 응답한다 (세션 매니저 공유)."""
    env = {
        "GITHUB_TOKEN": "gh",
        "RFA_GITHUB_LOGIN": "zetwhite",
        "RFA_GITHUB_REPOS": "o/r",
        "GITHUB_MCP_TOKEN": "mcp-secret",
        "RFA_DATA_DIR": str(tmp_path),
        "RFA_MCP_ALLOWED_HOSTS": "127.0.0.1:*,127.0.0.2:*",
    }
    app = create_app(env, GithubClient("t", transport=FakeGithub().transport()))
    binds = [Bind("127.0.0.1", free_port("127.0.0.1")), Bind("127.0.0.2", free_port("127.0.0.2"))]
    servers = build_servers(app, binds)
    thread = threading.Thread(target=asyncio.run, args=(serve_all(servers),))
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not all(s.started for s in servers):
            assert time.monotonic() < deadline, "servers did not start"
            time.sleep(0.05)
        headers = {
            "authorization": "Bearer mcp-secret",
            "accept": "application/json, text/event-stream",
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        for b, path in zip(binds, [MCP_PATH, DESK_PATH], strict=True):
            res = httpx.post(f"http://{b.host}:{b.port}{path}", headers=headers, json=body)
            assert res.status_code == 200, (b, res.text)
            names = sorted(t["name"] for t in res.json()["result"]["tools"])
            assert names == ["get_thread", "list_mentions"]
    finally:
        for s in servers:
            s.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()
