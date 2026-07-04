const state = {
  view: "dashboard",
  foundation: null,
  dashboard: null,
  settings: null,
  categories: null,
  wizardSteps: [],
  wizardState: null,
  catalogSummary: null,
  catalogRows: [],
  catalogTab: "overview",
  jobs: [],
  logs: [],
  system: null,
};

const titles = {
  dashboard: "Dashboard",
  wizard: "Wizard",
  catalog: "Catalog",
  settings: "Settings",
  jobs: "Jobs",
  logs: "Logs",
  system: "About",
  foundation: "Foundation",
};

const fieldLabels = {
  app_name: "App Name",
  public_base_url: "Public Base URL",
  timezone: "Timezone",
  log_level: "Log Level",
  data_directory: "Data Directory",
  host_data_path: "Host Data Path",
  container_data_path: "Container Data Path",
  host_media_root: "Host Media Root",
  container_media_root: "Container Media Root",
  host_exports_path: "Host Exports Path",
  container_exports_path: "Container Exports Path",
  environment_mode: "Environment Mode",
  api_host: "API Host",
  api_port: "API Port",
  emby_url: "Emby URL",
  jellyfin_url: "Jellyfin URL",
  nextpvr_url: "NextPVR URL",
  iptv_boss_export_path: "IPTV Boss Export Path",
  debug_enabled: "Debug Enabled",
  job_history_limit: "Job History Limit",
  setup_complete: "Setup Complete",
};

const userStatus = {
  ready: "Ready",
  needs_setup: "Needs setup",
  not_configured: "Not configured",
  error: "Error",
  queued: "Queued",
  running: "Running",
  complete: "Completed",
  failed: "Failed",
  interrupted: "Interrupted",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

function toast(message) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 2200);
}

function badge(label, status = "") {
  return `<span class="status-badge ${status}">${label || userStatus[status] || status || "Not configured"}</span>`;
}

function setBadge(id, label, status) {
  const node = document.getElementById(id);
  node.textContent = label || userStatus[status] || status;
  node.className = `status-badge ${status}`;
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === view));
  document.querySelectorAll("nav button").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
  document.getElementById("view-title").textContent = titles[view];
  refresh();
}

function inputFor(key, value) {
  if (key === "log_level") {
    return `<select name="${key}">${["debug", "info", "warning", "error"].map((item) => `<option ${value === item ? "selected" : ""}>${item}</option>`).join("")}</select>`;
  }
  if (key === "environment_mode") {
    return `<select name="${key}">${["local", "docker", "production"].map((item) => `<option ${value === item ? "selected" : ""}>${item}</option>`).join("")}</select>`;
  }
  if (typeof value === "boolean") {
    return `<select name="${key}"><option value="false" ${!value ? "selected" : ""}>false</option><option value="true" ${value ? "selected" : ""}>true</option></select>`;
  }
  if (typeof value === "number") {
    return `<input type="number" name="${key}" value="${value}" />`;
  }
  return `<input name="${key}" value="${value || ""}" />`;
}

function normalizeValues(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  for (const [key, value] of Object.entries(values)) {
    if (value === "true") values[key] = true;
    if (value === "false") values[key] = false;
    if (["api_port", "job_history_limit"].includes(key)) values[key] = Number(value || 0);
  }
  return values;
}

function renderDashboard() {
  const data = state.dashboard;
  document.getElementById("dash-app").textContent = data.app_name;
  setBadge("dash-setup", data.setup_status_label, data.setup_status);
  setBadge("dash-services", data.services_status_label, data.services_status);
  setBadge("dash-database", data.database_status_label, data.database_status);
  document.getElementById("wizard-progress").textContent = `${data.wizard_progress}%`;
  document.getElementById("jobs-count").textContent = `${data.jobs_total}`;
  document.getElementById("logs-count").textContent = `${data.logs_total}`;
  document.getElementById("catalog-count").textContent = data.catalog_sources;
  document.getElementById("catalog-channels").textContent = data.catalog_channels;
  document.getElementById("catalog-movies").textContent = data.catalog_movies;
  document.getElementById("catalog-series").textContent = data.catalog_series;
  document.getElementById("catalog-episodes").textContent = data.catalog_episodes;
  document.getElementById("sprint-scope").innerHTML = data.sprint_scope.map((item) => `<div>☑ ${item}</div>`).join("");
  document.getElementById("deferred-scope").innerHTML = data.deferred_scope.map((item) => `<div>☐ ${item}</div>`).join("");
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : "Never";
}

