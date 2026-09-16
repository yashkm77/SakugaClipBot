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
# {
#     ("yutaka_nakamura", None): {
#         "time": 123456789,
#         "clips": [...]
#     },
#     ("yutaka_nakamura", "boku_no_hero_academia"): {
#         "time": 123456789,
#         "clips": [...]
#     }
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

    # Existing short aliases
    "nakamura": "yutaka_nakamura",
    "yutaka": "yutaka_nakamura",
    "imai": "arifumi_imai",
    "webgen": "webgen",
}


# ============================================================
# MANUAL ANIME ALIASES
# ============================================================
#
# Sakugabooru uses tag names, not necessarily the normal anime title.
# Add more aliases here whenever needed.
#

ANIME_ALIASES = {
    # My Hero Academia
    "my hero academia": "boku_no_hero_academia",
    "mha": "boku_no_hero_academia",
    "bnha": "boku_no_hero_academia",
    "boku no hero academia": "boku_no_hero_academia",

    # Jujutsu Kaisen
    "jujutsu kaisen": "jujutsu_kaisen",
    "jjk": "jujutsu_kaisen",

    # Chainsaw Man
    "chainsaw man": "chainsaw_man",
    "csm": "chainsaw_man",

    # One Piece
    "one piece": "one_piece",
    "op": "one_piece",

    # Attack on Titan
    "attack on titan": "shingeki_no_kyojin",
    "aot": "shingeki_no_kyojin",
    "shingeki no kyojin": "shingeki_no_kyojin",

    # Mob Psycho 100
    "mob psycho 100": "mob_psycho_100",
    "mob psycho": "mob_psycho_100",

    # One Punch Man
    "one punch man": "one_punch_man",
    "opm": "one_punch_man",

    # Naruto
    "naruto": "naruto",
    "naruto shippuden": "naruto_shippuuden",

    # Bleach
    "bleach": "bleach",

    # Demon Slayer
    "demon slayer": "kimetsu_no_yaiba",
    "kimetsu no yaiba": "kimetsu_no_yaiba",

    # Frieren
    "frieren": "sousou_no_frieren",
    "frieren beyond journey's end": "sousou_no_frieren",
    "frieren beyond journey’s end": "sousou_no_frieren",

    # Solo Leveling
    "solo leveling": "ore_dake_level_up_na_ken",

    # Vinland Saga
    "vinland saga": "vinland_saga",

    # Fate
    "fate": "fate_series",

    # Fullmetal Alchemist
    "fullmetal alchemist": "fullmetal_alchemist",
    "fma": "fullmetal_alchemist",

    # Hunter x Hunter
    "hunter x hunter": "hunter_x_hunter",
    "hxh": "hunter_x_hunter",

    # Haikyuu
    "haikyuu": "haikyuu",

    # Black Clover
    "black clover": "black_clover",

    # Mushoku Tensei
    "mushoku tensei": "mushoku_tensei",

    # Kagurabachi
    "kagurabachi": "kagurabachi",
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
        timeout = aiohttp.ClientTimeout(total=20)

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

    return " ".join(name.lower().split())


def normalize_anime_query(name: str) -> str:
    """
    Normalize a user's anime search.

    Example:
        My Hero   Academia
        ->
        my hero academia
    """

    return " ".join(name.lower().strip().split())


def resolve_anime_tag(name: str) -> str:
    """
    Convert a normal anime name/alias into a Sakugabooru tag.

    If no alias exists, convert spaces to underscores and use the
    resulting value directly.
    """

    normalized = normalize_anime_query(name)

    if normalized in ANIME_ALIASES:
        return ANIME_ALIASES[normalized]

    return normalized.replace(" ", "_")


def get_remaining_cooldown(user_id: int):
    """
    Return remaining cooldown time for a user.

    Returns:
        0 if the user can use /clip.
        >0 if the user is still on cooldown.
    """

    now = time.monotonic()

    last_used = USER_COOLDOWNS.get(user_id)

    if last_used is None:
        return 0

    elapsed = now - last_used

    remaining = COOLDOWN_SECONDS - elapsed

    if remaining <= 0:
        USER_COOLDOWNS.pop(user_id, None)
        return 0

    return remaining


# ============================================================
# FETCH RANDOM CLIPS
# ============================================================

async def get_random_clips(
    animator_tag: str,
    anime_tag: str = None,
    amount: int = 4
):
    """
    Get random video clips for an animator.

    If anime_tag is supplied, Sakugabooru is searched with BOTH
    tags, so only posts containing the animator and anime tags
    are returned.

    Results are cached separately for:
        (animator, no anime filter)
        (animator, anime filter)
    """

    now = time.monotonic()

    # Cache key keeps filtered and unfiltered searches separate.
    cache_key = (
        animator_tag,
        anime_tag
    )

    # --------------------------------------------------------
    # CHECK CACHE
    # --------------------------------------------------------

    cached = CLIP_CACHE.get(cache_key)

    if cached:
        cache_age = now - cached["time"]

        if cache_age < CLIP_CACHE_SECONDS:
            clips = cached["clips"]

            if clips:
                return random.sample(
                    clips,
                    min(amount, len(clips))
                )

        else:
            # Remove expired cache
            CLIP_CACHE.pop(cache_key, None)

    # --------------------------------------------------------
    # BUILD SAKUGABOORU TAG QUERY
    # --------------------------------------------------------

    if anime_tag:
        search_tags = f"{animator_tag} {anime_tag}"
    else:
        search_tags = animator_tag

    # --------------------------------------------------------
    # FETCH FROM SAKUGABOORU
    # --------------------------------------------------------

    session = await get_http_session()

    url = (
        f"{BASE_URL}/post.json"
        f"?tags={search_tags}"
        f"&limit=100"
    )

    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                print(
                    f"Sakugabooru returned "
                    f"HTTP {resp.status} "
                    f"for {search_tags}"
                )
                return []

            posts = await resp.json()

    except asyncio.TimeoutError:
        print(
            f"Sakugabooru request timed out: "
            f"{search_tags}"
        )
        return []

    except aiohttp.ClientError as e:
        print(
            f"Sakugabooru request failed: "
            f"{search_tags} | {e}"
        )
        return []

    except Exception as e:
        print(
            f"Unexpected Sakugabooru error: "
            f"{search_tags} | {e}"
        )
        return []

    # --------------------------------------------------------
    # ONLY KEEP VIDEO POSTS
    # --------------------------------------------------------

    videos = [
        p
        for p in posts
        if p.get("file_ext") in ("mp4", "webm")
        and p.get("file_url")
    ]

    if not videos:
        return []

    # --------------------------------------------------------
    # SAVE TO CACHE
    # --------------------------------------------------------

    CLIP_CACHE[cache_key] = {
        "time": now,
        "clips": videos
    }

    # --------------------------------------------------------
    # RETURN RANDOM CLIPS
    # --------------------------------------------------------

    return random.sample(
        videos,
        min(amount, len(videos))
    )


# ============================================================
# FUZZY ANIMATOR LOOKUP
# ============================================================

async def find_closest_animator_tag(query: str):
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
                    if tag_name.startswith("not_"):
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
        print("Building animator tag cache...")

        ANIMATOR_CACHE = []

        for page in range(1, 21):
            url = (
                f"{BASE_URL}/tag.json"
                f"?limit=100"
                f"&page={page}"
                f"&order=count"
            )

            try:
                async with session.get(url) as resp:
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

                # NEVER INCLUDE not_* TAGS
                if name.startswith("not_"):
                    continue

                # Keep tags containing underscores
                if "_" in name:
                    ANIMATOR_CACHE.append(name)

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
    ),
    anime=(
        "Optional anime filter "
        "(e.g. My Hero Academia or MHA)"
    )
)
async def clip(
    interaction: discord.Interaction,
    animator: str,
    anime: str = None
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
    USER_COOLDOWNS[user_id] = time.monotonic()

    # --------------------------------------------------------
    # DEFER
    # --------------------------------------------------------

    await interaction.response.defer()

    # --------------------------------------------------------
    # CLEAN ANIMATOR QUERY
    # --------------------------------------------------------

    search_name = normalize_animator_query(
        animator
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
    # ANIME FILTER
    # --------------------------------------------------------

    anime_tag = None
    anime_display_name = None

    if anime:
        anime_display_name = normalize_anime_query(
            anime
        )

        anime_tag = resolve_anime_tag(
            anime
        )

        print(
            f"Anime filter: "
            f"{anime_display_name} -> "
            f"{anime_tag}"
        )

    # --------------------------------------------------------
    # GET CLIPS
    # --------------------------------------------------------

    clips = await get_random_clips(
        tag,
        anime_tag=anime_tag,
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
        if anime_display_name:
            await interaction.followup.send(
                (
                    f"No clips found for "
                    f"**{display_name}** "
                    f"from **{anime_display_name.title()}**."
                )
            )
        else:
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

    if anime_display_name:
        description = (
            f"Here are some random clips from "
            f"**{display_name}** in "
            f"**{anime_display_name.title()}**.\n\n"
            "Use **/clip** with an anime filter "
            "for clips from a specific anime."
        )
    else:
        description = (
            "Here are some random clips "
            "from Sakugabooru.\n\n"
            "Use **/clip** with an anime filter "
            "to search a specific anime."
        )

    embed = discord.Embed(
        title=display_name,
        description=description,
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
            f"Command sync failed: "
            f"{e}"
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
