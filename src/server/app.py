# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import asyncio
import base64
import json
import logging
import os
from typing import Annotated, List, Optional, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import AIMessageChunk, ToolMessage, BaseMessage
from langgraph.types import Command

from src.config.redis_config import write_event_to_stream, read_events_from_stream, get_redis_client

from src.config.report_style import ReportStyle
from src.config.tools import SELECTED_RAG_PROVIDER
from src.graph.builder import build_graph_with_memory
from src.podcast.graph.builder import build_graph as build_podcast_graph
from src.ppt.graph.builder import build_graph as build_ppt_graph
from src.prose.graph.builder import build_graph as build_prose_graph
from src.prompt_enhancer.graph.builder import build_graph as build_prompt_enhancer_graph
from src.rag.builder import build_retriever
from src.rag.retriever import Resource
from src.server.chat_request import (
    ChatRequest,
    EnhancePromptRequest,
    GeneratePodcastRequest,
    GeneratePPTRequest,
    GenerateProseRequest,
    TTSRequest,
)
from src.server.mcp_request import MCPServerMetadataRequest, MCPServerMetadataResponse
from src.server.mcp_utils import load_mcp_tools
from src.server.rag_request import (
    RAGConfigResponse,
    RAGResourceRequest,
    RAGResourcesResponse,
)
from src.server.config_request import ConfigResponse
from src.server.async_request import (
    AsyncTaskRequest,
    AsyncTaskResponse, 
    TaskStatusResponse,
    TaskListResponse,
    WorkerStatsResponse,
    TaskCancelResponse,
)
from src.llms.llm import get_configured_llm_models
from src.tools import VolcengineTTS
from src.async_tasks import TaskManager
from src.async_tasks.task_manager import TaskStatus

logger = logging.getLogger(__name__)

INTERNAL_SERVER_ERROR_DETAIL = "Internal Server Error"

