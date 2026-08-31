import os
import random
import time
import asyncio
import aiohttp
import discord

from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from difflib import SequenceMatcher


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

BASE_URL = "https://www.sakugabooru.com"

# Number of clips sent by /clip
CLIP_AMOUNT = 4

# Per-user cooldown
COOLDOWN_SECONDS = 5

# Clip cache duration
CLIP_CACHE_SECONDS = 300  # 5 minutes

# Animator tag cache
# Built once and kept until the bot restarts.
ANIMATOR_CACHE = None

# Shared aiohttp session
HTTP_SESSION = None

# User cooldowns
USER_COOLDOWNS = {}

# Clip cache
#
# Format:
#
# {
#     "yutaka_nakamura": {
#         "time": 123456789,
#         "clips": [...]
#     }
# }
#
CLIP_CACHE = {}


# ============================================================
# MANUAL ANIMATOR ALIASES
# ============================================================

ANIMATOR_ALIASES = {
    "weilin zhang": "weilin_zhang",
    "yutaka nakamura": "yutaka_nakamura",
    "keiichiro watanabe": "keiichiro_watanabe",
    "yoshinori kanada": "yoshinori_kanada",

    # Vincent Chansard
    "vincent chansard": "vincent_chansard",
}


# ============================================================
# DISCORD SETUP
# ============================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# HTTP SESSION
# ============================================================

async def get_http_session():
    """
    Return the shared aiohttp session.

    The bot reuses one HTTP session instead of creating a new
    ClientSession for every command.
    """

    global HTTP_SESSION

    if HTTP_SESSION is None or HTTP_SESSION.closed:

        timeout = aiohttp.ClientTimeout(
            total=20
        )

        HTTP_SESSION = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": (
                    "SakugaClipsBot/1.0 "
                    "(Discord bot)"
                )
            }
        )

    return HTTP_SESSION


# ============================================================
# HELPERS
# ============================================================

def clean_animator_name(name: str) -> str:
    """
    Clean Sakugabooru-style animator names.

    Example:

        Art;yutaka_nakamura
        ->
        yutaka nakamura
    """

    if ";" in name:
        name = name.split(";")[-1]

    return name.replace("_", " ").strip()


def normalize_animator_query(name: str) -> str:
    """
    Normalize a user's animator search.

    Example:

        Vincent    Chansard
        ->
        vincent chansard
    """

    name = clean_animator_name(name)

    return " ".join(
        name.lower().split()
    )


def get_remaining_cooldown(user_id: int):
    """
    Return remaining cooldown time for a user.

    Returns:
        0 if the user can use /clip.
        >0 if the user is still on cooldown.
    """

    now = time.monotonic()

    last_used = USER_COOLDOWNS.get(
        user_id
    )

    if last_used is None:
        return 0

    elapsed = now - last_used

    remaining = (
        COOLDOWN_SECONDS - elapsed
    )

    if remaining <= 0:

        USER_COOLDOWNS.pop(
            user_id,
            None
        )

        return 0

    return remaining


# ============================================================
# FETCH RANDOM CLIPS
# ============================================================

async def get_random_clips(
    tag: str,
    amount: int = 4
):
    """
    Get random video clips for an animator.

    Results are cached for a few minutes to reduce repeated
    requests to Sakugabooru.
    """

    now = time.monotonic()

    # --------------------------------------------------------
    # CHECK CACHE
    # --------------------------------------------------------

    cached = CLIP_CACHE.get(tag)

    if cached:

        cache_age = (
            now - cached["time"]
        )

        if cache_age < CLIP_CACHE_SECONDS:

            clips = cached["clips"]

            if clips:

                return random.sample(
                    clips,
                    min(
                        amount,
                        len(clips)
                    )
                )

        else:

            # Remove expired cache
            CLIP_CACHE.pop(
                tag,
                None
            )

    # --------------------------------------------------------
    # FETCH FROM SAKUGABOORU
    # --------------------------------------------------------

    session = await get_http_session()

    url = (
        f"{BASE_URL}/post.json"
        f"?tags={tag}"
        f"&limit=100"
    )

    try:

        async with session.get(url) as resp:

            if resp.status != 200:

                print(
                    f"Sakugabooru returned "
                    f"HTTP {resp.status} "
                    f"for {tag}"
                )

                return []

            posts = await resp.json()

    except asyncio.TimeoutError:

        print(
            f"Sakugabooru request timed out: "
            f"{tag}"
        )

        return []

    except aiohttp.ClientError as e:

        print(
            f"Sakugabooru request failed: "
            f"{tag} | {e}"
        )

        return []

    except Exception as e:

        print(
            f"Unexpected Sakugabooru error: "
            f"{tag} | {e}"
        )

        return []

    # --------------------------------------------------------
    # ONLY KEEP VIDEO POSTS
    # --------------------------------------------------------

    videos = [
        p
        for p in posts
        if p.get("file_ext")
        in ("mp4", "webm")
        and p.get("file_url")
    ]

    if not videos:
        return []

    # --------------------------------------------------------
    # SAVE TO CACHE
    # --------------------------------------------------------

    CLIP_CACHE[tag] = {
        "time": now,
        "clips": videos
    }

    # --------------------------------------------------------
    # RETURN RANDOM CLIPS
    # --------------------------------------------------------

    random.shuffle(videos)

    return videos[
        :min(
            amount,
            len(videos)
        )
    ]