function table(headers, rows) {
  if (!rows.length) return '<p class="muted">No catalog records yet.</p>';
  return `
    <table>
      <thead><tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>
  `;
}

function renderCatalogSummary() {
  const summary = state.catalogSummary;
  document.getElementById("cat-summary-channels").textContent = summary.channels;
  document.getElementById("cat-summary-movies").textContent = summary.movies;
  document.getElementById("cat-summary-series").textContent = summary.series;
  document.getElementById("cat-summary-episodes").textContent = summary.episodes;
  document.getElementById("cat-summary-sources").textContent = summary.sources;
  document.getElementById("cat-summary-last").textContent = formatDate(summary.last_import_time);
}

function renderCatalog() {
  renderCatalogSummary();
  document.querySelectorAll("[data-catalog-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.catalogTab === state.catalogTab);
  });
  const target = document.getElementById("catalog-table");
  if (state.catalogTab === "overview") {
    target.innerHTML = `
      <div class="detail-grid">
        <div><span>Channels</span><strong>${state.catalogSummary.channels}</strong></div>
        <div><span>Movies</span><strong>${state.catalogSummary.movies}</strong></div>
        <div><span>Series</span><strong>${state.catalogSummary.series}</strong></div>
        <div><span>Episodes</span><strong>${state.catalogSummary.episodes}</strong></div>
        <div><span>Sources</span><strong>${state.catalogSummary.sources}</strong></div>
        <div><span>Last Import</span><strong>${formatDate(state.catalogSummary.last_import_time)}</strong></div>
      </div>
    `;
    return;
  }
  if (state.catalogTab === "sources") {
    const rows = state.catalogRows.map((source) => `
      <tr>
        <td>${source.media_type}</td>
        <td><code>${source.catalog_internal_id}</code></td>
        <td>${source.source_name}</td>
        <td>${source.cuid || ""}</td>
        <td class="url-cell">${source.source_url}</td>
      </tr>
    `);
    target.innerHTML = table(["Type", "Internal ID", "Source", "CUID", "URL"], rows);
    return;
  }
  const rows = state.catalogRows.map((item) => `
    <tr>
      <td><strong>${item.title}</strong><br><code>${item.internal_id}</code></td>
      <td>${item.group_title || ""}</td>
      <td>${item.tvg_id || ""}</td>
      <td>${item.cuid || ""}</td>
      <td>${item.show_name || ""}</td>
      <td>${item.season_number || ""}</td>
      <td>${item.episode_number || ""}</td>
      <td>${item.confidence}</td>
    </tr>
  `);
  target.innerHTML = table(["Title", "Group", "TVG ID", "CUID", "Show", "Season", "Episode", "Confidence"], rows);
}

function currentWizardStep() {
  return state.wizardSteps.find((step) => step.id === state.wizardState.current_step) || state.wizardSteps[0];
}

