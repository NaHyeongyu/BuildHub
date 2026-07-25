import { describe, expect, it } from "vitest";
import type {
  ProjectContextGraphEdge,
  ProjectContextGraphNode,
  ProjectContextGraphNodeKind,
} from "../../api/projects";
import {
  applyKnowledgeNodeReview,
  buildContextGraphLayout,
  knowledgeNodeLayer,
  visibleContextGraphMetadata,
} from "./ContextGraphPanel";
import componentSource from "./ContextGraphPanel.tsx?raw";

function graphNode(
  id: string,
  kind: ProjectContextGraphNodeKind,
  overrides: Partial<ProjectContextGraphNode> = {},
): ProjectContextGraphNode {
  return {
    agent_visible: false,
    id,
    kind,
    label: id,
    metadata: {},
    occurred_at: null,
    sequence: null,
    session_id: null,
    summary: null,
    ...overrides,
  };
}

describe("ContextGraphPanel", () => {
  it("projects only output-derived knowledge into three review layers", () => {
    const nodes = [
      graphNode("prompt", "prompt"),
      graphNode("response", "response"),
      graphNode("decision", "decision"),
      graphNode("requirement", "requirement", {
        metadata: { review_state: "confirmed", status: "active" },
      }),
      graphNode("idea", "brainstorm", {
        metadata: { review_state: "rejected", status: "discarded" },
      }),
      graphNode("question", "open_question", {
        metadata: { status: "open" },
      }),
      graphNode("memory", "memory"),
      graphNode("file", "file"),
    ];

    const layout = buildContextGraphLayout(nodes, []);

    expect(layout.layers.map((layer) => layer.layer)).toEqual([
      "pending",
      "confirmed",
      "archived",
    ]);
    expect(layout.visibleNodes.map((node) => node.id)).toEqual([
      "decision",
      "question",
      "requirement",
      "idea",
    ]);
    expect(layout.layers[0].nodes.map((node) => node.id)).toEqual([
      "decision",
      "question",
    ]);
    expect(layout.layers[1].nodes.map((node) => node.id)).toEqual([
      "requirement",
    ]);
    expect(layout.layers[2].nodes.map((node) => node.id)).toEqual(["idea"]);
    expect(layout.nodePositions.requirement.layer).toBe("confirmed");
  });

  it("draws a truthful relation when knowledge nodes share source evidence", () => {
    const nodes = [
      graphNode("decision", "decision"),
      graphNode("requirement", "requirement"),
      graphNode("prompt", "prompt"),
    ];
    const edges: ProjectContextGraphEdge[] = [
      {
        id: "decision-source",
        inferred: true,
        kind: "derived_from",
        source: "decision",
        target: "prompt",
      },
      {
        id: "requirement-source",
        inferred: true,
        kind: "derived_from",
        source: "requirement",
        target: "prompt",
      },
    ];

    const layout = buildContextGraphLayout(nodes, edges);

    expect(layout.edges).toHaveLength(1);
    expect(layout.edges[0]).toMatchObject({
      inferred: true,
      kind: "shared_evidence",
      source: "decision",
      target: "requirement",
    });
    expect(layout.edges[0].path.startsWith("M ")).toBe(true);
  });

  it("filters by knowledge type, review layer, and local text query", () => {
    const nodes = [
      graphNode("decision", "decision", {
        label: "Use output-only extraction",
      }),
      graphNode("requirement", "requirement", {
        label: "Keep source evidence",
        metadata: { review_state: "confirmed" },
      }),
      graphNode("idea", "brainstorm", {
        label: "Try a five-lane view",
        metadata: { status: "discarded" },
      }),
    ];
    const kinds = new Set<ProjectContextGraphNodeKind>([
      "decision",
      "requirement",
    ]);

    expect(
      buildContextGraphLayout(nodes, [], kinds, "all", "output").visibleNodes
        .map((node) => node.id),
    ).toEqual(["decision"]);
    expect(
      buildContextGraphLayout(nodes, [], kinds, "confirmed").visibleNodes.map(
        (node) => node.id,
      ),
    ).toEqual(["requirement"]);
  });

  it("maps discarded, superseded, and rejected knowledge to archive", () => {
    expect(
      knowledgeNodeLayer(
        graphNode("discarded", "brainstorm", {
          metadata: { status: "discarded" },
        }),
      ),
    ).toBe("archived");
    expect(
      knowledgeNodeLayer(
        graphNode("superseded", "decision", {
          metadata: { status: "superseded" },
        }),
      ),
    ).toBe("archived");
    expect(
      knowledgeNodeLayer(
        graphNode("confirmed", "requirement", {
          metadata: { review_state: "confirmed" },
        }),
      ),
    ).toBe("confirmed");
  });

  it("allows concise provenance metadata but never renders source content", () => {
    const metadata = visibleContextGraphMetadata({
      confidence: 0.91,
      content: "private source content",
      diff: "+ secret",
      patch: "@@ -1 +1 @@",
      random_internal_value: "hidden",
      review_state: "confirmed",
      source_event_ids: ["private-id"],
      status: "active",
    });

    expect(Object.fromEntries(metadata)).toEqual({
      confidence: 0.91,
      review_state: "confirmed",
      status: "active",
    });
  });

  it("keeps the map minimal and moves evidence, review, and history into details", () => {
    expect(componentSource).toContain('className="context-graph-plane"');
    expect(componentSource).toContain('className="context-graph-node-orbit"');
    expect(componentSource).toContain('className="context-graph-evidence-list"');
    expect(componentSource).toContain('className="context-graph-relationship-list"');
    expect(componentSource).toContain('className="context-graph-inspector-meta"');
    expect(componentSource).toContain("reviewProjectContextGraphNode");
    expect(componentSource).toContain("Intl.DateTimeFormat(localeTag");
    expect(componentSource).not.toContain("context-graph-node-summary");
  });

  it("applies a node review locally without refetching the whole graph", () => {
    const graph = {
      edges: [],
      facets: {},
      nodes: [graphNode("decision", "decision")],
      query: null,
      safety_notice: "Review generated knowledge.",
      truncated: false,
    };

    const reviewed = applyKnowledgeNodeReview(graph, {
      node_id: "decision",
      review_state: "confirmed",
      reviewed_at: "2026-07-25T12:00:00Z",
    });

    expect(reviewed.nodes[0].metadata).toEqual({
      evidence_type: "confirmed",
      review_state: "confirmed",
      reviewed_at: "2026-07-25T12:00:00Z",
    });
    expect(reviewed.edges).toBe(graph.edges);
  });
});
