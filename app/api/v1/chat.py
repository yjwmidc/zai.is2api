import logging
import json
import time
import uuid
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.openai import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk, ChatCompletionChunkChoice, ChatCompletionChoice, Message
from app.services.token_manager import get_valid_zai_token, mark_token_invalid
from app.services.zai_client import ZaiClient
from app.db.session import get_db

router = APIRouter()
logger = logging.getLogger(__name__)

async def generate_chunks(zai_client: ZaiClient, request: ChatCompletionRequest, chat_id: str):
    try:
        async for content_delta in zai_client.stream_chat(request.messages, request.model):
            chunk = ChatCompletionChunk(
                id=chat_id,
                model=request.model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta={"content": content_delta},
                        finish_reason=None
                    )
                ]
            )
            yield f"data: {chunk.json()}\n\n"
            
        # End of stream
        chunk = ChatCompletionChunk(
            id=chat_id,
            model=request.model,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta={},
                    finish_reason="stop"
                )
            ]
        )
        yield f"data: {chunk.json()}\n\n"
        yield "data: [DONE]\n\n"
        
    except Exception as e:
        logger.error(f"Stream generation error: {e}")
        if "401" in str(e):
             # Trigger invalidation
             await mark_token_invalid(zai_client.token)
        # We can't easily return error in SSE, usually just close stream or send error event

@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest, db: AsyncSession = Depends(get_db)):
    # 1. Get Valid Token (1 RPM limit enforced inside)
    token = await get_valid_zai_token(db)
    
    if not token:
        raise HTTPException(status_code=429, detail="No available tokens or rate limit exceeded. Please try again later.")
    
    zai_client = ZaiClient(token)
    
    # Generate ID for response
    chat_id = f"chatcmpl-{uuid.uuid4()}"
    
    if request.stream:
        return StreamingResponse(
            generate_chunks(zai_client, request, chat_id),
            media_type="text/event-stream"
        )
    else:
        # Non-streaming: aggregate response
        full_content = ""
        try:
            async for content_delta in zai_client.stream_chat(request.messages, request.model):
                full_content += content_delta
        except Exception as e:
             if "401" in str(e):
                 await mark_token_invalid(token)
                 raise HTTPException(status_code=401, detail="Upstream authentication failed")
             # Do not expose internal error details to client
             raise HTTPException(status_code=500, detail="An internal server error occurred.")
             
        response = ChatCompletionResponse(
            id=chat_id,
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=Message(role="assistant", content=full_content),
                    finish_reason="stop"
                )
            ]
        )
        return response