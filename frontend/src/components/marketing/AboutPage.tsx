import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ArrowRight,
  Check,
  CircleCheck,
  GitBranch,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { MarketingShell } from "./MarketingShell";
import "./about.css";

const contextTaxMoments = [
  {
    number: "01",
    title: "New chat",
    description: "Explain the architecture and current goal again.",
  },
  {
    number: "02",
    title: "Different AI",
    description: "Lose the decisions that made the current code make sense.",
  },
  {
    number: "03",
    title: "Return later",
    description: "Reconstruct where the work stopped before moving again.",
  },
  {
    number: "04",
    title: "New teammate",
    description: "Repeat project history that should already be available.",
  },
] as const;

export const aboutStorySteps = [
  {
    number: "01",
    eyebrow: "CAPTURE",
    title: "Work normally.",
    description:
      "Keep using Codex CLI or Claude Code. Promty observes completed work only in repositories you explicitly connect.",
    detail: "Repository-scoped collection",
    image: "/marketing/promty-product-overview.png",
    imageAlt: "Promty project overview showing recent AI work and project statistics",
  },
  {
    number: "02",
    eyebrow: "REVIEW",
    title: "Keep what matters.",
    description:
      "Turn outcomes, decisions, rejected paths, and next steps into a compact draft you can inspect before it becomes memory.",
    detail: "Human-controlled Project Memory",
    image: "/marketing/promty-product-memory.png",
    imageAlt: "Promty Project Memory review with a verified summary and sources",
  },
  {
    number: "03",
    eyebrow: "CONTINUE",
    title: "Move with context.",
    description:
      "Give the next person or read-only agent the latest approved direction instead of another transcript to interpret.",
    detail: "Tool-independent handoff",
    image: "/marketing/promty-product-community.png",
    imageAlt: "Promty community view showing shared projects and their AI context",
  },
] as const;

const principles = [
  {
    icon: GitBranch,
    eyebrow: "PROJECT-SCOPED",
    title: "Only the repositories you choose.",
    description:
      "Promty starts where you install it. Other projects and folders stay outside the collection boundary.",
  },
  {
    icon: CircleCheck,
    eyebrow: "REVIEWABLE",
    title: "A draft before a decision.",
    description:
      "Inspect the source, remove unnecessary material, and approve what future collaborators can use.",
  },
  {
    icon: ShieldCheck,
    eyebrow: "READ-ONLY",
    title: "Context without write access.",
    description:
      "The CLI and MCP bridge can retrieve approved memory without modifying your project or its history.",
  },
] as const;

const reviewItems = [
  {
    key: "decision",
    type: "DECISION",
    text: "Require explicit review before Project Memory is approved.",
  },
  {
    key: "next-step",
    type: "NEXT STEP",
    text: "Finish the pending account settings experience.",
  },
] as const;

function usePrefersReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return reducedMotion;
}

function ContinuityCard() {
  const reducedMotion = usePrefersReducedMotion();
  const cardRef = useRef<HTMLDivElement>(null);

  function updateTilt(event: ReactPointerEvent<HTMLDivElement>) {
    if (reducedMotion || event.pointerType !== "mouse" || !cardRef.current) return;
    const bounds = cardRef.current.getBoundingClientRect();
    const horizontal = (event.clientX - bounds.left) / bounds.width - 0.5;
    const vertical = (event.clientY - bounds.top) / bounds.height - 0.5;
    cardRef.current.style.setProperty("--about-tilt-x", `${vertical * -2.2}deg`);
    cardRef.current.style.setProperty("--about-tilt-y", `${horizontal * 3}deg`);
    cardRef.current.style.setProperty("--about-glow-x", `${(horizontal + 0.5) * 100}%`);
    cardRef.current.style.setProperty("--about-glow-y", `${(vertical + 0.5) * 100}%`);
  }

  function resetTilt() {
    cardRef.current?.style.removeProperty("--about-tilt-x");
    cardRef.current?.style.removeProperty("--about-tilt-y");
    cardRef.current?.style.removeProperty("--about-glow-x");
    cardRef.current?.style.removeProperty("--about-glow-y");
  }

  return (
    <div
      className="about-continuity-card"
      data-marketing-reveal
      onPointerLeave={resetTilt}
      onPointerMove={updateTilt}
      ref={cardRef}
    >
      <div className="about-continuity-shine" aria-hidden="true" />
      <header>
        <span>NEW SESSION / promty</span>
        <strong><i /> CONTEXT READY</strong>
      </header>
      <div className="about-continuity-body">
        <span className="about-eyebrow">CONTINUE FROM THE LAST DECISION</span>
        <h2>Ready to continue the authentication refactor.</h2>
        <dl>
          <div>
            <dt>CURRENT GOAL</dt>
            <dd>Complete account deletion safely.</dd>
          </div>
          <div>
            <dt>LAST DECISION</dt>
            <dd>Require explicit review before memory is saved.</dd>
          </div>
          <div>
            <dt>NEXT STEP</dt>
            <dd>Finish the pending settings UX.</dd>
          </div>
        </dl>
      </div>
      <footer>NO PROJECT RE-EXPLANATION REQUIRED</footer>
    </div>
  );
}

