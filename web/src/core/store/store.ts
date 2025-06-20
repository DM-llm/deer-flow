// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import { nanoid } from "nanoid";
import { toast } from "sonner";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

import { chatStream, generatePodcast } from "../api";
import type { Message, Resource } from "../messages";
import { mergeMessage } from "../messages";
import { parseJSON } from "../utils";

import { getChatStreamSettings } from "./settings-store";

// 本地存储键
const CURRENT_THREAD_ID_KEY = "deer-flow-current-thread-id";

// 检查是否在客户端环境
const isClient = typeof window !== 'undefined';

// 初始线程ID - 从localStorage恢复或生成新的
let CURRENT_THREAD_ID = (() => {
  if (!isClient) {
    // 服务端渲染时使用临时ID
    return nanoid();
  }
  
  try {
    const stored = localStorage.getItem(CURRENT_THREAD_ID_KEY);
    if (stored) {
      console.log(`恢复thread_id: ${stored}`);
      return stored;
    }
  } catch (error) {
    console.error("恢复thread_id失败:", error);
  }
  const newId = nanoid();
  try {
    localStorage.setItem(CURRENT_THREAD_ID_KEY, newId);
    console.log(`生成新thread_id: ${newId}`);
  } catch (error) {
    console.error("保存thread_id失败:", error);
  }
  return newId;
})();

// 历史记录接口
interface ChatHistory {
  threadId: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  messages: Message[];
}

// 本地存储键
const CHAT_HISTORY_KEY = "deer-flow-chat-history";

// 防抖保存的超时句柄
let updateMessageSaveTimeout: NodeJS.Timeout | null = null;
let updateMessagesSaveTimeout: NodeJS.Timeout | null = null;

// 历史记录管理函数
function saveChatHistory(threadId: string, messages: Message[]) {
  if (messages.length === 0 || !isClient) return;
  
  try {
    const histories = getChatHistories();
    const userMessage = messages.find(m => m.role === "user");
    const title = userMessage?.content?.slice(0, 50) || "未命名对话";
    
    const existingIndex = histories.findIndex(h => h.threadId === threadId);
    const chatHistory: ChatHistory = {
      threadId,
      title,
      lastMessage: messages[messages.length - 1]?.content?.slice(0, 100) || "",
      timestamp: new Date().toISOString(),
      messages: messages.slice() // 复制数组
    };
    
    if (existingIndex >= 0) {
      histories[existingIndex] = chatHistory;
    } else {
      histories.unshift(chatHistory);
    }
    
    // 只保留最近50个对话
    const limitedHistories = histories.slice(0, 50);
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(limitedHistories));
  } catch (error) {
    console.error("保存对话历史失败:", error);
  }
}

function getChatHistories(): ChatHistory[] {
  if (!isClient) return [];
  
  try {
    const stored = localStorage.getItem(CHAT_HISTORY_KEY);
    return stored ? JSON.parse(stored) : [];
  } catch (error) {
    console.error("获取对话历史失败:", error);
    return [];
  }
}

function getChatHistoryByThreadId(threadId: string): ChatHistory | null {
  if (!isClient) return null;
  
  try {
    const histories = getChatHistories();
    return histories.find(h => h.threadId === threadId) || null;
  } catch (error) {
    console.error("获取特定对话历史失败:", error);
    return null;
  }
}

