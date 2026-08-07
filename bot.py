import os
import random
import aiohttp
import discord

from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from difflib import SequenceMatcher


# -----------------------------
# Config
# -----------------------------

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

BASE_URL = "https://www.sakugabooru.com"

ANIMATOR_CACHE = None


# Manual animator aliases
ANIMATOR_ALIASES = {
    "weilin zhang": "weilin_zhang",
    "yutaka nakamura": "yutaka_nakamura",
    "keiichiro watanabe": "keiichiro_watanabe",
}


# -----------------------------
# Discord setup
# -----------------------------

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# -----------------------------
# Fetch random clips
# -----------------------------

async def get_random_clips(tag: str, amount: int = 4):

    url = f"{BASE_URL}/post.json?tags={tag}&limit=100"

    async with aiohttp.ClientSession() as session:

        async with session.get(url) as resp:

            if resp.status != 200:
                return []

            posts = await resp.json()


    videos = [
        p for p in posts
        if p.get("file_ext") in ("mp4", "webm")
    ]


    if not videos:
        return []


    random.shuffle(videos)

    return videos[:min(amount, len(videos))]



# -----------------------------
# Fuzzy animator lookup
# -----------------------------

async def find_closest_animator_tag(query: str):

    global ANIMATOR_CACHE


    query = query.lower().replace(" ", "_")


    async with aiohttp.ClientSession() as session:


        # Exact lookup first
        url = f"{BASE_URL}/tag.json?name={query}&limit=1"


        async with session.get(url) as resp:

            if resp.status == 200:

                tags = await resp.json()

                if tags:

                    return tags[0]["name"]



        # Build cache
        if ANIMATOR_CACHE is None:

            ANIMATOR_CACHE = []


            for page in range(1, 21):

                url = (
                    f"{BASE_URL}/tag.json"
                    f"?limit=100&page={page}&order=count"
                )


                async with session.get(url) as resp:

                    if resp.status != 200:
                        break


                    tags = await resp.json()


                    for tag in tags:

                        name = tag.get("name", "")


                        # Ignore non-person tags
                        if "_" in name and not name.startswith("not_"):

                            ANIMATOR_CACHE.append(name)



    best = query
    best_score = 0.0


    for name in ANIMATOR_CACHE:


        # Ignore bad matches
        if name.startswith("not_"):
            continue


        score = SequenceMatcher(
            None,
            query,
            name
        ).ratio()


        if score > best_score:

            best_score = score
            best = name



    print(
        f"Search: {query} | Match: {best} | Score: {best_score}"
    )


    if best_score >= 0.75:

        return best


    return query



# -----------------------------
# Clip command
# -----------------------------

@bot.tree.command(
    name="clip",
    description="Get 3-4 random Sakugabooru clips from an animator"
)
@app_commands.describe(
    animator="Animator name (e.g. Yutaka Nakamura)"
)
async def clip(
    interaction: discord.Interaction,
    animator: str
):

    await interaction.response.defer()


    search_name = animator.strip().lower()


    if search_name in ANIMATOR_ALIASES:

        tag = ANIMATOR_ALIASES[search_name]

    else:

        tag = await find_closest_animator_tag(search_name)



    clips = await get_random_clips(
        tag,
        amount=4
    )


    display_name = tag.replace("_", " ").title()



    if not clips:

        await interaction.followup.send(
            f"No clips found for **{display_name}**."
        )

        return



    embed = discord.Embed(

        title=display_name,

        description="Use **/clip <animator name>** for more.",

        color=0x4DA3FF

    )


    embed.set_author(
        name="Sakuga Clips"
    )


    await interaction.followup.send(
        embed=embed
    )



    for post in clips:


        file_url = post.get("file_url")


        if file_url:


            if file_url.startswith("//"):

                file_url = "https:" + file_url


            elif file_url.startswith("/"):

                file_url = BASE_URL + file_url



            await interaction.channel.send(
                file_url
            )



# -----------------------------
# Ping command
# -----------------------------

@bot.tree.command(
    name="ping",
    description="Check if the bot is online"
)
async def ping(interaction: discord.Interaction):

    await interaction.response.send_message(
        "Pong! Sakuga Clips is online."
    )



# -----------------------------
# Ready
# -----------------------------

@bot.event
async def on_ready():

    try:

        synced = await bot.tree.sync()

        print(
            f"Synced {len(synced)} command(s)"
        )


    except Exception as e:

        print(e)



    print(
        f"Logged in as {bot.user}"
    )



# -----------------------------
# Start
# -----------------------------

bot.run(TOKEN)
