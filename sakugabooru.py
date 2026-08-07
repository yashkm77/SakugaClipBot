import aiohttp
import random

BASE_URL = "https://www.sakugabooru.com"

ANIMATOR_ALIASES = {
    "nakamura": "yutaka_nakamura",
    "yutaka": "yutaka_nakamura",
    "imai": "arifumi_imai",
    "webgen": "webgen",
}

async def get_random_clips(animator_input: str, count: int = 4):
    tag = ANIMATOR_ALIASES.get(animator_input.lower(), animator_input.lower())

    async with aiohttp.ClientSession() as session:
        clips = []

        # Search a few pages to gather enough clips
        for page in range(1, 6):
            url = f"{BASE_URL}/post.json?tags={tag}&page={page}&limit=100"

            async with session.get(url) as resp:
                if resp.status != 200:
                    continue

                posts = await resp.json()

                for post in posts:
                    ext = post.get("file_ext")
                    if ext not in ("mp4", "webm"):
                        continue

                    file_url = post.get("file_url")
                    if file_url:
                        clips.append(file_url)

        if not clips:
            return tag, []

        random.shuffle(clips)
        return tag, clips[:count]