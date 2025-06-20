// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import { env } from "~/env";

import type { MCPServerMetadata } from "../mcp";
import type { Resource } from "../messages";
import { extractReplayIdFromSearchParams } from "../replay/get-replay-id";
import { fetchStream } from "../sse";
import { sleep } from "../utils";

import { resolveServiceURL } from "./resolve-service-url";
import type { ChatEvent } from "./types";

export async function* chatStream(
  userMessage: string,
  params: {
    thread_id: string;
    resources?: Array<Resource>;
    auto_accepted_plan: boolean;
    max_plan_iterations: number;
    max_step_num: number;
    max_search_results?: number;
    interrupt_feedback?: string;
    enable_deep_thinking?: boolean;
    enable_background_investigation: boolean;
    report_style?: "academic" | "popular_science" | "news" | "social_media";
    mcp_settings?: {
      servers: Record<
        string,
        MCPServerMetadata & {
          enabled_tools: string[];
          add_to_agents: string[];
        }
      >;
    };
  },
  options: { abortSignal?: AbortSignal } = {},
) {
  // 检查是否需要使用静态回放或动态回放
  if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY || location.search.includes("mock")) {
    return yield* chatReplayStream(userMessage, params, options);
  }
  
  // 检查是否有replay参数，使用动态回放
  if (location.search.includes("replay=")) {
    const replayId = extractReplayIdFromSearchParams(location.search);
    if (replayId) {
      return yield* chatReplayStreamFromAPI(replayId, userMessage, params, options);
    }
    // 如果没有有效的replayId，回退到静态回放
    return yield* chatReplayStream(userMessage, params, options);
  }
  
  try {
    // 【核心修改】统一异步架构：创建异步任务 + 实时回放
    
    // 1. 创建异步任务
    const asyncResponse = await fetch(resolveServiceURL("chat/async"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: [{ role: "user", content: userMessage }],
        thread_id: params.thread_id,
        auto_accepted_plan: params.auto_accepted_plan,
        max_plan_iterations: params.max_plan_iterations,
        max_step_num: params.max_step_num,
        max_search_results: params.max_search_results,
        interrupt_feedback: params.interrupt_feedback,
        enable_deep_thinking: params.enable_deep_thinking,
        enable_background_investigation: params.enable_background_investigation,
        report_style: params.report_style,
        mcp_settings: params.mcp_settings,
        resources: params.resources,
      }),
      signal: options.abortSignal,
    });
    
    if (!asyncResponse.ok) {
      throw new Error(`创建异步任务失败: ${asyncResponse.statusText}`);
    }
    
    const { task_id } = await asyncResponse.json();
    console.log(`创建异步任务成功: task_id=${task_id}, thread_id=${params.thread_id}`);
    
    // 2. 智能等待机制：通过事件检测任务开始
    console.log("等待任务开始执行...");
    
    // 优先使用事件流检测，如果stream有事件说明任务已开始
    let taskStarted = false;
    let quickCheckAttempts = 0;
    const maxQuickChecks = 8; // 最多快速检查8次（4秒）
    
    while (!taskStarted && quickCheckAttempts < maxQuickChecks) {
      if (options.abortSignal?.aborted) {
        throw new Error("任务被用户取消");
      }
      
              try {
          // 轻量级检查：尝试读取stream是否有事件
          const replayUrl = `${resolveServiceURL("chat/replay")}?${new URLSearchParams({
            thread_id: params.thread_id,
            query_id: task_id,
            continuous: "false",
            offset: "0",
          }).toString()}`;
          
          const quickCheckResponse = await fetch(replayUrl, {
            method: "GET",
            headers: { "Range": "bytes=0-100" }, // 只读取前100字节
            signal: options.abortSignal,
          });
        
        if (quickCheckResponse.ok) {
          const text = await quickCheckResponse.text();
          if (text.includes("event:") && text.includes("research_start")) {
            taskStarted = true;
            console.log("检测到任务开始事件，开始回放");
            break;
          }
        }
      } catch (e) {
        // 忽略快速检查错误，继续等待
      }
      
      // 等待500ms后重试（比之前更频繁）
      await new Promise(resolve => setTimeout(resolve, 500));
      quickCheckAttempts++;
    }
    
    // 如果快速检查失败，回退到任务状态检查
    if (!taskStarted) {
      console.log("快速检查失败，回退到任务状态检查...");
      let statusCheckAttempts = 0;
      const maxStatusChecks = 10; // 最多状态检查10次（10秒）
      
      while (!taskStarted && statusCheckAttempts < maxStatusChecks) {
        if (options.abortSignal?.aborted) {
          throw new Error("任务被用户取消");
        }
        
        try {
          const statusResponse = await fetch(resolveServiceURL(`tasks/${task_id}`), {
            signal: options.abortSignal,
          });
          
          if (statusResponse.ok) {
            const taskInfo = await statusResponse.json();
            console.log(`任务状态: ${taskInfo.status}, 检查次数: ${statusCheckAttempts + 1}`);
            
            if (taskInfo.status === "running" || taskInfo.status === "completed") {
              taskStarted = true;
              console.log("任务状态确认开始执行，开始回放");
              break;
            }
          }
        } catch (e) {
          console.warn("任务状态检查失败:", e);
        }
        
        await new Promise(resolve => setTimeout(resolve, 1000));
        statusCheckAttempts++;
      }
    }
    
    if (!taskStarted) {
      console.warn("等待超时，直接开始回放（任务可能已在执行）");
    }
    
    // 3. 开始实时回放，使用 task_id 作为 query_id
    const stream = fetchStream(resolveServiceURL("chat/replay"), {
      method: "GET",
      body: null,
      searchParams: new URLSearchParams({
        thread_id: params.thread_id,
        query_id: task_id,  // 【关键修复】使用 task_id 而不是 "default"
        continuous: "true",  // 启用连续模式以接收实时事件
        offset: "0",
      }),
      signal: options.abortSignal,
    });
    
    // 4. 处理事件流
    for await (const event of stream) {
      if (event.event === "replay_end") {
        console.log("任务回放结束", event.data);
        break;
      }
      
      if (event.event === "error") {
        console.error("任务执行错误", event.data);
        throw new Error(event.data);
      }
      
      yield {
        type: event.event,
        data: JSON.parse(event.data),
      } as ChatEvent;
    }
    
  } catch (e) {
    console.error("异步任务执行失败:", e);
    throw e;
  }
}