# ============================================================
# FUZZY ANIMATOR LOOKUP
# ============================================================

async def find_closest_animator_tag(
    query: str
):

    global ANIMATOR_CACHE

    query = (
        query
        .lower()
        .replace(" ", "_")
        .strip()
    )

    session = await get_http_session()

    # --------------------------------------------------------
    # EXACT LOOKUP
    # --------------------------------------------------------
    #
    # IMPORTANT:
    #
    # We request multiple results and ONLY accept a tag whose
    # name is exactly the requested tag.
    #
    # This prevents:
    #
    #     vincent_chansard
    #
    # from accidentally becoming:
    #
    #     not_vincent_chansard
    #
    # --------------------------------------------------------

    url = (
        f"{BASE_URL}/tag.json"
        f"?name={query}"
        f"&limit=20"
    )

    try:

        async with session.get(url) as resp:

            if resp.status == 200:

                tags = await resp.json()

                for tag in tags:

                    tag_name = tag.get(
                        "name",
                        ""
                    )

                    # Never accept not_* tags
                    if tag_name.startswith(
                        "not_"
                    ):
                        continue

                    # Only accept exact match
                    if tag_name == query:

                        print(
                            f"Exact match: "
                            f"{query}"
                        )

                        return tag_name

    except Exception as e:

        print(
            f"Exact animator lookup failed: "
            f"{query} | {e}"
        )

    # --------------------------------------------------------
    # BUILD ANIMATOR CACHE
    # --------------------------------------------------------

    if ANIMATOR_CACHE is None:

        print(
            "Building animator tag cache..."
        )

        ANIMATOR_CACHE = []

        for page in range(1, 21):

            url = (
                f"{BASE_URL}/tag.json"
                f"?limit=100"
                f"&page={page}"
                f"&order=count"
            )

            try:

                async with session.get(
                    url
                ) as resp:

                    if resp.status != 200:

                        print(
                            f"Tag cache request "
                            f"failed on page "
                            f"{page}: "
                            f"HTTP {resp.status}"
                        )

                        break

                    tags = await resp.json()

            except Exception as e:

                print(
                    f"Tag cache failed on "
                    f"page {page}: {e}"
                )

                break

            for tag in tags:

                name = tag.get(
                    "name",
                    ""
                )

                # ------------------------------------------------
                # NEVER INCLUDE not_* TAGS
                # ------------------------------------------------

                if name.startswith(
                    "not_"
                ):
                    continue

                # Keep tags containing underscores
                if "_" in name:

                    ANIMATOR_CACHE.append(
                        name
                    )

        print(
            f"Animator cache built: "
            f"{len(ANIMATOR_CACHE)} tags"
        )

    # --------------------------------------------------------
    # FUZZY SEARCH
    # --------------------------------------------------------

    best = query
    best_score = 0.0

    for name in ANIMATOR_CACHE:

        # Extra protection
        if name.startswith(
            "not_"
        ):
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
        f"Search: {query} | "
        f"Match: {best} | "
        f"Score: {best_score:.3f}"
    )

    # --------------------------------------------------------
    # ACCEPT FUZZY MATCH
    # --------------------------------------------------------

    if best_score >= 0.75:

        return best

    # --------------------------------------------------------
    # NO GOOD MATCH
    # --------------------------------------------------------

    return query


