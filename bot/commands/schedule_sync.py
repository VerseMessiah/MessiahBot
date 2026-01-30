twitch_event = {
    "id": str,
    "title": str,
    "game": str,
    "starts_at": str,
    "recurring": bool
}

discord_event = {
    "name": str,
    "description": str,
    "scheduled_start_time": str,
    "entity_type": "external",
    "entity_metadata": {
        "location": str
    }
}

def normalize_twitch(raw):
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "Untitled Stream",
        "game": (raw.get("category") or {}).get("name", "Unknown Game"),
        "starts_at": raw.get("start_time"),
        "recurring": raw.get("is_recurring")
    }

def normalize_discord(raw):
    return {
        "name": str,
        "description": str,
        "scheduled_start_time": str,
        "entity_shape": "external",
        "entity_metadata": {
            "location": str
        }
    }

def twitch_to_discord(evt: dict) -> dict:
    return {
        "name": evt["title"],
        "description": f"Playing {evt["game"]} on Twitch",
        "scheduled_start_time": evt["starts_at"],
        "entity_type": "external",
        "entity_metadata": {
            "location": f"https://twitch.tv/versemessiah?eventid={evt["id"]}"
        }
    }

raw_twitch = [
    {
        "id": "abc123",
        "title": None,
        "category": None, 
        "start_time": "2026-02-01T02:00:00Z",
        "is_recurring": False
    },
    {
        "id": "def456",
        "title": "Late Night Stream",
        "category": {"name": "Fortnite"},
        "start_time": "2026-02-02T03:00:00Z",
        "is_recurring": True
    }
]

def get_event_id(location: str) -> str | None:
    if not location:
        return None
    if "event_id=" not in location:
        return None
    else:
        discord_event_id = location.split("event_id=")[1]
        return discord_event_id

normalized_twitch = []

for raw in raw_twitch:
    normalized_twitch.append(normalize_twitch(raw))

for event in normalized_twitch:
    print(event)
    mapped = twitch_to_discord(event)
    print(mapped)