async function* chatReplayStream(
  userMessage: string,
  params: {
    thread_id: string;
    auto_accepted_plan: boolean;
    max_plan_iterations: number;
    max_step_num: number;
    max_search_results?: number;
    interrupt_feedback?: string;
  } = {
    thread_id: "__mock__",
    auto_accepted_plan: false,
    max_plan_iterations: 3,
    max_step_num: 1,
    max_search_results: 3,
    interrupt_feedback: undefined,
  },
  options: { abortSignal?: AbortSignal } = {},
): AsyncIterable<ChatEvent> {
  const urlParams = new URLSearchParams(window.location.search);
  let replayFilePath = "";
  if (urlParams.has("mock")) {
    if (urlParams.get("mock")) {
      replayFilePath = `/mock/${urlParams.get("mock")!}.txt`;
    } else {
      if (params.interrupt_feedback === "accepted") {
        replayFilePath = "/mock/final-answer.txt";
      } else if (params.interrupt_feedback === "edit_plan") {
        replayFilePath = "/mock/re-plan.txt";
      } else {
        replayFilePath = "/mock/first-plan.txt";
      }
    }
    fastForwardReplaying = true;
  } else {
    const replayId = extractReplayIdFromSearchParams(window.location.search);
    if (replayId) {
      replayFilePath = `/replay/${replayId}.txt`;
    } else {
      // Fallback to a default replay
      replayFilePath = `/replay/eiffel-tower-vs-tallest-building.txt`;
    }
  }
  const text = await fetchReplay(replayFilePath, {
    abortSignal: options.abortSignal,
  });
  const normalizedText = text.replace(/\r\n/g, "\n");
  const chunks = normalizedText.split("\n\n");
  for (const chunk of chunks) {
    const [eventRaw, dataRaw] = chunk.split("\n") as [string, string];
    const [, event] = eventRaw.split("event: ", 2) as [string, string];
    const [, data] = dataRaw.split("data: ", 2) as [string, string];

    try {
      const chatEvent = {
        type: event,
        data: JSON.parse(data),
      } as ChatEvent;
      if (chatEvent.type === "message_chunk") {
        if (!chatEvent.data.finish_reason) {
          await sleepInReplay(50);
        }
      } else if (chatEvent.type === "tool_call_result") {
        await sleepInReplay(500);
      }
      yield chatEvent;
      if (chatEvent.type === "tool_call_result") {
        await sleepInReplay(800);
      } else if (chatEvent.type === "message_chunk") {
        if (chatEvent.data.role === "user") {
          await sleepInReplay(500);
        }
      }
    } catch (e) {
      console.error(e);
    }
  }
}

