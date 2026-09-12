from netra_api.learning.tutor.providers.groq import TUTOR_MODEL_ID, GroqTutorModelConfig


def test_tutor_model_id_matches_runtime_baseline():
    assert TUTOR_MODEL_ID == "openai/gpt-oss-120b"


def test_groq_tutor_model_config_defaults_to_the_pinned_model():
    config = GroqTutorModelConfig()
    assert config.model_id == TUTOR_MODEL_ID
    assert config.model_id != "latest"