function renderWizard() {
  const completed = new Set(state.wizardState.completed_steps);
  const current = currentWizardStep();
  setBadge("wizard-state", state.wizardState.setup_complete ? "Ready" : "Needs setup", state.wizardState.setup_complete ? "ready" : "needs_setup");
  document.getElementById("wizard-stepper").innerHTML = state.wizardSteps
    .map((step) => `<button class="${step.id === current.id ? "active" : ""} ${completed.has(step.id) ? "done" : ""}" data-wizard-step="${step.id}">${completed.has(step.id) ? "✓" : "○"} ${step.label}</button>`)
    .join("");
  document.querySelectorAll("[data-wizard-step]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.wizardState = await api("/api/wizard/state", { method: "PUT", body: JSON.stringify({ current_step: button.dataset.wizardStep }) });
      renderWizard();
    });
  });

  const fields = current.fields || [];
  const fieldInputs = fields
    .map((field) => `<label>${fieldLabels[field] || field}${inputFor(field, state.wizardState.values[field] ?? state.settings[field] ?? "")}</label>`)
    .join("");
  const review = current.id === "review" ? `<div class="detail-grid">${Object.entries(state.settings).slice(0, 10).map(([key, value]) => `<div><span>${fieldLabels[key] || key}</span><strong>${value || "Not configured"}</strong></div>`).join("")}</div>` : "";
  const complete = current.id === "complete" ? `<p class="muted">Completing this step marks first-run setup as ready. You can still edit settings afterward.</p>` : "";

  document.getElementById("wizard-panel").innerHTML = `
    <h2>${current.label}</h2>
    <p class="muted">${current.description}</p>
    <form id="wizard-form" class="form-grid single">${fieldInputs}</form>
    ${review}
    ${complete}
    <div class="button-row">
      <button id="wizard-save">Save Placeholder Values</button>
      <button id="wizard-complete" class="primary">Complete Step</button>
    </div>
  `;
  document.getElementById("wizard-save").addEventListener("click", saveWizardValues);
  document.getElementById("wizard-complete").addEventListener("click", completeWizardStep);
}

async function saveWizardValues() {
  const form = document.getElementById("wizard-form");
  if (!form) return;
  const values = normalizeValues(form);
  state.wizardState = await api("/api/wizard/values", { method: "PUT", body: JSON.stringify(values) });
  state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(values) });
  toast("Wizard values saved");
  await loadDashboard();
  renderDashboard();
  renderWizard();
}

async function completeWizardStep() {
  await saveWizardValues();
  state.wizardState = await api(`/api/wizard/steps/${currentWizardStep().id}/complete`, { method: "POST" });
  if (state.wizardState.setup_complete) {
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify({ setup_complete: true }) });
  }
  await loadDashboard();
  renderDashboard();
  renderWizard();
}

function renderSettings() {
  const form = document.getElementById("settings-form");
  form.innerHTML = Object.entries(state.categories)
    .map(([category, fields]) => `
      <fieldset>
        <legend>${category}</legend>
        <div class="form-grid">
          ${fields.map((field) => `<label>${fieldLabels[field] || field}${inputFor(field, state.settings[field])}</label>`).join("")}
        </div>
      </fieldset>
    `)
    .join("");
}

function renderJobs() {
  const list = document.getElementById("jobs-list");
  if (!state.jobs.length) {
    list.innerHTML = '<p class="muted">No jobs yet.</p>';
    return;
  }
  list.innerHTML = state.jobs
    .map((job) => `
      <article class="job">
        <div>
          <strong>${job.kind}</strong>
          <p>${job.message}</p>
        </div>
        <div class="progress"><span style="width:${job.progress}%"></span></div>
        ${badge(userStatus[job.status], job.status)}
      </article>
    `)
    .join("");
}

function renderLogs() {
  const list = document.getElementById("logs-list");
  if (!state.logs.length) {
    list.innerHTML = '<p class="muted">No logs yet.</p>';
    return;
  }
  list.innerHTML = state.logs
    .map((log) => `
      <article class="log ${log.level}">
        <div>
          <strong>${log.category}</strong>
          <p>${log.message}</p>
        </div>
        <span>${new Date(log.created_at).toLocaleString()}</span>
      </article>
    `)
    .join("");
}

function renderSystem() {
  const rows = {
    "App Version": state.system.app_version,
    "Environment Mode": state.system.environment_mode,
    "Git Branch": state.system.git_branch,
    "Git Commit": state.system.git_commit,
    "Database": state.system.database_label,
    "Persistent Data Path": state.system.data_path,
    "Docker": state.system.docker_label,
    "Container": state.system.container_label,
  };
  document.getElementById("system-grid").innerHTML = Object.entries(rows)
    .map(([key, value]) => `<div><span>${key}</span><strong>${value || "Unavailable"}</strong></div>`)
    .join("");
}