export const useStore = create<{
  responding: boolean;
  threadId: string | undefined;
  messageIds: string[];
  messages: Map<string, Message>;
  researchIds: string[];
  researchPlanIds: Map<string, string>;
  researchReportIds: Map<string, string>;
  researchActivityIds: Map<string, string[]>;
  ongoingResearchId: string | null;
  openResearchId: string | null;

  appendMessage: (message: Message) => void;
  updateMessage: (message: Message) => void;
  updateMessages: (messages: Message[]) => void;
  openResearch: (researchId: string | null) => void;
  closeResearch: () => void;
  setOngoingResearch: (researchId: string | null) => void;
  resetThread: () => void;
  saveCurrentChat: () => void;
}>((set, get) => ({
  responding: false,
  threadId: CURRENT_THREAD_ID,
  messageIds: [],
  messages: new Map<string, Message>(),
  researchIds: [],
  researchPlanIds: new Map<string, string>(),
  researchReportIds: new Map<string, string>(),
  researchActivityIds: new Map<string, string[]>(),
  ongoingResearchId: null,
  openResearchId: null,

  appendMessage(message: Message) {
    set((state) => ({
      messageIds: [...state.messageIds, message.id],
      messages: new Map(state.messages).set(message.id, message),
    }));
    
    // 自动保存到历史记录
    setTimeout(() => {
      const currentState = get();
      const currentMessages = Array.from(currentState.messages.values())
        .filter(m => m.threadId === currentState.threadId);
      if (currentMessages.length > 0) {
        saveChatHistory(currentState.threadId!, currentMessages);
      }
    }, 1000);
  },
  updateMessage(message: Message) {
    set((state) => ({
      messages: new Map(state.messages).set(message.id, message),
    }));
    
    // 添加自动保存逻辑（防抖）
    if (updateMessageSaveTimeout) clearTimeout(updateMessageSaveTimeout);
    updateMessageSaveTimeout = setTimeout(() => {
      const currentState = get();
      const currentMessages = Array.from(currentState.messages.values())
        .filter(m => m.threadId === currentState.threadId);
      if (currentMessages.length > 0) {
        saveChatHistory(currentState.threadId!, currentMessages);
        console.log(`💾 自动保存更新消息: thread_id=${currentState.threadId}, 消息数=${currentMessages.length}`);
      }
    }, 2000); // 2秒后保存，避免频繁保存
  },
  updateMessages(messages: Message[]) {
    set((state) => {
      const newMessages = new Map(state.messages);
      messages.forEach((m) => newMessages.set(m.id, m));
      return { messages: newMessages };
    });
    
    // 批量更新后也要保存（防抖）
    if (updateMessagesSaveTimeout) clearTimeout(updateMessagesSaveTimeout);
    updateMessagesSaveTimeout = setTimeout(() => {
      const currentState = get();
      const currentMessages = Array.from(currentState.messages.values())
        .filter(m => m.threadId === currentState.threadId);
      if (currentMessages.length > 0) {
        saveChatHistory(currentState.threadId!, currentMessages);
        console.log(`💾 自动保存批量更新: thread_id=${currentState.threadId}, 消息数=${currentMessages.length}`);
      }
    }, 2000);
  },
  openResearch(researchId: string | null) {
    set({ openResearchId: researchId });
  },
  closeResearch() {
    set({ openResearchId: null });
  },
  setOngoingResearch(researchId: string | null) {
    set({ ongoingResearchId: researchId });
  },
  resetThread() {
    // 保存当前对话到历史记录
    const currentState = get();
    const currentMessages = Array.from(currentState.messages.values())
      .filter(m => m.threadId === currentState.threadId);
    if (currentMessages.length > 0) {
      saveChatHistory(currentState.threadId!, currentMessages);
    }
    
    // 生成新的线程ID并持久化
    CURRENT_THREAD_ID = nanoid();
    if (isClient) {
    try {
      localStorage.setItem(CURRENT_THREAD_ID_KEY, CURRENT_THREAD_ID);
      console.log(`新对话thread_id: ${CURRENT_THREAD_ID}`);
    } catch (error) {
      console.error("保存新thread_id失败:", error);
      }
    }
    
    set({
      responding: false,
      threadId: CURRENT_THREAD_ID,
      messageIds: [],
      messages: new Map<string, Message>(),
      researchIds: [],
      researchPlanIds: new Map<string, string>(),
      researchReportIds: new Map<string, string>(),
      researchActivityIds: new Map<string, string[]>(),
      ongoingResearchId: null,
      openResearchId: null,
    });
  },
  saveCurrentChat() {
    const currentState = get();
    const currentMessages = Array.from(currentState.messages.values())
      .filter(m => m.threadId === currentState.threadId);
    if (currentMessages.length > 0 && currentState.threadId) {
      saveChatHistory(currentState.threadId, currentMessages);
      console.log(`💾 手动保存对话: thread_id=${currentState.threadId}, 消息数=${currentMessages.length}`);
      return true;
    }
    console.log(`⚠️ 没有可保存的消息`);
    return false;
  },
}));

export async function sendMessage(
  content?: string,
  {
    interruptFeedback,
    resources,
  }: {
    interruptFeedback?: string;
    resources?: Array<Resource>;
  } = {},
  options: { abortSignal?: AbortSignal } = {},
) {
  if (content != null) {
    appendMessage({
      id: nanoid(),
      threadId: CURRENT_THREAD_ID,
      role: "user",
      content: content,
      contentChunks: [content],
      resources,
    });
  }

  const settings = getChatStreamSettings();
  const stream = chatStream(
    content ?? "",
    {
      thread_id: CURRENT_THREAD_ID,
      interrupt_feedback: interruptFeedback,
      resources,
      auto_accepted_plan: settings.autoAcceptedPlan,
      enable_deep_thinking: settings.enableDeepThinking ?? false,
      enable_background_investigation:
        settings.enableBackgroundInvestigation ?? true,
      max_plan_iterations: settings.maxPlanIterations,
      max_step_num: settings.maxStepNum,
      max_search_results: settings.maxSearchResults,
      report_style: settings.reportStyle,
      mcp_settings: settings.mcpSettings,
    },
    options,
  );

  setResponding(true);
  let messageId: string | undefined;
  try {
    for await (const event of stream) {
      const { type, data } = event;
      messageId = data.id;
      let message: Message | undefined;
      if (type === "tool_call_result") {
        message = findMessageByToolCallId(data.tool_call_id);
      } else if (!existsMessage(messageId)) {
        message = {
          id: messageId,
          threadId: data.thread_id,
          agent: data.agent,
          role: data.role,
          content: "",
          contentChunks: [],
          reasoningContent: "",
          reasoningContentChunks: [],
          isStreaming: true,
          interruptFeedback,
        };
        appendMessage(message);
      }
      message ??= getMessage(messageId);
      if (message) {
        message = mergeMessage(message, event);
        updateMessage(message);
      }
    }
  } catch {
    toast("An error occurred while generating the response. Please try again.");
    // Update message status.
    // TODO: const isAborted = (error as Error).name === "AbortError";
    if (messageId != null) {
      const message = getMessage(messageId);
      if (message?.isStreaming) {
        message.isStreaming = false;
        useStore.getState().updateMessage(message);
      }
    }
    useStore.getState().setOngoingResearch(null);
  } finally {
    setResponding(false);
  }
}