function ProductStory() {
  const reducedMotion = usePrefersReducedMotion();
  const storyRef = useRef<HTMLDivElement>(null);
  const [activeStep, setActiveStep] = useState(0);

  useEffect(() => {
    const root = storyRef.current;
    if (reducedMotion || !root || !("IntersectionObserver" in window)) return undefined;
    const steps = [...root.querySelectorAll<HTMLElement>("[data-about-story-step]")];
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (!visible) return;
        setActiveStep(Number((visible.target as HTMLElement).dataset.aboutStoryStep));
      },
      { rootMargin: "-30% 0px -42%", threshold: [0, 0.35, 0.7] },
    );
    steps.forEach((step) => observer.observe(step));
    return () => observer.disconnect();
  }, [reducedMotion]);

  return (
    <div className="about-story-layout" ref={storyRef}>
      <div className="about-story-steps">
        {aboutStorySteps.map((step, index) => (
          <button
            aria-pressed={activeStep === index}
            className={activeStep === index ? "is-active" : undefined}
            data-about-story-step={index}
            key={step.number}
            onClick={() => setActiveStep(index)}
            onFocus={() => setActiveStep(index)}
            type="button"
          >
            <span>{step.number} / {step.eyebrow}</span>
            <h3>{step.title}</h3>
            <p>{step.description}</p>
            <strong><Check aria-hidden="true" size={15} /> {step.detail}</strong>
            <img
              alt={step.imageAlt}
              className="about-story-step-preview"
              loading={index === 0 ? "eager" : "lazy"}
              src={step.image}
            />
          </button>
        ))}
      </div>

      <div className="about-story-visual" data-marketing-reveal>
        <div className="about-product-window">
          <div className="about-product-window-bar">
            <span><i /><i /><i /></span>
            <strong>promty.org/app</strong>
            <small>{String(activeStep + 1).padStart(2, "0")} / 03</small>
          </div>
          <div className="about-product-stage">
            {aboutStorySteps.map((step, index) => (
              <figure
                aria-hidden={activeStep !== index}
                className={activeStep === index ? "is-active" : undefined}
                key={step.number}
              >
                <img
                  alt={step.imageAlt}
                  loading={index === 0 ? "eager" : "lazy"}
                  src={step.image}
                />
                <figcaption>
                  <span>{step.eyebrow}</span>
                  <strong>{step.detail}</strong>
                </figcaption>
              </figure>
            ))}
          </div>
          <div className="about-product-progress" aria-hidden="true">
            {aboutStorySteps.map((step, index) => (
              <i className={activeStep === index ? "is-active" : undefined} key={step.number} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ReviewDemo() {
  const [includedItems, setIncludedItems] = useState(() =>
    new Set(reviewItems.map((item) => item.key)),
  );

  function toggleItem(key: (typeof reviewItems)[number]["key"]) {
    setIncludedItems((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="about-review-demo" data-marketing-reveal>
      <header>
        <div>
          <span>PENDING MEMORY</span>
          <strong>account-settings</strong>
        </div>
        <small aria-live="polite">{includedItems.size} INCLUDED</small>
      </header>
      <div className="about-review-items">
        {reviewItems.map((item) => {
          const isIncluded = includedItems.has(item.key);
          return (
            <button
              aria-pressed={isIncluded}
              className={isIncluded ? "is-included" : undefined}
              key={item.key}
              onClick={() => toggleItem(item.key)}
              type="button"
            >
              <span>{item.type}</span>
              <small>{isIncluded ? "INCLUDED" : "EXCLUDED"}</small>
              <p>{item.text}</p>
              <i aria-hidden="true">{isIncluded ? <Check size={14} /> : null}</i>
            </button>
          );
        })}
      </div>
      <div className="about-review-warning">
        <ShieldCheck aria-hidden="true" size={17} />
        <div>
          <strong>Check before approving</strong>
          <span>Remove passwords, tokens, and sensitive information.</span>
        </div>
      </div>
      <footer>
        <span>Only reviewed memory becomes available to agents.</span>
        <a href="/app">Open review queue <ArrowRight aria-hidden="true" size={15} /></a>
      </footer>
    </div>
  );
}

export function AboutPage() {
  return (
    <MarketingShell appearance="figma" current="about">
      <div className="about-page">
        <section className="about-hero">
          <div className="about-ambient about-ambient-one" aria-hidden="true" />
          <div className="about-ambient about-ambient-two" aria-hidden="true" />
          <div className="about-hero-copy" data-marketing-reveal>
            <span className="about-eyebrow">PROJECT MEMORY FOR CONTINUOUS AI WORK</span>
            <h1>Explain it once.<br /><span>Keep moving with any AI.</span></h1>
            <p>
              Promty remembers the decisions, failed paths, and next steps that
              matter—so every new session can start ready.
            </p>
            <div className="about-actions">
              <a className="about-button is-primary" href="/app">
                Connect a project <ArrowRight aria-hidden="true" size={17} />
              </a>
              <a className="about-button is-secondary" href="#how-it-works">
                See how it works
              </a>
            </div>
            <span className="about-tool-line">CODEX CLI · CLAUDE CODE · GITHUB</span>
          </div>
          <ContinuityCard />
        </section>

        <section className="about-context-tax">
          <div className="about-section-intro" data-marketing-reveal>
            <span className="about-eyebrow">THE DAILY CONTEXT TAX</span>
            <h2>You should not have to onboard your AI every morning.</h2>
            <p>
              The interruption is not one big failure. It is the same small
              reconstruction repeated across sessions, tool switches, and handoffs.
            </p>
          </div>
          <div className="about-moment-grid" data-marketing-reveal>
            {contextTaxMoments.map((moment) => (
              <article key={moment.number}>
                <span>{moment.number}</span>
                <h3>{moment.title}</h3>
                <p>{moment.description}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="about-story" id="how-it-works">
          <div className="about-story-heading" data-marketing-reveal>
            <span className="about-eyebrow">HOW PROMTY WORKS</span>
            <h2>Less repetition.<br />More continuity.</h2>
            <p>
              Promty keeps the useful reasoning available without asking you to
              change the tools or workflow that already work.
            </p>
          </div>
          <ProductStory />
        </section>

        <section className="about-principles">
          <div className="about-section-intro" data-marketing-reveal>
            <span className="about-eyebrow">BUILT FOR TRUST</span>
            <h2>Useful context stays under your control.</h2>
            <p>
              Continuity only works when the boundary is clear, the result is
              inspectable, and people remain responsible for what gets shared.
            </p>
          </div>
          <div className="about-principle-grid" data-marketing-reveal>
            {principles.map((principle) => {
              const Icon = principle.icon;
              return (
                <article key={principle.eyebrow}>
                  <Icon aria-hidden="true" size={22} />
                  <span>{principle.eyebrow}</span>
                  <h3>{principle.title}</h3>
                  <p>{principle.description}</p>
                </article>
              );
            })}
          </div>
        </section>

        <section className="about-review" id="review">
          <div className="about-review-copy" data-marketing-reveal>
            <span className="about-eyebrow">YOU STAY IN CONTROL</span>
            <h2>Automatic draft.<br />Human decision.</h2>
            <p>
              Promty can organize completed work, but nothing becomes approved
              Project Memory until you decide what belongs.
            </p>
            <ul>
              <li><Check aria-hidden="true" size={16} /> Inspect the pending source</li>
              <li><Check aria-hidden="true" size={16} /> Remove unnecessary context</li>
              <li><Check aria-hidden="true" size={16} /> Check sensitive information</li>
            </ul>
          </div>
          <ReviewDemo />
        </section>

        <section className="about-final-cta">
          <div className="about-final-glow" aria-hidden="true" />
          <div data-marketing-reveal>
            <Sparkles aria-hidden="true" size={22} />
            <span className="about-eyebrow">START WITH ONE PROJECT</span>
            <h2>Do not spend the next session explaining the last one.</h2>
            <p>Connect a repository and let useful context carry forward.</p>
            <div className="about-actions">
              <a className="about-button is-primary" href="/app">
                Open Promty <ArrowRight aria-hidden="true" size={17} />
              </a>
              <a className="about-button is-secondary" href="/docs/collector">
                Read the setup guide
              </a>
            </div>
          </div>
        </section>
      </div>
    </MarketingShell>
  );
}
