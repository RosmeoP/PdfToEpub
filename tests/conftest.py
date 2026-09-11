import pytest

from pdftoepub.cache import clear_extract_cache


@pytest.fixture(autouse=True)
def _reset_extract_cache() -> None:
    clear_extract_cache()
    yield
    clear_extract_cache()
