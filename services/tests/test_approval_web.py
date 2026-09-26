import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from review.app import INDEX_HTML, create_app


@pytest.fixture
def html(tmp_path: Path) -> str:
    res = TestClient(create_app(tmp_path)).get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    return res.text


def test_serves_approval_page(html):
    assert "<title>RFA 결재함</title>" in html
    assert html == INDEX_HTML.read_text(encoding="utf-8")


def test_page_calls_only_existing_endpoints(html, tmp_path):
    """페이지가 부르는 API 경로가 실제 앱에 있는지 (경로 오타 방지)."""
    routes = {r.path for r in create_app(tmp_path).routes}
    fetches = re.findall(r"fetch\((\S+?)[,)]", html)
    assert sorted(fetches) == ['"/reviews"', "`/reviews/${doc.id}/${path}`"]
    assert "/reviews" in routes
    actions = re.findall(r'act\("(\w+)"', html)
    assert sorted(actions) == ["approve", "reject"]
    for action in actions:
        assert f"/reviews/{{review_id}}/{action}" in routes


def test_page_never_injects_html_from_data(html):
    """GitHub 질문·LLM 초안이 HTML 로 해석되지 않도록 HTML 주입 API 를 쓰지 않는다."""
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in html
