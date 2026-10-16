import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["YBSG_HOME"] = tempfile.mkdtemp(prefix="ybsg-test-")
import pytest
from ybsg.catalog import Catalog

@pytest.fixture(scope="session")
def cat():
    return Catalog()
