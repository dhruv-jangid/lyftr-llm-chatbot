import os
import sys
import tempfile

import pytest

# Make "app" importable and "fake_llm" importable.
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, HERE)

import fake_llm  # noqa: E402

# Configure the app BEFORE it is imported: fake keys, fake LLM server, temp DB.
_server, _port = fake_llm.start()
_tmpdir = tempfile.mkdtemp()
TEST_KEYS = ["test-key-one", "test-key-two"]
os.environ.update({
    "LLM_API_KEYS": ",".join(TEST_KEYS),
    "LLM_MODEL": "openai/fake-model",
    "LLM_API_BASE": f"http://127.0.0.1:{_port}/v1",
    "DATABASE_URL": f"sqlite:///{_tmpdir}/test.db",
    "ALLOWED_ORIGINS": "http://localhost:3000",
    "ENABLE_FAILURE_SIMULATION": "true",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_fake():
    fake_llm.CALLS.clear()
    fake_llm.FAILING_KEYS.clear()
    yield
    fake_llm.FAILING_KEYS.clear()
