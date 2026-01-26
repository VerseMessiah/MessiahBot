from schedule_sync import normalize_event

raw = {
    "id": "abc123",
    "title": None,              # missing title
    "category": None,           # missing category
    "start_time": "2026-02-01T02:00:00Z",
    "is_recurring": False
}

print(normalize_event(raw))