// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import { Calendar, MessageCircle } from "lucide-react";
import React, { useEffect, useState } from "react";

import { Button } from "~/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "~/components/ui/dialog";
import { ScrollArea } from "~/components/ui/scroll-area";
import { getAllChatHistories } from "~/core/store";

interface ChatHistoryItem {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
}

interface ChatHistoryDialogProps {
  open: boolean;
  onClose: () => void;
  onSelectChat: (threadId: string) => void;
}

export function ChatHistoryDialog({
  open,
  onClose,
  onSelectChat,
}: ChatHistoryDialogProps) {
  const [historyItems, setHistoryItems] = useState<ChatHistoryItem[]>([]);
  
  useEffect(() => {
    if (open) {
      // 对话框打开时获取最新的历史记录
      const histories = getAllChatHistories();
      setHistoryItems(histories);
    }
  }, [open]);

  const handleSelectChat = (threadId: string) => {
    onSelectChat(threadId);
  };

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>对话历史记录</DialogTitle>
          <DialogDescription>
            选择一个历史对话继续，或开始新的对话
          </DialogDescription>
        </DialogHeader>
        
        <ScrollArea className="h-96">
          <div className="space-y-2">
            {historyItems.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                <MessageCircle className="h-12 w-12 mb-4" />
                <p>暂无历史对话记录</p>
                <p className="text-sm">开始新的对话吧！</p>
              </div>
            ) : (
              historyItems.map((item) => (
                <div
                  key={item.id}
                  className="border rounded-lg p-4 hover:bg-accent cursor-pointer transition-colors"
                  onClick={() => handleSelectChat(item.id)}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h3 className="font-medium text-sm mb-1">{item.title}</h3>
                      <p className="text-sm text-muted-foreground line-clamp-2">
                        {item.lastMessage}
                      </p>
                    </div>
                    <div className="flex items-center text-xs text-muted-foreground ml-2">
                      <Calendar className="h-3 w-3 mr-1" />
                      {item.timestamp}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </ScrollArea>
        
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
} 