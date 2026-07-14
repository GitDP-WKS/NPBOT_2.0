from res_ai_v2.review_experience import present_task


def test_conflict_task_has_clear_question_and_unique_options():
    task = {
        "task_type": "mapping_conflict",
        "payload": {
            "address": {"locality": "Усады", "district": "Лаишевский"},
            "options": [
                {"branch": "Приволжские сети", "res": "Лаишевский район электрических сетей"},
                {"branch": "Приволжские сети", "res": "Лаишевский район электрических сетей"},
                {"branch": "Приволжские сети", "res": "Пригородный район электрических сетей"},
            ],
            "allow_multiple": True,
        },
    }

    view = present_task(task)

    assert view.question == "Какой РЭС действительно обслуживает этот адрес?"
    assert view.address_line == "Усады · Лаишевский"
    assert len(view.options) == 2
    assert view.allow_multiple is True


def test_missing_context_asks_for_district():
    task = {
        "task_type": "missing_context",
        "payload": {
            "address": {"locality": "Усады"},
            "options": [],
            "allow_address_edit": True,
        },
    }

    view = present_task(task)

    assert "району" in view.question.lower()
    assert view.address_line == "Усады"
    assert view.allow_address_edit is True


def test_unknown_address_does_not_invent_option():
    task = {
        "task_type": "unknown_address",
        "payload": {"address": {}, "options": []},
    }

    view = present_task(task)

    assert view.question == "Какой РЭС обслуживает этот адрес?"
    assert view.address_line == "Адрес не распознан"
    assert view.options == []
