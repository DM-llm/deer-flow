# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Callable
import signal
import sys

from .task_manager import TaskManager, TaskStatus, TaskInfo
from .stream_runner import StreamGraphRunner

logger = logging.getLogger(__name__)


class BackgroundWorker:
    """后台工作器 - 管理任务队列和异步执行"""
    
    def __init__(self, max_concurrent_tasks: int = 3):
        self.task_manager = TaskManager()
        self.stream_runner = StreamGraphRunner(self.task_manager)
        self.max_concurrent_tasks = max_concurrent_tasks
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self.executor = ThreadPoolExecutor(max_workers=max_concurrent_tasks)
        self.is_running = False
        self._stop_event = threading.Event()
        
        # 注册信号处理器，用于优雅关闭
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        """处理关闭信号"""
        logger.info(f"收到关闭信号 {signum}，开始优雅关闭...")
        self.stop()
        
    def start(self):
        """启动后台工作器"""
        if self.is_running:
            logger.warning("后台工作器已在运行中")
            return
            
        self.is_running = True
        logger.info("启动后台工作器...")
        
        # 启动主循环
        try:
            asyncio.run(self._main_loop())
        except KeyboardInterrupt:
            logger.info("收到键盘中断，停止后台工作器")
        except Exception as e:
            logger.error(f"后台工作器异常退出: {e}")
        finally:
            self.cleanup()
    
    def stop(self):
        """停止后台工作器"""
        logger.info("正在停止后台工作器...")
        self.is_running = False
        self._stop_event.set()
        
    def cleanup(self):
        """清理资源"""
        logger.info("清理后台工作器资源...")
        
        # 取消所有运行中的任务
        for task_id, task in self.running_tasks.items():
            if not task.done():
                logger.info(f"取消任务: {task_id}")
                task.cancel()
                # 更新任务状态
                self.task_manager.update_task_status(
                    task_id, 
                    TaskStatus.CANCELLED,
                    current_step="服务关闭，任务被取消"
                )
        
        # 关闭线程池
        self.executor.shutdown(wait=True)
        logger.info("后台工作器资源清理完成")
    
    async def _main_loop(self):
        """主循环 - 检查待处理任务并执行"""
        logger.info("后台工作器主循环启动")
        
        while self.is_running:
            try:
                # 清理已完成的任务
                await self._cleanup_completed_tasks()
                
                # 检查是否有可执行的任务槽位
                if len(self.running_tasks) < self.max_concurrent_tasks:
                    # 获取待处理任务
                    pending_tasks = await self._get_pending_tasks()
                    
                    for task_info in pending_tasks:
                        if len(self.running_tasks) >= self.max_concurrent_tasks:
                            break
                            
                        # 启动任务
                        await self._start_task(task_info)
                
                # 等待一段时间再检查
                await asyncio.sleep(1.0)  # 每秒检查一次
                
            except Exception as e:
                logger.error(f"主循环异常: {e}")
                await asyncio.sleep(5.0)  # 异常后等待5秒再继续
    
    async def _cleanup_completed_tasks(self):
        """清理已完成的任务"""
        completed_task_ids = []
        
        for task_id, task in self.running_tasks.items():
            if task.done():
                completed_task_ids.append(task_id)
                
                try:
                    # 获取任务结果
                    result = await task
                    logger.info(f"任务完成: task_id={task_id}, result={result}")
                except Exception as e:
                    logger.error(f"任务执行异常: task_id={task_id}, error={e}")
        
        # 从运行列表中移除已完成的任务
        for task_id in completed_task_ids:
            del self.running_tasks[task_id]
    
    async def _get_pending_tasks(self) -> list:
        """获取待处理任务"""
        try:
            # 获取所有任务ID
            task_ids = self.task_manager.redis_client.lrange(
                self.task_manager.task_list_key, 0, -1
            )
            
            pending_tasks = []
            for task_id in task_ids:
                task_info = self.task_manager.get_task(task_id)
                if (task_info and 
                    task_info.status == TaskStatus.PENDING and 
                    task_id not in self.running_tasks):
                    pending_tasks.append(task_info)
                    
                    # 限制每次处理的任务数量
                    if len(pending_tasks) >= 10:
                        break
            
            return pending_tasks
            
        except Exception as e:
            logger.error(f"获取待处理任务失败: {e}")
            return []
    
    async def _start_task(self, task_info: TaskInfo):
        """启动任务执行"""
        try:
            task_id = task_info.task_id
            logger.info(f"启动任务执行: task_id={task_id}, thread_id={task_info.thread_id}")
            
            # 创建异步任务
            task = asyncio.create_task(
                self.stream_runner.execute_async(
                    task_id=task_info.task_id,
                    messages=task_info.config.get("messages", []),
                    thread_id=task_info.thread_id,
                    resources=task_info.config.get("resources", []),
                    max_plan_iterations=task_info.config.get("max_plan_iterations", 1),
                    max_step_num=task_info.config.get("max_step_num", 3),
                    max_search_results=task_info.config.get("max_search_results", 3),
                    auto_accepted_plan=task_info.config.get("auto_accepted_plan", True),
                    interrupt_feedback=task_info.config.get("interrupt_feedback"),
                    mcp_settings=task_info.config.get("mcp_settings", {}),
                    enable_background_investigation=task_info.config.get("enable_background_investigation", True),
                    report_style=task_info.config.get("report_style", "academic"),
                    enable_deep_thinking=task_info.config.get("enable_deep_thinking", False),
                ),
                name=f"task-{task_id}"
            )
            
            # 添加到运行列表
            self.running_tasks[task_id] = task
            
            logger.info(f"任务已启动: task_id={task_id}")
            
        except Exception as e:
            logger.error(f"启动任务失败: task_id={task_info.task_id}, error={e}")
            
            # 更新任务状态为失败
            self.task_manager.update_task_status(
                task_info.task_id,
                TaskStatus.FAILED,
                error_message=f"启动失败: {str(e)}",
                current_step="任务启动失败"
            )
    
    def submit_task(self, task_info: TaskInfo) -> bool:
        """提交任务到队列（同步接口，供 FastAPI 调用）"""
        try:
            logger.info(f"提交任务到队列: task_id={task_info.task_id}")
            # 任务已经在 TaskManager.create_task() 中保存到 Redis
            # 这里只需要确认任务状态为 PENDING
            return task_info.status == TaskStatus.PENDING
            
        except Exception as e:
            logger.error(f"提交任务失败: task_id={task_info.task_id}, error={e}")
            return False
    
    def get_task_status(self, task_id: str) -> Optional[dict]:
        """获取任务状态（包含运行时信息）"""
        try:
            task_info = self.task_manager.get_task(task_id)
            if not task_info:
                return None
            
            result = task_info.to_dict()
            
            # 添加运行时信息
            if task_id in self.running_tasks:
                task = self.running_tasks[task_id]
                result["runtime_info"] = {
                    "is_running": not task.done(),
                    "task_name": task.get_name() if hasattr(task, 'get_name') else None,
                }
            
            return result
            
        except Exception as e:
            logger.error(f"获取任务状态失败: task_id={task_id}, error={e}")
            return None
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        try:
            # 如果任务正在运行，取消 asyncio 任务
            if task_id in self.running_tasks:
                task = self.running_tasks[task_id]
                if not task.done():
                    task.cancel()
                    logger.info(f"已取消运行中的任务: task_id={task_id}")
            
            # 更新任务状态
            success = self.task_manager.cancel_task(task_id)
            return success
            
        except Exception as e:
            logger.error(f"取消任务失败: task_id={task_id}, error={e}")
            return False
    
    def get_worker_stats(self) -> dict:
        """获取工作器统计信息"""
        try:
            running_tasks = list(self.running_tasks.keys())
            pending_tasks = self.task_manager.get_running_tasks()  # 实际上获取的是所有状态的任务
            
            return {
                "is_running": self.is_running,
                "max_concurrent_tasks": self.max_concurrent_tasks,
                "current_running_count": len(running_tasks),
                "running_task_ids": running_tasks,
                "available_slots": self.max_concurrent_tasks - len(running_tasks),
            }
            
        except Exception as e:
            logger.error(f"获取工作器状态失败: {e}")
            return {"error": str(e)}


# 全局后台工作器实例
_background_worker: Optional[BackgroundWorker] = None

def get_background_worker() -> BackgroundWorker:
    """获取全局后台工作器实例（单例模式）"""
    global _background_worker
    if _background_worker is None:
        _background_worker = BackgroundWorker()
    return _background_worker

def start_background_worker():
    """启动后台工作器（用于独立进程）"""
    worker = get_background_worker()
    worker.start()

if __name__ == "__main__":
    # 支持直接运行后台工作器
    start_background_worker() 