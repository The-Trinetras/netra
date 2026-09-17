from m2_retrieval import RetrievalEvaluationCase


def test_recall_at_five_is_bounded_to_top_five():
    case = RetrievalEvaluationCase("q", ("a",), ("x", "y", "z", "w", "v", "a"))
    assert case.recall_at_5() == 0.0
