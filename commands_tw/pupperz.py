import random
import aiohttp
import os
from twitchio.ext import commands
from overlay_server import update_overlay

GITHUB_API = "https://api.github.com/repos/VerseMessiah/pupperz/contents/"
REPO_URL = "https://raw.githubusercontent.com/VerseMessiah/pupperz/main/"
IMGUR_CLIENT_ID = os.getenv("IMGUR_CLIENT_ID")

class Pupperz(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.cached_images = []
        self.last_fetched = 0
        self.cache_duration = 3600

    async def get_images(self):
        if self.cached_images:
            return self.cached_images
        async with aiohttp.ClientSession() as session:
            async with session.get(GITHUB_API) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.cached_images = [
                        f"{REPO_URL}{file['name']}"
                        for file in data
                        if file["name"].lower().endswith((".png", ".jpg", ".jpeg"))
                    ]
                    return self.cached_images
        return []

    async def upload_to_imgur(self, image_url):
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as img_resp:
                img_data = await img_resp.read()

            headers = {
                "Authorization": f"Client-ID {IMGUR_CLIENT_ID}"
            }
            data = {
                "image": img_data,
                "type": "file"
            }
            async with session.post("https://api.imgur.com/3/image", headers=headers, data=data) as upload_resp:
                json_resp = await upload_resp.json()
                return json_resp['data']['link']

    @commands.command(name="pupperz")
    async def pupperz(self, ctx):
        images = await self.get_images()
        if images:
            chosen = random.choice(images)
            imgur_url = await self.upload_to_imgur(chosen)
            print(f"🐶 Uploaded image to Imgur: {imgur_url}")
            # Optional: you could still post a message here, or not
        else:
            print("❌ Failed to fetch images")
