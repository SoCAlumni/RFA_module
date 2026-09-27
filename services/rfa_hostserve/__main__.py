"""python -m rfa_hostserve {review|knowledge|github-mcp} --port N

env:
  RFA_SANDBOX_HOST  샌드박스용 추가 바인드 주소 (없으면 127.0.0.1 만)
  RFA_TLS_CERT, RFA_TLS_KEY  github-mcp 의 샌드박스 바인드에 쓸 인증서 (scripts/make_certs.sh)
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import uvicorn

LOOPBACK = "127.0.0.1"


@dataclass(frozen=True)
class Bind:
    host: str
    port: int
    tls_cert: str | None = None
    tls_key: str | None = None


def _review() -> Any:
    from review.app import create_app

    return create_app()


def _knowledge() -> Any:
    from knowledge_stub.app import create_app

    return create_app()


def _github_mcp() -> Any:
    from mcp_channels.server import create_app

    return create_app()


APPS: dict[str, Callable[[], Any]] = {
    "review": _review,
    "knowledge": _knowledge,
    "github-mcp": _github_mcp,
}
TLS_REQUIRED_ON_SANDBOX = {"github-mcp"}


def plan_binds(target: str, port: int, env: Mapping[str, str]) -> list[Bind]:
    """어느 주소로 띄울지. 샌드박스 바인드는 RFA_SANDBOX_HOST 가 있을 때만."""
    binds = [Bind(LOOPBACK, port)]
    sandbox_host = env.get("RFA_SANDBOX_HOST", "").strip()
    if not sandbox_host or sandbox_host == LOOPBACK:
        return binds
    if target in TLS_REQUIRED_ON_SANDBOX:
        cert, key = env.get("RFA_TLS_CERT"), env.get("RFA_TLS_KEY")
        if not (cert and key):
            raise SystemExit(
                f"{target}: RFA_SANDBOX_HOST 바인드에는 RFA_TLS_CERT/RFA_TLS_KEY 가 필요해요"
            )
        binds.append(Bind(sandbox_host, port, cert, key))
    else:
        binds.append(Bind(sandbox_host, port))
    return binds


async def _serve(app: Any, binds: list[Bind]) -> None:
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                app,
                host=b.host,
                port=b.port,
                ssl_certfile=b.tls_cert,
                ssl_keyfile=b.tls_key,
                # 앱 lifespan(예: MCP 세션 매니저)은 한 번만 돌린다
                lifespan="auto" if i == 0 else "off",
            )
        )
        for i, b in enumerate(binds)
    ]
    await asyncio.gather(*(s.serve() for s in servers))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rfa_hostserve")
    parser.add_argument("target", choices=sorted(APPS))
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    binds = plan_binds(args.target, args.port, os.environ)
    for b in binds:
        scheme = "https" if b.tls_cert else "http"
        print(f"{args.target}: {scheme}://{b.host}:{b.port}", flush=True)
    asyncio.run(_serve(APPS[args.target](), binds))


if __name__ == "__main__":
    main()
