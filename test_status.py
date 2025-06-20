#!/usr/bin/env python3

import requests
import json
import time
import subprocess

def check_task_status():
    task_id = "aac8d65f-5817-4857-9218-eb4c2defd919"
    thread_id = "test_thread_fix"
    
    # 检查任务状态
    print("📊 检查任务状态...")
    task_url = f"http://localhost:8000/api/tasks/{task_id}"
    try:
        task_response = requests.get(task_url)
        print(f"任务状态响应: {task_response.status_code}")
        if task_response.status_code == 200:
            print(f"任务详情: {json.dumps(task_response.json(), indent=2, ensure_ascii=False)}")
        else:
            print(f"任务状态错误: {task_response.text}")
    except Exception as e:
        print(f"获取任务状态失败: {e}")
    
    # 检查Redis Stream
    print("\n🔍 检查Redis Stream...")
    stream_key = f"chat:{thread_id}:{task_id}"
    
    try:
        # 检查事件数量
        result = subprocess.run(
            ["redis-cli", "XLEN", stream_key], 
            capture_output=True, text=True, shell=True
        )
        print(f"Redis Stream 事件数量: {result.stdout.strip()}")
        
        # 读取所有事件
        result = subprocess.run(
            ["redis-cli", "XREAD", "COUNT", "100", "STREAMS", stream_key, "0"], 
            capture_output=True, text=True, shell=True
        )
        print(f"Redis Stream 内容:\n{result.stdout}")
        
    except Exception as e:
        print(f"检查Redis失败: {e}")
    
    # 测试创建新任务
    print("\n🆕 创建新的测试任务...")
    url = "http://localhost:8000/api/chat/async"
    data = {
        "thread_id": f"test_thread_{int(time.time())}", 
        "messages": [
            {"role": "user", "content": "简单测试：1+1=?"}
        ]
    }
    
    try:
        response = requests.post(url, json=data)
        print(f"新任务创建响应: {response.status_code}")
        if response.status_code == 200:
            new_task = response.json()
            print(f"新任务: {new_task}")
            
            # 等待10秒
            print("⏳ 等待10秒观察新任务...")
            time.sleep(10)
            
            # 检查新任务状态
            new_task_id = new_task.get("task_id")
            new_thread_id = new_task.get("thread_id")
            
            task_response = requests.get(f"http://localhost:8000/api/tasks/{new_task_id}")
            print(f"新任务状态: {task_response.json()}")
            
            # 检查新任务的Redis Stream
            new_stream_key = f"chat:{new_thread_id}:{new_task_id}"
            result = subprocess.run(
                ["redis-cli", "XLEN", new_stream_key], 
                capture_output=True, text=True, shell=True
            )
            print(f"新任务 Redis Stream 事件数量: {result.stdout.strip()}")
            
        else:
            print(f"新任务创建失败: {response.text}")
    except Exception as e:
        print(f"创建新任务失败: {e}")

if __name__ == "__main__":
    check_task_status() 