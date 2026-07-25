import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type CSSProperties,
  type RefObject,
} from "react";
import {
  Archive,
  Check,
  CircleHelp,
  ExternalLink,
  GitBranch,
  Lightbulb,
  ListChecks,
  Network,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  type LucideProps,
} from "lucide-react";
import {
  fetchProjectContextGraph,
  reviewProjectContextGraphNode,
  type ProjectContextGraphEdge,
  type ProjectContextGraphEdgeKind,
  type ProjectContextGraphNode,
  type ProjectContextGraphNodeKind,
  type ProjectContextGraphNodeReviewAction,
  type ProjectContextGraphNodeReviewResponse,
  type ProjectContextGraphResponse,
} from "../../api/projects";
import {
  useI18n,
  type TranslationKey,
} from "../../i18n/I18nProvider";
import "./context-graph.css";

const CONTEXT_GRAPH_LIMIT = 40;
const KNOWLEDGE_CANVAS_WIDTH = 920;
const KNOWLEDGE_CANVAS_HEIGHT = 640;
const MAX_VISIBLE_KNOWLEDGE_NODES = 20;
const KNOWLEDGE_LAYER_CAPACITY: Record<KnowledgeLayer, number> = {
  pending: 6,
  confirmed: 10,
  archived: 4,
};

const KNOWLEDGE_NODE_KINDS = [
  "decision",
  "requirement",
  "brainstorm",
  "open_question",
] as const satisfies readonly ProjectContextGraphNodeKind[];

type KnowledgeNodeKind = (typeof KNOWLEDGE_NODE_KINDS)[number];
export type KnowledgeLayer = "pending" | "confirmed" | "archived";
type KnowledgeLayerFilter = "all" | KnowledgeLayer;
type KnowledgeVisualEdgeKind =
  | ProjectContextGraphEdgeKind
  | "shared_evidence"
  | "shared_memory";

type NodeDefinition = {
  icon: ComponentType<LucideProps>;
  labelKey: TranslationKey;
  mark: string;
};

type LayerDefinition = {
  descriptionKey: TranslationKey;
  icon: ComponentType<LucideProps>;
  labelKey: TranslationKey;
  layer: KnowledgeLayer;
};

const NODE_DEFINITIONS: Record<KnowledgeNodeKind, NodeDefinition> = {
  decision: {
    icon: GitBranch,
    labelKey: "contextGraph.decision",
    mark: "D",
  },
  requirement: {
    icon: ListChecks,
    labelKey: "contextGraph.requirement",
    mark: "R",
  },
  brainstorm: {
    icon: Lightbulb,
    labelKey: "contextGraph.brainstorm",
    mark: "I",
  },
  open_question: {
    icon: CircleHelp,
    labelKey: "contextGraph.openQuestion",
    mark: "?",
  },
};

const LAYER_DEFINITIONS: LayerDefinition[] = [
  {
    descriptionKey: "contextGraph.reviewRequired",
    icon: Sparkles,
    labelKey: "contextGraph.pending",
    layer: "pending",
  },
  {
    descriptionKey: "contextGraph.ownerReviewed",
    icon: Check,
    labelKey: "contextGraph.confirmed",
    layer: "confirmed",
  },
  {
    descriptionKey: "contextGraph.traceable",
    icon: Archive,
    labelKey: "contextGraph.archived",
    layer: "archived",
  },
];

const SAFE_METADATA_KEYS = new Set([
  "artifact_review_state",
  "confidence",
  "evidence_type",
  "review_state",
  "reviewed_at",
  "source_event_count",
  "status",
  "subtype",
]);
const SENSITIVE_METADATA_KEY_PATTERN =
  /content|diff|patch|prompt|response|secret|source_event|source_chunk/i;

type KnowledgeNodePosition = {
  layer: KnowledgeLayer;
  x: number;
  y: number;
};

export type KnowledgeGraphLayoutEdge = {
  id: string;
  inferred: boolean;
  kind: KnowledgeVisualEdgeKind;
  path: string;
  source: string;
  target: string;
};

export type ContextGraphLayout = {
  canvasHeight: number;
  edges: KnowledgeGraphLayoutEdge[];
  hiddenNodeCount: number;
  layers: Array<{
    layer: KnowledgeLayer;
    nodes: ProjectContextGraphNode[];
  }>;
  nodePositions: Record<string, KnowledgeNodePosition>;
  visibleNodes: ProjectContextGraphNode[];
};

type KnowledgeConnection = {
  direction: "incoming" | "outgoing";
  edge: KnowledgeGraphLayoutEdge;
  node: ProjectContextGraphNode;
};

