from discord import GuildPreview
from discord import Guild, ScheduledEvent, EntityType

async def sync_events(guild: Guild):
    events: list[ScheduledEvent] = await guild.fetch_scheduled_events()


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
    "location": {
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

def normalize_discord(ev: ScheduledEvent) -> dict:
    
    return {
        "id": str(ev.id),
        "name": ev.name,
        "description": ev.description or "",
        "starts_at": ev.start_time.isoformat() if ev.start_time else None,
        "location": ev.location if ev.location else None,
    }


def twitch_to_discord(evt: dict) -> dict:
    return {
        "name": evt["title"],
        "description": f"Playing {evt["game"]} on Twitch",
        "scheduled_start_time": evt["starts_at"],
        "entity_type": "external",
        "location": {
            "location": f"https://twitch.tv/versemessiah?event_id={evt["id"]}"
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

def needs_update(twitch_evt, discord_evt) -> bool:
    if twitch_evt["title"] != discord_evt["name"]:
        return True
    if twitch_evt["starts_at"] != discord_evt["scheduled_start_time"]:
        return True
    expected_description = f"Playing {twitch_evt['game']} on Twitch"
    if expected_description != discord_evt["description"]:
        return True
    
    return False

twitch_events = []
discord_events = []

for raw in raw_twitch:
    twitch_events.append(normalize_twitch(raw))
print(twitch_events)

for event in twitch_events:
    discord_events.append(twitch_to_discord(event))
print(discord_events)

discord_by_twitch_id = {}
for de in discord_events:
    twitch_id = get_event_id(de["location"])

    if twitch_id:
        discord_by_twitch_id[twitch_id] = de

print(discord_by_twitch_id)

for te in twitch_events:
    twitch_id = te["id"]

    if twitch_id in discord_by_twitch_id:
        de = discord_by_twitch_id[twitch_id]

        if needs_update(te, de):
            print(f"UPDATE Discord event for Twitch ID {twitch_id}")
        else: 
            print(f"No Change for Twitch ID {twitch_id}")

    else:
        print(f"CREATE Discord event for Twitch ID {twitch_id}")

