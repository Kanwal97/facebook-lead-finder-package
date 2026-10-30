import datetime as dt

from facebook import config


def test_load_merges_shared_junk_and_halves_in_warmup(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "profile: {about: freelance dev}\n"
        "filters: {junk_keywords: [Dropshipping]}\n"
        "facebook:\n"
        "  pacing: {groups_per_run: 6, warmup_until: 2026-11-15}\n"
        "  discovery: {junk_keywords: [crypto]}\n")
    cfg = config.load(path, today=dt.date(2026, 11, 1))
    assert cfg["discovery"]["junk_keywords"] == ["crypto", "dropshipping"]
    assert cfg["pacing"]["groups_per_run"] == 3          # halved
    assert cfg["pacing"]["scroll_pause_s"] == [4, 11]    # defaults kept
    later = config.load(path, today=dt.date(2026, 12, 1))
    assert later["pacing"]["groups_per_run"] == 6
