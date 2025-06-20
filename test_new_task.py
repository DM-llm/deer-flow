#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import json
import random

def create_and_test_new_task():
    """创建新任务并测试修复后的事件类型处理"""
    
    print("🚀 创建新任务测试修复后的事件类型...")
    
    # 生成唯一的线程ID
    thread_id = f"test_fixed_{int(time.time())}"
    
    # 创建新的异步任务
    create_url = "http://localhost:8000/api/chat/async"
    create_data = {
        "thread_id": thread_id,
        "messages": [
            {"role": "user", "content": "测试新的事件类型处理：2+2=?"}
        ],
        "max_plan_iterations": 1,
        "max_step_num": 3,
        "auto_accepted_plan": True,
        "enable_background_investigation": True,
        "report_style": "academic"
    }
    
    print(f"📡 创建任务...")
    try:
        create_response = requests.post(create_url, json=create_data, timeout=10)
        if create_response.status_code != 200:
            print(f"❌ 创建任务失败: {create_response.text}")
            return
        
        task_info = create_response.json()
        task_id = task_info["task_id"]
        print(f"✅ 任务创建成功: {task_id}")
        print(f"🧵 线程ID: {thread_id}")
        
        # 等待任务开始执行
        print("⏳ 等待5秒让任务开始执行...")
        time.sleep(5)
        
        # 开始回放测试
        replay_url = f"http://localhost:8000/api/chat/replay"
        params = {
            "thread_id": thread_id,
            "query_id": task_id,
            "offset": "0",
            "continuous": False
        }
        
        print(f"📺 开始回放测试...")
        response = requests.get(replay_url, params=params, stream=True, timeout=20)
        
        if response.status_code != 200:
            print(f"❌ 回放失败: {response.text}")
            return
        
        # 分析事件流
        events_count = 0
        event_types = {}
        start_time = time.time()
        
        for line in response.iter_lines(decode_unicode=True):
            if line and line.strip():
                # 统计事件类型
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                    events_count += 1
                    event_types[event_type] = event_types.get(event_type, 0) + 1
                    
                    # 只打印前几个事件，避免输出过多
                    if events_count <= 10:
                        print(f"  📨 事件 {events_count}: {event_type}")
                
                # 检查是否结束
                if "replay_end" in line:
                    print("🏁 回放结束")
                    break
                
                # 超时保护
                if time.time() - start_time > 15:
                    print("⏰ 超时停止")
                    break
                
                # 事件数量保护
                if events_count > 200:
                    print("🔢 事件数量过多，停止")
                    break
        
        elapsed = time.time() - start_time
        
        # 分析结果
        print(f"\n📊 测试结果分析:")
        print(f"   ⏱️  耗时: {elapsed:.2f}秒")
        print(f"   📈 事件总数: {events_count}")
        print(f"   📋 事件类型分布:")
        
        for event_type, count in sorted(event_types.items()):
            percentage = (count / events_count * 100) if events_count > 0 else 0
            print(f"      - {event_type}: {count} ({percentage:.1f}%)")
        
        # 判断修复效果
        correct_event_types = ["message_chunk", "tool_calls", "tool_call_chunks", "tool_call_result", "research_start", "research_end"]
        found_correct_types = [t for t in correct_event_types if t in event_types]
        system_event_ratio = event_types.get("system_event", 0) / events_count if events_count > 0 else 0
        
        print(f"\n🎯 修复效果评估:")
        if found_correct_types and system_event_ratio < 0.8:
            print(f"   🎉 修复成功！")
            print(f"   ✅ 发现正确事件类型: {found_correct_types}")
            print(f"   ✅ system_event 比例降低到: {system_event_ratio:.1%}")
        else:
            print(f"   ⚠️ 修复效果有限")
            print(f"   📝 正确事件类型: {found_correct_types}")
            print(f"   📊 system_event 比例: {system_event_ratio:.1%}")
        
    except requests.exceptions.Timeout:
        print("⏰ 请求超时")
    except requests.exceptions.ConnectionError:
        print("🔌 连接错误")
    except KeyboardInterrupt:
        print("⌨️ 用户中断")
    except Exception as e:
        print(f"❌ 测试出错: {e}")

if __name__ == "__main__":
    create_and_test_new_task() 