"""The tests need the two PostgreSQL servers: ``make up`` (migrated) and ``make sync-exceptions``. They reseed a small
corpus before each test that asks for it."""

import pytest

from dclabaccount import exceptions, generator
from dclabaccount.db import Router


@pytest.fixture(scope="session")
def router():
    exceptions.sync()
    router = Router()
    yield router
    router.close()


@pytest.fixture
def corpus(router):
    return generator.seed(generator.TEST_SPECS)