// 切换到历史对话
export async function switchToHistoryChat(threadId: string): Promise<boolean> {
  try {
    // 1. 保存当前对话（如果有消息）
    const currentMessages = Array.from(useStore.getState().messages.values())
      .filter(msg => msg.threadId === CURRENT_THREAD_ID);
    
    if (currentMessages.length > 0) {
      console.log(`保存当前对话，thread_id: ${CURRENT_THREAD_ID}, 消息数: ${currentMessages.length}`);
      saveChatHistory(CURRENT_THREAD_ID, currentMessages);
    }

    // 2. 加载历史对话的消息
    const historyData = getChatHistoryByThreadId(threadId);
    if (!historyData) {
      console.log(`未找到历史对话数据: ${threadId}`);
      return false;
    }

    console.log(`加载历史对话: ${threadId}, 消息数: ${historyData.messages.length}`);

    // 3. 切换线程并清空当前状态
    CURRENT_THREAD_ID = threadId;
    
    // 【关键修复】更新 localStorage 中的 thread_id
    if (isClient) {
    try {
      localStorage.setItem(CURRENT_THREAD_ID_KEY, threadId);
      console.log(`✅ 已更新 localStorage 中的 thread_id: ${threadId}`);
    } catch (error) {
      console.error("❌ 更新 localStorage thread_id 失败:", error);
      }
    }
    
    // 清空当前消息和研究状态
    useStore.setState({
      threadId: threadId,
      messageIds: [],
      messages: new Map(),
      responding: false,
      // 重置研究相关状态
      ongoingResearchId: null,
      openResearchId: null,
      researchIds: [],
      researchPlanIds: new Map(),
      researchActivityIds: new Map(),
      researchReportIds: new Map(),
    });

    // 4. 恢复历史消息
    const messageMap = new Map<string, Message>();
    const messageIds: string[] = [];
    
    for (const message of historyData.messages) {
      messageMap.set(message.id, message);
      messageIds.push(message.id);
    }
    
    useStore.setState({
      messageIds: messageIds,
      messages: messageMap,
    });

    console.log(`成功切换到历史对话: ${threadId}`);
    
    // 5. 【新增】检查 Redis 中是否有相关的研究事件并恢复研究状态
    setTimeout(async () => {
      try {
        console.log(`🔍 开始检查线程 ${threadId} 是否有 Redis 研究事件...`);
        
        // 使用新的研究状态检查API
        const { checkThreadResearchStatus } = await import("~/core/api/chat");
        const researchStatus = await checkThreadResearchStatus(threadId);
        
        console.log(`📊 线程 ${threadId} 研究状态检查结果:`, researchStatus);
        
        // 如果有错误，记录但不影响基本功能
        if (researchStatus.error) {
          console.warn(`⚠️ 研究状态检查有错误，但继续处理: ${researchStatus.error}`);
        }
        
        // 检查是否有研究事件
        if (!researchStatus.has_research_events) {
          console.log(`ℹ️ 线程 ${threadId} 没有研究事件需要处理`);
          return;
        }
        
        // 【关键修复】检查是否有真正的调查内容（不只是 research_start 标记）
        const hasRealResearchContent = await checkForRealResearchContent(threadId);
        if (!hasRealResearchContent) {
          console.log(`ℹ️ 线程 ${threadId} 只有 research_start 标记，没有真正的调查内容，跳过显示调查面板`);
          return;
        }
        
        console.log(`🎯 线程 ${threadId} 发现真实研究内容，开始处理...`);
        
        // 处理正在进行的研究
        if (researchStatus.ongoing_research) {
          const { research_id, topic, query_id, status } = researchStatus.ongoing_research;
          console.log(`⚡ 发现正在进行的研究: ${research_id}, 主题: ${topic}, 状态: ${status}`);
          
          // 检查是否已存在该研究消息
          const existingMessage = useStore.getState().messages.get(research_id);
          if (!existingMessage) {
            const researchMessage: Message = {
              id: research_id,
              threadId: threadId,
              role: "assistant",
              agent: "researcher",
              content: `🔍 正在进行研究调查: ${topic}`,
              contentChunks: [`🔍 正在进行研究调查: ${topic}`],
              reasoningContent: "",
              reasoningContentChunks: [],
              isStreaming: true,
            };
            
            // 添加到消息列表
            useStore.setState(state => ({
              messageIds: [...state.messageIds, research_id],
              messages: new Map(state.messages).set(research_id, researchMessage),
            }));
            
            console.log(`📝 创建正在进行的研究消息: ${research_id}`);
          }
          
          // 设置研究状态
          try {
            appendResearch(research_id);
            openResearch(research_id);
            useStore.getState().setOngoingResearch(research_id);
            console.log(`✅ 正在进行的研究状态设置完成: ${research_id}`);
            
            // 启动实时回放以获取最新事件
            console.log(`🎬 启动实时回放获取最新研究进展...`);
            const { chatSmartReplayStream } = await import("~/core/api/chat");
            
            // 异步启动回放，不阻塞主流程
            (async () => {
              try {
                for await (const event of chatSmartReplayStream(threadId)) {
                  // 检查当前线程是否还是目标线程
                  if (useStore.getState().threadId !== threadId) {
                    console.log(`👤 用户已切换到其他线程，停止实时回放`);
                    break;
                  }
                  
                  console.log(`📡 实时回放事件: ${event.type}`, event.data);
                  
                  // 处理实时事件
                  if (event.type === "message_chunk" || event.type === "tool_calls" || event.type === "tool_call_chunks" || event.type === "tool_call_result") {
                    const messageId = event.data.id;
                    let message = useStore.getState().messages.get(messageId);
                    
                    if (!message && (event.type === "message_chunk" || event.type === "tool_calls" || event.type === "tool_call_chunks")) {
                      // 创建新消息
                      message = {
                        id: messageId,
                        threadId: event.data.thread_id || threadId,
                        agent: event.data.agent,
                        role: event.data.role,
                        content: "",
                        contentChunks: [],
                        reasoningContent: "",
                        reasoningContentChunks: [],
                        isStreaming: true,
                      };
                      
                      useStore.setState(state => ({
                        messageIds: [...state.messageIds, messageId],
                        messages: new Map(state.messages).set(messageId, message!),
                      }));
                      
                      console.log(`📝 创建新的实时消息: ${messageId}`);
                    }
                    
                    // 更新现有消息
                    if (message) {
                      try {
                        const { mergeMessage } = await import("~/core/messages/merge-message");
                        const updatedMessage = mergeMessage(message, event);
                        useStore.getState().updateMessage(updatedMessage);
                      } catch (error) {
                        console.error("❌ 合并实时消息失败:", error);
                        console.error("事件数据:", event);
                        console.error("消息数据:", message);
                        // 即使合并失败，也要尝试基本的内容更新
                        if (event.type === "message_chunk" && event.data.content) {
                          const basicUpdate = {
                            ...message,
                            content: message.content + event.data.content,
                            contentChunks: [...message.contentChunks, event.data.content],
                          };
                          useStore.getState().updateMessage(basicUpdate);
                        }
                      }
                    }
                  }
                  
                  // 处理research_end事件
                  else if (event.type === "research_end") {
                    console.log(`🏁 实时检测到研究结束: ${event.data.research_id}`);
                    useStore.getState().setOngoingResearch(null);
                  }
                }
              } catch (error) {
                console.error("❌ 实时回放失败:", error);
              }
            })();
            
          } catch (error) {
            console.error("❌ 设置正在进行的研究状态失败:", error);
          }
        }
        
        // 处理已完成的研究 - 恢复研究状态但不设为正在进行
        else if (researchStatus.completed_research && researchStatus.completed_research.length > 0) {
          console.log(`📋 发现 ${researchStatus.completed_research.length} 个已完成的研究，恢复研究状态...`);
          
          for (const completedResearch of researchStatus.completed_research) {
            const { research_id, topic } = completedResearch;
            console.log(`📊 处理已完成的研究: ${research_id}, 主题: ${topic}`);
            
            // 【修复2】通过replay加载完整的研究消息
            try {
              console.log(`🎬 开始回放已完成研究的消息: ${research_id}`);
              const { chatSmartReplayStream } = await import("~/core/api/chat");
              
              // 异步加载研究消息，不阻塞主流程
              (async () => {
                try {
                  let researchMessagesLoaded = false;
                  
                  for await (const event of chatSmartReplayStream(threadId, "0", completedResearch.query_id)) {
                    // 检查当前线程是否还是目标线程
                    if (useStore.getState().threadId !== threadId) {
                      console.log(`👤 用户已切换到其他线程，停止已完成研究回放`);
                      break;
                    }
                    
                    // 处理研究相关的消息
                    if (event.type === "message_chunk" || event.type === "tool_calls" || event.type === "tool_call_chunks" || event.type === "tool_call_result") {
                      const messageId = event.data.id;
                      const messageAgent = event.data.agent;
                      
                      // 只处理研究相关的消息
                      if (messageAgent === "researcher" || messageAgent === "coder" || messageAgent === "reporter" || messageAgent === "planner") {
                        let message = useStore.getState().messages.get(messageId);
                        
                        if (!message && (event.type === "message_chunk" || event.type === "tool_calls" || event.type === "tool_call_chunks")) {
                          // 创建新消息
                          message = {
                            id: messageId,
                            threadId: event.data.thread_id || threadId,
                            agent: event.data.agent,
                            role: event.data.role,
                            content: "",
                            contentChunks: [],
                            reasoningContent: "",
                            reasoningContentChunks: [],
                            isStreaming: false, // 已完成的研究消息不应该是streaming状态
                          };
                          
                          useStore.setState(state => ({
                            messageIds: [...state.messageIds, messageId],
                            messages: new Map(state.messages).set(messageId, message!),
                          }));
                          
                          console.log(`📝 创建已完成研究消息: ${messageId}, agent: ${messageAgent}`);
                          researchMessagesLoaded = true;
                        }
                        
                        // 更新现有消息
                        if (message) {
                          try {
                            const { mergeMessage } = await import("~/core/messages/merge-message");
                            const updatedMessage = mergeMessage(message, event);
                            updatedMessage.isStreaming = false; // 确保已完成状态
                            useStore.getState().updateMessage(updatedMessage);
                          } catch (error) {
                            console.error("❌ 合并已完成研究消息失败:", error);
                            console.error("事件数据:", event);
                            console.error("消息数据:", message);
                            // 即使合并失败，也要尝试基本的内容更新
                            if (event.type === "message_chunk" && event.data.content) {
                              const basicUpdate = {
                                ...message,
                                content: message.content + event.data.content,
                                contentChunks: [...message.contentChunks, event.data.content],
                                isStreaming: false,
                              };
                              useStore.getState().updateMessage(basicUpdate);
                            }
                          }
                        }
                      }
                    }
                    
                    // 处理research_end事件
                    else if (event.type === "research_end") {
                      console.log(`🏁 检测到已完成研究结束: ${event.data.research_id}`);
                      break;
                    }
                  }
                  
                  // 如果成功加载了消息，恢复研究状态
                  if (researchMessagesLoaded) {
                    // 检查是否有对应的研究消息（通常是第一个researcher消息）
                    const state = useStore.getState();
                    const researchMessage = Array.from(state.messages.values()).find(msg => 
                      msg.agent === "researcher" && msg.content.includes(topic)
                    );
                    
                    if (researchMessage) {
                      console.log(`✅ 恢复已完成研究状态: ${research_id} -> ${researchMessage.id}`);
                      
                      // 收集所有相关的研究消息ID
                      const allMessages = Array.from(state.messages.values())
                        .filter(msg => msg.threadId === threadId)
                        .sort((a, b) => {
                          // 根据消息ID中的时间戳排序，如果没有则按创建顺序
                          const aIndex = state.messageIds.indexOf(a.id);
                          const bIndex = state.messageIds.indexOf(b.id);
                          return aIndex - bIndex;
                        });
                      
                      // 找到研究相关的所有消息
                      const researchRelatedMessages = allMessages.filter(msg => 
                        msg.agent === "researcher" || 
                        msg.agent === "planner" || 
                        msg.agent === "reporter" ||
                        msg.agent === "coder" ||
                        (msg.toolCalls && msg.toolCalls.length > 0) // 包含工具调用的消息
                      );
                      
                      // 构建活动消息ID列表
                      const activityIds = researchRelatedMessages.map(msg => msg.id);
                      
                      // 找到计划消息和报告消息
                      const planMessage = researchRelatedMessages.find(msg => msg.agent === "planner");
                      const reportMessage = researchRelatedMessages.find(msg => msg.agent === "reporter");
                      
                      console.log(`📊 恢复研究状态详情:`, {
                        researchId: researchMessage.id,
                        activityCount: activityIds.length,
                        planMessageId: planMessage?.id,
                        reportMessageId: reportMessage?.id,
                        topic
                      });
                      
                      // 手动添加到研究状态，但不设为正在进行
                      const currentState = useStore.getState();
                      if (!currentState.researchIds.includes(researchMessage.id)) {
                        const newResearchActivityIds = new Map(currentState.researchActivityIds);
                        const newResearchPlanIds = new Map(currentState.researchPlanIds);
                        const newResearchReportIds = new Map(currentState.researchReportIds);
                        
                        // 设置活动消息
                        newResearchActivityIds.set(researchMessage.id, activityIds);
                        
                        // 设置计划消息
                        if (planMessage) {
                          newResearchPlanIds.set(researchMessage.id, planMessage.id);
                        }
                        
                        // 设置报告消息
                        if (reportMessage) {
                          newResearchReportIds.set(researchMessage.id, reportMessage.id);
                        }
                        
                        useStore.setState(state => ({
                          researchIds: [...state.researchIds, researchMessage.id],
                          researchActivityIds: newResearchActivityIds,
                          researchPlanIds: newResearchPlanIds,
                          researchReportIds: newResearchReportIds,
                          // 自动打开这个研究以供查看
                          openResearchId: researchMessage.id,
                        }));
                        
                        console.log(`✅ 成功恢复研究状态: ${researchMessage.id}, 包含 ${activityIds.length} 个活动`);
                      }
                    }
                  }
                  
                } catch (error) {
                  console.error("❌ 回放已完成研究失败:", error);
                }
              })();
              
            } catch (error) {
              console.error("❌ 恢复已完成研究状态失败:", error);
            }
          }
        }
        
        // 如果没有正在进行的研究但有最新的研究ID，可以考虑显示最新的研究
        else if (researchStatus.latest_research_id) {
          console.log(`📌 线程 ${threadId} 最新研究ID: ${researchStatus.latest_research_id}，但无正在进行的研究`);
        }
        
        console.log(`✅ 线程 ${threadId} 研究状态处理完成`);
        
      } catch (error) {
        console.error("❌ 检查 Redis 研究事件失败:", error);
        // 检查失败不影响基本的历史对话显示
        // 确保不会卡在研究状态
        useStore.getState().setOngoingResearch(null);
      }
    }, 100);
    
    return true;
  } catch (error) {
    console.error("切换历史对话失败:", error);
    return false;
  }
}

