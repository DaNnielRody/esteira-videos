"""Authored source enters the same provider boundary with truthful provenance."""
import hashlib
from pathlib import Path

import pytest

from video_pipeline import provider


def test_authored_source_is_read_again_and_preserves_provenance(tmp_path: Path) -> None:
    folder = tmp_path / "ExampleScene"
    folder.mkdir()
    source = folder / "scene.py"
    source.write_text("class ExampleScene: pass\n")
    adapter = provider.AuthoredSourceProvider(tmp_path)
    request = provider.ProviderRequest(scene_name="ExampleScene", description="example")
    result = adapter.generate(request)
    assert result.code == source.read_text()
    assert result.raw_response["provider"] == "authored_source"
    assert result.raw_response["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_text("class ExampleScene: changed = True\n")
    assert adapter.generate(request).code == source.read_text()
    assert adapter.unload().ok


@pytest.mark.parametrize("name", ["../Escape", "MissingScene"])
def test_authored_source_rejects_missing_or_escaping_scene(tmp_path: Path, name: str) -> None:
    adapter = provider.AuthoredSourceProvider(tmp_path)
    with pytest.raises(provider.ProviderError):
        adapter.generate(provider.ProviderRequest(scene_name=name, description="example"))
