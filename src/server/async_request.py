# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum

from src.rag.retriever import Resource
from src.config.report_style import ReportStyle


class AsyncTaskRequest(BaseModel):
    """异步任务创建请求"""
    messages: Optional[List[Dict[str, Any]]] = Field(
        [], description="消息历史"
    )
    thread_id: str = Field(
        ..., description="线程ID"
    )
    resources: Optional[List[Resource]] = Field(
        [], description="研究资源"
    )
    debug: Optional[bool] = Field(
        False, description="是否启用调试日志"
    )
    max_plan_iterations: Optional[int] = Field(
        1, description="最大计划迭代次数"
    )
    max_step_num: Optional[int] = Field(
        3, description="计划中的最大步骤数"
    )
    max_search_results: Optional[int] = Field(
        3, description="最大搜索结果数"
    )
    auto_accepted_plan: Optional[bool] = Field(
        True, description="是否自动接受计划"
    )
    interrupt_feedback: Optional[str] = Field(
        None, description="中断反馈"
    )
    mcp_settings: Optional[Dict[str, Any]] = Field(
        {}, description="MCP设置"
    )
    enable_background_investigation: Optional[bool] = Field(
        True, description="是否启用背景调查"
    )
    report_style: Optional[ReportStyle] = Field(
        ReportStyle.ACADEMIC, description="报告风格"
    )
    enable_deep_thinking: Optional[bool] = Field(
        False, description="是否启用深度思考"
    )


class AsyncTaskResponse(BaseModel):
    """异步任务创建响应"""
    task_id: str = Field(..., description="任务ID")
    thread_id: str = Field(..., description="线程ID")
    status: str = Field(..., description="任务状态")
    message: str = Field(..., description="响应消息")
    created_at: str = Field(..., description="创建时间")


class TaskStatusResponse(BaseModel):
    """任务状态响应"""
    task_id: str = Field(..., description="任务ID")
    thread_id: str = Field(..., description="线程ID")
    user_input: str = Field(..., description="用户输入")
    status: str = Field(..., description="任务状态")
    progress: float = Field(..., description="进度 (0.0-1.0)")
    current_step: Optional[str] = Field(None, description="当前步骤")
    created_at: Optional[str] = Field(None, description="创建时间")
    started_at: Optional[str] = Field(None, description="开始时间")
    completed_at: Optional[str] = Field(None, description="完成时间")
    error_message: Optional[str] = Field(None, description="错误信息")
    runtime_info: Optional[Dict[str, Any]] = Field(None, description="运行时信息")


class TaskListResponse(BaseModel):
    """任务列表响应"""
    tasks: List[TaskStatusResponse] = Field(..., description="任务列表")
    total_count: int = Field(..., description="总任务数")


class WorkerStatsResponse(BaseModel):
    """工作器状态响应"""
    is_running: bool = Field(..., description="工作器是否运行中")
    max_concurrent_tasks: int = Field(..., description="最大并发任务数")
    current_running_count: int = Field(..., description="当前运行任务数")
    running_task_ids: List[str] = Field(..., description="运行中的任务ID列表")
    available_slots: int = Field(..., description="可用任务槽位数")


class TaskCancelResponse(BaseModel):
    """任务取消响应"""
    task_id: str = Field(..., description="任务ID")
    success: bool = Field(..., description="取消是否成功")
    message: str = Field(..., description="响应消息") 