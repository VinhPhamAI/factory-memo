import type { LocatedReference } from "@/types";
import type { ChunkStatus } from "./knowledge-search";

export type ChatSource = {
  kb: string;
  source_id: string;
  source_kind: "upload" | "conversation";
  title: string;
  reference?: LocatedReference;
};

type ChatToolResult = {
  rank: number;
  chunk_id: string;
  title: string;
  status: ChunkStatus;
  excerpt?: string;
  sources: ChatSource[];
};

type CatalogEntry = { name: string; title: string; description?: string };

/** Safe, bounded subset of a tool call for live or saved chat display. */
export type ChatTool = {
  id: string;
  name: "list_knowledge_bases" | "search_knowledge";
  state: "running" | "results" | "empty" | "unavailable";
  kb?: string;
  query?: string;
  count?: number;
  truncated?: boolean;
  catalog: CatalogEntry[];
  results: ChatToolResult[];
};

const KB_NAME = /^[a-z][a-z0-9_]{0,59}$/;
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;

/** A stable reply identifier can join a stream to its saved history message. */
export function parseReplyId(value: unknown): string | undefined {
  return typeof value === "string" && UUID.test(value) ? value : undefined;
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function positiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function nonnegativeInteger(value: unknown): value is number {
  return value === 0 || positiveInteger(value);
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function text(value: unknown, limit: number): string | undefined {
  return typeof value === "string" && value.length <= limit ? value : undefined;
}

function kbName(value: unknown): value is string {
  return typeof value === "string" && KB_NAME.test(value);
}

/** Parse the only location fields the source viewer uses. */
function parseLocation(value: unknown): LocatedReference | undefined {
  if (!object(value)) return undefined;
  const quote = text(value.quote, 500);
  switch (value.type) {
    case "page":
    case "image": {
      if (!positiveInteger(value.page)) return undefined;
      const { bbox, page_width, page_height } = value;
      const label = text(value.label, 240);
      if (Array.isArray(bbox) && bbox.length === 4 && bbox.every(finite)) {
        return {
          type: value.type,
          page: value.page,
          bbox: [bbox[0], bbox[1], bbox[2], bbox[3]],
          page_width:
            finite(page_width) && page_width > 0 ? page_width : undefined,
          page_height:
            finite(page_height) && page_height > 0 ? page_height : undefined,
          label,
          quote,
        };
      }
      return { type: value.type, page: value.page, label, quote };
    }
    case "paragraph":
      return nonnegativeInteger(value.index)
        ? {
            type: value.type,
            index: value.index,
            table: positiveInteger(value.table) ? value.table : undefined,
            cell: text(value.cell, 80),
            quote,
          }
        : undefined;
    case "cells": {
      const sheet = text(value.sheet, 240);
      const range = text(value.range, 80);
      return sheet && range ? { type: value.type, sheet, range, quote } : undefined;
    }
    case "passage":
      return nonnegativeInteger(value.chunk_index)
        ? { type: value.type, chunk_index: value.chunk_index, quote }
        : undefined;
    default:
      return undefined;
  }
}

/** Validate the source identity before it can be used to construct a local link. */
export function parseChatSources(value: unknown): ChatSource[] {
  return Array.isArray(value)
    ? value.slice(0, 2).flatMap((item) => {
        if (
          !object(item) ||
          !kbName(item.kb) ||
          typeof item.source_id !== "string" ||
          !UUID.test(item.source_id) ||
          (item.source_kind !== "upload" &&
            item.source_kind !== "conversation")
        )
          return [];
        return [
          {
            kb: item.kb,
            source_id: item.source_id,
            source_kind: item.source_kind,
            title: text(item.title, 500) || "Nguồn tri thức",
            reference: parseLocation(item.reference),
          },
        ];
      })
    : [];
}

function parseResult(value: unknown): ChatToolResult | null {
  if (
    !object(value) ||
    !positiveInteger(value.rank) ||
    typeof value.chunk_id !== "string" ||
    !UUID.test(value.chunk_id) ||
    (value.status !== "draft" && value.status !== "approved")
  )
    return null;
  return {
    rank: value.rank,
    chunk_id: value.chunk_id,
    title: text(value.title, 500) || "Đoạn tri thức",
    status: value.status,
    excerpt: text(value.excerpt, 10000),
    sources: parseChatSources(value.sources),
  };
}

/** Parse the public projection only, dropping unknown fields and oversized lists. */
export function parseChatTool(value: unknown): ChatTool | null {
  if (!object(value)) return null;
  const { name, state } = value;
  const id = text(value.id, 200);
  if (
    !id ||
    (name !== "list_knowledge_bases" && name !== "search_knowledge") ||
    (state !== "running" &&
      state !== "results" &&
      state !== "empty" &&
      state !== "unavailable")
  )
    return null;
  const kb = kbName(value.kb) ? value.kb : undefined;
  const query = text(value.query, 500);
  if (name === "search_knowledge" && (!kb || !query)) return null;
  return {
    id,
    name,
    state,
    kb,
    query,
    count: nonnegativeInteger(value.count) ? value.count : undefined,
    truncated: value.truncated === true,
    catalog:
      Array.isArray(value.catalog) && name === "list_knowledge_bases"
        ? value.catalog.slice(0, 50).flatMap((entry) => {
            if (!object(entry) || !kbName(entry.name)) return [];
            return [
              {
                name: entry.name,
                title: text(entry.title, 240) || entry.name,
                description: text(entry.description, 240),
              },
            ];
          })
        : [],
    results:
      Array.isArray(value.results) && name === "search_knowledge"
        ? value.results
            .slice(0, 20)
            .map(parseResult)
            .filter((result) => result !== null)
        : [],
  };
}

export function parseChatTools(value: unknown): ChatTool[] {
  return Array.isArray(value)
    ? value
        .slice(0, 2)
        .map(parseChatTool)
        .filter((call) => call !== null)
    : [];
}

/** Keep a matching finished live call until chat reloads from compact history. */
export function reconcileChatTools(
  saved: ChatTool[],
  live: ChatTool[],
): ChatTool[] {
  return saved.map(
    (call) =>
      live.find(
        (item) =>
          item.id === call.id &&
          item.name === call.name &&
          item.state === call.state &&
          item.state !== "running",
      ) ?? call,
  );
}
