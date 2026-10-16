import React from "react";
import ReactDOM from "react-dom/client";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
} from "react-router-dom";
import { WorkspaceProvider } from "@/hooks/workspace";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Shell } from "@/components/shell";
import { QaShell } from "@/components/qa-shell";
import { isLive } from "@/services/config";
import "./index.css";

const router = createBrowserRouter([
  {
    element: isLive ? <QaShell /> : <Shell />,
    // Lazy child routes load after the first render; nothing to show meanwhile.
    hydrateFallbackElement: <div aria-busy="true" />,
    children: [
      {
        path: "/",
        element: <Navigate to={isLive ? "/ask" : "/record"} replace />,
      },
      {
        path: "/ask/:threadId?",
        lazy: async () => ({
          Component: (await import("@/pages/ask")).AskPage,
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
                Component: (await import("@/pages/capture-review"))
                  .CaptureReviewPage,
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
        <RouterProvider router={router} />
      </TooltipProvider>
    </WorkspaceProvider>
  </React.StrictMode>,
);
