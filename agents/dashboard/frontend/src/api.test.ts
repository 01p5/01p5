import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api } from "./api";

type FetchFn = typeof fetch;

function makeResponse(opts: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  json?: unknown;
  text?: string;
}): Response {
  const status = opts.status ?? 200;
  const ok = opts.ok ?? (status >= 200 && status < 300);
  return {
    ok,
    status,
    statusText: opts.statusText ?? "",
    json: async (): Promise<unknown> => opts.json,
    text: async (): Promise<string> => opts.text ?? "",
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock as unknown as FetchFn);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.health", () => {
  it("GETs /healthz and returns the parsed JSON body", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: { ok: true } }));
    const r = await api.health();
    expect(fetchMock).toHaveBeenCalledWith("/healthz");
    expect(r).toEqual({ ok: true });
  });
});

describe("api.listTasks / getTask / submitTask", () => {
  it("listTasks GETs /tasks", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: [] }));
    await api.listTasks();
    expect(fetchMock).toHaveBeenCalledWith("/tasks");
  });

  it("getTask GETs /tasks/<encoded-id>", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: { task_id: "x" } }));
    await api.getTask("foo bar");
    expect(fetchMock).toHaveBeenCalledWith("/tasks/foo%20bar");
  });

  it("submitTask POSTs JSON with the natural_language body", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: { task_id: "t-1" } }));
    const r = await api.submitTask("list pods");
    expect(r).toEqual({ task_id: "t-1" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/tasks");
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).headers).toMatchObject({
      "Content-Type": "application/json",
    });
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      natural_language: "list pods",
    });
  });
});

describe("api.listApprovals / resolveApproval", () => {
  it("listApprovals GETs /approvals", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: [] }));
    await api.listApprovals();
    expect(fetchMock).toHaveBeenCalledWith("/approvals");
  });

  it("resolveApproval POSTs JSON to /approvals/<id>", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: { resolved: "y" } }));
    await api.resolveApproval("a/b", { approved: true, reason: "ok" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/approvals/a%2Fb");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      approved: true,
      reason: "ok",
    });
  });
});

describe("api.audit", () => {
  it("returns parsed JSONL records, dropping malformed lines", async () => {
    const text = [
      JSON.stringify({ ts: 1, task_id: "a", agent: "x", tool: "t", args: {}, result: null, approved: null }),
      "garbage{{{",
      "",
      JSON.stringify({ ts: 2, task_id: "b", agent: "y", tool: "u", args: {}, result: null, approved: true }),
    ].join("\n");
    fetchMock.mockResolvedValueOnce(makeResponse({ text }));
    const recs = await api.audit();
    expect(recs).toHaveLength(2);
    expect(recs[0].task_id).toBe("a");
    expect(recs[1].task_id).toBe("b");
  });

  it("returns [] on non-ok response", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ ok: false, status: 500 }));
    const recs = await api.audit();
    expect(recs).toEqual([]);
  });

  it("returns [] on empty body", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ text: "" }));
    const recs = await api.audit();
    expect(recs).toEqual([]);
  });
});

describe("api.listTools / invokeTool", () => {
  it("listTools GETs /tools", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: [] }));
    await api.listTools();
    expect(fetchMock).toHaveBeenCalledWith("/tools");
  });

  it("invokeTool POSTs to /tools/<agent>/<tool> with JSON args", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({
      json: { task_id: "t", agent: "sysadmin", tool: "get_pods", result: "ok" },
    }));
    const r = await api.invokeTool("sysadmin", "get_pods", { ns: "default" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/tools/sysadmin/get_pods");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ ns: "default" });
    expect(r.result).toBe("ok");
  });

  it("invokeTool URL-encodes weird agent/tool names", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: { task_id: "t", agent: "a", tool: "b", result: "ok" } }));
    await api.invokeTool("a b", "c/d", {});
    expect(fetchMock.mock.calls[0][0]).toBe("/tools/a%20b/c%2Fd");
  });
});

describe("api.terraformStacks / ansiblePlaybooks", () => {
  it("terraformStacks GETs /stacks/terraform", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: ["terraform", "terraform/aws"] }));
    const r = await api.terraformStacks();
    expect(fetchMock).toHaveBeenCalledWith("/stacks/terraform");
    expect(r).toEqual(["terraform", "terraform/aws"]);
  });

  it("ansiblePlaybooks GETs /stacks/ansible", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ json: ["ansible/site.yml"] }));
    await api.ansiblePlaybooks();
    expect(fetchMock).toHaveBeenCalledWith("/stacks/ansible");
  });
});

describe("error path", () => {
  it("throws with status + statusText + error detail when response not ok", async () => {
    fetchMock.mockResolvedValueOnce(
      makeResponse({ ok: false, status: 500, statusText: "Server Error", json: { error: "boom" } }),
    );
    await expect(api.health()).rejects.toThrow(/500 Server Error: boom/);
  });

  it("throws with just status when no JSON error body", async () => {
    fetchMock.mockResolvedValueOnce(
      makeResponse({ ok: false, status: 404, statusText: "Not Found", json: undefined }),
    );
    await expect(api.health()).rejects.toThrow(/404 Not Found/);
  });
});
