import os
from pathlib import Path

import pytest

from sf_symbols_to_tgs.native import export_symbols
from sf_symbols_to_tgs.validate import validate_path
from sf_symbols_to_tgs.vector_to_tgs import convert_directory


@pytest.mark.skipif(not os.environ.get("SF_SYMBOLS_ASSETS_CAR"), reason="set SF_SYMBOLS_ASSETS_CAR to run native SF Symbols integration")
def test_native_export_convert_validate(tmp_path):
    symbols = tmp_path / "symbols.txt"
    vector_dir = tmp_path / "vector-json"
    tgs_dir = tmp_path / "tgs"
    symbols.write_text("car\nbus\n", encoding="utf-8")

    export_symbols(os.environ["SF_SYMBOLS_ASSETS_CAR"], symbols, vector_dir, weight="Regular")
    convert_directory(vector_dir, tgs_dir)

    assert len(validate_path(tgs_dir)) == 2