function isKnowledgeNode(
  node: ProjectContextGraphNode,
): node is ProjectContextGraphNode & { kind: KnowledgeNodeKind } {
  return (KNOWLEDGE_NODE_KINDS as readonly string[]).includes(node.kind);
}

export function knowledgeNodeLayer(
  node: ProjectContextGraphNode,
): KnowledgeLayer {
  const status =
    typeof node.metadata.status === "string" ? node.metadata.status : null;
  const reviewState =
    typeof node.metadata.review_state === "string"
      ? node.metadata.review_state
      : null;

  if (
    reviewState === "rejected" ||
    status === "discarded" ||
    status === "superseded" ||
    status === "archived"
  ) {
    return "archived";
  }
  if (reviewState === "confirmed") {
    return "confirmed";
  }
  return "pending";
}

function contextGraphNodeTimestamp(node: ProjectContextGraphNode) {
  if (!node.occurred_at) {
    return Number.POSITIVE_INFINITY;
  }
  const timestamp = Date.parse(node.occurred_at);
  return Number.isNaN(timestamp) ? Number.POSITIVE_INFINITY : timestamp;
}

function compareKnowledgeNodes(
  left: ProjectContextGraphNode,
  right: ProjectContextGraphNode,
) {
  const leftTimestamp = contextGraphNodeTimestamp(left);
  const rightTimestamp = contextGraphNodeTimestamp(right);
  if (leftTimestamp !== rightTimestamp) {
    return rightTimestamp - leftTimestamp;
  }
  const kindDifference =
    KNOWLEDGE_NODE_KINDS.indexOf(left.kind as KnowledgeNodeKind) -
    KNOWLEDGE_NODE_KINDS.indexOf(right.kind as KnowledgeNodeKind);
  if (kindDifference !== 0) {
    return kindDifference;
  }
  return left.label.localeCompare(right.label);
}

function matchesKnowledgeQuery(
  node: ProjectContextGraphNode,
  normalizedQuery: string,
) {
  if (!normalizedQuery) {
    return true;
  }
  const searchable = [
    node.label,
    node.summary,
    node.metadata.subtype,
    node.metadata.status,
    node.metadata.review_state,
  ]
    .filter((value) => typeof value === "string")
    .join(" ")
    .toLocaleLowerCase();
  return normalizedQuery
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => searchable.includes(term));
}

function layerNodePosition(
  layer: KnowledgeLayer,
  index: number,
  count: number,
): KnowledgeNodePosition {
  if (layer === "pending") {
    const x = 235 + ((index + 0.5) / Math.max(count, 1)) * 510;
    return { layer, x, y: 122 + (index % 2) * 16 };
  }

  if (layer === "archived") {
    const x = 260 + ((index + 0.5) / Math.max(count, 1)) * 420;
    return { layer, x, y: 564 + (index % 2) * 10 };
  }

  const rowCount = count > 5 ? 2 : 1;
  const row = rowCount === 1 ? 0 : index % 2;
  const column = rowCount === 1 ? index : Math.floor(index / 2);
  const columns = Math.ceil(count / rowCount);
  const x = 170 + ((column + 0.5) / Math.max(columns, 1)) * 610;
  return { layer, x, y: rowCount === 1 ? 350 : 310 + row * 118 };
}

function knowledgeEdgePath(
  source: KnowledgeNodePosition,
  target: KnowledgeNodePosition,
) {
  const verticalDistance = Math.abs(target.y - source.y);
  const curve = Math.max(24, Math.min(82, verticalDistance * 0.42));
  const direction = target.y >= source.y ? 1 : -1;
  return [
    `M ${source.x} ${source.y}`,
    `C ${source.x} ${source.y + curve * direction},`,
    `${target.x} ${target.y - curve * direction},`,
    `${target.x} ${target.y}`,
  ].join(" ");
}

function pairKey(source: string, target: string) {
  return [source, target].sort().join("\u001f");
}

/**
 * Produces a bounded, knowledge-only visual projection.
 * Raw prompts, responses, files, and memory records remain available as detail
 * evidence but never become nodes on the map.
 */
