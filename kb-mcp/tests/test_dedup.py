from kb.dedup import DedupConfig, decide

CFG = DedupConfig(merge_threshold=0.92, skip_threshold=0.98)

def test_no_neighbor_creates():
    assert decide(None, CFG) == "created"

def test_below_merge_creates():
    assert decide(0.50, CFG) == "created"
    assert decide(0.9199, CFG) == "created"

def test_merge_band():
    assert decide(0.92, CFG) == "merged"
    assert decide(0.9799, CFG) == "merged"

def test_skip_band():
    assert decide(0.98, CFG) == "skipped"
    assert decide(1.0, CFG) == "skipped"