# ============================================================
# CLIP COMMAND
# ============================================================

@bot.tree.command(
    name="clip",
    description=(
        "Get 3-4 random Sakugabooru "
        "clips from an animator"
    )
)
@app_commands.describe(
    animator=(
        "Animator name "
        "(e.g. Yutaka Nakamura)"
    )
)
async def clip(
    interaction: discord.Interaction,
    animator: str
):

    # --------------------------------------------------------
    # COOLDOWN
    # --------------------------------------------------------

    user_id = interaction.user.id

    remaining = get_remaining_cooldown(
        user_id
    )

    if remaining > 0:

        await interaction.response.send_message(
            (
                f"⏳ Please wait "
                f"**{remaining:.1f}s** "
                f"before using `/clip` again."
            ),
            ephemeral=True
        )

        return

    # Mark user as used
    USER_COOLDOWNS[user_id] = (
        time.monotonic()
    )

    # --------------------------------------------------------
    # DEFER
    # --------------------------------------------------------

    await interaction.response.defer()

    # --------------------------------------------------------
    # CLEAN QUERY
    # --------------------------------------------------------

    search_name = (
        normalize_animator_query(
            animator
        )
    )

    # --------------------------------------------------------
    # ALIAS LOOKUP
    # --------------------------------------------------------

    if search_name in ANIMATOR_ALIASES:

        tag = ANIMATOR_ALIASES[
            search_name
        ]

        print(
            f"Alias match: "
            f"{search_name} -> {tag}"
        )

    else:

        tag = await find_closest_animator_tag(
            search_name
        )

    # --------------------------------------------------------
    # GET CLIPS
    # --------------------------------------------------------

    clips = await get_random_clips(
        tag,
        amount=CLIP_AMOUNT
    )

    display_name = (
        clean_animator_name(tag)
        .title()
    )

    # --------------------------------------------------------
    # NO RESULTS
    # --------------------------------------------------------

    if not clips:

        await interaction.followup.send(
            (
                f"No clips found for "
                f"**{display_name}**."
            )
        )

        return

    # --------------------------------------------------------
    # EMBED
    # --------------------------------------------------------

    embed = discord.Embed(
        title=display_name,
        description=(
            "Here are some random clips "
            "from Sakugabooru.\n\n"
            "Use **/clip <animator name>** "
            "for more."
        ),
        color=0x4DA3FF
    )

    embed.set_author(
        name="Sakuga Clips"
    )

    embed.set_footer(
        text="Source: Sakugabooru"
    )

    # --------------------------------------------------------
    # SEND EMBED
    # --------------------------------------------------------

    await interaction.followup.send(
        embed=embed
    )

    # --------------------------------------------------------
    # SEND CLIPS
    # --------------------------------------------------------

    for post in clips:

        file_url = post.get(
            "file_url"
        )

        if not file_url:
            continue

        # Handle protocol-relative URLs
        if file_url.startswith("//"):

            file_url = (
                "https:" + file_url
            )

        # Handle relative URLs
        elif file_url.startswith("/"):

            file_url = (
                BASE_URL + file_url
            )

        try:

            await interaction.channel.send(
                file_url
            )

        except discord.HTTPException as e:

            print(
                f"Failed to send clip: "
                f"{e}"
            )


# ============================================================
# PING COMMAND
# ============================================================

@bot.tree.command(
    name="ping",
    description="Check if the bot is online"
)
async def ping(
    interaction: discord.Interaction
):

    await interaction.response.send_message(
        "Pong! Sakuga Clips is online."
    )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    try:

        synced = await bot.tree.sync()

        print(
            f"Synced {len(synced)} command(s)"
        )

    except Exception as e:

        print(
            f"Command sync failed: {e}"
        )

    print(
        f"Logged in as {bot.user}"
    )

    print(
        f"Connected to "
        f"{len(bot.guilds)} server(s)"
    )


# ============================================================
# DISCONNECT
# ============================================================

@bot.event
async def on_disconnect():

    print(
        "Bot disconnected from Discord."
    )


# ============================================================
# START
# ============================================================

if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN is missing from "
        "environment variables."
    )


try:

    bot.run(TOKEN)

finally:

    if HTTP_SESSION is not None:

        try:

            asyncio.run(
                HTTP_SESSION.close()
            )

        except Exception:

            pass
