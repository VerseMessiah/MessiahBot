import os
from plexapi.server import PlexServer

def get_plex_client() -> PlexServer:
    url = os.getenv("PLEX_URL", "").strip()
    token = os.getenv("PLEX_TOKEN", "").strip()
    if not url or not token:
        raise RuntimeError("Missing PLEX_URL or PLEX_TOKEN environment variables")
    return PlexServer(url, token)
