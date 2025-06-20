# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import json
import logging
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from src.config.redis_config import get_redis_client

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"      # 等待执行
    RUNNING = "running"      # 正在执行
    COMPLETED = "completed"  # 执行完成
    FAILED = "failed"        # 执行失败
    CANCELLED = "cancelled"  # 用户取消


class TaskInfo:
    """任务信息类"""
    
    def __init__(
        self,
        task_id: str,
        thread_id: str,
        user_input: str,
        config: dict,
        status: TaskStatus = TaskStatus.PENDING,
        created_at: Optional[datetime] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
        progress: float = 0.0,
        current_step: Optional[str] = None
    ):
        self.task_id = task_id
        self.thread_id = thread_id
        self.user_input = user_input
        self.config = config
        self.status = status
        self.created_at = created_at or datetime.now()
        self.started_at = started_at
        self.completed_at = completed_at
        self.error_message = error_message
        self.progress = progress
        self.current_step = current_step

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "user_input": self.user_input,
            "config": self.config,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "progress": self.progress,
            "current_step": self.current_step,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskInfo":
        """从字典创建 TaskInfo 实例"""
        return cls(
            task_id=data["task_id"],
            thread_id=data["thread_id"],
            user_input=data["user_input"],
            config=data["config"],
            status=TaskStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            error_message=data.get("error_message"),
            progress=data.get("progress", 0.0),
            current_step=data.get("current_step"),
        )


