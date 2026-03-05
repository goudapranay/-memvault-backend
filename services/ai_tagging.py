"""
services/ai_tagging.py  –  GPT-4 Vision auto-tagging for MemVault memories
"""
import base64
import logging
from typing import List, Tuple, Optional

import httpx

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are MemVault's AI memory tagger. Given a photo or video thumbnail, 
return a JSON object with:
- tags: list of 5-10 short lowercase tags (people types, events, places, objects, emotions, seasons)
- description: one warm, human sentence describing the memory (e.g. "A sunny family beach day with kids building sandcastles")
- location_guess: city/country if identifiable, else null
- event_type: one of [birthday, holiday, travel, milestone, everyday, celebration, nature, food, null]
- people_count: estimated number of people (integer or null)

Respond ONLY with valid JSON, no markdown.
Example: {"tags":["beach","family","summer","kids","sandcastles"],"description":"A sunny family beach day","location_guess":"Goa, India","event_type":"travel","people_count":4}"""


async def generate_ai_tags(
    image_bytes: bytes,
    mime_type: str,
    api_key: str,
    filename: str = "",
) -> Tuple[List[str], Optional[str]]:
    """
    Call GPT-4 Vision to auto-tag a memory.
    Returns (tags_list, description_string).
    Falls back to filename-based tags if API unavailable.
    """
    if not api_key:
        return _fallback_tags(filename), None

    b64 = base64.b64encode(image_bytes).decode()
    data_url = f"data:{mime_type};base64,{b64}"

    payload = {
        "model": "gpt-4o-mini",
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "low"}},
                    {"type": "text", "text": "Tag this memory."},
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            import json
            parsed = json.loads(content)
            tags = parsed.get("tags", [])
            desc = parsed.get("description")
            # Bonus: add event_type as a tag if present
            event = parsed.get("event_type")
            if event and event not in tags:
                tags.append(event)
            return tags[:12], desc
    except Exception as e:
        logger.warning(f"AI tagging failed: {e}")
        return _fallback_tags(filename), None


def _fallback_tags(filename: str) -> List[str]:
    """Generate basic tags from filename when AI is unavailable."""
    import re
    name = filename.lower()
    name = re.sub(r"\.(jpg|jpeg|png|heic|mp4|mov|avi|webp|avif)$", "", name)
    words = re.split(r"[\s_\-]+", name)
    return [w for w in words if len(w) > 2][:8] or ["memory"]


async def batch_tag_memories(
    memories: list,  # list of (memory_id, image_bytes, mime_type, filename)
    api_key: str,
) -> List[dict]:
    """Tag multiple memories, returning list of {id, tags, description}."""
    results = []
    for mem_id, img_bytes, mime, fname in memories:
        tags, desc = await generate_ai_tags(img_bytes, mime, api_key, fname)
        results.append({"id": mem_id, "tags": tags, "description": desc})
    return results
