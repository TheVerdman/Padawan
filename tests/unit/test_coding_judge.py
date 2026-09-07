from __future__ import annotations

import stat
import subprocess
from zipfile import ZipFile, ZipInfo

import pytest

from padawan.atlas.coding_judge import DockerBatchJudge, file_sha256, unpack_package
from padawan.atlas.coding_runner import extract_cpp


def archive(tmp_path, *, additions=None, count=1):
    target = tmp_path / "cases.zip"
    with ZipFile(target, "w") as zipped:
        zipped.writestr(
            "config.yaml",
            f"""type: default
time_limit: 2s
memory_limit: 256m
checker: checker.cpp
subtasks:
- n_cases: {count}
  score: 100
""",
        )
        zipped.writestr("checker.cpp", "int main() { return 0; }")
        zipped.writestr("testdata/1.in", "1\n")
        zipped.writestr("testdata/1.ans", "1\n")
        for name, value in (additions or {}).items():
            zipped.writestr(name, value)
    return target


@pytest.mark.parametrize("path", ["../escape", "/escape", "testdata/../../escape", "x\\escape"])
def test_archive_cannot_escape_before_any_extraction(tmp_path, path):
    source = archive(tmp_path, additions={path: "not extracted"})
    with pytest.raises(ValueError):
        unpack_package(
            source, tmp_path / "out", problem_id="fixture", expected_digest=file_sha256(source)
        )
    assert not (tmp_path / "out").exists()


def test_symlink_archive_is_refused(tmp_path):
    member = ZipInfo("link")
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    source = archive(tmp_path, additions={member: "../../escape"})
    with pytest.raises(ValueError, match="non-regular"):
        unpack_package(
            source, tmp_path / "out", problem_id="fixture", expected_digest=file_sha256(source)
        )


def test_missing_declared_case_is_never_silently_skipped(tmp_path):
    source = archive(tmp_path, count=2)
    with pytest.raises(ValueError, match="missing"):
        unpack_package(
            source, tmp_path / "out", problem_id="fixture", expected_digest=file_sha256(source)
        )


def test_complete_extra_tests_require_explicit_inclusion_and_are_retained(tmp_path):
    source = archive(tmp_path, additions={"testdata/2.in": "2", "testdata/2.ans": "2"})
    with pytest.raises(ValueError, match="coverage"):
        unpack_package(
            source, tmp_path / "out", problem_id="fixture", expected_digest=file_sha256(source)
        )
    result = unpack_package(
        source,
        tmp_path / "out",
        problem_id="fixture",
        expected_digest=file_sha256(source),
        include_extra_cases=True,
    )
    assert result.declared_case_count == 1
    assert len(result.cases) == 2


def test_incomplete_extra_tests_remain_invalid(tmp_path):
    source = archive(tmp_path, additions={"testdata/2.in": "2"})
    with pytest.raises(ValueError, match="complete pairs"):
        unpack_package(
            source,
            tmp_path / "out",
            problem_id="fixture",
            expected_digest=file_sha256(source),
            include_extra_cases=True,
        )


def test_cpp_parser_takes_final_code_block_without_evaluating_text():
    assert extract_cpp("Explanation\n```cpp\nint main(){}\n```\nDone") == "int main(){}"
    assert extract_cpp("int main(){}") == "int main(){}"


def test_expired_deadline_has_no_container_creation_effect(tmp_path, monkeypatch):
    testlib = tmp_path / "testlib.h"
    testlib.write_text("// disposable header for a no-execution control\n")
    judge = DockerBatchJudge(
        image="sha256:" + "0" * 64,
        scratch=tmp_path / "scratch",
        testlib=testlib,
        testlib_digest=file_sha256(testlib),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("an expired deadline must not send a Docker create/start effect")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    output = tmp_path / "stdout"
    assert judge._run(
        ["true"],
        work=tmp_path,
        writable=False,
        memory=1024**3,
        seconds=1,
        output=output,
        deadline=-1,
    ) == (124, False)
    assert output.read_bytes() == b""
    assert output.with_suffix(".stderr").read_bytes() == b""