app = FastAPI(
    title="DeerFlow API",
    description="API for Deer",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

graph = build_graph_with_memory()

# 初始化异步任务相关组件
task_manager = TaskManager()


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    thread_id = request.thread_id
    if thread_id == "__default__":
        thread_id = str(uuid4())
    return StreamingResponse(
        _astream_workflow_generator(
            request.model_dump()["messages"],
            thread_id,
            request.resources,
            request.max_plan_iterations,
            request.max_step_num,
            request.max_search_results,
            request.auto_accepted_plan,
            request.interrupt_feedback,
            request.mcp_settings,
            request.enable_background_investigation,
            request.report_style,
            request.enable_deep_thinking,
        ),
        media_type="text/event-stream",
    )


async def _astream_workflow_generator(
    messages: List[dict],
    thread_id: str,
    resources: List[Resource],
    max_plan_iterations: int,
    max_step_num: int,
    max_search_results: int,
    auto_accepted_plan: bool,
    interrupt_feedback: str,
    mcp_settings: dict,
    enable_background_investigation: bool,
    report_style: ReportStyle,
    enable_deep_thinking: bool,
):
    # 生成查询ID作为stream suffix
    query_id = str(uuid4())
    
    input_ = {
        "messages": messages,
        "plan_iterations": 0,
        "final_report": "",
        "current_plan": None,
        "observations": [],
        "auto_accepted_plan": auto_accepted_plan,
        "enable_background_investigation": enable_background_investigation,
        "research_topic": messages[-1]["content"] if messages else "",
    }
    if not auto_accepted_plan and interrupt_feedback:
        resume_msg = f"[{interrupt_feedback}]"
        # add the last message to the resume message
        if messages:
            resume_msg += f" {messages[-1]['content']}"
        input_ = Command(resume=resume_msg)
    
    # 写入研究开始事件
    try:
        # 使用query_id作为research_id，确保前端能正确识别正在进行的研究
        research_id = query_id
        start_event_payload = {
            "thread_id": thread_id,
            "query_id": query_id,
            "research_id": research_id,  # 添加research_id字段
            "id": research_id,  # 添加id字段用于消息识别
            "role": "assistant",
            "agent": "researcher",  # 添加agent字段
            "content": "开始研究调查...",
            "status": "running",  # 改为running状态
            "topic": messages[-1]["content"] if messages else "",  # 重命名为topic
            "research_topic": messages[-1]["content"] if messages else "",
        }
        write_event_to_stream(thread_id, "research_start", start_event_payload, query_id)
        logger.debug(f"成功写入research_start事件到Redis Stream: {thread_id}:{query_id}, research_id: {research_id}")
    except Exception as e:
        logger.error(f"写入research_start事件到Redis Stream失败: {e}")
    
    async for agent, _, event_data in graph.astream(
        input_,
        config={
            "thread_id": thread_id,
            "resources": resources,
            "max_plan_iterations": max_plan_iterations,
            "max_step_num": max_step_num,
            "max_search_results": max_search_results,
            "mcp_settings": mcp_settings,
            "report_style": report_style.value,
            "enable_deep_thinking": enable_deep_thinking,
        },
        stream_mode=["messages", "updates"],
        subgraphs=True,
    ):
        if isinstance(event_data, dict):
            if "__interrupt__" in event_data:
                event_payload = {
                    "thread_id": thread_id,
                    "query_id": query_id,
                    "id": event_data["__interrupt__"][0].ns[0],
                    "role": "assistant",
                    "content": event_data["__interrupt__"][0].value,
                    "finish_reason": "interrupt",
                    "options": [
                        {"text": "Edit plan", "value": "edit_plan"},
                        {"text": "Start research", "value": "accepted"},
                    ],
                }
                # 写入Redis Stream
                try:
                    write_event_to_stream(thread_id, "interrupt", event_payload, query_id)
                    logger.debug(f"成功写入interrupt事件到Redis Stream: {thread_id}:{query_id}")
                except Exception as e:
                    logger.error(f"写入interrupt事件到Redis Stream失败: {e}")
                
                yield _make_event("interrupt", event_payload)
            continue
        message_chunk, message_metadata = cast(
            tuple[BaseMessage, dict[str, any]], event_data
        )
        event_stream_message: dict[str, any] = {
            "thread_id": thread_id,
            "query_id": query_id,
            "agent": agent[0].split(":")[0],
            "id": message_chunk.id,
            "role": "assistant",
            "content": message_chunk.content,
        }
        if message_chunk.additional_kwargs.get("reasoning_content"):
            event_stream_message["reasoning_content"] = message_chunk.additional_kwargs[
                "reasoning_content"
            ]
        if message_chunk.response_metadata.get("finish_reason"):
            event_stream_message["finish_reason"] = message_chunk.response_metadata.get(
                "finish_reason"
            )
        if isinstance(message_chunk, ToolMessage):
            # Tool Message - Return the result of the tool call
            event_stream_message["tool_call_id"] = message_chunk.tool_call_id
            
            # 写入Redis Stream
            try:
                write_event_to_stream(thread_id, "tool_call_result", event_stream_message, query_id)
                logger.debug(f"成功写入tool_call_result事件到Redis Stream: {thread_id}:{query_id}")
            except Exception as e:
                logger.error(f"写入tool_call_result事件到Redis Stream失败: {e}")
            
            yield _make_event("tool_call_result", event_stream_message)
        elif isinstance(message_chunk, AIMessageChunk):
            # AI Message - Raw message tokens
            if message_chunk.tool_calls:
                # AI Message - Tool Call
                event_stream_message["tool_calls"] = message_chunk.tool_calls
                event_stream_message["tool_call_chunks"] = (
                    message_chunk.tool_call_chunks
                )
                
                # 写入Redis Stream
                try:
                    write_event_to_stream(thread_id, "tool_calls", event_stream_message, query_id)
                    logger.debug(f"成功写入tool_calls事件到Redis Stream: {thread_id}:{query_id}")
                except Exception as e:
                    logger.error(f"写入tool_calls事件到Redis Stream失败: {e}")
                
                yield _make_event("tool_calls", event_stream_message)
            elif message_chunk.tool_call_chunks:
                # AI Message - Tool Call Chunks
                # 过滤掉无效的tool_call_chunks
                valid_chunks = []
                for chunk in message_chunk.tool_call_chunks:
                    # 过滤掉无效的chunk
                    if should_save_tool_call_chunk(chunk):
                        valid_chunks.append(chunk)
                
                # 只有存在有效chunks时才保存和发送
                if valid_chunks:
                    event_stream_message["tool_call_chunks"] = valid_chunks
                
                # 写入Redis Stream
                try:
                    write_event_to_stream(thread_id, "tool_call_chunks", event_stream_message, query_id)
                    logger.debug(f"成功写入tool_call_chunks事件到Redis Stream: {thread_id}:{query_id}, 有效chunks: {len(valid_chunks)}")
                except Exception as e:
                    logger.error(f"写入tool_call_chunks事件到Redis Stream失败: {e}")
                
                yield _make_event("tool_call_chunks", event_stream_message)
            else:
                # AI Message - Raw message tokens
                
                # 写入Redis Stream
                try:
                    write_event_to_stream(thread_id, "message_chunk", event_stream_message, query_id)
                    logger.debug(f"成功写入message_chunk事件到Redis Stream: {thread_id}:{query_id}")
                except Exception as e:
                    logger.error(f"写入message_chunk事件到Redis Stream失败: {e}")
                
                yield _make_event("message_chunk", event_stream_message)
    
    # 写入研究结束事件
    try:
        research_id = query_id
        end_event_payload = {
            "thread_id": thread_id,
            "query_id": query_id,
            "research_id": research_id,  # 添加research_id字段
            "id": research_id,  # 添加id字段用于消息识别
            "role": "assistant",
            "agent": "reporter",  # 添加agent字段
            "content": "研究调查完成",
            "status": "completed",
            "finish_reason": "completed",
        }
        write_event_to_stream(thread_id, "research_end", end_event_payload, query_id)
        logger.debug(f"成功写入research_end事件到Redis Stream: {thread_id}:{query_id}, research_id: {research_id}")
    except Exception as e:
        logger.error(f"写入research_end事件到Redis Stream失败: {e}")


def _make_event(event_type: str, data: dict[str, any]):
    if data.get("content") == "":
        data.pop("content")
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/tts")
async def text_to_speech(request: TTSRequest):
    """Convert text to speech using volcengine TTS API."""
    try:
        app_id = os.getenv("VOLCENGINE_TTS_APPID", "")
        if not app_id:
            raise HTTPException(
                status_code=400, detail="VOLCENGINE_TTS_APPID is not set"
            )
        access_token = os.getenv("VOLCENGINE_TTS_ACCESS_TOKEN", "")
        if not access_token:
            raise HTTPException(
                status_code=400, detail="VOLCENGINE_TTS_ACCESS_TOKEN is not set"
            )
        cluster = os.getenv("VOLCENGINE_TTS_CLUSTER", "volcano_tts")
        voice_type = os.getenv("VOLCENGINE_TTS_VOICE_TYPE", "BV700_V2_streaming")

        tts_client = VolcengineTTS(
            appid=app_id,
            access_token=access_token,
            cluster=cluster,
            voice_type=voice_type,
        )
        # Call the TTS API
        result = tts_client.text_to_speech(
            text=request.text[:1024],
            encoding=request.encoding,
            speed_ratio=request.speed_ratio,
            volume_ratio=request.volume_ratio,
            pitch_ratio=request.pitch_ratio,
            text_type=request.text_type,
            with_frontend=request.with_frontend,
            frontend_type=request.frontend_type,
        )

        if not result["success"]:
            raise HTTPException(status_code=500, detail=str(result["error"]))

        # Decode the base64 audio data
        audio_data = base64.b64decode(result["audio_data"])

        # Return the audio file
        return Response(
            content=audio_data,
            media_type=f"audio/{request.encoding}",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=tts_output.{request.encoding}"
                )
            },
        )
    except Exception as e:
        logger.exception(f"Error in TTS endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/podcast/generate")
