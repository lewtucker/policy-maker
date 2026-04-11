"""
Natural Language Policy Authoring for Policy Maker.
Adapted from OC_Policy: removed identity/audit/token auth; added per-user skill prompt.
"""
import os
import json
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import anthropic

router = APIRouter()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

_DEFAULT_SKILL_PATH = Path(__file__).parent / "default_skill.txt"


def _load_default_skill() -> str:
    if _DEFAULT_SKILL_PATH.exists():
        return _DEFAULT_SKILL_PATH.read_text(encoding="utf-8")
    return ""


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class ChatResponse(BaseModel):
    reply: str
    proposed_rules: list[dict] | None = None
    proposed_action: str | None = None
    proposed_rule_id: str | None = None


def _extract_proposed(text: str) -> tuple[str | None, list[dict] | None, str | None]:
    pattern = r"```PROPOSED_RULE\s*\n(.*?)\n```"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None, None, None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None, None, None

    action = data.get("action")
    if action == "add":
        return action, [data["rule"]], None
    elif action == "add_batch":
        return action, data["rules"], None
    elif action == "delete":
        return action, None, data.get("rule_id")
    return None, None, None


def create_chat_handler(get_engine_fn, get_skill_fn):
    """
    Factory returning the /chat endpoint.
    get_engine_fn(email) -> PolicyEngine
    get_skill_fn(email)  -> str (system prompt text)
    """

    @router.post("/chat")
    async def chat(req: ChatRequest, request: Request):
        email = request.session.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="Not authenticated")

        if not ANTHROPIC_API_KEY:
            raise HTTPException(
                status_code=503,
                detail="ANTHROPIC_API_KEY not set — natural language policy authoring is disabled.",
            )

        engine = get_engine_fn(email)
        system_prompt = get_skill_fn(email)

        policies_json = json.dumps([r.to_dict() for r in engine.rules], indent=2)

        # Run the analyzer so Claude has real findings to reason about
        from policy_analyzer import analyze
        findings = analyze(engine.rules, known_people=[], known_groups=[])
        analysis_json = json.dumps([f.to_dict() for f in findings], indent=2)

        # Substitute context into the skill prompt
        system_prompt = system_prompt.replace("{policies_json}", policies_json)
        system_prompt = system_prompt.replace("{analysis_json}", analysis_json)

        messages = [{"role": t["role"], "content": t["content"]} for t in req.history]
        messages.append({"role": "user", "content": req.message})

        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )

        reply_text = response.content[0].text
        action, rules, rule_id = _extract_proposed(reply_text)

        return ChatResponse(
            reply=reply_text,
            proposed_rules=rules,
            proposed_action=action,
            proposed_rule_id=rule_id,
        )

    return router
