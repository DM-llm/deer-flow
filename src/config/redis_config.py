# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import os
import logging
import redis
from typing import Optional

logger = logging.getLogger(__name__)

# Redis 连接配置
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Redis客户端实例
_redis_client: Optional[redis.Redis] = None

def get_redis_client() -> redis.Redis:
    """获取Redis客户端实例（单例模式）"""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                decode_responses=True,  # 自动解码响应为字符串
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            # 测试连接
            _redis_client.ping()
            logger.info(f"成功连接到Redis: {REDIS_HOST}:{REDIS_PORT}")
        except Exception as e:
            logger.error(f"Redis连接失败: {e}")
            # 如果Redis连接失败，返回一个空的mock客户端避免系统崩溃
            _redis_client = MockRedisClient()
    
    return _redis_client

class MockRedisClient:
    """Redis连接失败时的Mock客户端，避免系统崩溃"""
    
    def __init__(self):
        logger.warning("使用MockRedisClient，Redis功能将被禁用")
    
    def xadd(self, *args, **kwargs):
        logger.debug("MockRedisClient.xadd 被调用但未执行")
        return "mock-stream-id"
    
    def xrange(self, *args, **kwargs):
        logger.debug("MockRedisClient.xrange 被调用但未执行")
        return []
    
    def ping(self):
        return True
    
    def __getattr__(self, name):
        def mock_method(*args, **kwargs):
            logger.debug(f"MockRedisClient.{name} 被调用但未执行")
            return None
        return mock_method

def write_event_to_stream(thread_id: str, event: str, data: dict, stream_suffix: str = "default") -> str:
    """
    将事件写入Redis Stream
    
    Args:
        thread_id: 线程ID
        event: 事件类型
        data: 事件数据
        stream_suffix: stream后缀，默认为"default"
    
    Returns:
        stream_id: Redis stream的消息ID
    """
    try:
        import json
        client = get_redis_client()
        stream_key = f"chat:{thread_id}:{stream_suffix}"
        
        # 将数据序列化为JSON字符串，以支持复杂数据类型
        serialized_data = {}
        for key, value in data.items():
            if value is None:
                serialized_data[key] = "null"
            elif isinstance(value, (dict, list)):
                serialized_data[key] = json.dumps(value, ensure_ascii=False)
            else:
                serialized_data[key] = str(value)
        
        # 准备写入的数据
        stream_data = {
            "event": event,
            "thread_id": thread_id,
            "data_json": json.dumps(serialized_data, ensure_ascii=False)
        }
        
        # 写入Redis Stream
        stream_id = client.xadd(stream_key, stream_data)
        logger.debug(f"成功写入Redis Stream: {stream_key}, ID: {stream_id}")
        return stream_id
        
    except Exception as e:
        logger.error(f"写入Redis Stream失败: {e}")
        return f"error-{thread_id}"

def read_events_from_stream(thread_id: str, start: str = "0", stream_suffix: str = "default", count: int = 100) -> list:
    """
    从Redis Stream读取事件
    
    Args:
        thread_id: 线程ID
        start: 开始读取的位置，默认从头开始
        stream_suffix: stream后缀，默认为"default"
        count: 每次读取的最大事件数量
    
    Returns:
        events: 事件列表
    """
    try:
        import json
        client = get_redis_client()
        stream_key = f"chat:{thread_id}:{stream_suffix}"
        
        logger.info(f"🔍 [read_events_from_stream] 参数: thread_id={thread_id}, start={start}, stream_suffix={stream_suffix}, count={count}")
        logger.info(f"🔑 [read_events_from_stream] 生成的stream_key: {stream_key}")
        
        # 检查客户端类型
        client_type = type(client).__name__
        logger.info(f"🔌 [read_events_from_stream] Redis客户端类型: {client_type}")
        
        # 从Redis Stream读取数据
        messages = client.xrange(stream_key, min=start, count=count)
        logger.info(f"📥 [read_events_from_stream] 原始Redis响应: {len(messages)} 条消息")
        
        if messages:
            logger.info(f"📝 [read_events_from_stream] 第一条消息示例: {messages[0]}")
        
        events = []
        for message_id, fields in messages:
            logger.debug(f"🔍 [read_events_from_stream] 处理消息: ID={message_id}, fields={fields}")
            
            # 反序列化数据
            event_data = {}
            if "data_json" in fields:
                try:
                    serialized_data = json.loads(fields["data_json"])
                    for key, value in serialized_data.items():
                        if value == "null":
                            event_data[key] = None
                        elif isinstance(value, str) and (value.startswith("{") or value.startswith("[")):
                            try:
                                event_data[key] = json.loads(value)
                            except:
                                event_data[key] = value
                        else:
                            event_data[key] = value
                except json.JSONDecodeError:
                    logger.error(f"解析事件数据失败: {fields.get('data_json')}")
                    event_data = {"error": "数据解析失败"}
            
            events.append({
                "id": message_id,
                "event": fields.get("event"),
                "thread_id": fields.get("thread_id"),
                "data": event_data
            })
        
        logger.info(f"✅ [read_events_from_stream] 从Redis Stream读取到 {len(events)} 个事件: {stream_key}")
        return events
        
    except Exception as e:
        logger.error(f"❌ [read_events_from_stream] 从Redis Stream读取失败: {e}")
        import traceback
        logger.error(f"❌ [read_events_from_stream] 堆栈跟踪: {traceback.format_exc()}")
        return [] 