// 获取所有历史记录
export function getAllChatHistories() {
  return getChatHistories().map(h => ({
    id: h.threadId,
    title: h.title,
    lastMessage: h.lastMessage,
    timestamp: new Date(h.timestamp).toLocaleString(),
  }));
}

function setResponding(value: boolean) {
  useStore.setState({ responding: value });
}

function existsMessage(id: string) {
  return useStore.getState().messageIds.includes(id);
}

function getMessage(id: string) {
  return useStore.getState().messages.get(id);
}

function findMessageByToolCallId(toolCallId: string) {
  return Array.from(useStore.getState().messages.values())
    .reverse()
    .find((message) => {
      if (message.toolCalls) {
        return message.toolCalls.some((toolCall) => toolCall.id === toolCallId);
      }
      return false;
    });
}

function appendMessage(message: Message) {
  // 确保消息属于当前线程
  if (message.threadId === CURRENT_THREAD_ID) {
    useStore.getState().appendMessage(message);
  }
  
  if (
    message.agent === "coder" ||
    message.agent === "reporter" ||
    message.agent === "researcher"
  ) {
    const currentOngoingId = getOngoingResearchId();
    
    // 【修复1】只有当没有正在进行的研究，且是researcher的第一条消息时才创建新研究
    if (!currentOngoingId && message.agent === "researcher") {
      // 检查是否已经有研究状态了
      const state = useStore.getState();
      const existingResearchForMessage = state.researchIds.find(researchId => {
        const activityIds = state.researchActivityIds.get(researchId);
        return activityIds && activityIds.includes(message.id);
      });
      
      if (!existingResearchForMessage) {
        console.log(`🔬 创建新研究: ${message.id}, agent: ${message.agent}`);
        const id = message.id;
        appendResearch(id);
        openResearch(id);
      } else {
        console.log(`🔍 消息 ${message.id} 已属于研究 ${existingResearchForMessage}`);
      }
    } else if (currentOngoingId) {
      console.log(`📝 将消息 ${message.id} 添加到现有研究 ${currentOngoingId}`);
    }
    
    // 总是尝试添加到研究活动中
    appendResearchActivity(message);
  }
}

