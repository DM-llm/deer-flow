#!/usr/bin/env python3

import requests
import json
import time

def test_simple_task():
    # 创建简单任务
    url = "http://localhost:8000/api/chat/async"
    data = {
        "thread_id": f"simple_test_{int(time.time())}", 
        "messages": [
            {"role": "user", "content": "Hello"}
        ]
    }
    
    print("🔄 创建简单测试任务...")
    response = requests.post(url, json=data)
    print(f"响应: {response.status_code} - {response.text}")
    
    if response.status_code == 200:
        result = response.json()
        task_id = result.get("task_id")
        thread_id = result.get("thread_id")
        
        print(f"✅ 任务创建: {task_id}")
        print(f"📝 等待20秒观察事件...")
        
        # 等待并检查状态
        for i in range(4):
            time.sleep(5)
            print(f"⏱️  {(i+1)*5}秒 - 检查状态...")
            
            # 任务状态
            task_response = requests.get(f"http://localhost:8000/api/tasks/{task_id}")
            if task_response.status_code == 200:
                status = task_response.json()
                print(f"   状态: {status.get('status')} - {status.get('current_step')} - 进度: {status.get('progress', 0):.1%}")
            
            # Redis事件数量 (简单检查)
            try:
                import subprocess
                result = subprocess.run(
                    ["redis-cli", "XLEN", f"chat:{thread_id}:{task_id}"], 
                    capture_output=True, text=True, shell=True
                )
                event_count = result.stdout.strip()
                print(f"   Redis事件数: {event_count}")
            except:
                print("   Redis检查失败")
        
        print(f"\n🔍 最终回放测试...")
        replay_url = f"http://localhost:8000/api/chat/replay?thread_id={thread_id}&query_id={task_id}&continuous=false&offset=0"
        
        replay_response = requests.get(replay_url, stream=True)
        print(f"回放状态: {replay_response.status_code}")
        
        event_count = 0
        for line in replay_response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith('event:') or decoded_line.startswith('data:'):
                    event_count += 1
                    print(f"[{event_count}] {decoded_line[:100]}...")
                if "replay_end" in decoded_line:
                    break
        
        print(f"✅ 总计回放事件: {event_count}")

if __name__ == "__main__":
    test_simple_task() 