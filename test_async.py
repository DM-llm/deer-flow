#!/usr/bin/env python3

import requests
import json
import time

def test_async_task():
    # 创建异步任务
    url = "http://localhost:8000/api/chat/async"
    data = {
        "thread_id": "test_thread_fix", 
        "messages": [
            {"role": "user", "content": "测试Redis事件写入"}
        ]
    }
    
    print("🔄 创建异步任务...")
    response = requests.post(url, json=data)
    print(f"响应状态: {response.status_code}")
    print(f"响应内容: {response.text}")
    
    if response.status_code == 200:
        result = response.json()
        task_id = result.get("task_id")
        thread_id = result.get("thread_id")
        
        print(f"✅ 任务创建成功: task_id={task_id}")
        
        # 等待5秒让任务执行
        print("⏳ 等待5秒...")
        time.sleep(5)
        
        # 检查任务状态
        task_url = f"http://localhost:8000/api/tasks/{task_id}"
        task_response = requests.get(task_url)
        print(f"📊 任务状态: {task_response.json()}")
        
        # 测试回放
        replay_url = f"http://localhost:8000/api/chat/replay?thread_id={thread_id}&query_id={task_id}&continuous=false&offset=0"
        print(f"🔍 测试回放: {replay_url}")
        
        replay_response = requests.get(replay_url, stream=True)
        print(f"回放响应状态: {replay_response.status_code}")
        
        # 读取SSE事件
        for line in replay_response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                print(f"事件: {decoded_line}")
                if "replay_end" in decoded_line:
                    break
        
        return task_id, thread_id
    else:
        print(f"❌ 任务创建失败: {response.text}")
        return None, None

if __name__ == "__main__":
    test_async_task() 