function updateMessage(message: Message) {
  const currentOngoingId = getOngoingResearchId();
  
  // 如果是reporter完成了消息，清除研究状态
  if (
    currentOngoingId &&
    message.agent === "reporter" &&
    !message.isStreaming
  ) {
    console.log(`研究完成，清除ongoing状态: ${currentOngoingId}`);
    useStore.getState().setOngoingResearch(null);
  }
  
  // 如果收到finish_reason，也可能需要清除状态
  if (
    currentOngoingId &&
    message.finishReason &&
    (message.finishReason === "stop" || message.finishReason === "completed")
  ) {
    console.log(`消息完成，检查是否需要清除ongoing状态: ${message.finishReason}`);
    // 给一个小延迟，确保所有相关消息都处理完毕
    setTimeout(() => {
      const stillOngoing = useStore.getState().ongoingResearchId;
      if (stillOngoing === currentOngoingId) {
        console.log(`延迟清除ongoing状态: ${currentOngoingId}`);
        useStore.getState().setOngoingResearch(null);
      }
    }, 1000);
  }
  
  useStore.getState().updateMessage(message);
}

function getOngoingResearchId() {
  return useStore.getState().ongoingResearchId;
}

function appendResearch(researchId: string) {
  const currentState = useStore.getState();
  
  // 【修复3】防止重复添加研究
  if (currentState.researchIds.includes(researchId)) {
    console.log(`⚠️ 研究 ${researchId} 已存在，跳过重复添加`);
    return;
  }
  
  let planMessage: Message | undefined;
  const reversedMessageIds = [...currentState.messageIds].reverse();
  for (const messageId of reversedMessageIds) {
    const message = getMessage(messageId);
    if (message?.agent === "planner") {
      planMessage = message;
      break;
    }
  }
  
  const messageIds = [researchId];
  // 只有找到planMessage时才添加
  if (planMessage) {
    messageIds.unshift(planMessage.id);
  }
  
  console.log(`➕ 添加新研究: ${researchId}, 计划消息: ${planMessage?.id || '无'}`);
  
  useStore.setState({
    ongoingResearchId: researchId,
    researchIds: [...currentState.researchIds, researchId],
    researchPlanIds: planMessage 
      ? new Map(currentState.researchPlanIds).set(researchId, planMessage.id)
      : currentState.researchPlanIds,
    researchActivityIds: new Map(currentState.researchActivityIds).set(
      researchId,
      messageIds,
    ),
  });
}

