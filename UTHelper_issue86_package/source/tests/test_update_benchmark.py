from scripts.benchmark_update_pipeline import benchmark_update_pipeline


def test_benchmark_reports_progress_and_transfer_reductions():
    result = benchmark_update_pipeline(
        fixture_bytes=46_882_816,
        chunk_bytes=64 * 1024,
        speeds_mib_s=(3.04, 20.0, 100.0),
        interruption_fraction=0.75,
    )

    assert result["raw_progress_events"] == 717
    assert [row["throttled_events"] for row in result["progress"]] == [145, 91, 91]
    assert all(row["reduction_percent"] >= 79.0 for row in result["progress"])
    assert result["restart_payload_percent"] == 175.0
    assert result["resume_payload_percent"] == 100.0
    assert result["android_remote_handoff_percent"] == 200.0
    assert result["android_local_handoff_percent"] == 100.0
