import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import type { ChatThread, KnowledgeCard, Source } from "@/types";
import { seedSources } from "@/fixtures/seed";

interface WorkspaceDB extends DBSchema {
  sources: { key: string; value: Source };
  threads: { key: string; value: ChatThread };
  /** Approved cards are the demo's "knowledge_items" table. */
  cards: { key: string; value: KnowledgeCard };
  settings: { key: string; value: boolean };
}

export class WorkspaceStorage {
  private database?: Promise<IDBPDatabase<WorkspaceDB>>;
  constructor(private readonly name = "voice-agent-workspace-v1") {}

  private open() {
    this.database ??= openDB<WorkspaceDB>(this.name, 1, {
      upgrade(db) {
        db.createObjectStore("sources", { keyPath: "id" });
        db.createObjectStore("threads", { keyPath: "id" });
        db.createObjectStore("cards", { keyPath: "id" });
        db.createObjectStore("settings");
      },
    });
    return this.database;
  }

  async load() {
    const db = await this.open();
    // Check and seed in one transaction, including across simultaneous tabs.
    const tx = db.transaction(["sources", "settings"], "readwrite");
    if (!(await tx.objectStore("settings").get("seeded"))) {
      for (const source of seedSources())
        await tx.objectStore("sources").put(source);
      await tx.objectStore("settings").put(true, "seeded");
    }
    await tx.done;
    const sources = await db.getAll("sources");
    const threads = (await db.getAll("threads")).map((thread) => ({
      ...thread,
      messages: thread.messages.map((message) =>
        message.status === "streaming"
          ? { ...message, status: "stopped" as const }
          : message,
      ),
    }));
    const cards = await db.getAll("cards");
    return { sources, threads, cards };
  }

  async saveSource(source: Source) {
    await (await this.open()).put("sources", source);
  }
  async saveCard(card: KnowledgeCard) {
    await (await this.open()).put("cards", card);
  }
  async saveThread(thread: ChatThread) {
    await (await this.open()).put("threads", thread);
  }
  async reset() {
    const db = await this.open();
    const tx = db.transaction(
      ["sources", "threads", "cards", "settings"],
      "readwrite",
    );
    await Promise.all([
      tx.objectStore("sources").clear(),
      tx.objectStore("threads").clear(),
      tx.objectStore("cards").clear(),
      tx.objectStore("settings").clear(),
    ]);
    await tx.done;
    return this.load();
  }
  async close() {
    if (this.database) (await this.database).close();
  }
}

export const storage = new WorkspaceStorage();
export function storageError(error: unknown) {
  return error instanceof DOMException && error.name === "QuotaExceededError"
    ? "Bộ nhớ trình duyệt đã đầy. Thay đổi chưa được lưu."
    : "Không lưu được vào trình duyệt. Thay đổi vẫn còn trên màn hình, hãy thử lại.";
}
