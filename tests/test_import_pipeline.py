
import app.ingestion.import_pipeline as pipeline


def sample_record():
    return {
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "09:55",
        "subject": "Data Structures",
        "teacher_name": "Dr Rao",
        "room": "LHC 301",
        "original_text": "Monday 09:00 Data Structures",
        "confidence": 0.95,
        "review_status": "needs_review",
    }


def fake_extraction(records):
    return {
        "status": "extraction_completed",
        "source_document": "sample.png",
        "records": records,
    }


def test_records_are_not_imported_without_explicit_approval(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "run_extraction_pipeline",
        lambda **kwargs: fake_extraction([sample_record()]),
    )

    def unexpected_import(**kwargs):
        raise AssertionError("Importer must not be called")

    monkeypatch.setattr(
        pipeline, "import_timetable_records", unexpected_import
    )

    result = pipeline.run_import_pipeline(
        image_path="sample.png",
        llm_client=lambda system, prompt: "{}",
        timetable_id="sem4_a",
    )

    assert result["status"] == "nothing_imported"
    assert result["imported_count"] == 0
    assert result["pending_review_indices"] == [0]


def test_only_explicitly_approved_records_are_imported(monkeypatch):
    records = [sample_record(), sample_record()]
    captured = {}

    monkeypatch.setattr(
        pipeline,
        "run_extraction_pipeline",
        lambda **kwargs: fake_extraction(records),
    )

    def fake_import(**kwargs):
        captured.update(kwargs)
        return [{"status": "imported"}]

    monkeypatch.setattr(
        pipeline, "import_timetable_records", fake_import
    )

    result = pipeline.run_import_pipeline(
        image_path="sample.png",
        llm_client=lambda system, prompt: "{}",
        timetable_id="sem4_a",
        approved_record_indices=[1],
        semester=4,
        section="A",
    )

    assert len(captured["records"]) == 1
    assert captured["records"][0]["review_status"] == "verified"
    assert captured["timetable_id"] == "sem4_a"
    assert result["approved_count"] == 1
    assert result["pending_review_indices"] == [0]


def test_invalid_records_are_not_imported(monkeypatch):
    invalid = sample_record()
    invalid["room"] = ""

    monkeypatch.setattr(
        pipeline,
        "run_extraction_pipeline",
        lambda **kwargs: fake_extraction([invalid]),
    )

    def unexpected_import(**kwargs):
        raise AssertionError("Invalid record must not be imported")

    monkeypatch.setattr(
        pipeline, "import_timetable_records", unexpected_import
    )

    result = pipeline.run_import_pipeline(
        image_path="sample.png",
        llm_client=lambda system, prompt: "{}",
        timetable_id="sem4_a",
        approved_record_indices=[0],
    )

    assert result["status"] == "nothing_imported"
    assert result["invalid_records"][0]["index"] == 0
    assert "room" in result["invalid_records"][0]["missing_fields"]


def test_missing_timetable_id_is_rejected():
    try:
        pipeline.run_import_pipeline(
            image_path="sample.png",
            llm_client=lambda system, prompt: "{}",
            timetable_id="",
        )
    except ValueError as exc:
        assert "timetable_id" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_empty_extraction_does_not_call_importer(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "run_extraction_pipeline",
        lambda **kwargs: fake_extraction([]),
    )

    def unexpected_import(**kwargs):
        raise AssertionError("Empty extraction must not call importer")

    monkeypatch.setattr(
        pipeline, "import_timetable_records", unexpected_import
    )

    result = pipeline.run_import_pipeline(
        image_path="sample.png",
        llm_client=lambda system, prompt: "{}",
        timetable_id="sem4_a",
        approved_record_indices=[0],
    )

    assert result["status"] == "nothing_imported"
    assert result["imported_count"] == 0
