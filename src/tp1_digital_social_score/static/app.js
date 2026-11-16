// Test page of the Digital Social Score API: global score, plus the per-label detail in a
// collapsed "Voir plus" section. Every text coming from the user or the API is inserted with
// textContent, never parsed as HTML, and the page only talks to its own origin (see the
// Content-Security-Policy in api.py).
"use strict";

const KEY_STORAGE = "dss-api-key"; // sessionStorage: forgotten when the tab is closed

const EXAMPLES = {
  neutral: "Thank you for fixing the article, great work!",
  insult: "You are a stupid idiot and nobody cares about your edits.",
  threat: "I will find you and hurt you, John Smith, you will regret this.",
  hate: "Those people are all vermin, go back to your country.",
  pii: "Contact me at jane.doe@example.com or 192.168.0.12, this is f*** nonsense",
  emoji: "\u{1F621}\u{1F595}",
};

const LABEL_NAMES = {
  toxic: "Toxique",
  severe_toxic: "Très toxique",
  obscene: "Obscène",
  threat: "Menace",
  insult: "Insulte",
  identity_hate: "Haine identitaire",
};

// Display bands only, not the model's decision (see the footer of the page).
function levelOf(score) {
  if (score >= 50) return { key: "high", label: "Élevé" };
  if (score >= 20) return { key: "mid", label: "Modéré" };
  return { key: "low", label: "Faible" };
}

const ERRORS = {
  401: "Clé API absente ou invalide.",
  413: "Requête trop volumineuse.",
  422: "Texte refusé : il doit contenir entre 1 et 5 000 caractères.",
  503: "L'API est saturée, réessayez dans quelques secondes.",
};

const $ = (id) => document.getElementById(id);
const percent = (value) => `${Math.min(100, Math.max(0, value))}%`;
const formatNumber = (value, digits) =>
  value.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });

let thresholds = null; // fetched once per page, from GET /thresholds

function readKey() {
  try {
    return sessionStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}
function saveKey(value) {
  try {
    if (value) sessionStorage.setItem(KEY_STORAGE, value);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    // Storage blocked (private mode...): the key simply stays in the field.
  }
}

function showError(message) {
  const box = $("error");
  box.textContent = message;
  box.hidden = !message;
}

async function call(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("API injoignable : vérifiez votre connexion ou le port-forward.");
  }
  if (!response.ok) {
    throw new Error(ERRORS[response.status] || `Erreur inattendue (HTTP ${response.status}).`);
  }
  return response.json();
}

function scoreText(text, key) {
  return call("/score", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": key },
    body: JSON.stringify({ text }),
  });
}

async function loadThresholds(key) {
  if (thresholds) return thresholds;
  try {
    const body = await call("/thresholds", { headers: { "X-API-Key": key } });
    thresholds = body.thresholds;
  } catch {
    thresholds = null; // the detail is still shown, only without the threshold marks
  }
  return thresholds;
}

function renderLabels(labels, limits) {
  const rows = Object.entries(labels)
    .sort(([, a], [, b]) => b - a)
    .map(([label, probability]) => {
      const limit = limits ? limits[label] : null;
      const over = typeof limit === "number" && probability > limit;

      const name = document.createElement("span");
      name.className = "label-name";
      name.textContent = LABEL_NAMES[label] || label;
      const code = document.createElement("small");
      code.textContent = label;
      name.append(code);

      const fill = document.createElement("div");
      fill.className = "label-fill";
      fill.style.setProperty("--value", percent(probability * 100));
      const bar = document.createElement("div");
      bar.className = "label-bar";
      bar.append(fill);
      if (typeof limit === "number") {
        const mark = document.createElement("span");
        mark.className = "label-threshold";
        mark.style.setProperty("--position", percent(limit * 100));
        bar.append(mark);
      }

      const value = document.createElement("span");
      value.className = "label-value";
      value.textContent = formatNumber(probability, 3);
      if (over) {
        const markOver = document.createElement("span");
        markOver.className = "over-mark";
        markOver.textContent = " ▲";
        markOver.title = "Au-dessus du seuil";
        value.append(markOver);
      }
      if (typeof limit === "number") {
        const seuil = document.createElement("span");
        seuil.className = "label-seuil";
        seuil.textContent = `seuil ${formatNumber(limit, 2)}`;
        value.append(seuil);
      }

      const row = document.createElement("li");
      row.className = over ? "label-row over" : "label-row";
      row.append(name, bar, value);
      return row;
    });
  $("labels").replaceChildren(...rows);
}

function renderResult(body, limits) {
  const score = Number(body.score);
  const level = levelOf(score);
  $("score-value").textContent = formatNumber(score, 1);
  $("level").textContent = level.label;
  const gauge = $("gauge");
  gauge.setAttribute("aria-valuenow", String(score));
  gauge.querySelector(".gauge-fill").style.setProperty("--value", percent(score));
  renderLabels(body.labels || {}, limits);
  const result = $("result");
  result.dataset.level = level.key;
  result.hidden = false;
}

async function analyze() {
  showError("");
  const key = $("api-key").value.trim();
  const text = $("text").value;
  if (!key) {
    showError("Renseignez d'abord votre clé API.");
    $("api-key").focus();
    return;
  }
  if (!text.trim()) {
    showError("Le texte est vide.");
    $("text").focus();
    return;
  }

  const button = $("analyze");
  button.disabled = true;
  button.textContent = "Analyse…";
  try {
    const body = await scoreText(text, key);
    renderResult(body, await loadThresholds(key));
  } catch (error) {
    $("result").hidden = true;
    showError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Analyser";
  }
}

function updateCount() {
  $("count").textContent = String($("text").value.length);
}

async function checkHealth() {
  const badge = $("health");
  try {
    const response = await fetch("/health", { cache: "no-store" });
    badge.dataset.state = response.ok ? "ok" : "down";
    badge.textContent = response.ok ? "API en ligne" : "API indisponible";
  } catch {
    badge.dataset.state = "down";
    badge.textContent = "API injoignable";
  }
}

function init() {
  const keyInput = $("api-key");
  keyInput.value = readKey();
  keyInput.addEventListener("input", () => {
    saveKey(keyInput.value.trim());
    thresholds = null; // re-fetched with the new key
  });
  $("toggle-key").addEventListener("click", () => {
    const hidden = keyInput.type === "password";
    keyInput.type = hidden ? "text" : "password";
    $("toggle-key").textContent = hidden ? "Masquer" : "Afficher";
  });

  for (const chip of document.querySelectorAll("[data-example]")) {
    chip.addEventListener("click", () => {
      $("text").value = EXAMPLES[chip.dataset.example];
      updateCount();
      $("result").hidden = true;
      $("text").focus();
    });
  }

  $("text").addEventListener("input", updateCount);
  $("text").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) analyze();
  });
  $("analyze").addEventListener("click", analyze);

  checkHealth();
  setInterval(checkHealth, 30000);
}

document.addEventListener("DOMContentLoaded", init);