export function buildContextGraphLayout(
  nodes: ProjectContextGraphNode[],
  edges: ProjectContextGraphEdge[],
  enabledKinds: ReadonlySet<ProjectContextGraphNodeKind> = new Set(
    KNOWLEDGE_NODE_KINDS,
  ),
  layerFilter: KnowledgeLayerFilter = "all",
  query = "",
): ContextGraphLayout {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const allKnowledgeNodes = nodes
    .filter(isKnowledgeNode)
    .filter((node) => enabledKinds.has(node.kind))
    .filter(
      (node) =>
        layerFilter === "all" || knowledgeNodeLayer(node) === layerFilter,
    )
    .filter((node) => matchesKnowledgeQuery(node, normalizedQuery))
    .sort(compareKnowledgeNodes);
  const layers = LAYER_DEFINITIONS.map(({ layer }) => ({
    layer,
    nodes: allKnowledgeNodes
      .filter((node) => knowledgeNodeLayer(node) === layer)
      .slice(0, KNOWLEDGE_LAYER_CAPACITY[layer]),
  }));
  const visibleNodes = layers
    .flatMap((layer) => layer.nodes)
    .slice(0, MAX_VISIBLE_KNOWLEDGE_NODES);
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const nodePositions: Record<string, KnowledgeNodePosition> = {};
  for (const layer of layers) {
    for (const [index, node] of layer.nodes.entries()) {
      nodePositions[node.id] = layerNodePosition(
        layer.layer,
        index,
        layer.nodes.length,
      );
    }
  }

  const visualEdges: KnowledgeGraphLayoutEdge[] = [];
  const usedPairs = new Set<string>();
  const appendEdge = (
    source: string,
    target: string,
    kind: KnowledgeVisualEdgeKind,
    inferred: boolean,
    id: string,
  ) => {
    const sourcePosition = nodePositions[source];
    const targetPosition = nodePositions[target];
    const key = pairKey(source, target);
    if (
      !sourcePosition ||
      !targetPosition ||
      source === target ||
      usedPairs.has(key)
    ) {
      return;
    }
    usedPairs.add(key);
    visualEdges.push({
      id,
      inferred,
      kind,
      path: knowledgeEdgePath(sourcePosition, targetPosition),
      source,
      target,
    });
  };

  for (const edge of edges) {
    if (visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)) {
      appendEdge(
        edge.source,
        edge.target,
        edge.kind,
        edge.inferred,
        edge.id,
      );
    }
  }

  const connectSharedTargets = (
    sourceEdges: ProjectContextGraphEdge[],
    kind: "shared_evidence" | "shared_memory",
  ) => {
    const sourceIdsByTarget = new Map<string, string[]>();
    for (const edge of sourceEdges) {
      if (!visibleNodeIds.has(edge.source)) {
        continue;
      }
      const sourceIds = sourceIdsByTarget.get(edge.target) ?? [];
      if (!sourceIds.includes(edge.source)) {
        sourceIds.push(edge.source);
      }
      sourceIdsByTarget.set(edge.target, sourceIds);
    }
    for (const [targetId, sourceIds] of sourceIdsByTarget) {
      const anchor = sourceIds[0];
      for (const sourceId of sourceIds.slice(1)) {
        appendEdge(
          anchor,
          sourceId,
          kind,
          true,
          `${kind}:${targetId}:${anchor}:${sourceId}`,
        );
      }
    }
  };

  connectSharedTargets(
    edges.filter((edge) => edge.kind === "derived_from"),
    "shared_evidence",
  );
  connectSharedTargets(
    edges.filter((edge) => edge.kind === "captured_in"),
    "shared_memory",
  );

  return {
    canvasHeight: KNOWLEDGE_CANVAS_HEIGHT,
    edges: visualEdges,
    hiddenNodeCount: Math.max(
      allKnowledgeNodes.length - visibleNodes.length,
      0,
    ),
    layers,
    nodePositions,
    visibleNodes,
  };
}

function isDisplayableMetadataValue(value: unknown) {
  if (["string", "number", "boolean"].includes(typeof value)) {
    return true;
  }
  return (
    Array.isArray(value) &&
    value.length <= 12 &&
    value.every((item) =>
      ["string", "number", "boolean"].includes(typeof item),
    )
  );
}

export function visibleContextGraphMetadata(
  metadata: Record<string, unknown>,
) {
  return Object.entries(metadata).filter(
    ([key, value]) =>
      SAFE_METADATA_KEYS.has(key) &&
      !SENSITIVE_METADATA_KEY_PATTERN.test(key) &&
      isDisplayableMetadataValue(value),
  );
}

export function applyKnowledgeNodeReview(
  graph: ProjectContextGraphResponse,
  review: ProjectContextGraphNodeReviewResponse,
): ProjectContextGraphResponse {
  return {
    ...graph,
    nodes: graph.nodes.map((node) =>
      node.id === review.node_id
        ? {
            ...node,
            metadata: {
              ...node.metadata,
              evidence_type:
                review.review_state === "confirmed"
                  ? "confirmed"
                  : "inferred",
              review_state: review.review_state,
              reviewed_at: review.reviewed_at,
            },
          }
        : node,
    ),
  };
}

function formatMetadataValue(value: unknown) {
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  return String(value);
}

