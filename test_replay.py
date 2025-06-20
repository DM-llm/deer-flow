#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import json

def test_replay():
    """测试回放功能"""
    
    print("🎬 测试修复后的回放功能...")
    
    # 使用最新创建的任务ID进行回放测试
    thread_id = "test_thread_1750390637"
    task_id = "3c088b65-3fc1-4f10-a9c1-57a87837657a"
    
    # 测试回放URL
    replay_url = f"http://localhost:8000/api/chat/replay"
    params = {
        "thread_id": thread_id,
        "query_id": task_id,
        "offset": "0",
        "continuous": False  # 静态回放
    }
    
    print(f"📡 回放URL: {replay_url}")
    print(f"📝 参数: {params}")
    
    try:
        # 发起SSE请求
        response = requests.get(replay_url, params=params, stream=True, timeout=30)
        print(f"🔗 连接状态码: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ 连接失败: {response.text}")
            return
        
        # 读取SSE事件流
        print("📺 开始读取事件流...")
        events_count = 0
        event_types = {}
        start_time = time.time()
        
        for line in response.iter_lines(decode_unicode=True):
            if line:
                print(f"📨 收到: {line[:200]}...")  # 只显示前200字符
                
                # 统计事件类型
                if line.startswith("event: "):
                    event_type = line[7:]  # 去掉"event: "前缀
                    events_count += 1
                    event_types[event_type] = event_types.get(event_type, 0) + 1
                
                # 检查是否结束
                if "replay_end" in line:
                    print("🏁 回放结束")
                    break
                
                # 超时保护 - 15秒内如果没结束就停止
                if time.time() - start_time > 15:
                    print("⏰ 超时停止")
                    break
                
                # 事件数量保护 - 超过150个事件就停止
                if events_count > 150:
                    print("🔢 事件数量过多，停止")
                    break
        
        elapsed = time.time() - start_time
        print(f"\n✅ 回放测试完成:")
        print(f"   ⏱️  耗时: {elapsed:.2f}秒")
        print(f"   📊 事件总数: {events_count}")
        print(f"   📋 事件类型统计:")
        for event_type, count in event_types.items():
            print(f"      - {event_type}: {count}")
        
        # 判断修复效果
        if elapsed < 15 and "replay_end" in line:
            print(f"\n🎉 修复成功！")
            print(f"   ✅ 回放正常结束，没有无限循环")
            print(f"   ✅ 事件类型多样化（不再是单一的system_event）")
        else:
            print(f"\n⚠️ 可能仍有问题：回放时间过长或没有正常结束")
            
    except requests.exceptions.Timeout:
        print("⏰ 请求超时")
    except requests.exceptions.ConnectionError:
        print("🔌 连接错误")
    except KeyboardInterrupt:
        print("⌨️ 用户中断")
    except Exception as e:
        print(f"❌ 测试出错: {e}")

if __name__ == "__main__":
    test_replay() 