async def generate_podcast(request: GeneratePodcastRequest):
    try:
        report_content = request.content
        print(report_content)
        workflow = build_podcast_graph()
        final_state = workflow.invoke({"input": report_content})
        audio_bytes = final_state["output"]
        return Response(content=audio_bytes, media_type="audio/mp3")
    except Exception as e:
        logger.exception(f"Error occurred during podcast generation: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/ppt/generate")
async def generate_ppt(request: GeneratePPTRequest):
    try:
        report_content = request.content
        print(report_content)
        workflow = build_ppt_graph()
        final_state = workflow.invoke({"input": report_content})
        generated_file_path = final_state["generated_file_path"]
        with open(generated_file_path, "rb") as f:
            ppt_bytes = f.read()
        return Response(
            content=ppt_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    except Exception as e:
        logger.exception(f"Error occurred during ppt generation: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/prose/generate")
async def generate_prose(request: GenerateProseRequest):
    try:
        sanitized_prompt = request.prompt.replace("\r\n", "").replace("\n", "")
        logger.info(f"Generating prose for prompt: {sanitized_prompt}")
        workflow = build_prose_graph()
        events = workflow.astream(
            {
                "content": request.prompt,
                "option": request.option,
                "command": request.command,
            },
            stream_mode="messages",
            subgraphs=True,
        )
        return StreamingResponse(
            (f"data: {event[0].content}\n\n" async for _, event in events),
            media_type="text/event-stream",
        )
    except Exception as e:
        logger.exception(f"Error occurred during prose generation: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/prompt/enhance")
async def enhance_prompt(request: EnhancePromptRequest):
    try:
        sanitized_prompt = request.prompt.replace("\r\n", "").replace("\n", "")
        logger.info(f"Enhancing prompt: {sanitized_prompt}")

        # Convert string report_style to ReportStyle enum
        report_style = None
        if request.report_style:
            try:
                # Handle both uppercase and lowercase input
                style_mapping = {
                    "ACADEMIC": ReportStyle.ACADEMIC,
                    "POPULAR_SCIENCE": ReportStyle.POPULAR_SCIENCE,
                    "NEWS": ReportStyle.NEWS,
                    "SOCIAL_MEDIA": ReportStyle.SOCIAL_MEDIA,
                    "academic": ReportStyle.ACADEMIC,
                    "popular_science": ReportStyle.POPULAR_SCIENCE,
                    "news": ReportStyle.NEWS,
                    "social_media": ReportStyle.SOCIAL_MEDIA,
                }
                report_style = style_mapping.get(
                    request.report_style, ReportStyle.ACADEMIC
                )
            except Exception:
                # If invalid style, default to ACADEMIC
                report_style = ReportStyle.ACADEMIC
        else:
            report_style = ReportStyle.ACADEMIC

        workflow = build_prompt_enhancer_graph()
        final_state = workflow.invoke(
            {
                "prompt": request.prompt,
                "context": request.context,
                "report_style": report_style,
            }
        )
        return {"result": final_state["output"]}
    except Exception as e:
        logger.exception(f"Error occurred during prompt enhancement: {str(e)}")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)