function appendResearchActivity(message: Message) {
  const researchId = getOngoingResearchId();
  if (researchId) {
    const researchActivityIds = useStore.getState().researchActivityIds;
    const current = researchActivityIds.get(researchId)!;
    if (!current.includes(message.id)) {
      useStore.setState({
        researchActivityIds: new Map(researchActivityIds).set(researchId, [
          ...current,
          message.id,
        ]),
      });
    }
    if (message.agent === "reporter") {
      useStore.setState({
        researchReportIds: new Map(useStore.getState().researchReportIds).set(
          researchId,
          message.id,
        ),
      });
    }
  }
}

export function openResearch(researchId: string | null) {
  useStore.getState().openResearch(researchId);
}

export function closeResearch() {
  useStore.getState().closeResearch();
}

// 紧急重置正在进行的研究状态
export function resetOngoingResearch() {
  const currentOngoing = useStore.getState().ongoingResearchId;
  if (currentOngoing) {
    console.log(`手动重置ongoing研究状态: ${currentOngoing}`);
    useStore.getState().setOngoingResearch(null);
    return true;
  }
  return false;
}

// 获取当前ongoing研究的信息（用于调试）
export function getOngoingResearchInfo() {
  const state = useStore.getState();
  return {
    ongoingResearchId: state.ongoingResearchId,
    openResearchId: state.openResearchId,
    researchIds: state.researchIds,
    responding: state.responding,
  };
}

