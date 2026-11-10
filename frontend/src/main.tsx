import React from "react";
import ReactDOM from "react-dom/client";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
} from "react-router-dom";
import { WorkspaceProvider } from "@/hooks/workspace";
import { AuthProvider, RequireAuth } from "@/hooks/auth";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Shell } from "@/components/shell";
import { QaShell } from "@/components/qa-shell";
import { isLive } from "@/services/config";
import "./index.css";

const router = createBrowserRouter([
  {
    // Live mode needs a signed-in account; the mock demo has no backend.
    element: isLive ? (
      <RequireAuth>
        <QaShell />
      </RequireAuth>
    ) : (
      <Shell />
    ),
    // Lazy child routes load after the first render; nothing to show meanwhile.
    hydrateFallbackElement: <div aria-busy="true" />,
    children: [
      {
        path: "/",
        element: <Navigate to={isLive ? "/ask" : "/record"} replace />,
      },
      {
        path: "/ask/:threadId?",
        // Live mode chats with the standalone agent; the mock demo keeps its fixture QA.
        lazy: async () => ({
          Component: isLive
            ? (await import("@/pages/conversations")).ConversationsPage
            : (await import("@/pages/ask")).AskPage,
        }),
      },
      ...(isLive
        ? [
            {
              path: "/capture",
              lazy: async () => ({
                Component: (await import("@/pages/capture")).CapturePage,
              }),
            },
            {
              path: "/capture/:kb/:conversationId",
              lazy: async () => ({
                Component: (await import("@/pages/knowledge-management"))
                  .LegacyCaptureReviewRedirect,
              }),
            },
            {
              path: "/knowledge",
              element: <Navigate to="/files" replace />,
            },
            {
              path: "/files",
              lazy: async () => ({
                Component: (await import("@/pages/files")).FilesPage,
              }),
            },
            {
              path: "/admin/knowledge-bases",
              lazy: async () => ({ Component: (await import("@/pages/knowledge-admin")).KnowledgeAdminPage }),
            },
            {
              path: "/tickets/:id?",
              lazy: async () => ({
                Component: (await import("@/pages/tickets")).TicketsPage,
              }),
            },
            {
              path: "/inquiries",
              lazy: async () => ({
                Component: (await import("@/pages/inquiries")).InquiriesPage,
              }),
            },
            {
              path: "/inquiries/:id",
              lazy: async () => ({
                Component: (await import("@/pages/inquiry")).InquiryPage,
              }),
            },
            {
              path: "/knowledge/:kb/sources/:kind/:sourceId",
              lazy: async () => ({
                Component: (await import("@/pages/knowledge-management"))
                  .KnowledgeSourcePage,
              }),
            },
          ]
        : []),
      ...(!isLive
        ? [
            {
              path: "/sources/:sourceId?",
              lazy: async () => ({
                Component: (await import("@/pages/sources")).SourcesPage,
              }),
            },
            {
              path: "/record",
              lazy: async () => ({
                Component: (await import("@/pages/record")).RecordPage,
              }),
            },
            {
              path: "/analyze",
              lazy: async () => ({
                Component: (await import("@/pages/analyze")).AnalyzePage,
              }),
            },
            {
              path: "/card",
              lazy: async () => ({
                Component: (await import("@/pages/card")).CardPage,
              }),
            },
          ]
        : []),
      {
        path: "*",
        element: <Navigate to={isLive ? "/ask" : "/record"} replace />,
      },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WorkspaceProvider>
      <TooltipProvider delayDuration={250}>
        {isLive ? (
          <AuthProvider>
            <RouterProvider router={router} />
          </AuthProvider>
        ) : (
          <RouterProvider router={router} />
        )}
      </TooltipProvider>
    </WorkspaceProvider>
  </React.StrictMode>,
);
