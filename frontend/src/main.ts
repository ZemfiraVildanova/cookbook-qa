// One page, four states: loading, answer with citations, refusal with reason, error (SPEC 3, R-20).
// Types come from the OpenAPI document generated from schemas.py (src/api.d.ts).
import type { components } from "./api";

type AskResponse = components["schemas"]["AskResponse"];
type ErrorResponse = components["schemas"]["ErrorResponse"];
type AskRequest = components["schemas"]["AskRequest"];

const REFUSAL_LABELS: Record<NonNullable<AskResponse["refusal_reason"]>, string> = {
  out_of_corpus: "Not in this cookbook",
  out_of_domain: "Not a recipe question",
  safety: "No safety verdicts: check the ingredients",
};

const form = must(document.querySelector<HTMLFormElement>("#ask-form"));
const textarea = must(document.querySelector<HTMLTextAreaElement>("#question"));
const submit = must(document.querySelector<HTMLButtonElement>("#submit"));
const counter = must(document.querySelector<HTMLElement>("#counter"));
const result = must(document.querySelector<HTMLElement>("#result"));

function must<T>(value: T | null): T {
  if (value === null) throw new Error("missing element");
  return value;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function updateCounter(): void {
  counter.textContent = `${textarea.value.trim().length} / 500`;
}
textarea.addEventListener("input", updateCounter);
updateCounter();

function renderLoading(): void {
  result.replaceChildren(el("div", "card", "Thinking…"));
}

function renderError(status: number, body: unknown, requestId: string | null): void {
  const card = el("div", "card");
  card.append(el("span", "status error", status === 0 ? "Network error" : `Error ${status}`));
  let message = "The service could not answer right now. Please try again.";
  if (status === 0) {
    message = "Could not reach the service. Check your connection and try again.";
  } else if (status === 422) {
    message = "The question must be between 1 and 500 characters.";
  } else if (isErrorResponse(body)) {
    message = body.error.message;
  }
  card.append(el("p", "answer-text", message));
  const rid = isErrorResponse(body) ? body.error.request_id : requestId;
  if (rid) card.append(el("div", "meta", `request id ${rid}`));
  result.replaceChildren(card);
}

function isErrorResponse(body: unknown): body is ErrorResponse {
  return typeof body === "object" && body !== null && "error" in body;
}

function constraintChips(c: AskResponse["applied_constraints"]): HTMLElement | null {
  const chips: string[] = [];
  if (c.max_minutes !== null && c.max_minutes !== undefined) chips.push(`≤ ${c.max_minutes} min`);
  if (c.diet) chips.push(c.diet);
  for (const term of c.include_ingredients ?? []) chips.push(`with ${term}`);
  for (const term of c.exclude_ingredients ?? []) chips.push(`without ${term}`);
  if (chips.length === 0) return null;
  const wrap = el("div", "chips");
  wrap.append(el("span", "meta", "Constraints applied:"));
  for (const chip of chips) wrap.append(el("span", "chip", chip));
  return wrap;
}

function renderAnswer(data: AskResponse): void {
  const card = el("div", "card");
  if (data.refused) {
    const reason = data.refusal_reason ?? "out_of_corpus";
    card.append(el("span", "status refusal", `Refused · ${REFUSAL_LABELS[reason]}`));
  } else {
    card.append(el("span", "status answer", "Answer"));
  }
  card.append(el("p", "answer-text", data.answer ?? ""));
  if (data.citations.length > 0) {
    const list = el("ul", "citations");
    for (const c of data.citations) {
      const item = el("li");
      const link = el("a", undefined, c.title);
      link.href = c.url;
      link.target = "_blank";
      link.rel = "noopener";
      item.append(link);
      list.append(item);
    }
    card.append(el("div", "meta", data.refused ? "Ingredient lists above come from:" : "Sources:"));
    card.append(list);
  }
  const chips = constraintChips(data.applied_constraints);
  if (chips) card.append(chips);
  const m = data.meta;
  card.append(
    el(
      "div",
      "meta",
      `${m.latency_ms} ms · ${m.model ?? "no model call"} · retrieval ${m.retrieval_mode} · request ${m.request_id}`,
    ),
  );
  result.replaceChildren(card);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = textarea.value.trim();
  if (!question) return;
  const payload: AskRequest = { question };
  submit.disabled = true;
  renderLoading();
  try {
    const response = await fetch("./ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const requestId = response.headers.get("x-request-id");
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (response.ok) {
      renderAnswer(body as AskResponse);
    } else {
      renderError(response.status, body, requestId);
    }
  } catch {
    renderError(0, null, null); // network failure: fixed text, no request id (SPEC 3.7)
  } finally {
    submit.disabled = false;
  }
});