export async function listenToPodcast(researchId: string) {
  const planMessageId = useStore.getState().researchPlanIds.get(researchId);
  const reportMessageId = useStore.getState().researchReportIds.get(researchId);
  if (planMessageId && reportMessageId) {
    const planMessage = getMessage(planMessageId)!;
    const title = parseJSON(planMessage.content, { title: "Untitled" }).title;
    const reportMessage = getMessage(reportMessageId);
    if (reportMessage?.content) {
      appendMessage({
        id: nanoid(),
        threadId: CURRENT_THREAD_ID,
        role: "user",
        content: "Please generate a podcast for the above research.",
        contentChunks: [],
      });
      const podCastMessageId = nanoid();
      const podcastObject = { title, researchId };
      const podcastMessage: Message = {
        id: podCastMessageId,
        threadId: CURRENT_THREAD_ID,
        role: "assistant",
        agent: "podcast",
        content: JSON.stringify(podcastObject),
        contentChunks: [],
        reasoningContent: "",
        reasoningContentChunks: [],
        isStreaming: true,
      };
      appendMessage(podcastMessage);
      // Generating podcast...
      let audioUrl: string | undefined;
      try {
        audioUrl = await generatePodcast(reportMessage.content);
      } catch (e) {
        console.error(e);
        useStore.setState((state) => ({
          messages: new Map(useStore.getState().messages).set(
            podCastMessageId,
            {
              ...state.messages.get(podCastMessageId)!,
              content: JSON.stringify({
                ...podcastObject,
                error: e instanceof Error ? e.message : "Unknown error",
              }),
              isStreaming: false,
            },
          ),
        }));
        toast("An error occurred while generating podcast. Please try again.");
        return;
      }
      useStore.setState((state) => ({
        messages: new Map(useStore.getState().messages).set(podCastMessageId, {
          ...state.messages.get(podCastMessageId)!,
          content: JSON.stringify({ ...podcastObject, audioUrl }),
          isStreaming: false,
        }),
      }));
    }
  }
}

export function useResearchMessage(researchId: string) {
  return useStore(
    useShallow((state) => {
      const messageId = state.researchPlanIds.get(researchId);
      return messageId ? state.messages.get(messageId) : undefined;
    }),
  );
}

