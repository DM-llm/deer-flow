// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import { type StreamEvent } from "./StreamEvent";

export async function* fetchStream(
  url: string,
  init: RequestInit & { searchParams?: URLSearchParams },
): AsyncIterable<StreamEvent> {
  // 构建完整的URL，包含查询参数
  let fullUrl = url;
  if (init.searchParams) {
    const separator = url.includes('?') ? '&' : '?';
    fullUrl = `${url}${separator}${init.searchParams.toString()}`;
  }
  
  // 从init中移除searchParams，避免传递给fetch
  const { searchParams, ...fetchInit } = init;
  
  const response = await fetch(fullUrl, {
    // 使用传入的method，默认为POST以保持向后兼容
    method: fetchInit.method || "POST",
    headers: {
      "Cache-Control": "no-cache",
      // 如果是GET请求，不需要Content-Type
      ...(fetchInit.method === "GET" ? {} : { "Content-Type": "application/json" }),
    },
    ...fetchInit,
  });
  if (response.status !== 200) {
    throw new Error(`Failed to fetch from ${url}: ${response.status}`);
  }
  // Read from response body, event by event. An event always ends with a '\n\n'.
  const reader = response.body
    ?.pipeThrough(new TextDecoderStream())
    .getReader();
  if (!reader) {
    throw new Error("Response body is not readable");
  }
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += value;
    while (true) {
      const index = buffer.indexOf("\n\n");
      if (index === -1) {
        break;
      }
      const chunk = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const event = parseEvent(chunk);
      if (event) {
        yield event;
      }
    }
  }
}

function parseEvent(chunk: string) {
  let resultEvent = "message";
  let resultData: string | null = null;
  for (const line of chunk.split("\n")) {
    const pos = line.indexOf(": ");
    if (pos === -1) {
      continue;
    }
    const key = line.slice(0, pos);
    const value = line.slice(pos + 2);
    if (key === "event") {
      resultEvent = value;
    } else if (key === "data") {
      resultData = value;
    }
  }
  if (resultEvent === "message" && resultData === null) {
    return undefined;
  }
  return {
    event: resultEvent,
    data: resultData,
  } as StreamEvent;
}
