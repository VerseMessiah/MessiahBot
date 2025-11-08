import os
from plexapi.server import PlexServer

def get_plex_server():
    """Connect to the Plex server using environment variables."""
    base_url = os.getenv("PLEX_URL")
    token = os.getenv("PLEX_TOKEN")
    if not base_url or not token:
        raise ValueError("PLEX_URL and PLEX_TOKEN environment variables must be set.")
    
    plex = PlexServer(base_url, token)
    return plex

def get_libary_names():
    plex = get_plex_server()
    return [lib.title for lib in plex.library.sections()]