class TaskManager:
    """异步任务管理器"""
    
    def __init__(self):
        self.redis_client = get_redis_client()
        self.task_key_prefix = "task:"
        self.task_list_key = "tasks:list"
        self.thread_tasks_prefix = "thread_tasks:"  # 新增：线程-任务关联前缀
        
    def create_task(
        self,
        thread_id: str,
        user_input: str,
        config: dict
    ) -> TaskInfo:
        """
        创建新的异步任务
        
        Args:
            thread_id: 线程ID
            user_input: 用户输入
            config: 任务配置
            
        Returns:
            TaskInfo: 新创建的任务信息
        """
        task_id = str(uuid4())
        
        task_info = TaskInfo(
            task_id=task_id,
            thread_id=thread_id,
            user_input=user_input,
            config=config,
            status=TaskStatus.PENDING
        )
        
        try:
            # 保存任务信息到 Redis
            task_key = f"{self.task_key_prefix}{task_id}"
            self.redis_client.setex(
                task_key,
                timedelta(days=7),  # 任务信息保留7天
                json.dumps(task_info.to_dict(), ensure_ascii=False)
            )
            
            # 添加到任务列表（用于查询所有任务）
            self.redis_client.lpush(self.task_list_key, task_id)
            
            # 保持任务列表最多1000个任务
            self.redis_client.ltrim(self.task_list_key, 0, 999)
            
            # 新增：关联线程和任务
            self._link_thread_to_task(thread_id, task_id)
            
            logger.info(f"创建异步任务: task_id={task_id}, thread_id={thread_id}")
            return task_info
            
        except Exception as e:
            logger.error(f"创建任务失败: {e}")
            raise

    def _link_thread_to_task(self, thread_id: str, task_id: str):
        """
        关联线程和任务
        
        Args:
            thread_id: 线程ID
            task_id: 任务ID
        """
        try:
            thread_tasks_key = f"{self.thread_tasks_prefix}{thread_id}"
            # 使用有序集合存储，score为时间戳，便于按时间排序
            timestamp = time.time()
            self.redis_client.zadd(thread_tasks_key, {task_id: timestamp})
            
            # 设置过期时间为30天
            self.redis_client.expire(thread_tasks_key, timedelta(days=30))
            
            logger.debug(f"关联线程和任务: thread_id={thread_id}, task_id={task_id}")
            
        except Exception as e:
            logger.error(f"关联线程和任务失败: thread_id={thread_id}, task_id={task_id}, error={e}")
    
    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """
        获取任务信息
        
        Args:
            task_id: 任务ID
            
        Returns:
            TaskInfo: 任务信息，如果不存在返回 None
        """
        try:
            task_key = f"{self.task_key_prefix}{task_id}"
            task_data = self.redis_client.get(task_key)
            
            if not task_data:
                return None
                
            return TaskInfo.from_dict(json.loads(task_data))
            
        except Exception as e:
            logger.error(f"获取任务信息失败: task_id={task_id}, error={e}")
            return None
    
    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_message: Optional[str] = None,
        progress: Optional[float] = None,
        current_step: Optional[str] = None
    ) -> bool:
        """
        更新任务状态
        
        Args:
            task_id: 任务ID
            status: 新状态
            error_message: 错误信息（可选）
            progress: 进度（0.0-1.0，可选）
            current_step: 当前步骤描述（可选）
            
        Returns:
            bool: 更新是否成功
        """
        try:
            task_info = self.get_task(task_id)
            if not task_info:
                logger.warning(f"任务不存在: task_id={task_id}")
                return False
            
            # 更新状态
            task_info.status = status
            
            # 更新时间戳
            now = datetime.now()
            if status == TaskStatus.RUNNING and not task_info.started_at:
                task_info.started_at = now
            elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                task_info.completed_at = now
            
            # 更新其他字段
            if error_message is not None:
                task_info.error_message = error_message
            if progress is not None:
                task_info.progress = max(0.0, min(1.0, progress))  # 确保在 0-1 范围内
            if current_step is not None:
                task_info.current_step = current_step
            
            # 保存到 Redis
            task_key = f"{self.task_key_prefix}{task_id}"
            self.redis_client.setex(
                task_key,
                timedelta(days=7),
                json.dumps(task_info.to_dict(), ensure_ascii=False)
            )
            
            logger.debug(f"更新任务状态: task_id={task_id}, status={status.value}")
            return True
            
        except Exception as e:
            logger.error(f"更新任务状态失败: task_id={task_id}, error={e}")
            return False
    
    def get_tasks_by_thread(self, thread_id: str) -> List[TaskInfo]:
        """
        获取指定线程的所有任务
        
        Args:
            thread_id: 线程ID
            
        Returns:
            List[TaskInfo]: 任务列表（按创建时间倒序）
        """
        try:
            thread_tasks_key = f"{self.thread_tasks_prefix}{thread_id}"
            # 按score倒序获取任务ID（最新的在前）
            task_ids = self.redis_client.zrevrange(thread_tasks_key, 0, -1)
            
            tasks = []
            for task_id in task_ids:
                task_info = self.get_task(task_id)
                if task_info:
                    tasks.append(task_info)
            
            return tasks
            
        except Exception as e:
            logger.error(f"获取线程任务失败: thread_id={thread_id}, error={e}")
            return []
    
    def get_latest_task_by_thread(self, thread_id: str) -> Optional[TaskInfo]:
        """
        获取线程的最新任务
        
        Args:
            thread_id: 线程ID
            
        Returns:
            TaskInfo: 最新任务信息，如果不存在返回 None
        """
        try:
            thread_tasks_key = f"{self.thread_tasks_prefix}{thread_id}"
            # 获取最新的一个任务ID
            task_ids = self.redis_client.zrevrange(thread_tasks_key, 0, 0)
            
            if not task_ids:
                return None
                
            task_id = task_ids[0]
            return self.get_task(task_id)
            
        except Exception as e:
            logger.error(f"获取线程最新任务失败: thread_id={thread_id}, error={e}")
            return None
    
    def get_running_task_by_thread(self, thread_id: str) -> Optional[TaskInfo]:
        """
        获取线程中正在运行的任务
        
        Args:
            thread_id: 线程ID
            
        Returns:
            TaskInfo: 正在运行的任务信息，如果不存在返回 None
        """
        try:
            tasks = self.get_tasks_by_thread(thread_id)
            
            # 查找正在运行的任务
            for task in tasks:
                if task.status == TaskStatus.RUNNING:
                    return task
                    
            return None
            
        except Exception as e:
            logger.error(f"获取线程运行任务失败: thread_id={thread_id}, error={e}")
            return None
    
    def get_running_tasks(self) -> List[TaskInfo]:
        """
        获取所有正在运行的任务
        
        Returns:
            List[TaskInfo]: 正在运行的任务列表
        """
        try:
            task_ids = self.redis_client.lrange(self.task_list_key, 0, -1)
            
            running_tasks = []
            for task_id in task_ids:
                task_info = self.get_task(task_id)
                if task_info and task_info.status == TaskStatus.RUNNING:
                    running_tasks.append(task_info)
            
            return running_tasks
            
        except Exception as e:
            logger.error(f"获取运行中任务失败: {e}")
            return []
    
    def cancel_task(self, task_id: str) -> bool:
        """
        取消任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            bool: 取消是否成功
        """
        task_info = self.get_task(task_id)
        if not task_info:
            return False
        
        if task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            logger.warning(f"任务已完成，无法取消: task_id={task_id}, status={task_info.status.value}")
            return False
        
        return self.update_task_status(task_id, TaskStatus.CANCELLED)
    
    def cleanup_old_tasks(self, days: int = 7) -> int:
        """
        清理过期任务
        
        Args:
            days: 保留天数，默认7天
            
        Returns:
            int: 清理的任务数量
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=days)
            task_ids = self.redis_client.lrange(self.task_list_key, 0, -1)
            
            cleaned_count = 0
            for task_id in task_ids:
                task_info = self.get_task(task_id)
                if task_info and task_info.created_at < cutoff_time:
                    # 删除任务信息
                    task_key = f"{self.task_key_prefix}{task_id}"
                    self.redis_client.delete(task_key)
                    
                    # 从任务列表中移除
                    self.redis_client.lrem(self.task_list_key, 1, task_id)
                    cleaned_count += 1
            
            logger.info(f"清理过期任务完成: 清理数量={cleaned_count}")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"清理过期任务失败: {e}")
            return 0 