export function useMessage(messageId: string | null | undefined) {
  return useStore(
    useShallow((state) =>
      messageId ? state.messages.get(messageId) : undefined,
    ),
  );
}

export function useMessageIds() {
  return useStore(useShallow((state) => {
    // 只返回当前线程的消息ID
    return state.messageIds.filter(messageId => {
      const message = state.messages.get(messageId);
      return message && message.threadId === state.threadId;
    });
  }));
}

export function useLastInterruptMessage() {
  return useStore(
    useShallow((state) => {
      // 只获取当前线程的消息
      const currentThreadMessageIds = state.messageIds.filter(messageId => {
        const message = state.messages.get(messageId);
        return message && message.threadId === state.threadId;
      });
      
      if (currentThreadMessageIds.length >= 2) {
        const lastMessage = state.messages.get(
          currentThreadMessageIds[currentThreadMessageIds.length - 1]!,
        );
        return lastMessage?.finishReason === "interrupt" ? lastMessage : null;
      }
      return null;
    }),
  );
}

export function useLastFeedbackMessageId() {
  const waitingForFeedbackMessageId = useStore(
    useShallow((state) => {
      // 只获取当前线程的消息
      const currentThreadMessageIds = state.messageIds.filter(messageId => {
        const message = state.messages.get(messageId);
        return message && message.threadId === state.threadId;
      });
      
      if (currentThreadMessageIds.length >= 2) {
        const lastMessage = state.messages.get(
          currentThreadMessageIds[currentThreadMessageIds.length - 1]!,
        );
        if (lastMessage && lastMessage.finishReason === "interrupt") {
          return currentThreadMessageIds[currentThreadMessageIds.length - 2];
        }
      }
      return null;
    }),
  );
  return waitingForFeedbackMessageId;
}

export function useToolCalls() {
  return useStore(
    useShallow((state) => {
      return state.messageIds
        ?.map((id) => getMessage(id)?.toolCalls)
        .filter((toolCalls) => toolCalls != null)
        .flat();
    }),
  );
}

// 检查是否有真正的调查内容（不只是 research_start 标记）
async function checkForRealResearchContent(threadId: string): Promise<boolean> {
  try {
    console.log(`🔍 检查线程 ${threadId} 是否有真正的调查内容...`);
    
    // 通过回放事件来检查是否有实际的调查活动
    const { chatSmartReplayStream } = await import("~/core/api/chat");
    
    let hasToolCalls = false;
    let hasReportContent = false;
    let hasResearchActivities = false;
    let eventCount = 0;
    
    try {
      for await (const event of chatSmartReplayStream(threadId, "0")) {
        eventCount++;
        
        // 检查是否有工具调用（表示实际的调查活动）
        if (event.type === "tool_calls" || event.type === "tool_call_result") {
          hasToolCalls = true;
          console.log(`✅ 发现工具调用事件: ${event.type}`);
        }
        
        // 检查是否有报告内容（agent="reporter"）
        if (event.type === "message_chunk" && event.data.agent === "reporter") {
          hasReportContent = true;
          console.log(`✅ 发现报告内容: agent=${event.data.agent}`);
        }
        
        // 检查是否有研究相关的活动（除了简单的开始标记）
        if (event.type === "message_chunk" && 
            (event.data.agent === "researcher" || event.data.agent === "coder") &&
            event.data.content && 
            !event.data.content.includes("开始研究调查")) {
          hasResearchActivities = true;
          console.log(`✅ 发现研究活动: agent=${event.data.agent}, content=${event.data.content?.slice(0, 50)}...`);
        }
        
        // 如果已经发现了足够的证据，提前结束
        if (hasToolCalls || hasReportContent || hasResearchActivities) {
          console.log(`✅ 线程 ${threadId} 有真正的调查内容: toolCalls=${hasToolCalls}, report=${hasReportContent}, activities=${hasResearchActivities}`);
          return true;
        }
        
        // 限制检查的事件数量，避免太耗时
        if (eventCount > 100) {
          console.log(`⚠️ 线程 ${threadId} 事件太多，停止检查 (已检查 ${eventCount} 个事件)`);
          break;
        }
      }
    } catch (replayError) {
      console.warn(`⚠️ 线程 ${threadId} 回放检查失败，假设没有调查内容:`, replayError);
      return false;
    }
    
    console.log(`📊 线程 ${threadId} 调查内容检查完成: 事件数=${eventCount}, toolCalls=${hasToolCalls}, report=${hasReportContent}, activities=${hasResearchActivities}`);
    
    // 只有在有实际调查活动时才返回 true
    return hasToolCalls || hasReportContent || hasResearchActivities;
    
  } catch (error) {
    console.error(`❌ 检查线程调查内容失败 ${threadId}:`, error);
    // 出错时保守假设没有调查内容
    return false;
  }
}

