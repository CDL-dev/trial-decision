from streamlit_app.presets import load_source_presets


def test_load_source_presets_reads_sim_clone_json_files():
    presets = load_source_presets(
        r"D:\decisionsystem\sim_clone\presets.json",
        r"D:\decisionsystem\sim_clone\city_presets.json",
    )
    assert "presets" in presets
    assert "city_presets" in presets
    assert isinstance(presets["presets"], dict)
    assert isinstance(presets["city_presets"], dict)