@app.post("/api/mcp/server/metadata", response_model=MCPServerMetadataResponse)
async def mcp_server_metadata(request: MCPServerMetadataRequest):
    """Get information about an MCP server."""
    try:
        # Set default timeout with a longer value for this endpoint
        timeout = 300  # Default to 300 seconds for this endpoint

        # Use custom timeout from request if provided
        if request.timeout_seconds is not None:
            timeout = request.timeout_seconds

        # Load tools from the MCP server using the utility function
        tools = await load_mcp_tools(
            server_type=request.transport,
            command=request.command,
            args=request.args,
            url=request.url,
            env=request.env,
            timeout_seconds=timeout,
        )

        # Create the response with tools
        response = MCPServerMetadataResponse(
            transport=request.transport,
            command=request.command,
            args=request.args,
            url=request.url,
            env=request.env,
            tools=tools,
        )

        return response
    except Exception as e:
        if not isinstance(e, HTTPException):
            logger.exception(f"Error in MCP server metadata endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)
        raise


@app.get("/api/rag/config", response_model=RAGConfigResponse)
async def rag_config():
    """Get the config of the RAG."""
    return RAGConfigResponse(provider=SELECTED_RAG_PROVIDER)


@app.get("/api/rag/resources", response_model=RAGResourcesResponse)
async def rag_resources(request: Annotated[RAGResourceRequest, Query()]):
    """Get the resources of the RAG."""
    retriever = build_retriever()
    if retriever:
        return RAGResourcesResponse(resources=retriever.list_resources(request.query))
    return RAGResourcesResponse(resources=[])


@app.get("/api/config", response_model=ConfigResponse)
async def config():
    """Get the config of the server."""
    return ConfigResponse(
        rag=RAGConfigResponse(provider=SELECTED_RAG_PROVIDER),
        models=get_configured_llm_models(),
    )


@app.get("/api/chat/replay")
async def chat_replay(
    thread_id: Annotated[str, Query(description="Thread ID to replay")],
    offset: Annotated[str, Query(description="Offset to start reading from")] = "0",
    continuous: Annotated[bool, Query(description="Continuous mode: keep reading new events")] = False,
    query_id: Annotated[Optional[str], Query(description="Query ID, if not provided will find latest")] = None,
):
    """
    从Redis Stream回放对话事件
    
    Args:
        thread_id: 线程ID
        offset: 开始读取的位置，默认从头开始
        continuous: 连续模式，是否在读取完现有事件后继续等待新事件
        query_id: 查询ID，如果不提供将自动查找最新的
    
    Returns:
        SSE格式的事件流
    """
    
    async def replay_generator():
        try:
            import asyncio
            from src.async_tasks.task_manager import TaskManager
            
            # 如果没有提供query_id或使用默认值，尝试查找最新的
            target_query_id = query_id
            logger.info(f"🔧 query_id参数: '{query_id}', 类型: {type(query_id)}")
            if not target_query_id or target_query_id == "default":
                logger.info(f"🔍 需要查找最新query_id: thread_id={thread_id}")
                # 查找该线程的最新查询ID
                target_query_id = await find_latest_query_id(thread_id)
                logger.info(f"🎯 find_latest_query_id返回: '{target_query_id}'")
                if not target_query_id:
                    logger.warning(f"未找到thread_id={thread_id}的查询记录")
                    yield f"event: error\ndata: {json.dumps({'thread_id': thread_id, 'error': 'No query found for this thread'}, ensure_ascii=False)}\n\n"
                    return
            else:
                logger.info(f"🎯 直接使用提供的query_id: '{target_query_id}'")
            
            logger.info(f"开始回放对话: thread_id={thread_id}, query_id={target_query_id}, offset={offset}, continuous={continuous}")
            
            current_start = offset
            events_count = 0
            
            # 调试：输出重要信息
            logger.info(f"📊 回放参数: thread_id={thread_id}, target_query_id={target_query_id}, current_start={current_start}")
            
            # 先读取所有现有的历史事件
            while True:
                # 从Redis Stream读取事件（每次读取100个）
                logger.info(f"🔍 尝试从Redis读取事件: thread_id={thread_id}, start={current_start}, stream_suffix={target_query_id}")
                events = read_events_from_stream(thread_id, start=current_start, stream_suffix=target_query_id, count=100)
                logger.info(f"📖 Redis读取结果: 获得 {len(events)} 个事件")
                
                if not events:
                    # 没有更多历史事件了
                    logger.info("ℹ️ 没有找到更多事件")
                    if not continuous:
                        # 非连续模式：直接结束
                        logger.info("📴 非连续模式，结束回放")
                        break
                    else:
                        # 连续模式：检查是否有运行中任务
                        task_mgr = TaskManager()
                        running_task = task_mgr.get_running_task_by_thread(thread_id)
                        if not running_task:
                            # 没有运行中任务，结束
                            logger.info("📴 连续模式但无运行中任务，结束回放")
                            break
                        
                        # 有运行中任务，等待1秒后继续检查新事件
                        logger.info("⏰ 连续模式等待新事件...")
                        await asyncio.sleep(1)
                        continue
                
                # 处理读取到的事件
                for i, event in enumerate(events):
                    try:
                        # 重构事件数据为前端期望的格式
                        event_type = event.get("event", "unknown")
                        event_data = event.get("data", {})
                        
                        logger.debug(f"🎬 处理事件 {i+1}: type={event_type}, data_keys={list(event_data.keys())}")
                        
                        # 确保thread_id在数据中
                        if "thread_id" not in event_data:
                            event_data["thread_id"] = thread_id
                        
                        # 生成SSE格式的事件
                        sse_event = f"event: {event_type}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                        yield sse_event
                        
                        # 更新下次读取的起始位置（使用下一个ID避免重复读取）
                        event_id = event.get("id")
                        if event_id:
                            current_start = get_next_stream_id(event_id)
                        
                        events_count += 1
                        
                        logger.debug(f"✅ 已发送事件: {event_type}, ID: {event_id}, 下次起始: {current_start}")
                        
                    except Exception as e:
                        logger.error(f"❌ 处理回放事件时出错: {e}, 事件: {event}")
                        continue
                
                # 如果这批事件数量少于100，说明已经读取完所有历史事件
                if len(events) < 100:
                    if not continuous:
                        logger.info("📋 历史事件读取完毕，非连续模式结束")
                        break
                    
                    # 连续模式：继续循环等待新事件
                    logger.info("📋 历史事件读取完毕，连续模式继续等待...")
                    # current_start 已经更新为最后事件的下一个ID，继续使用即可
            
            # 发送结束标记
            mode = "continuous" if continuous else "static"
            yield f"event: replay_end\ndata: {json.dumps({'thread_id': thread_id, 'query_id': target_query_id, 'mode': mode, 'total_events': events_count}, ensure_ascii=False)}\n\n"
            logger.info(f"回放完成: thread_id={thread_id}, query_id={target_query_id}, 模式={mode}, 共回放 {events_count} 个事件")
            
        except Exception as e:
            logger.error(f"回放过程中发生错误: {e}")
            error_event = {
                "thread_id": thread_id,
                "error": str(e),
                "message": "回放过程中发生错误"
            }
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        replay_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        }
    )


