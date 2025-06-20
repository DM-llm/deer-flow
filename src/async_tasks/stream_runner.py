# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable, cast
from langchain_core.messages import AIMessageChunk, ToolMessage, BaseMessage
from langgraph.types import Command

from src.config.redis_config import write_event_to_stream
from src.graph.builder import build_graph_with_memory
from src.rag.retriever import Resource
from src.config.report_style import ReportStyle
from .task_manager import TaskManager, TaskStatus

logger = logging.getLogger(__name__)


def should_save_tool_call_chunk(chunk) -> bool:
    """
    判断tool_call_chunk是否应该保存
    过滤掉无效的chunk（例如name为None的chunk）
    """
    if chunk is None:
        return False
    
    # 如果chunk有name字段，必须不为None
    if hasattr(chunk, 'name') and chunk.name is None:
        return False
    
    # 如果chunk有args字段，必须不为空
    if hasattr(chunk, 'args') and (chunk.args is None or chunk.args == ''):
        return False
    
    return True


class StreamGraphRunner:
    """流式图执行器 - 负责异步执行 LangGraph 并写入 Redis Stream"""
    
    def __init__(self, task_manager: Optional[TaskManager] = None):
        self.graph = build_graph_with_memory()
        self.task_manager = task_manager or TaskManager()
        
    async def execute_async(
        self,
        task_id: str,
        messages: List[dict],
        thread_id: str,
        resources: List[Resource] = None,
        max_plan_iterations: int = 1,
        max_step_num: int = 3,
        max_search_results: int = 3,
        auto_accepted_plan: bool = True,
        interrupt_feedback: str = None,
        mcp_settings: dict = None,
        enable_background_investigation: bool = True,
        report_style = ReportStyle.ACADEMIC,
        enable_deep_thinking: bool = False,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> dict:
        """
        异步执行工作流并将事件写入 Redis Stream
        
        Args:
            task_id: 任务ID
            messages: 消息列表
            thread_id: 线程ID
            其他参数: 工作流配置参数
            progress_callback: 进度回调函数（可选）
            
        Returns:
            dict: 执行结果
        """
        try:
            # 使用 task_id 作为 query_id，与回放 API 保持一致
            query_id = task_id
            
            # 更新任务状态为运行中
            self.task_manager.update_task_status(
                task_id, 
                TaskStatus.RUNNING, 
                current_step="初始化工作流"
            )
            
            # 写入研究开始事件
            try:
                research_id = query_id
                start_event_payload = {
                    "task_id": task_id,
                    "thread_id": thread_id,
                    "query_id": query_id,
                    "research_id": research_id,
                    "id": research_id,
                    "role": "assistant",
                    "agent": "researcher",
                    "content": "开始研究调查...",
                    "status": "running",
                    "topic": messages[-1]["content"] if messages else "",
                    "research_topic": messages[-1]["content"] if messages else "",
                }
                write_event_to_stream(thread_id, "research_start", start_event_payload, task_id)
                logger.info(f"✅ 写入research_start事件: {thread_id}:{task_id}")
            except Exception as e:
                logger.error(f"❌ 写入research_start事件失败: {e}")
            
            # 构建输入数据
            input_data = {
                "messages": messages,
                "plan_iterations": 0,
                "final_report": "",
                "current_plan": None,
                "observations": [],
                "auto_accepted_plan": auto_accepted_plan,
                "enable_background_investigation": enable_background_investigation,
                "research_topic": messages[-1]["content"] if messages else "",
            }
            
            # 处理中断反馈
            if not auto_accepted_plan and interrupt_feedback:
                resume_msg = f"[{interrupt_feedback}]"
                if messages:
                    resume_msg += f" {messages[-1]['content']}"
                input_data = Command(resume=resume_msg)
            
            # 构建配置 - 与原始chat_stream保持一致
            config = {
                "thread_id": thread_id,
                "resources": resources or [],
                "max_plan_iterations": max_plan_iterations,
                "max_step_num": max_step_num,
                "max_search_results": max_search_results,
                "mcp_settings": mcp_settings or {},
                "report_style": report_style.value if hasattr(report_style, 'value') else str(report_style),
                "enable_deep_thinking": enable_deep_thinking,
            }
            
            logger.info(f"🚀 开始异步执行工作流: task_id={task_id}, thread_id={thread_id}")
            
            # 执行图并处理事件流 - 完全复制原有的_astream_workflow_generator逻辑
            event_count = 0
            final_result = None
            
            async for agent, _, event_data in self.graph.astream(
                input_data,
                config=config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
            ):
                event_count += 1
                logger.debug(f"📨 收到事件 #{event_count}: agent={agent}, type={type(event_data)}")
                
                # 处理字典类型事件（中断事件）
                if isinstance(event_data, dict):
                    if "__interrupt__" in event_data:
                        await self._handle_interrupt_event(task_id, thread_id, event_data, query_id)
                    continue
                
                # 处理消息事件 - 完全按照原有逻辑处理
                message_chunk, message_metadata = cast(
                    tuple[BaseMessage, dict[str, any]], event_data
                )
                
                # 构建基础事件消息
                event_stream_message: dict[str, any] = {
                    "thread_id": thread_id,
                    "query_id": query_id,
                    "agent": agent[0].split(":")[0],
                    "id": message_chunk.id,
                    "role": "assistant",
                    "content": message_chunk.content,
                }
                
                # 添加推理内容（如果存在）
                if message_chunk.additional_kwargs.get("reasoning_content"):
                    event_stream_message["reasoning_content"] = message_chunk.additional_kwargs[
                        "reasoning_content"
                    ]
                
                # 添加完成原因（如果存在）
                if message_chunk.response_metadata.get("finish_reason"):
                    event_stream_message["finish_reason"] = message_chunk.response_metadata.get(
                        "finish_reason"
                    )
                
                # 根据消息类型处理不同的事件
                if isinstance(message_chunk, ToolMessage):
                    # Tool Message - 工具调用结果
                    event_stream_message["tool_call_id"] = message_chunk.tool_call_id
                    await self._write_event_to_redis("tool_call_result", event_stream_message, thread_id, task_id)
                    
                elif isinstance(message_chunk, AIMessageChunk):
                    # AI Message Chunk - 根据内容类型分类处理
                    if message_chunk.tool_calls:
                        # AI Message - 工具调用
                        event_stream_message["tool_calls"] = message_chunk.tool_calls
                        event_stream_message["tool_call_chunks"] = (
                            message_chunk.tool_call_chunks
                        )
                        await self._write_event_to_redis("tool_calls", event_stream_message, thread_id, task_id)
                        
                    elif message_chunk.tool_call_chunks:
                        # AI Message - 工具调用分块
                        # 过滤掉无效的tool_call_chunks
                        valid_chunks = []
                        for chunk in message_chunk.tool_call_chunks:
                            if should_save_tool_call_chunk(chunk):
                                valid_chunks.append(chunk)
                        
                        # 只有存在有效chunks时才保存和发送
                        if valid_chunks:
                            event_stream_message["tool_call_chunks"] = valid_chunks
                            await self._write_event_to_redis("tool_call_chunks", event_stream_message, thread_id, task_id)
                    else:
                        # AI Message - 普通消息分块
                        await self._write_event_to_redis("message_chunk", event_stream_message, thread_id, task_id)
                
                # 更新进度（每50个事件更新一次）
                if event_count % 50 == 0:
                    progress = min(0.9, event_count / 1000.0)  # 最大90%，留10%给完成状态
                    self.task_manager.update_task_status(
                        task_id,
                        TaskStatus.RUNNING,
                        progress=progress,
                        current_step=f"处理事件中... ({event_count} 个事件)"
                    )
                    if progress_callback:
                        progress_callback(progress, f"处理事件中... ({event_count} 个事件)")
            
            # 写入研究结束事件
            try:
                research_id = query_id
                end_event_payload = {
                    "thread_id": thread_id,
                    "query_id": query_id,
                    "research_id": research_id,
                    "id": research_id,
                    "role": "assistant",
                    "agent": "reporter",
                    "content": "研究调查完成",
                    "status": "completed",
                    "finish_reason": "completed",
                }
                write_event_to_stream(thread_id, "research_end", end_event_payload, task_id)
                logger.info(f"✅ 写入research_end事件: {thread_id}:{task_id}")
            except Exception as e:
                logger.error(f"❌ 写入research_end事件失败: {e}")
            
            # 更新任务状态为完成
            self.task_manager.update_task_status(
                task_id,
                TaskStatus.COMPLETED,
                progress=1.0,
                current_step=f"任务完成，共处理 {event_count} 个事件"
            )
            
            logger.info(f"✅ 异步任务执行完成: task_id={task_id}, 事件总数={event_count}")
            
            return {
                "task_id": task_id,
                "status": "completed",
                "event_count": event_count,
                "final_result": final_result
            }
            
        except Exception as e:
            logger.error(f"❌ 异步任务执行失败: task_id={task_id}, 错误: {e}", exc_info=True)
            
            # 写入错误事件
            try:
                await self._write_error_event(task_id, thread_id, str(e), task_id)
            except Exception as error_write_error:
                logger.error(f"❌ 写入错误事件失败: {error_write_error}")
            
            # 更新任务状态为失败
            self.task_manager.update_task_status(
                task_id,
                TaskStatus.FAILED,
                error_message=str(e)
            )
            
            raise
    
    async def _write_event_to_redis(self, event_type: str, event_data: dict, thread_id: str, task_id: str):
        """写入事件到Redis Stream，并添加错误处理"""
        try:
            write_event_to_stream(thread_id, event_type, event_data, task_id)
            logger.debug(f"✅ 写入{event_type}事件: {thread_id}:{task_id}")
        except Exception as e:
            logger.error(f"❌ 写入{event_type}事件失败: {e}")
    
    async def _handle_interrupt_event(self, task_id: str, thread_id: str, event_data: dict, query_id: str):
        """处理中断事件"""
        try:
            event_payload = {
                "task_id": task_id,
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
            await self._write_event_to_redis("interrupt", event_payload, thread_id, task_id)
            logger.info(f"✅ 处理中断事件: {thread_id}:{task_id}")
        except Exception as e:
            logger.error(f"❌ 处理中断事件失败: {e}")
    
    async def _write_error_event(self, task_id: str, thread_id: str, error_message: str, query_id: str):
        """写入错误事件"""
        try:
            error_payload = {
                "task_id": task_id,
                "thread_id": thread_id,
                "query_id": query_id,
                "id": f"error_{task_id}",
                "role": "assistant",
                "agent": "system",
                "content": error_message,
                "message": error_message,
                "finish_reason": "error",
            }
            write_event_to_stream(thread_id, "error", error_payload, task_id)
            logger.info(f"✅ 写入错误事件: {thread_id}:{task_id}")
        except Exception as e:
            logger.error(f"❌ 写入错误事件失败: {e}")
    
    def stop_task(self, task_id: str) -> bool:
        """停止任务（暂未实现）"""
        logger.warning(f"停止任务功能暂未实现: {task_id}")
        return False 