from kb.db import rrf_fuse

def test_consensus_ranks_first():
    vector = ["a", "b", "c"]
    fts = ["a", "c", "d"]
    fused = rrf_fuse([vector, fts])
    ids = [i for i, _ in fused]
    assert ids[0] == "a"            # top of both lists
    assert set(ids) == {"a", "b", "c", "d"}

def test_deterministic_and_tie_broken_by_id():
    fused1 = rrf_fuse([["x", "y"], ["y", "x"]])
    fused2 = rrf_fuse([["x", "y"], ["y", "x"]])
    assert fused1 == fused2
    # x and y have identical fused scores -> stable order by id
    assert [i for i, _ in fused1] == ["x", "y"]

def test_empty_lists():
    assert rrf_fuse([[], []]) == []