# ==================== 异步任务 API ====================

@app.get("/api/threads/{thread_id}/running-task")
async def get_thread_running_task(thread_id: str):
    """
    检查线程是否有运行中的任务
    
    Args:
        thread_id: 线程ID
        
    Returns:
        dict: 运行任务信息或None
    """
    try:
        running_task = task_manager.get_running_task_by_thread(thread_id)
        
        if running_task:
            return {
                "has_running_task": True,
                "task_id": running_task.task_id,
                "status": running_task.status.value,
                "progress": running_task.progress,
                "current_step": running_task.current_step,
                "started_at": running_task.started_at.isoformat() if running_task.started_at else None
            }
        else:
            return {
                "has_running_task": False,
                "task_id": None
            }
            
    except Exception as e:
        logger.error(f"检查线程运行任务失败: thread_id={thread_id}, error={e}")
        raise HTTPException(status_code=500, detail=f"检查运行任务失败: {str(e)}")

@app.post("/api/chat/start-async")
async def start_async_chat_task(request: ChatRequest):
    """
    基于ChatRequest启动异步任务（简化版API）
    
    Args:
        request: 聊天请求（复用现有格式）
        
    Returns:
        dict: 任务创建响应
    """
    try:
        logger.info(f"启动异步聊天任务: thread_id={request.thread_id}")
        
        # 构建任务配置
        task_config = {
            "messages": [msg.dict() for msg in request.messages] if request.messages else [],
            "resources": request.resources or [],
            "max_plan_iterations": request.max_plan_iterations,
            "max_step_num": request.max_step_num,
            "max_search_results": request.max_search_results,
            "auto_accepted_plan": request.auto_accepted_plan,
            "interrupt_feedback": request.interrupt_feedback,
            "mcp_settings": request.mcp_settings or {},
            "enable_background_investigation": request.enable_background_investigation,
            "report_style": request.report_style.value if request.report_style else "academic",
            "enable_deep_thinking": request.enable_deep_thinking,
        }
        
        # 获取用户输入
        user_input = ""
        if request.messages:
            for msg in reversed(request.messages):
                if msg.role == "user" and msg.content:
                    if isinstance(msg.content, str):
                        user_input = msg.content
                    elif isinstance(msg.content, list):
                        # 提取文本内容
                        for item in msg.content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                user_input = item.get("text", "")
                                break
                    break
        
        # 创建任务
        task_info = task_manager.create_task(
            thread_id=request.thread_id,
            user_input=user_input,
            config=task_config
        )
        
        # 提交任务到后台工作器
        success = background_worker.submit_task(task_info)
        if not success:
            raise HTTPException(status_code=500, detail="提交任务到后台工作器失败")
        
        return {
            "task_id": task_info.task_id,
            "thread_id": task_info.thread_id,
            "status": task_info.status.value,
            "message": "异步聊天任务已创建并开始执行",
            "created_at": task_info.created_at.isoformat()
        }
        
    except Exception as e:
        logger.error(f"启动异步聊天任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"启动异步任务失败: {str(e)}")

@app.post("/api/chat/async", response_model=AsyncTaskResponse)
async def create_async_task(request: AsyncTaskRequest):
    """
    创建异步任务（内置执行，无需后台工作器）
    
    Args:
        request: 异步任务请求
        
    Returns:
        AsyncTaskResponse: 任务创建响应
    """
    try:
        logger.info(f"创建异步任务: thread_id={request.thread_id}")
        
        # 获取用户输入
        user_input = ""
        if request.messages:
            for msg in reversed(request.messages):
                if msg.get("role") == "user" and msg.get("content"):
                    user_input = msg["content"]
                    break
        
        # 创建任务记录
        task_info = task_manager.create_task(
            thread_id=request.thread_id,
            user_input=user_input,
            config={
                "messages": request.messages or [],
                "resources": request.resources or [],
                "max_plan_iterations": request.max_plan_iterations,
                "max_step_num": request.max_step_num,
                "max_search_results": request.max_search_results,
                "auto_accepted_plan": request.auto_accepted_plan,
                "interrupt_feedback": request.interrupt_feedback,
                "mcp_settings": request.mcp_settings or {},
                "enable_background_investigation": request.enable_background_investigation,
                "report_style": request.report_style.value if request.report_style else "academic",
                "enable_deep_thinking": request.enable_deep_thinking,
            }
        )
        
        # 直接在当前进程中异步执行任务
        async def execute_async_task():
            """异步执行任务的内部函数"""
            try:
                # 更新任务状态为运行中
                task_manager.update_task_status(task_info.task_id, TaskStatus.RUNNING)
                
                # 导入必要的模块
                from src.async_tasks.stream_runner import StreamGraphRunner
                
                # 创建StreamGraphRunner并执行
                runner = StreamGraphRunner()
                
                # 执行工作流
                result = await runner.execute_async(
                    task_id=task_info.task_id,
                    messages=task_info.config.get("messages", []),
                    thread_id=task_info.thread_id,
                    resources=task_info.config.get("resources", []),
                    max_plan_iterations=task_info.config.get("max_plan_iterations", 1),
                    max_step_num=task_info.config.get("max_step_num", 3),
                    max_search_results=task_info.config.get("max_search_results", 10),
                    auto_accepted_plan=task_info.config.get("auto_accepted_plan", True),
                    interrupt_feedback=task_info.config.get("interrupt_feedback", ""),
                    mcp_settings=task_info.config.get("mcp_settings", {}),
                    enable_background_investigation=task_info.config.get("enable_background_investigation", True),
                    report_style=ReportStyle(task_info.config.get("report_style", "academic")),
                    enable_deep_thinking=task_info.config.get("enable_deep_thinking", False)
                )
                
                # 更新任务状态为完成
                task_manager.update_task_status(task_info.task_id, TaskStatus.COMPLETED)
                logger.info(f"异步任务执行完成: task_id={task_info.task_id}")
                
            except Exception as e:
                logger.error(f"异步任务执行失败: task_id={task_info.task_id}, error={e}")
                task_manager.update_task_status(task_info.task_id, TaskStatus.FAILED, error_message=str(e))
        
        # 创建后台任务
        asyncio.create_task(execute_async_task())
        
        return AsyncTaskResponse(
            task_id=task_info.task_id,
            thread_id=task_info.thread_id,
            status=task_info.status.value,
            message="异步任务已创建并开始执行",
            created_at=task_info.created_at.isoformat()
        )
        
    except Exception as e:
        logger.error(f"创建异步任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建异步任务失败: {str(e)}")


@app.get("/api/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    获取任务状态
    
    Args:
        task_id: 任务ID
        
    Returns:
        TaskStatusResponse: 任务状态响应
    """
    try:
        # 直接从TaskManager获取任务状态
        task_info = task_manager.get_task(task_id)
        
        if not task_info:
            raise HTTPException(status_code=404, detail="任务不存在")
        
        task_dict = task_info.to_dict()
        return TaskStatusResponse(**task_dict)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务状态失败: task_id={task_id}, error={e}")
        raise HTTPException(status_code=500, detail=f"获取任务状态失败: {str(e)}")


@app.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks(
    thread_id: Optional[str] = Query(None, description="线程ID过滤"),
    status: Optional[str] = Query(None, description="状态过滤"),
    limit: int = Query(20, description="返回数量限制", ge=1, le=100)
):
    """
    获取任务列表
    
    Args:
        thread_id: 可选的线程ID过滤
        status: 可选的状态过滤
        limit: 返回数量限制
        
    Returns:
        TaskListResponse: 任务列表响应
    """
    try:
        if thread_id:
            # 获取特定线程的任务
            tasks = task_manager.get_tasks_by_thread(thread_id)
        else:
            # 获取所有任务（这里简化实现，实际可能需要分页）
            task_ids = task_manager.redis_client.lrange(
                task_manager.task_list_key, 0, limit - 1
            )
            tasks = []
            for task_id in task_ids:
                task_info = task_manager.get_task(task_id)
                if task_info:
                    tasks.append(task_info)
        
        # 状态过滤
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        
        # 限制数量
        tasks = tasks[:limit]
        
        # 转换为响应格式
        task_responses = []
        for task_info in tasks:
            task_dict = task_info.to_dict()
            task_responses.append(TaskStatusResponse(**task_dict))
        
        return TaskListResponse(
            tasks=task_responses,
            total_count=len(task_responses)
        )
        
    except Exception as e:
        logger.error(f"获取任务列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取任务列表失败: {str(e)}")


@app.post("/api/tasks/{task_id}/cancel", response_model=TaskCancelResponse)
async def cancel_task(task_id: str):
    """
    取消任务
    
    Args:
        task_id: 任务ID
        
    Returns:
        TaskCancelResponse: 取消响应
    """
    try:
        # 检查任务是否存在
        task_info = task_manager.get_task(task_id)
        if not task_info:
            return TaskCancelResponse(
                task_id=task_id,
                success=False,
                message="任务不存在"
            )
        
        # 如果任务还在运行，则更新状态为取消
        if task_info.status.value in ["pending", "running"]:
            task_manager.update_task_status(task_id, TaskStatus.CANCELLED)
            return TaskCancelResponse(
                task_id=task_id,
                success=True,
                message="任务已成功取消"
            )
        else:
            return TaskCancelResponse(
                task_id=task_id,
                success=False,
                message="任务已完成或已取消，无法取消"
            )
            
    except Exception as e:
        logger.error(f"取消任务失败: task_id={task_id}, error={e}")
        raise HTTPException(status_code=500, detail=f"取消任务失败: {str(e)}")


@app.get("/api/worker/stats", response_model=WorkerStatsResponse)
async def get_worker_stats():
    """
    获取工作器状态（简化版本，无需后台工作器）
    
    Returns:
        WorkerStatsResponse: 工作器状态响应
    """
    try:
        # 简化的统计信息，基于TaskManager
        total_tasks = len(task_manager.redis_client.lrange(task_manager.task_list_key, 0, -1))
        
        # 统计各种状态的任务数量
        pending_count = 0
        running_count = 0
        completed_count = 0
        failed_count = 0
        
        task_ids = task_manager.redis_client.lrange(task_manager.task_list_key, 0, -1)
        for task_id in task_ids[:100]:  # 限制检查数量避免性能问题
            task_info = task_manager.get_task(task_id)
            if task_info:
                if task_info.status.value == "pending":
                    pending_count += 1
                elif task_info.status.value == "running":
                    running_count += 1
                elif task_info.status.value == "completed":
                    completed_count += 1
                elif task_info.status.value == "failed":
                    failed_count += 1
        
        stats = {
            "is_running": True,  # API服务器本身在运行
            "total_tasks": total_tasks,
            "pending_tasks": pending_count,
            "running_tasks": running_count,
            "completed_tasks": completed_count,
            "failed_tasks": failed_count,
            "max_concurrent_tasks": 10,  # 理论上的异步任务并发数
            "uptime_seconds": 0  # 暂时不计算
        }
        
        return WorkerStatsResponse(**stats)
        
    except Exception as e:
        logger.error(f"获取工作器状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取工作器状态失败: {str(e)}")


@app.post("/api/worker/cleanup")
async def cleanup_old_tasks(days: int = Query(7, description="清理天数", ge=1, le=30)):
    """
    清理过期任务
    
    Args:
        days: 清理几天前的任务
        
    Returns:
        dict: 清理结果
    """
    try:
        cleaned_count = task_manager.cleanup_old_tasks(days)
        
        return {
            "success": True,
            "message": f"成功清理 {cleaned_count} 个过期任务",
            "cleaned_count": cleaned_count,
            "retention_days": days
        }
        
    except Exception as e:
        logger.error(f"清理过期任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理过期任务失败: {str(e)}")


async def find_latest_query_id(thread_id: str) -> Optional[str]:
    """
    查找指定线程的最新查询ID
    
    Args:
        thread_id: 线程ID
        
    Returns:
        str: 最新的查询ID，如果没有找到返回None
    """
    try:
        from src.config.redis_config import get_redis_client
        redis_client = get_redis_client()
        
        # 搜索所有相关的stream keys
        pattern = f"chat:{thread_id}:*"
        keys = redis_client.keys(pattern)
        
        if not keys:
            logger.info(f"🔍 find_latest_query_id: 没有找到匹配的键 pattern={pattern}")
            return None
        
        # 提取query_id并找到最新的（基于时间戳）
        latest_query_id = None
        latest_timestamp = 0
        
        for key in keys:
            # key格式: chat:{thread_id}:{query_id}
            key_str = key.decode('utf-8') if isinstance(key, bytes) else key
            parts = key_str.split(':')
            if len(parts) >= 3:
                query_id = ':'.join(parts[2:])  # 支持query_id中包含冒号
                
                # 获取stream的最新事件来判断时间
                try:
                    # 获取该stream的最后一个事件
                    last_events = redis_client.xrevrange(key_str, count=1)
                    if last_events:
                        event_id = last_events[0][0]
                        # Redis stream ID格式: timestamp-sequence
                        timestamp = int(event_id.decode('utf-8').split('-')[0])
                        if timestamp > latest_timestamp:
                            latest_timestamp = timestamp
                            latest_query_id = query_id
                except Exception as e:
                    logger.warning(f"处理stream key {key_str} 时出错: {e}")
                    continue
        
        logger.info(f"🎯 find_latest_query_id结果: thread_id={thread_id}, latest_query_id={latest_query_id}")
        return latest_query_id
        
    except Exception as e:
        logger.error(f"find_latest_query_id出错: {e}")
        return None


def get_next_stream_id(current_id: str) -> str:
    """
    获取下一个Stream ID，用于避免重复读取同一个事件
    
    Args:
        current_id: 当前事件ID，格式为 "timestamp-sequence"
        
    Returns:
        str: 下一个ID
    """
    try:
        if current_id == "0":
            return "0"
        
        # 解析当前ID
        parts = current_id.split('-')
        if len(parts) != 2:
            return current_id
        
        timestamp = int(parts[0])
        sequence = int(parts[1])
        
        # 返回下一个sequence
        return f"{timestamp}-{sequence + 1}"
    except:
        # 如果解析失败，直接返回原ID
        return current_id


def should_save_tool_call_chunk(chunk) -> bool:
    """判断tool_call_chunk是否值得保存到Redis Stream"""
    try:
        args = chunk.get('args', '')
        
        # 空内容不保存
        if not args or args is None:
            return False
        
        # 转换为字符串处理
        args_str = str(args).strip()
        
        # 空字符串不保存
        if not args_str:
            return False
        
        # 只有单个字符的无意义片段不保存（如 "2", "%", "www", "{", "}"等）
        if len(args_str) <= 2:
            # 但是保留有意义的短字符串，如 ": ", ", ", ":\""等
            meaningful_shorts = [': ', ', ', '": ', '" ', '": "', '", "', '}', '];']
            if args_str not in meaningful_shorts:
                return False
        
        # 过滤掉一些明显无用的单字符或短片段
        useless_patterns = ['2', '%', 'www', '#', '.', '/', '\\n', '=', ' =', '）\\n', '10', ' 文', '费用', '不超过', '类似', '降', '80', '：', '8', '.s']
        if args_str in useless_patterns:
            return False
        
        return True
        
    except Exception as e:
        logger.warning(f"判断tool_call_chunk有效性失败: {e}, chunk: {chunk}")
        # 出错时默认保存，避免丢失重要数据
        return True


@app.get("/api/threads/{thread_id}/research-status")
async def get_thread_research_status(thread_id: str):
    """
    检查指定线程是否有研究事件
    
    Returns:
        - has_research_events: 是否有研究相关事件
        - ongoing_research: 正在进行的研究信息（如果有）
        - completed_research: 已完成的研究列表
        - latest_research_id: 最新的研究ID
    """
    try:
        logger.info(f"🔍 检查线程 {thread_id} 的研究状态")
        
        # 1. 检查是否有运行中的任务
        running_task_info = await get_thread_running_task(thread_id)
        
        # 2. 搜索Redis Stream中的研究事件
        from src.config.redis_config import get_redis_client
        client = get_redis_client()
        
        # 搜索该线程的所有stream keys
        pattern = f"chat:{thread_id}:*"
        stream_keys = client.keys(pattern)
        
        research_events = {
            "has_research_events": False,
            "ongoing_research": None,
            "completed_research": [],
            "latest_research_id": None,
            "running_task": running_task_info
        }
        
        if not stream_keys:
            logger.info(f"📭 线程 {thread_id} 没有找到任何stream keys")
            return research_events
        
        logger.info(f"🔑 线程 {thread_id} 找到 {len(stream_keys)} 个stream keys: {stream_keys}")
        
        # 3. 扫描每个stream，查找研究相关事件
        all_research_events = []
        
        for stream_key in stream_keys:
            try:
                # 读取该stream的所有事件（限制数量避免太耗时）
                messages = client.xrange(stream_key, count=200)
                
                for message_id, fields in messages:
                    event_type = fields.get("event")
                    
                    if event_type in ["research_start", "research_end"]:
                        try:
                            import json
                            data_json = fields.get("data_json", "{}")
                            event_data = json.loads(data_json)
                            
                            all_research_events.append({
                                "stream_key": stream_key,
                                "message_id": message_id,
                                "event_type": event_type,
                                "event_data": event_data,
                                "timestamp": message_id  # Redis stream ID可以作为时间戳
                            })
                            
                            logger.debug(f"📊 发现研究事件: {event_type}, stream={stream_key}, data={event_data}")
                            
                        except json.JSONDecodeError as e:
                            logger.warning(f"⚠️ 解析事件数据失败: {e}")
                            continue
                            
            except Exception as e:
                logger.warning(f"⚠️ 读取stream失败 {stream_key}: {e}")
                continue
        
        if not all_research_events:
            logger.info(f"📭 线程 {thread_id} 没有找到研究事件")
            return research_events
        
        # 4. 分析研究事件，构建状态
        research_events["has_research_events"] = True
        
        # 按时间戳排序（Redis stream ID是递增的）
        all_research_events.sort(key=lambda x: x["timestamp"])
        
        # 跟踪每个研究的状态
        research_status = {}  # research_id -> latest_status
        
        for event in all_research_events:
            event_type = event["event_type"]
            event_data = event["event_data"]
            research_id = event_data.get("research_id") or event_data.get("id")
            
            if not research_id:
                continue
                
            if event_type == "research_start":
                research_status[research_id] = {
                    "research_id": research_id,
                    "status": "running",
                    "topic": event_data.get("topic", ""),
                    "query_id": event_data.get("query_id", ""),
                    "start_time": event["timestamp"],
                    "stream_key": event["stream_key"]
                }
            elif event_type == "research_end":
                if research_id in research_status:
                    research_status[research_id]["status"] = "completed"
                    research_status[research_id]["end_time"] = event["timestamp"]
                else:
                    # 可能只有end事件，创建一个completed状态的记录
                    research_status[research_id] = {
                        "research_id": research_id,
                        "status": "completed",
                        "topic": event_data.get("topic", ""),
                        "query_id": event_data.get("query_id", ""),
                        "end_time": event["timestamp"],
                        "stream_key": event["stream_key"]
                    }
        
        # 5. 分类研究状态
        ongoing_research = None
        completed_research = []
        latest_research_id = None
        
        for research_id, status in research_status.items():
            if status["status"] == "running":
                ongoing_research = status
            elif status["status"] == "completed":
                completed_research.append(status)
            
            # 更新最新的研究ID（按时间戳）
            if latest_research_id is None:
                latest_research_id = research_id
            else:
                current_latest = research_status[latest_research_id]
                latest_time = current_latest.get("end_time") or current_latest.get("start_time")
                this_time = status.get("end_time") or status.get("start_time")
                if this_time > latest_time:
                    latest_research_id = research_id
        
        # 按时间倒序排列已完成的研究
        completed_research.sort(key=lambda x: x.get("end_time", x.get("start_time", "")), reverse=True)
        
        research_events.update({
            "ongoing_research": ongoing_research,
            "completed_research": completed_research,
            "latest_research_id": latest_research_id,
        })
        
        logger.info(f"✅ 线程 {thread_id} 研究状态分析完成: ongoing={bool(ongoing_research)}, completed={len(completed_research)}, latest={latest_research_id}")
        
        return research_events
        
    except Exception as e:
        logger.error(f"❌ 检查线程研究状态失败 {thread_id}: {e}")
        import traceback
        logger.error(f"❌ 堆栈跟踪: {traceback.format_exc()}")
        return {
            "has_research_events": False,
            "ongoing_research": None,
            "completed_research": [],
            "latest_research_id": None,
            "running_task": None,
            "error": str(e)
        }
