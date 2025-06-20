// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

"use client";

import { GithubOutlined } from "@ant-design/icons";
import { History, Plus } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { Suspense, useState, useCallback } from "react";

import { Button } from "~/components/ui/button";
import { switchToHistoryChat, useStore } from "~/core/store";

import { Logo } from "../../components/deer-flow/logo";
import { ThemeToggle } from "../../components/deer-flow/theme-toggle";
import { Tooltip } from "../../components/deer-flow/tooltip";
import { SettingsDialog } from "../settings/dialogs/settings-dialog";
import { ChatHistoryDialog } from "./components/chat-history-dialog";

const Main = dynamic(() => import("./main"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center">
      Loading DeerFlow...
    </div>
  ),
});

export default function HomePage() {
  // 获取store中的resetThread函数
  const resetThread = useStore((state) => state.resetThread);
  
  // 新增：历史记录对话框状态
  const [showHistoryDialog, setShowHistoryDialog] = useState(false);
  const handleShowHistory = useCallback(() => {
    setShowHistoryDialog(true);
  }, []);
  const handleCloseHistory = useCallback(() => {
    setShowHistoryDialog(false);
  }, []);
  
  // 新增：新对话处理函数
  const handleNewChat = useCallback(() => {
    resetThread();
  }, [resetThread]);

  return (
    <div className="flex h-screen w-screen justify-center overscroll-none">
      <header className="fixed top-0 left-0 flex h-12 w-full items-center justify-between px-4">
        <Logo />
        <div className="flex items-center">
          <Tooltip title="开始新对话">
            <Button variant="ghost" size="icon" onClick={handleNewChat}>
              <Plus size={16} />
            </Button>
          </Tooltip>
          <Tooltip title="查看对话历史">
            <Button variant="ghost" size="icon" onClick={handleShowHistory}>
              <History size={16} />
            </Button>
          </Tooltip>
          <Tooltip title="Star DeerFlow on GitHub">
            <Button variant="ghost" size="icon" asChild>
              <Link
                href="https://github.com/bytedance/deer-flow"
                target="_blank"
              >
                <GithubOutlined />
              </Link>
            </Button>
          </Tooltip>
          <ThemeToggle />
          <Suspense>
            <SettingsDialog />
          </Suspense>
        </div>
      </header>
      <Main />
      
      {/* 历史记录对话框 */}
      <ChatHistoryDialog
        open={showHistoryDialog}
        onClose={handleCloseHistory}
        onSelectChat={async (threadId) => {
          // 切换到历史对话
          const success = await switchToHistoryChat(threadId);
          if (success) {
            setShowHistoryDialog(false);
          }
        }}
      />
    </div>
  );
}
