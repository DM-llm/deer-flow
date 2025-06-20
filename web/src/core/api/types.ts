// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import type { Option } from "../messages";

// Tool Calls

export interface ToolCall {
  type: "tool_call";
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolCallChunk {
  type: "tool_call_chunk";
  index: number;
  id: string;
  name: string;
  args: string;
}

// Events

interface GenericEvent<T extends string, D extends object> {
  type: T;
  data: {
    id: string;
    thread_id: string;
    agent: "coordinator" | "planner" | "researcher" | "coder" | "reporter";
    role: "user" | "assistant" | "tool";
    finish_reason?: "stop" | "tool_calls" | "interrupt" | "completed";
  } & D;
}

export interface MessageChunkEvent
  extends GenericEvent<
    "message_chunk",
    {
      content?: string;
      reasoning_content?: string;
    }
  > {}

export interface ToolCallsEvent
  extends GenericEvent<
    "tool_calls",
    {
      tool_calls: ToolCall[];
      tool_call_chunks: ToolCallChunk[];
    }
  > {}

export interface ToolCallChunksEvent
  extends GenericEvent<
    "tool_call_chunks",
    {
      tool_call_chunks: ToolCallChunk[];
    }
  > {}

export interface ToolCallResultEvent
  extends GenericEvent<
    "tool_call_result",
    {
      tool_call_id: string;
      content?: string;
    }
  > {}

export interface InterruptEvent
  extends GenericEvent<
    "interrupt",
    {
      options: Option[];
    }
  > {}

// 额外事件：replay_end / error / research_end

export interface ReplayEndEvent extends GenericEvent<
  "replay_end",
  {
    message?: string;
  }
> {}

export interface ErrorEvent extends GenericEvent<
  "error",
  {
    message: string;
  }
> {}

export interface ResearchEndEvent extends GenericEvent<
  "research_end",
  {
    research_id: string;
    status: "completed";
  }
> {}

export type ChatEvent =
  | MessageChunkEvent
  | ToolCallsEvent
  | ToolCallChunksEvent
  | ToolCallResultEvent
  | InterruptEvent
  | ReplayEndEvent
  | ErrorEvent
  | ResearchEndEvent;
