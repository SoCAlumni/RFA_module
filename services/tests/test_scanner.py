from pathlib import Path

import pytest
from knowledge_stub.loader import load_docs
from review.scanner import ScanRules, load_rules, scan

REPO_DATA = Path(__file__).resolve().parents[2] / "data"
RULES = ScanRules(hosts=("gpu-pool.internal",), path_prefixes=("/nfs/", "\\\\fileserver\\"))


def kinds(text: str, rules: ScanRules = RULES) -> list[tuple[str, str]]:
    return [(h.type, h.match) for h in scan(text, rules)]


@pytest.mark.parametrize(
    "token",
    [
        "hf_AbCdEf1234567890GhIjKlMn",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_11ABCDEFG0123456789_abcdefghij",
        "sk-ant-api03-abcdefghijklmnopqrst",
        "AKIAIOSFODNN7EXAMPLE",
        "xoxb-1234567890-abcdef",
    ],
)
def test_detects_tokens(token):
    assert kinds(f"토큰은 {token} 입니다") == [("token", token)]


@pytest.mark.parametrize(
    "text",
    ["hf_short", "myhf_AbCdEf1234567890GhIjKlMn", "sk-short", "AKIA123", "xoxa-1234567890-abc"],
)
def test_ignores_non_tokens(text):
    assert kinds(text) == []


@pytest.mark.parametrize(
    ("text", "ip"),
    [
        ("서버 10.12.3.4:8000 에서", "10.12.3.4"),
        ("(172.16.0.1)", "172.16.0.1"),
        ("172.31.255.254.", "172.31.255.254"),
        ("192.168.0.10,", "192.168.0.10"),
    ],
)
def test_detects_private_ips(text, ip):
    assert kinds(text) == [("private_ip", ip)]


@pytest.mark.parametrize(
    "text",
    ["8.8.8.8", "172.32.0.1", "172.15.0.1", "10.0.0.256", "110.1.2.3", "10.1.2.3.4", "v1.10.1.2"],
)
def test_ignores_public_or_malformed_ips(text):
    assert kinds(text) == []


def test_internal_host_is_case_insensitive_and_whole_name():
    assert kinds("접속: GPU-Pool.internal/api") == [("internal_host", "GPU-Pool.internal")]
    assert kinds("my-gpu-pool.internal") == []
    assert kinds("gpu-pool.internal.example.com") == []


def test_internal_path_takes_until_whitespace_or_quote():
    assert kinds("결과는 /nfs/prism/jobs/ 아래") == [("internal_path", "/nfs/prism/jobs/")]
    assert kinds("경로 '/nfs/a/b.txt'") == [("internal_path", "/nfs/a/b.txt")]
    assert kinds(r"공유 \\fileserver\team\x") == [("internal_path", r"\\fileserver\team\x")]


def test_overlapping_hits_keep_longer_one():
    text = "/nfs/keys/hf_AbCdEf1234567890GhIjKlMn.txt 와 10.0.0.1"
    assert kinds(text) == [
        ("internal_path", "/nfs/keys/hf_AbCdEf1234567890GhIjKlMn.txt"),
        ("private_ip", "10.0.0.1"),
    ]


def test_spans_point_at_match_and_are_sorted():
    text = "a 10.0.0.1 b hf_AbCdEf1234567890GhIjKlMn c"
    hits = scan(text, RULES)
    assert [h.span[0] for h in hits] == sorted(h.span[0] for h in hits)
    assert all(text[h.span[0] : h.span[1]] == h.match for h in hits)


def test_load_rules_skips_comments_blank_and_missing_files(tmp_path):
    (tmp_path / "internal_hosts.txt").write_text("# 주석\n\nhost.a\n  host.b  \n", encoding="utf-8")
    assert load_rules(tmp_path) == ScanRules(hosts=("host.a", "host.b"), path_prefixes=())


def test_repo_rules_catch_demo_data_secrets():
    rules = load_rules(REPO_DATA / "policy")
    orbit = "\n".join(d.body for d in load_docs(REPO_DATA / "knowledge", "orbit"))
    prism = "\n".join(d.body for d in load_docs(REPO_DATA / "knowledge", "prism"))
    clean = "\n".join(d.body for d in load_docs(REPO_DATA / "knowledge", "quantization"))
    assert {h.type for h in scan(orbit, rules)} == {"token", "private_ip"}
    assert kinds(prism, rules) == [("internal_path", "/nfs/prism/jobs/")]
    assert scan(clean, rules) == []