function renderFoundation() {
  document.getElementById("principles").innerHTML = state.foundation.principles
    .map((principle) => `<article class="principle"><strong>${principle.number}. ${principle.title}</strong><p>${principle.rule}</p></article>`)
    .join("");
  document.getElementById("modules").innerHTML = state.foundation.modules
    .map((module) => `<article class="module"><span class="status-badge ${module.status}">${userStatus[module.status] || module.status}</span><h2>${module.label}</h2><p>${module.responsibility}</p></article>`)
    .join("");
}

async function loadDashboard() { state.dashboard = await api("/api/dashboard"); }
async function loadWizard() { [state.wizardSteps, state.wizardState, state.settings] = await Promise.all([api("/api/wizard/steps"), api("/api/wizard/state"), api("/api/settings")]); }
async function loadSettings() { [state.settings, state.categories] = await Promise.all([api("/api/settings"), api("/api/settings/categories")]); }
async function loadCatalog() {
  state.catalogSummary = await api("/api/catalog/summary");
  const endpoints = {
    live: "/api/catalog/live",
    movies: "/api/catalog/movies",
    series: "/api/catalog/series",
    episodes: "/api/catalog/episodes",
    sources: "/api/catalog/sources",
  };
  state.catalogRows = state.catalogTab === "overview" ? [] : await api(endpoints[state.catalogTab]);
}
async function loadJobs() { state.jobs = await api("/api/jobs"); }
async function loadLogs() { state.logs = await api("/api/logs"); }
async function loadSystem() { state.system = await api("/api/system"); }
async function loadFoundation() { state.foundation = await api("/api/foundation"); }

async function refresh() {
  try {
    await loadDashboard();
    renderDashboard();
    if (state.view === "wizard") { await loadWizard(); renderWizard(); }
    if (state.view === "catalog") { await loadCatalog(); renderCatalog(); }
    if (state.view === "settings") { await loadSettings(); renderSettings(); }
    if (state.view === "jobs") { await loadJobs(); renderJobs(); }
    if (state.view === "logs") { await loadLogs(); renderLogs(); }
    if (state.view === "system") { await loadSystem(); renderSystem(); }
    if (state.view === "foundation") { await loadFoundation(); renderFoundation(); }
  } catch (error) {
    toast(error.message);
  }
}

function bind() {
  document.querySelectorAll("nav button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  document.getElementById("refresh").addEventListener("click", refresh);
  document.querySelectorAll("[data-catalog-tab]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.catalogTab = button.dataset.catalogTab;
      await loadCatalog();
      renderCatalog();
    });
  });
  document.getElementById("import-catalog").addEventListener("click", async () => {
    const form = document.getElementById("catalog-import-form");
    const values = Object.fromEntries(new FormData(form).entries());
    const paths = [values.live_path, values.movies_path, values.series_path].filter(Boolean);
    const result = await api("/api/catalog/import", {
      method: "POST",
      body: JSON.stringify({ source_name: values.source_name || "Manual Import", paths }),
    });
    toast(`Catalog import queued: ${result.job_id}`);
    state.catalogTab = "overview";
    setView("jobs");
  });
  document.getElementById("clear-catalog-test-data").addEventListener("click", async () => {
    await api("/api/catalog/clear-test-data", { method: "POST" });
    toast("Catalog test data cleared");
    await loadDashboard();
    renderDashboard();
    if (state.view === "catalog") {
      await loadCatalog();
      renderCatalog();
    }
  });
  document.getElementById("save-settings").addEventListener("click", async () => {
    const values = normalizeValues(document.getElementById("settings-form"));
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(values) });
    toast("Settings saved");
    renderSettings();
    await loadDashboard();
    renderDashboard();
  });
  document.getElementById("start-job").addEventListener("click", async () => {
    await api("/api/jobs", { method: "POST", body: JSON.stringify({ kind: "test_job" }) });
    toast("Test job started");
    setView("jobs");
    const timer = setInterval(async () => {
      await loadJobs();
      renderJobs();
      await loadDashboard();
      renderDashboard();
      if (!state.jobs.some((job) => ["queued", "running"].includes(job.status))) clearInterval(timer);
    }, 500);
  });
}

bind();
refresh();
