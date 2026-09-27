import json

from rfa_workflow.cli import main
from rfa_workflow.deps import Deps
from rfa_workflow.llm import RuleLLM
from wf_support import github_mention


def test_run_prints_result_and_exit_code(env, capsys):
    code = main(["run", "--mention-json", github_mention().model_dump_json()], env.deps(RuleLLM()))
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["outcome"], out["approval_id"], out["round"]) == ("pending", 1, 1)


def test_run_from_file(env, tmp_path, capsys):
    path = tmp_path / "mention.json"
    path.write_text(github_mention().model_dump_json(), encoding="utf-8")
    assert main(["run", "--mention-file", str(path)], env.deps(RuleLLM())) == 0


def test_redo_rejected_approval(env, capsys):
    main(["run", "--mention-json", github_mention().model_dump_json()], env.deps(RuleLLM()))
    env.reject(1, "릴리즈 날짜가 들어가 있음")
    capsys.readouterr()

    assert main(["redo", "1"], env.deps(RuleLLM())) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["outcome"], out["round"]) == ("pending", 2)


def test_redo_unknown_approval_fails(env, capsys):
    assert main(["redo", "9"], env.deps(RuleLLM())) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "failed" and "404" in out["summary"]


def test_deps_from_env_reads_urls_and_llm_mode():
    deps = Deps.from_env({"HEAD_URL": "http://head:9", "APPROVALS_URL": "http://appr:8"})
    assert str(deps.head._http.base_url) == "http://head:9"
    assert str(deps.approvals._http.base_url) == "http://appr:8"
    assert isinstance(deps.llm, RuleLLM)
    defaults = Deps.from_env({"HEAD_URL": ""})  # 빈 값이면 기본 주소
    assert str(defaults.head._http.base_url) == "http://127.0.0.1:8791"
    assert str(defaults.approvals._http.base_url) == "http://127.0.0.1:8790"
