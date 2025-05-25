import discord
from discord.ext import commands
import aiohttp

TWITCH_CLIENT_ID = '24c3r6ncbg6ihrnug1m5sa18jit564'
TWITCH_CLIENT_SECRET = 'd44o5pw354kjb1xyh24078lbd9kyxf'
TWITCH_TOKEN_URL = 'https://id.twitch.tv/oauth2/token'
TWITCH_USERS_ENDPOINT = 'https://api.twitch.tv/helix/users/follows'
TARGET_TWITCH_ID = 'YOUR_TWITCH_USER_ID'  # Replace with your actual Twitch user ID

twitch_token = None

class Verify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_twitch_token(self):
        global twitch_token
        async with aiohttp.ClientSession() as session:
            async with session.post(TWITCH_TOKEN_URL, params={
                'client_id': TWITCH_CLIENT_ID,
                'client_secret': TWITCH_CLIENT_SECRET,
                'grant_type': 'client_credentials'
            }) as resp:
                data = await resp.json()
                twitch_token = data['access_token']

    async def check_twitch_follow(self, username, target_id):
        global twitch_token
        if not twitch_token:
            await self.get_twitch_token()

        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.twitch.tv/helix/users?login={username}", headers={
                'Client-ID': TWITCH_CLIENT_ID,
                'Authorization': f'Bearer {twitch_token}'
            }) as resp:
                user_data = await resp.json()
                if not user_data.get('data'):
                    return False
                user_id = user_data['data'][0]['id']

            async with session.get(f"{TWITCH_USERS_ENDPOINT}?from_id={user_id}&to_id={target_id}", headers={
                'Client-ID': TWITCH_CLIENT_ID,
                'Authorization': f'Bearer {twitch_token}'
            }) as resp:
                data = await resp.json()
                return data.get('total', 0) > 0

    @commands.command()
    async def verify(self, ctx):
        await ctx.send("Please enter your Twitch username:")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await self.bot.wait_for('message', check=check, timeout=60)
            twitch_username = msg.content.strip()

            print(f"Verifying Twitch username: {twitch_username}")
            is_follower = await self.check_twitch_follow(twitch_username, TARGET_TWITCH_ID)
            print(f"Twitch follow status for {twitch_username}: {is_follower}")

            if is_follower:
                role = discord.utils.get(ctx.guild.roles, name='Disciple')  # Give Disciple role
                if role:
                    try:
                        await ctx.author.add_roles(role)
                        await ctx.send(f"{ctx.author.mention}, you have been blessed as a Disciple. 🛐")
                        print(f"✅ Role '{role.name}' assigned to {ctx.author}")
                    except discord.Forbidden:
                        await ctx.send("I tried to assign your role but was denied by the Discord hierarchy 😔")
                        print("❌ Discord blocked role assignment — check bot permissions and role order.")
                else:
                    await ctx.send("The Disciple role doesn't exist. Please let the mods know.")
                    print("❌ Role not found in the guild.")
            else:
                await ctx.send("You don’t appear to be following the Twitch account — or your username was entered wrong.")
                print("❌ Follow check failed.")

        except Exception as e:
            await ctx.send("Something went wrong or you took too long. Try again?")
            print(f"❌ Verification error: {e}")

# Required to register this cog with the bot
async def setup(bot):
    await bot.add_cog(Verify(bot))
