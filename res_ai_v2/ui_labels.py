from __future__ import annotations

STATUS_LABELS = {
    "source_only": "Только данные источника",
    "consistent": "Противоречий не найдено",
    "human_verified": "Проверено человеком",
    "conflict": "Противоречие",
    "rejected": "Отклонено",
    "open": "Открыто",
    "closed": "Закрыто",
    "cancelled": "Отменено",
    "candidate": "Кандидат",
    "published": "Рабочая версия",
    "archived": "Архивная версия",
}

TASK_TYPE_LABELS = {
    "import_issue": "Ошибка или неполные данные при загрузке",
    "mapping_conflict": "Один адрес связан с разными РЭС",
    "missing_context": "Не хватает района или другой части адреса",
    "prediction_review": "Проверка результата определения",
    "model_error": "Ошибка модели",
    "low_confidence": "Низкая уверенность модели",
    "unknown_address": "Адрес не найден",
}

SOURCE_KIND_LABELS = {
    "address": "Адресная база",
    "text": "Размеченные тексты",
    "mixed": "Смешанные данные",
    "unknown": "Тип не определен",
}

ACTION_LABELS = {
    "review_vote": "Голос проверяющего сохранен",
    "apply_review": "Решение применено",
    "undo_review": "Решение отменено",
    "review_applied": "Решение применено",
    "review_reversed": "Решение отменено",
    "legacy_migrated": "Данные старой версии перенесены",
    "model_published": "Модель опубликована",
    "model_rolled_back": "Выполнен откат модели",
    "file_imported": "Файл загружен",
}

ENTITY_LABELS = {
    "review_vote": "Голос проверяющего",
    "review_task": "Задание проверки",
    "review_decision": "Решение проверки",
    "model": "Модель",
    "source_file": "Загруженный файл",
    "legacy": "Старая версия",
}


def status_label(value: object) -> str:
    text = str(value or "")
    return STATUS_LABELS.get(text, text or "Не указан")


def task_type_label(value: object) -> str:
    text = str(value or "")
    return TASK_TYPE_LABELS.get(text, text or "Не указан")


def source_kind_label(value: object) -> str:
    text = str(value or "")
    return SOURCE_KIND_LABELS.get(text, text or "Не определен")


def action_label(value: object) -> str:
    text = str(value or "")
    return ACTION_LABELS.get(text, text or "Не указано")


def entity_label(value: object) -> str:
    text = str(value or "")
    return ENTITY_LABELS.get(text, text or "Не указано")
