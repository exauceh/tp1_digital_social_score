// Test page of the Digital Social Score API: shows the verdict the API returns (clean, slightly
// toxic, toxic) and nothing else, because the API deliberately returns no score, probabilities or
// thresholds. Every text coming from the user or the API is inserted with
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

// The API's verdict -> what the page shows, and the colour band (CSS data-level).
const VERDICTS = {
  clean: { key: "low", label: "Non toxique" },
  slightly_toxic: { key: "mid", label: "Un peu toxique" },
  toxic: { key: "high", label: "Toxique" },
};

const ERRORS = {
  401: "Clé API absente ou invalide.",
  413: "Requête trop volumineuse.",
  422: "Texte refusé : il doit contenir entre 1 et 5 000 caractères.",
  503: "L'API est saturée, réessayez dans quelques secondes.",
};

const $ = (id) => document.getElementById(id);

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

function renderResult(body) {
  const verdict = VERDICTS[body.verdict];
  if (!verdict) throw new Error("Réponse inattendue de l'API.");
  $("level").textContent = verdict.label;
  const result = $("result");
  result.dataset.level = verdict.key;
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
    renderResult(body);
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
