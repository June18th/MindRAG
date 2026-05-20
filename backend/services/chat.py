"""Chat service - LangChain ReAct agent with streaming."""
import asyncio
import json
import logging
import pathlib
import time
import uuid
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.config import settings
from core.redis import get_redis

logger = logging.getLogger(__name__)

MAX_REACT_ROUNDS = 4
GENERATION_TIMEOUT = 120

active_generations: dict[str, dict] = {}
cancelled_generations: set = set()

INTERNAL_CMD_TOKEN = f"WSS_STOP_CMD_{int(time.time() * 1000) % 1000000}"


def get_internal_cmd_token() -> str:
    return INTERNAL_CMD_TOKEN


# --- System prompt ---
_PROMPT_FILE = pathlib.Path(__file__).parent.parent / "prompts" / "chat_system.md"
SYSTEM_PROMPT = _PROMPT_FILE.read_text(encoding="utf-8").strip()


# --- LLM ---
def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.deepseek_api_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_api_url,
        temperature=0.3,
        max_tokens=2000,
        streaming=True,
    )


# --- LangChain Tools ---

@tool
def search_knowledge(query: str, top_k: int = 5) -> str:
    """在知识库中搜索相关内容，返回最相关的文档片段和元数据。绝大多数问题都必须先调用此工具。"""
    # Executed synchronously within the agent loop; async search dispatched via _run_search
    return json.dumps({
        "query": query,
        "results": [],
        "total": 0,
        "message": "搜索由异步 handle 执行",
    }, ensure_ascii=False)


@tool
def generate_summary(prompt: str) -> str:
    """基于检索到的知识库内容生成最终回答。检索结果充分时调用，prompt 参数包含所有检索内容和用户问题。"""
    return ""  # Handled via streaming path in agent loop


@tool
def submit_feedback(rating: str, reason: str = "") -> str:
    """记录用户对回答质量的反馈。rating 为 'good' 或 'bad'。"""
    return "feedback_recorded"


TOOLS = [search_knowledge, generate_summary, submit_feedback]

# User-facing tool names map
TOOL_MAP = {t.name: t for t in TOOLS}


# --- Main processing ---