const replayCache = new Map<string, string>();
export async function fetchReplay(
  url: string,
  options: { abortSignal?: AbortSignal } = {},
) {
  if (replayCache.has(url)) {
    return replayCache.get(url)!;
  }
  const res = await fetch(url, {
    signal: options.abortSignal,
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch replay: ${res.statusText}`);
  }
  const text = await res.text();
  replayCache.set(url, text);
  return text;
}

export async function fetchReplayTitle() {
  const res = chatReplayStream(
    "",
    {
      thread_id: "__mock__",
      auto_accepted_plan: false,
      max_plan_iterations: 3,
      max_step_num: 1,
      max_search_results: 3,
    },
    {},
  );
  for await (const event of res) {
    if (event.type === "message_chunk") {
      return event.data.content;
    }
  }
}

export async function sleepInReplay(ms: number) {
  if (fastForwardReplaying) {
    await sleep(0);
  } else {
    await sleep(ms);
  }
}

let fastForwardReplaying = false;
export function fastForwardReplay(value: boolean) {
  fastForwardReplaying = value;
}

/**
 * 从后端API回放对话事件流（动态回放）
 */
async function* chatReplayStreamFromAPI(
  threadId: string,
  userMessage: string,
  params: {
    thread_id: string;
    auto_accepted_plan: boolean;
    max_plan_iterations: number;
    max_step_num: number;
    max_search_results?: number;
    interrupt_feedback?: string;
  },
  options: { abortSignal?: AbortSignal } = {},
): AsyncIterable<ChatEvent> {
  try {
    console.log(`开始从API回放对话: threadId=${threadId}`);
    
    const stream = fetchStream(resolveServiceURL("chat/replay"), {
      method: "GET",
      body: null,
      searchParams: new URLSearchParams({
        thread_id: threadId,
        offset: "0", // 从头开始回放
      }),
      signal: options.abortSignal,
    });

    for await (const event of stream) {
      try {
        const chatEvent = {
          type: event.event,
          data: JSON.parse(event.data),
        } as ChatEvent;

        console.log(`回放事件: ${chatEvent.type}`, chatEvent.data);

        // 处理特殊事件
        if (chatEvent.type === "replay_end") {
          console.log("回放结束", chatEvent.data);
          break;
        }

        if (chatEvent.type === "error") {
          console.error("回放错误", chatEvent.data);
          break;
        }

        // 添加回放延迟以模拟真实对话速度
        if (chatEvent.type === "message_chunk" && !chatEvent.data.finish_reason) {
          await sleepInReplay(50);
        } else if (chatEvent.type === "tool_call_result") {
          await sleepInReplay(300);
        }

        yield chatEvent;

        // 事件后延迟
        if (chatEvent.type === "tool_call_result") {
          await sleepInReplay(500);
        } else if (chatEvent.type === "message_chunk" && chatEvent.data.role === "user") {
          await sleepInReplay(300);
        }

      } catch (e) {
        console.error("解析回放事件时出错:", e);
      }
    }
  } catch (error) {
    console.error("API回放失败:", error);
    // 回退到静态回放
    yield* chatReplayStream(userMessage, params, options);
  }
}

/**
 * 直接调用replay API（用于前端主动回放）
 */
export async function* chatReplayStreamAPI(
  threadId: string,
  offset: string = "0",
  options: { abortSignal?: AbortSignal } = {},
): AsyncIterable<ChatEvent> {
  try {
    console.log(`开始API回放: threadId=${threadId}, offset=${offset}`);
    
    const stream = fetchStream(resolveServiceURL("chat/replay"), {
      method: "GET",
      body: null,
      searchParams: new URLSearchParams({
        thread_id: threadId,
        offset: offset,
      }),
      signal: options.abortSignal,
    });

    for await (const event of stream) {
      try {
        const chatEvent = {
          type: event.event,
          data: JSON.parse(event.data),
        } as ChatEvent;

        if (chatEvent.type === "replay_end") {
          console.log("API回放结束", chatEvent.data);
          break;
        }

        if (chatEvent.type === "error") {
          console.error("API回放错误", chatEvent.data);
          break;
        }

        yield chatEvent;

      } catch (e) {
        console.error("解析API回放事件时出错:", e);
      }
    }
  } catch (error) {
    console.error("API回放失败:", error);
    throw error;
  }
}

/**
 * 简单回放：从Redis Stream读取对话历史和实时事件
 */
export async function* chatReplayFromRedis(
  threadId: string,
  continuous: boolean = true,
  offset: string = "0",
  queryId?: string,
  options: { abortSignal?: AbortSignal } = {},
): AsyncIterable<ChatEvent> {
  try {
    console.log(`开始Redis回放: threadId=${threadId}, continuous=${continuous}, queryId=${queryId}`);
    
    const searchParams = new URLSearchParams({
      thread_id: threadId,
      offset: offset,
      continuous: continuous.toString(),
    });
    
    // 如果提供了queryId，添加到参数中
    if (queryId) {
      searchParams.set('query_id', queryId);
    }
    
    const stream = fetchStream(resolveServiceURL("chat/replay"), {
      method: "GET",
      body: null,
      searchParams: searchParams,
      signal: options.abortSignal,
    });

    for await (const event of stream) {
      try {
        const chatEvent = {
          type: event.event,
          data: JSON.parse(event.data),
        } as ChatEvent;

        if (chatEvent.type === "replay_end") {
          console.log("回放结束", chatEvent.data);
          break;
        }

        if (chatEvent.type === "error") {
          console.error("回放错误", chatEvent.data);
          break;
        }

        yield chatEvent;

      } catch (e) {
        console.error("解析回放事件时出错:", e);
      }
    }
  } catch (error) {
    console.error("Redis回放失败:", error);
    // 如果Redis回放失败，回退到静态文件回放
    console.log("回退到静态文件回放");
    yield* chatReplayStream("", {
      thread_id: threadId,
      auto_accepted_plan: false,
      max_plan_iterations: 3,
      max_step_num: 1,
      max_search_results: 3,
    }, options);
  }
}

/**
 * 检查线程是否有运行中的任务
 */
export async function checkThreadRunningTask(threadId: string): Promise<{
  has_running_task: boolean;
  task_id?: string;
  status?: string;
  progress?: number;
  current_step?: string;
}> {
  try {
    const response = await fetch(resolveServiceURL(`threads/${threadId}/running-task`));
    if (!response.ok) {
      throw new Error(`检查运行任务失败: ${response.statusText}`);
    }
    return await response.json();
  } catch (error) {
    console.error("检查线程运行任务失败:", error);
    return { has_running_task: false };
  }
}

/**
 * 智能回放：自动检查任务状态并选择合适的回放模式
 */
export async function* chatSmartReplayStream(
  threadId: string,
  offset: string = "0",
  queryId?: string,
  options: { abortSignal?: AbortSignal } = {},
): AsyncIterable<ChatEvent> {
  try {
    console.log(`开始智能回放: threadId=${threadId}, queryId=${queryId}`);
    
    // 1. 检查是否有运行中的任务
    const runningTaskInfo = await checkThreadRunningTask(threadId);
    
    // 2. 根据任务状态选择回放模式
    const continuous = runningTaskInfo.has_running_task;
    console.log(`线程${threadId}运行状态: ${continuous ? '有运行中任务，使用连续模式' : '无运行中任务，使用静态模式'}`);
    
    if (runningTaskInfo.has_running_task) {
      console.log(`检测到运行中任务: ${runningTaskInfo.task_id}, 状态: ${runningTaskInfo.status}, 进度: ${runningTaskInfo.progress}`);
    }
    
    // 3. 调用Redis回放API
    yield* chatReplayFromRedis(threadId, continuous, offset, queryId, options);
    
  } catch (error) {
    console.error("智能回放失败:", error);
    // 回退到静态回放
    console.log("回退到静态文件回放");
    yield* chatReplayStream("", {
      thread_id: threadId,
      auto_accepted_plan: false,
      max_plan_iterations: 3,
      max_step_num: 1,
      max_search_results: 3,
    }, options);
  }
}

/**
 * 检查线程的研究状态
 * @param threadId 线程ID
 * @returns 研究状态信息
 */
export async function checkThreadResearchStatus(
  threadId: string,
  options: { abortSignal?: AbortSignal } = {},
): Promise<{
  has_research_events: boolean;
  ongoing_research?: {
    research_id: string;
    status: "running";
    topic: string;
    query_id: string;
    start_time: string;
    stream_key: string;
  };
  completed_research: Array<{
    research_id: string;
    status: "completed";
    topic: string;
    query_id: string;
    end_time: string;
    stream_key: string;
  }>;
  latest_research_id?: string;
  running_task: any;
  error?: string;
}> {
  try {
    console.log(`🔍 检查线程 ${threadId} 的研究状态`);
    
    const url = resolveServiceURL(`threads/${threadId}/research-status`);
    const response = await fetch(url, {
      method: "GET",
      signal: options.abortSignal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const result = await response.json();
    console.log(`📊 线程 ${threadId} 研究状态检查结果:`, result);
    
    return result;
  } catch (error) {
    console.error(`❌ 检查线程研究状态失败 ${threadId}:`, error);
    return {
      has_research_events: false,
      ongoing_research: undefined,
      completed_research: [],
      latest_research_id: undefined,
      running_task: null,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}
