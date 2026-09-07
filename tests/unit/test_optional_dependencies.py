from __future__ import annotations

import pytest

from padawan.atlas import dependencies
from padawan.atlas.coding_judge import read_statement_shards, unpack_package


@pytest.mark.parametrize("feature", ["judge_package", "statement_shards", "tokenizer"])
def test_missing_coding_extra_fails_at_the_selected_feature(monkeypatch, tmp_path, feature):
    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(dependencies, "import_module", missing)
    with pytest.raises(RuntimeError, match="pip install.*coding"):
        if feature == "judge_package":
            unpack_package(
                tmp_path / "missing.zip",
                tmp_path / "out",
                problem_id="fixture",
                expected_digest="0",
            )
        elif feature == "statement_shards":
            read_statement_shards([])
        else:
            dependencies.coding_dependency("transformers")
    assert not (tmp_path / "out").exists()