async def process_chat_message(
    user_id: str,
    message: str,
    send_json: callable,
) -> None:
    """Process a chat message through LangChain ReAct agent."""
    from services.conversation import (
        append_to_conversation_history,
        ensure_conversation_session,
        get_conversation_history,
        get_or_create_conversation_id,
        persist_conversation,
    )
    from services.rate_limiter import check_chat_by_user

    await check_chat_by_user(user_id)

    conversation_id = await get_or_create_conversation_id(user_id)
    generation_id = uuid.uuid4().hex

    # Ensure MySQL session row exists (matches Java ensureConversationSession)
    try:
        from core.database import async_session_factory
        async with async_session_factory() as db:
            await ensure_conversation_session(int(user_id), conversation_id, message[:50], db)
            await db.commit()
    except Exception:
        pass

    await send_json({
        "type": "start", "generationId": generation_id,
        "conversationId": conversation_id, "timestamp": int(time.time() * 1000),
    })

    gen_state = {
        "userId": user_id, "conversationId": conversation_id,
        "content": "", "status": "streaming", "references": {}, "startedAt": time.time(),
    }
    active_generations[generation_id] = gen_state

    try:
        history = await get_conversation_history(conversation_id)
        history = history[-6:]
        for h in history:
            if len(h.get("content", "")) > 800:
                h["content"] = h["content"][:800] + "..."

        # Build LangChain messages
        chat_history = []
        for h in history:
            if h["role"] == "user":
                chat_history.append(HumanMessage(content=h["content"]))
            elif h["role"] == "assistant":
                chat_history.append(AIMessage(content=h["content"]))

        llm = _build_llm()
        agent = create_react_agent(llm, TOOLS, prompt=SystemMessage(content=SYSTEM_PROMPT))

        # Stream agent execution
        full_response = ""
        async for event in agent.astream_events(
            {"messages": chat_history + [HumanMessage(content=message)]},
            version="v2",
            config={"recursion_limit": MAX_REACT_ROUNDS * 3, "timeout": GENERATION_TIMEOUT},
        ):
            if generation_id in cancelled_generations:
                break

            kind = event.get("event", "")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    full_response += chunk.content
                    await send_json({
                        "type": "chunk", "generationId": generation_id,
                        "conversationId": conversation_id, "chunk": chunk.content,
                    })

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                await send_json({
                    "type": "tool_call", "tool": tool_name,
                    "toolCallId": event.get("run_id", ""),
                    "status": "executing",
                    "generationId": generation_id, "conversationId": conversation_id,
                })

                # Intercept search_knowledge to run real async search
                if tool_name == "search_knowledge":
                    input_data = event.get("data", {}).get("input", {})
                    query_text = (input_data if isinstance(input_data, str) else input_data.get("query", ""))
                    if query_text:
                        from services.search import hybrid_search
                        results = await hybrid_search(query_text, user_id, 5)
                        if results:
                            gen_state["references"] = {
                                str(i + 1): {
                                    "fileMd5": r.get("fileMd5", ""),
                                    "fileName": r.get("fileName", ""),
                                    "pageNumber": r.get("pageNumber"),
                                    "anchorText": r.get("anchorText"),
                                    "retrievalMode": r.get("retrievalMode"),
                                    "score": r.get("score"),
                                    "evidenceSnippet": r.get("textContent", "")[:160],
                                }
                                for i, r in enumerate(results)
                            }

            elif kind == "on_tool_end":
                await send_json({
                    "type": "tool_call",
                    "tool": event.get("name", ""),
                    "toolCallId": event.get("run_id", ""),
                    "status": "success",
                    "generationId": generation_id, "conversationId": conversation_id,
                })

        gen_state["content"] = full_response

        if generation_id in cancelled_generations:
            gen_state["status"] = "cancelled"
            await send_json({
                "type": "completion", "generationId": generation_id,
                "conversationId": conversation_id, "status": "cancelled",
            })
        else:
            gen_state["status"] = "finished"
            # Persist
            try:
                from core.database import async_session_factory
                async with async_session_factory() as db:
                    await persist_conversation(
                        db, int(user_id), message, full_response,
                        conversation_id, gen_state.get("references"),
                    )
                    await db.commit()
                persistence_degraded = False
            except Exception as e:
                logger.exception("Persist failed: %s", e)
                persistence_degraded = True

            await append_to_conversation_history(conversation_id, "user", message)
            await append_to_conversation_history(conversation_id, "assistant", full_response)

            await send_json({
                "type": "completion", "generationId": generation_id,
                "conversationId": conversation_id, "status": "finished",
                "referenceMappings": gen_state.get("references"),
                "persistenceDegraded": persistence_degraded,
            })

    except Exception as e:
        logger.error("Chat error gen=%s: %s", generation_id, e)
        gen_state["status"] = "failed"
        await send_json({"type": "error", "generationId": generation_id, "error": str(e)})
    finally:
        active_generations.pop(generation_id, None)
        cancelled_generations.discard(generation_id)


async def stop_response(user_id: str, generation_id: str | None, send_json: callable) -> None:
    if not generation_id:
        for gid, s in active_generations.items():
            if s.get("userId") == user_id:
                generation_id = gid
                break
    if generation_id and generation_id in active_generations:
        cancelled_generations.add(generation_id)
        active_generations[generation_id]["status"] = "cancelled"
        await send_json({
            "type": "stop", "generationId": generation_id,
            "message": "响应已停止", "timestamp": int(time.time() * 1000),
        })


async def get_generation_state(generation_id: str, user_id: str) -> dict | None:
    gen = active_generations.get(generation_id)
    return gen if gen and gen.get("userId") == user_id else None


async def get_active_generation(user_id: str) -> dict | None:
    for gen in active_generations.values():
        if gen.get("userId") == user_id:
            return gen
    return None
