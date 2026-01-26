twitch_event = {
    "id": str,
    "title": str,
    "category": str,
    "start_time": str,
    "is_recurring": bool
}

def normalize_event(raw):
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "Untitled Stream",
        "game": (raw.get("category") or {}).get("name", "Unknown Game"),
        "starts_at": raw.get("start_time"),
        "recurring": raw.get("is_recurring")
    }