function formatOccurredAt(value: string | null, localeTag: string) {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return Intl.DateTimeFormat(localeTag, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function knowledgeNodeReviewState(node: ProjectContextGraphNode) {
  return node.metadata.review_state === "confirmed" ||
    node.metadata.review_state === "rejected"
    ? node.metadata.review_state
    : "unreviewed";
}

function edgeTranslationKey(
  edge: KnowledgeGraphLayoutEdge,
): TranslationKey {
  switch (edge.kind) {
    case "answered_by":
      return "contextGraph.relationAnsweredBy";
    case "changed":
      return "contextGraph.relationChanged";
    case "captured_in":
      return "contextGraph.relationCapturedIn";
    case "derived_from":
      return "contextGraph.relationDerivedFrom";
    case "references":
      return "contextGraph.relationReferences";
    case "supersedes":
      return "contextGraph.relationSupersedes";
    case "shared_evidence":
      return "contextGraph.sharedOutput";
    case "shared_memory":
      return "contextGraph.capturedTogether";
  }
}

function KnowledgeNodeButton({
  isMuted,
  isRelated,
  isSelected,
  node,
  onSelect,
  position,
}: {
  isMuted: boolean;
  isRelated: boolean;
  isSelected: boolean;
  node: ProjectContextGraphNode & { kind: KnowledgeNodeKind };
  onSelect: (nodeId: string) => void;
  position: KnowledgeNodePosition;
}) {
  const { t } = useI18n();
  const definition = NODE_DEFINITIONS[node.kind];
  const layer = knowledgeNodeLayer(node);
  return (
    <button
      aria-label={`${t(definition.labelKey)}: ${node.label}`}
      aria-pressed={isSelected}
      className="context-graph-node"
      data-kind={node.kind}
      data-layer={layer}
      data-muted={isMuted || undefined}
      data-related={isRelated || undefined}
      data-selected={isSelected || undefined}
      onClick={() => onSelect(node.id)}
      style={
        {
          "--context-node-x": `${(position.x / KNOWLEDGE_CANVAS_WIDTH) * 100}%`,
          "--context-node-y": `${position.y}px`,
        } as CSSProperties
      }
      type="button"
    >
      <span className="context-graph-node-orbit" aria-hidden="true">
        <span>{definition.mark}</span>
      </span>
      <strong>{node.label}</strong>
    </button>
  );
}

function ContextGraphDesktop({
  layout,
  relatedNodeIds,
  selectedNodeId,
  onSelectNode,
}: {
  layout: ContextGraphLayout;
  relatedNodeIds: ReadonlySet<string>;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="context-graph-desktop" data-testid="context-graph-desktop">
      <div className="context-graph-map-caption">
        <span>{t("contextGraph.layeredView")}</span>
        <small>{t("contextGraph.mapHint")}</small>
      </div>
      <div className="context-graph-canvas">
        {LAYER_DEFINITIONS.map((definition) => (
          <div
            className="context-graph-plane"
            data-layer={definition.layer}
            key={definition.layer}
          >
            <span>
              {t(definition.labelKey)}
              <small>{t(definition.descriptionKey)}</small>
            </span>
          </div>
        ))}
        <svg
          aria-hidden="true"
          className="context-graph-edges"
          focusable="false"
          preserveAspectRatio="none"
          viewBox={`0 0 ${KNOWLEDGE_CANVAS_WIDTH} ${KNOWLEDGE_CANVAS_HEIGHT}`}
        >
          {layout.edges.map((edge) => (
            <path
              className="context-graph-edge"
              d={edge.path}
              data-active={
                selectedNodeId !== null &&
                (edge.source === selectedNodeId ||
                  edge.target === selectedNodeId)
                  ? "true"
                  : undefined
              }
              data-inferred={edge.inferred || undefined}
              data-kind={edge.kind}
              key={edge.id}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {layout.visibleNodes.filter(isKnowledgeNode).map((node) => {
          const isSelected = node.id === selectedNodeId;
          const isRelated = relatedNodeIds.has(node.id);
          const isMuted =
            selectedNodeId !== null && !isSelected && !isRelated;
          return (
            <KnowledgeNodeButton
              isMuted={isMuted}
              isRelated={isRelated}
              isSelected={isSelected}
              key={node.id}
              node={node}
              onSelect={onSelectNode}
              position={layout.nodePositions[node.id]}
            />
          );
        })}
      </div>
    </div>
  );
}

function sourceEvidenceNodes(
  graph: ProjectContextGraphResponse,
  nodeId: string,
) {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const evidenceIds = new Set<string>();
  const capturedMemoryIds = new Set<string>();
  for (const edge of graph.edges) {
    if (edge.source !== nodeId) {
      continue;
    }
    if (edge.kind === "derived_from") {
      evidenceIds.add(edge.target);
    }
    if (edge.kind === "captured_in") {
      capturedMemoryIds.add(edge.target);
    }
  }

  for (const edge of graph.edges) {
    if (edge.kind === "answered_by" && evidenceIds.has(edge.source)) {
      evidenceIds.add(edge.target);
    }
  }

  const evidence = [...evidenceIds]
    .map((id) => nodesById.get(id))
    .filter((node): node is ProjectContextGraphNode => Boolean(node))
    .sort((left, right) => {
      const priority: Record<ProjectContextGraphNodeKind, number> = {
        response: 0,
        memory: 1,
        prompt: 2,
        file: 3,
        decision: 4,
        requirement: 4,
        brainstorm: 4,
        open_question: 4,
      };
      return priority[left.kind] - priority[right.kind];
    });
  const memories = [...capturedMemoryIds]
    .map((id) => nodesById.get(id))
    .filter((node): node is ProjectContextGraphNode => Boolean(node));
  return [...evidence, ...memories].filter(
    (node, index, values) =>
      values.findIndex((candidate) => candidate.id === node.id) === index,
  );
}

function evidenceKindLabel(
  node: ProjectContextGraphNode,
  t: (key: TranslationKey, values?: Record<string, string | number>) => string,
) {
  if (node.kind === "response") {
    return t("contextGraph.sourceOutput");
  }
  if (node.kind === "memory") {
    return t("contextGraph.generatedMemory");
  }
  return t("contextGraph.sourceEvidence");
}

function ContextNodeInspector({
  connections,
  evidence,
  inspectorRef,
  isReviewing,
  node,
  onOpenSession,
  onReviewNode,
  onSelectNode,
}: {
  connections: KnowledgeConnection[];
  evidence: ProjectContextGraphNode[];
  inspectorRef: RefObject<HTMLElement | null>;
  isReviewing: boolean;
  node: ProjectContextGraphNode | null;
  onOpenSession?: (sessionId: string) => void;
  onReviewNode: (
    nodeId: string,
    action: ProjectContextGraphNodeReviewAction,
  ) => void;
  onSelectNode: (nodeId: string) => void;
}) {
  const { localeTag, t } = useI18n();
  if (!node || !isKnowledgeNode(node)) {
    return (
      <aside
        className="context-graph-inspector context-graph-inspector-empty"
        ref={inspectorRef}
        tabIndex={-1}
      >
        <Network aria-hidden="true" size={22} strokeWidth={1.4} />
        <h3>{t("contextGraph.selectNode")}</h3>
        <p>{t("contextGraph.selectNodeDescription")}</p>
      </aside>
    );
  }

  const definition = NODE_DEFINITIONS[node.kind];
  const occurredAt = formatOccurredAt(node.occurred_at, localeTag);
  const metadata = visibleContextGraphMetadata(node.metadata).filter(
    ([key]) => !["review_state", "subtype"].includes(key),
  );
  const reviewState = knowledgeNodeReviewState(node);
  const layer = knowledgeNodeLayer(node);
  const openableEvidence = evidence.find((item) => item.session_id);
  const relatedNodeCount = connections.length;

  return (
    <aside
      aria-label={t("contextGraph.nodeDetails", { label: node.label })}
      className="context-graph-inspector"
      data-layer={layer}
      ref={inspectorRef}
      tabIndex={-1}
    >
      <header className="context-graph-inspector-header">
        <span>
          {t(definition.labelKey)} · {t(`contextGraph.${layer}`)}
        </span>
        <h3>{node.label}</h3>
        <div className="context-graph-inspector-badges">
          <span data-state={layer}>{t(`contextGraph.${layer}`)}</span>
          <span data-agent={node.agent_visible || undefined}>
            {node.agent_visible
              ? t("contextGraph.available")
              : t("contextGraph.notApproved")}
          </span>
        </div>
      </header>

      <section className="context-graph-detail-section">
        <h4>{t("contextGraph.whyItExists")}</h4>
        <p>
          {node.summary ??
            t("contextGraph.noNodeSummary")}
        </p>
      </section>

      <section className="context-graph-detail-section">
        <div className="context-graph-section-heading">
          <h4>{t("contextGraph.sourceOutput")}</h4>
          <span>{evidence.length}</span>
        </div>
        {evidence.length > 0 ? (
          <div className="context-graph-evidence-list">
            {evidence.slice(0, 3).map((source) => (
              <article key={source.id}>
                <span>{evidenceKindLabel(source, t)}</span>
                <strong>{source.label}</strong>
                {source.summary ? <p>{source.summary}</p> : null}
                {source.session_id && onOpenSession ? (
                  <button
                    onClick={() => onOpenSession(source.session_id!)}
                    type="button"
                  >
                    {t("contextGraph.openSourceSession")}
                    <ExternalLink
                      aria-hidden="true"
                      size={13}
                      strokeWidth={1.7}
                    />
                  </button>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <p className="context-graph-detail-empty">
            {t("contextGraph.sourceOutputEmpty")}
          </p>
        )}
      </section>

      <section className="context-graph-detail-section">
        <div className="context-graph-section-heading">
          <h4>{t("contextGraph.relationships")}</h4>
          <span>{relatedNodeCount}</span>
        </div>
        {connections.length > 0 ? (
          <ul className="context-graph-relationship-list">
            {connections.slice(0, 8).map((connection) => (
              <li key={`${connection.edge.id}:${connection.node.id}`}>
                <button
                  onClick={() => onSelectNode(connection.node.id)}
                  type="button"
                >
                  <span data-kind={connection.node.kind}>
                    {NODE_DEFINITIONS[
                      connection.node.kind as KnowledgeNodeKind
                    ]?.mark ?? "·"}
                  </span>
                  <span>
                    <small>{t(edgeTranslationKey(connection.edge))}</small>
                    <strong>{connection.node.label}</strong>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="context-graph-detail-empty">
            {t("contextGraph.noConnections")}
          </p>
        )}
      </section>

      <section className="context-graph-detail-section">
        <h4>{t("contextGraph.history")}</h4>
        <dl className="context-graph-inspector-meta">
          {occurredAt ? (
            <div>
              <dt>{t("contextGraph.extractedFromOutput")}</dt>
              <dd>
                <time dateTime={node.occurred_at ?? undefined}>
                  {occurredAt}
                </time>
              </dd>
            </div>
          ) : null}
          <div>
            <dt>{t("contextGraph.reviewState")}</dt>
            <dd>{t(`contextGraph.${layer}`)}</dd>
          </div>
          {metadata.map(([key, value]) => (
            <div key={key}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd>{formatMetadataValue(value)}</dd>
            </div>
          ))}
        </dl>
      </section>

      <div className="context-graph-inspector-actions">
        <button
          aria-pressed={reviewState === "confirmed"}
          className="context-graph-confirm-action"
          disabled={isReviewing}
          onClick={() =>
            onReviewNode(
              node.id,
              reviewState === "confirmed" ? "reset" : "confirm",
            )
          }
          type="button"
        >
          <ThumbsUp aria-hidden="true" size={14} strokeWidth={1.7} />
          {isReviewing
            ? t("contextGraph.reviewing")
            : t("contextGraph.confirmKnowledge")}
        </button>
        <button
          aria-pressed={reviewState === "rejected"}
          className="context-graph-exclude-action"
          disabled={isReviewing}
          onClick={() =>
            onReviewNode(
              node.id,
              reviewState === "rejected" ? "reset" : "reject",
            )
          }
          type="button"
        >
          <ThumbsDown aria-hidden="true" size={14} strokeWidth={1.7} />
          {t("contextGraph.rejectKnowledge")}
        </button>
        {openableEvidence?.session_id && onOpenSession ? (
          <button
            className="context-graph-source-action"
            onClick={() => onOpenSession(openableEvidence.session_id!)}
            type="button"
          >
            {t("contextGraph.openSourceOutput")}
            <ExternalLink aria-hidden="true" size={14} strokeWidth={1.7} />
          </button>
        ) : null}
      </div>
    </aside>
  );
}

export function ContextGraphPanel({
  projectId,
  onOpenSession,
}: {
  projectId: string;
  onOpenSession?: (sessionId: string) => void;
}) {
  const { t } = useI18n();
  const [searchState, setSearchState] = useState({ projectId, value: "" });
  const searchInput =
    searchState.projectId === projectId ? searchState.value : "";
  const [graphState, setGraphState] = useState<{
    projectId: string;
    response: ProjectContextGraphResponse;
  } | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [requestVersion, setRequestVersion] = useState(0);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [reviewingNodeId, setReviewingNodeId] = useState<string | null>(null);
  const [layerFilter, setLayerFilter] =
    useState<KnowledgeLayerFilter>("all");
  const [enabledKinds, setEnabledKinds] = useState<
    Set<ProjectContextGraphNodeKind>
  >(() => new Set(KNOWLEDGE_NODE_KINDS));
  const inspectorRef = useRef<HTMLElement | null>(null);
  const graph =
    graphState?.projectId === projectId ? graphState.response : null;

  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setErrorMessage(null);
    void fetchProjectContextGraph(projectId, {
      limit: CONTEXT_GRAPH_LIMIT,
      signal: controller.signal,
    })
      .then((response) => {
        if (!controller.signal.aborted) {
          setGraphState({ projectId, response });
        }
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (!controller.signal.aborted) {
          setErrorMessage(
            error instanceof Error
              ? error.message
              : t("contextGraph.requestFailed"),
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      });
    return () => controller.abort();
  }, [projectId, requestVersion, t]);

  const knowledgeNodes = useMemo(
    () => graph?.nodes.filter(isKnowledgeNode) ?? [],
    [graph?.nodes],
  );
  const layerCounts = useMemo(
    () =>
      knowledgeNodes.reduce(
        (counts, node) => {
          counts[knowledgeNodeLayer(node)] += 1;
          return counts;
        },
        { archived: 0, confirmed: 0, pending: 0 },
      ),
    [knowledgeNodes],
  );
  const layout = useMemo(
    () =>
      buildContextGraphLayout(
        graph?.nodes ?? [],
        graph?.edges ?? [],
        enabledKinds,
        layerFilter,
        searchInput,
      ),
    [
      enabledKinds,
      graph?.edges,
      graph?.nodes,
      layerFilter,
      searchInput,
    ],
  );
  const selectedNode =
    knowledgeNodes.find((node) => node.id === selectedNodeId) ?? null;
  const visibleNodesById = useMemo(
    () => new Map(layout.visibleNodes.map((node) => [node.id, node])),
    [layout.visibleNodes],
  );
  const connections = useMemo<KnowledgeConnection[]>(() => {
    if (!selectedNodeId) {
      return [];
    }
    return layout.edges.flatMap<KnowledgeConnection>(
      (edge): KnowledgeConnection[] => {
      if (edge.source === selectedNodeId) {
        const node = visibleNodesById.get(edge.target);
        return node
          ? [{ direction: "outgoing" as const, edge, node }]
          : [];
      }
      if (edge.target === selectedNodeId) {
        const node = visibleNodesById.get(edge.source);
        return node
          ? [{ direction: "incoming" as const, edge, node }]
          : [];
      }
      return [];
      },
    );
  }, [layout.edges, selectedNodeId, visibleNodesById]);
  const relatedNodeIds = useMemo(() => {
    const ids = new Set(connections.map((connection) => connection.node.id));
    if (selectedNodeId) {
      ids.add(selectedNodeId);
    }
    return ids;
  }, [connections, selectedNodeId]);
  const evidence = useMemo(
    () =>
      graph && selectedNodeId
        ? sourceEvidenceNodes(graph, selectedNodeId)
        : [],
    [graph, selectedNodeId],
  );

  useEffect(() => {
    setSelectedNodeId((currentNodeId) => {
      if (currentNodeId && visibleNodesById.has(currentNodeId)) {
        return currentNodeId;
      }
      const preferredNode =
        layout.visibleNodes.find(
          (node) => knowledgeNodeLayer(node) === "confirmed",
        ) ??
        layout.visibleNodes[0] ??
        null;
      return preferredNode?.id ?? null;
    });
  }, [layout.visibleNodes, visibleNodesById]);

  const toggleKind = (kind: KnowledgeNodeKind) => {
    setEnabledKinds((currentKinds) => {
      const nextKinds = new Set(currentKinds);
      if (nextKinds.has(kind)) {
        if (nextKinds.size === 1) {
          return currentKinds;
        }
        nextKinds.delete(kind);
      } else {
        nextKinds.add(kind);
      }
      return nextKinds;
    });
  };

  const selectGraphNode = (nodeId: string) => {
    setSelectedNodeId(nodeId);
    if (
      typeof window.matchMedia !== "function" ||
      !window.matchMedia("(max-width: 900px)").matches
    ) {
      return;
    }
    window.requestAnimationFrame(() => {
      const inspector = inspectorRef.current;
      if (!inspector) {
        return;
      }
      const reduceMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      inspector.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "start",
      });
      inspector.focus({ preventScroll: true });
    });
  };

  const reviewKnowledgeNode = async (
    nodeId: string,
    action: ProjectContextGraphNodeReviewAction,
  ) => {
    setReviewingNodeId(nodeId);
    setErrorMessage(null);
    try {
      const review = await reviewProjectContextGraphNode(
        projectId,
        nodeId,
        action,
      );
      setGraphState((current) => {
        if (!current || current.projectId !== projectId) {
          return current;
        }
        return {
          ...current,
          response: applyKnowledgeNodeReview(current.response, review),
        };
      });
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : t("contextGraph.reviewFailed"),
      );
    } finally {
      setReviewingNodeId(null);
    }
  };

  return (
    <section
      aria-labelledby="context-graph-title"
      className="context-graph-panel"
    >
      <header className="context-graph-heading">
        <div>
          <span className="context-graph-eyebrow">
            {t("contextGraph.eyebrow")}
          </span>
          <h2 id="context-graph-title">{t("contextGraph.title")}</h2>
          <p>{t("contextGraph.description")}</p>
        </div>
        <dl aria-label={t("contextGraph.graphSummary")}>
          {LAYER_DEFINITIONS.map((definition) => (
            <div data-layer={definition.layer} key={definition.layer}>
              <dd>{layerCounts[definition.layer]}</dd>
              <dt>{t(definition.labelKey)}</dt>
            </div>
          ))}
        </dl>
      </header>

      <div className="context-graph-toolbar">
        <label className="context-graph-search">
          <Search aria-hidden="true" size={16} strokeWidth={1.7} />
          <input
            aria-label={t("contextGraph.searchLabel")}
            autoComplete="off"
            onChange={(event) =>
              setSearchState({ projectId, value: event.target.value })
            }
            placeholder={t("contextGraph.searchPlaceholder")}
            spellCheck={false}
            type="search"
            value={searchInput}
          />
        </label>
        <div
          aria-label={t("contextGraph.nodeTypes")}
          className="context-graph-kind-filters"
          role="group"
        >
          {KNOWLEDGE_NODE_KINDS.map((kind) => {
            const definition = NODE_DEFINITIONS[kind];
            const isActive = enabledKinds.has(kind);
            const count = knowledgeNodes.filter(
              (node) => node.kind === kind,
            ).length;
            return (
              <button
                aria-pressed={isActive}
                data-active={isActive || undefined}
                data-kind={kind}
                key={kind}
                onClick={() => toggleKind(kind)}
                type="button"
              >
                <span />
                {t(definition.labelKey)}
                <small>{count}</small>
              </button>
            );
          })}
        </div>
        <div
          aria-label={t("contextGraph.layers")}
          className="context-graph-layer-filters"
          role="group"
        >
          {(["all", "pending", "confirmed", "archived"] as const).map(
            (layer) => (
              <button
                aria-pressed={layerFilter === layer}
                data-active={layerFilter === layer || undefined}
                key={layer}
                onClick={() => setLayerFilter(layer)}
                type="button"
              >
                {t(`contextGraph.${layer}`)}
              </button>
            ),
          )}
        </div>
        <span className="context-graph-result-status" role="status">
          {t("contextGraph.outputsOnly", {
            count: layout.visibleNodes.length,
          })}
        </span>
      </div>

      {graph?.safety_notice ? (
        <div className="context-graph-safety-note">
          <ShieldCheck aria-hidden="true" size={15} strokeWidth={1.7} />
          <span>{graph.safety_notice}</span>
        </div>
      ) : null}

      {graph?.truncated || layout.hiddenNodeCount > 0 ? (
        <div className="context-graph-truncated" role="status">
          {t("contextGraph.truncated")}
        </div>
      ) : null}

      {errorMessage && graph ? (
        <div className="context-graph-inline-error" role="alert">
          <span>{errorMessage}</span>
          <button
            onClick={() => setRequestVersion((version) => version + 1)}
            type="button"
          >
            {t("common.retry")}
          </button>
        </div>
      ) : null}

      {!graph && isLoading ? (
        <div
          aria-label={t("contextGraph.loading")}
          className="context-graph-loading"
          role="status"
        >
          <span />
          <span />
          <span />
        </div>
      ) : !graph && errorMessage ? (
        <div className="context-graph-error" role="alert">
          <RefreshCw aria-hidden="true" size={22} strokeWidth={1.5} />
          <h3>{t("contextGraph.loadFailedTitle")}</h3>
          <p>{errorMessage}</p>
          <button
            onClick={() => setRequestVersion((version) => version + 1)}
            type="button"
          >
            {t("contextGraph.tryAgain")}
          </button>
        </div>
      ) : layout.visibleNodes.length === 0 ? (
        <div className="context-graph-empty" role="status">
          <Network aria-hidden="true" size={22} strokeWidth={1.5} />
          <h3>{t("contextGraph.emptyTitle")}</h3>
          <p>{t("contextGraph.emptyDescription")}</p>
        </div>
      ) : (
        <div className="context-graph-explorer">
          <div className="context-graph-map">
            <ContextGraphDesktop
              layout={layout}
              onSelectNode={selectGraphNode}
              relatedNodeIds={relatedNodeIds}
              selectedNodeId={selectedNodeId}
            />
          </div>
          <ContextNodeInspector
            connections={connections}
            evidence={evidence}
            inspectorRef={inspectorRef}
            isReviewing={reviewingNodeId === selectedNode?.id}
            node={selectedNode}
            onOpenSession={onOpenSession}
            onReviewNode={(nodeId, action) => {
              void reviewKnowledgeNode(nodeId, action);
            }}
            onSelectNode={selectGraphNode}
          />
        </div>
      )}
    </section>
  );
}
