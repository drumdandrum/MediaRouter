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
  providers: [],
  accounts: [],
  embySettings: null,
  embyStatus: null,
  embySessions: [],
  embyBindings: [],
  embyChannelMappings: [],
  embyChannelMappingPage: { total: 0, limit: 100, offset: 0 },
  embyChannelMappingSearch: "",
  editingAccountId: null,
  catalogImportProviderId: "",
  catalogImportAccountId: "",
  brokerStatus: null,
  brokerReservations: [],
  brokerCatalogItems: [],
  brokerDecision: null,
  brokerAutoRefresh: localStorage.getItem("mediaRouterBrokerAutoRefresh") !== "false",
  brokerRefreshIntervalMs: Number(localStorage.getItem("mediaRouterBrokerRefreshIntervalMs") || 3000) || 3000,
  brokerPollTimer: null,
  brokerTtlTimer: null,
  brokerPollInFlight: false,
  brokerPollFailures: 0,
  brokerLastUpdatedAt: null,
  strmSettings: null,
  strmHistory: [],
  strmGeneratedFiles: [],
  strmResult: null,
  strmPathValidation: null,
  strmCatalogSummary: null,
  liveM3uSettings: null,
  liveM3uHistory: [],
  liveM3uResult: null,
  liveM3uPathValidation: null,
  liveM3uEstimate: null,
  jobs: [],
  logs: [],
  system: null,
};

const titles = {
  dashboard: "Dashboard",
  wizard: "Wizard",
  catalog: "Catalog",
  broker: "Broker",
  outputs: "Outputs",
  providers: "Providers",
  accounts: "Accounts",
  integrations: "Integrations",
  settings: "Settings",
  jobs: "Jobs",
  logs: "Logs",
  system: "About",
  foundation: "Foundation",
};

const fieldLabels = {
  app_name: "App Name",
  public_base_url: "Public Base URL",
  runtime_public_base_url: "Runtime Public Base URL",
  trust_proxy_headers: "Trust Proxy Client Headers",
  trusted_proxy_client_header: "Trusted Proxy Client Header",
  trusted_proxy_networks: "Trusted Proxy Networks",
  startup_coalescing_window_seconds: "Startup Coalescing Window (seconds)",
  live_provisional_ttl_seconds: "Live Provisional TTL (seconds)",
  live_promotion_minimum_age_seconds: "Live Promotion Minimum Age (seconds)",
  live_promotion_request_threshold: "Live Promotion Request Threshold",
  live_active_ttl_seconds: "Live Active TTL (seconds)",
  live_same_identity_supersession: "Live Same-Identity Supersession",
  movie_provisional_ttl_seconds: "Movie Provisional TTL (seconds)",
  movie_promotion_minimum_age_seconds: "Movie Promotion Minimum Age (seconds)",
  movie_promotion_request_threshold: "Movie Promotion Request Threshold",
  movie_active_ttl_seconds: "Movie Active TTL (seconds)",
  movie_provisional_supersession: "Movie Provisional Supersession",
  episode_provisional_ttl_seconds: "Episode Provisional TTL (seconds)",
  episode_promotion_minimum_age_seconds: "Episode Promotion Minimum Age (seconds)",
  episode_promotion_request_threshold: "Episode Promotion Request Threshold",
  episode_active_ttl_seconds: "Episode Active TTL (seconds)",
  episode_provisional_supersession: "Episode Provisional Supersession",
  active_lease_sliding_renewal: "Active Lease Sliding Renewal",
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
  cancelled: "Cancelled",
  interrupted: "Interrupted",
  Unknown: "Unknown",
  Healthy: "Healthy",
  Degraded: "Degraded",
  Disabled: "Disabled",
  Offline: "Offline",
};

class ApiError extends Error {
  constructor(message, detail = null) {
    super(message);
    this.detail = detail;
  }
}

function friendlyErrorMessage(detail, fallback = "Request failed.") {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (typeof detail === "object") return detail.failure_message || detail.message || detail.detail || fallback;
  return String(detail);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(friendlyErrorMessage(body.detail, `Request failed: ${response.status}`), body.detail || body);
  }
  return response.json();
}

function toast(message) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 2200);
}

function setFormError(id, message = "") {
  const node = document.getElementById(id);
  if (node) node.textContent = message;
}

function badge(label, status = "") {
  const css = String(status || label || "").toLowerCase().replaceAll(" ", "_");
  return `<span class="status-badge ${css}">${label || userStatus[status] || status || "Not configured"}</span>`;
}

function setBadge(id, label, status) {
  const node = document.getElementById(id);
  node.textContent = label || userStatus[status] || status;
  node.className = `status-badge ${status}`;
}

function setView(view) {
  const leavingBroker = state.view === "broker" && view !== "broker";
  if (leavingBroker) stopBrokerPolling();
  state.view = view;
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === view));
  document.querySelectorAll("nav button").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
  document.getElementById("view-title").textContent = titles[view];
  refresh().then(() => {
    if (view === "broker") startBrokerPolling({ immediate: false });
  });
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
    if (["api_port", "job_history_limit", "startup_coalescing_window_seconds"].includes(key)) values[key] = Number(value || 0);
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
  document.getElementById("providers-count").textContent = data.providers_configured;
  document.getElementById("accounts-count").textContent = data.accounts_configured;
  document.getElementById("healthy-accounts-count").textContent = data.healthy_accounts;
  document.getElementById("problem-accounts-count").textContent = data.problem_accounts;
  document.getElementById("disabled-accounts-count").textContent = data.disabled_accounts;
  document.getElementById("availability-count").textContent = data.source_availability_records;
  document.getElementById("average-sources-count").textContent = data.average_sources_per_item;
  document.getElementById("broker-active-count").textContent = data.broker_active_reservations;
  document.getElementById("broker-expired-count").textContent = data.broker_expired_reservations;
  document.getElementById("broker-capacity-count").textContent = data.broker_accounts_at_capacity;
  document.getElementById("broker-available-count").textContent = data.broker_available_accounts;
  document.getElementById("sprint-scope").innerHTML = data.sprint_scope.map((item) => `<div>☑ ${item}</div>`).join("");
  document.getElementById("deferred-scope").innerHTML = data.deferred_scope.map((item) => `<div>☐ ${item}</div>`).join("");
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : "Never";
}

function remainingTtl(value, status = "active") {
  if (!value || status !== "active") return "";
  const seconds = Math.max(0, Math.ceil((new Date(value).getTime() - Date.now()) / 1000));
  return `${seconds}s`;
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

function publicBaseUrl() {
  const runtimeBase = String(state.settings?.runtime_public_base_url || "").trim();
  if (runtimeBase) return runtimeBase.replace(/\/$/, "");
  const envBase = String(state.settings?.public_base_url || "").trim().replace(/\/$/, "");
  if (envBase && !/^https?:\/\/media-router(?::\d+)?(?:\/|$)/i.test(envBase)) return envBase;
  return window.location.origin.replace(/\/$/, "");
}

function runtimeRouteFor(item) {
  const routes = { channel: "live", movie: "movie", episode: "episode" };
  return routes[item?.media_type] || "";
}

function runtimeUrlFor(item, debug = false) {
  const route = runtimeRouteFor(item);
  if (!route) return "";
  return `${publicBaseUrl()}/r/${route}/${item.internal_id}${debug ? "?debug=true" : ""}`;
}

function brokerDecisionRuntimeUrl(decision) {
  if (!decision || !decision.reservation) return "";
  return decision.runtime_url || decision.stream_url || "";
}

function runtimeUrlKind(url) {
  if (!url) return "generic";
  try {
    return new URL(url, window.location.origin).searchParams.has("ticket") ? "ticketed" : "generic";
  } catch (_) {
    return "generic";
  }
}

function bindExactRuntimeActions(scope, url) {
  scope.querySelector("[data-broker-runtime-open]")?.addEventListener("click", () => {
    window.open(url, "_blank", "noopener");
  });
  scope.querySelector("[data-broker-runtime-copy]")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(url);
    toast("Runtime URL copied");
  });
}

function runtimeCell(item) {
  const url = runtimeUrlFor(item);
  if (!url) return '<span class="muted">No runtime route</span>';
  return `
    <div class="runtime-preview">
      <code>${url}</code>
      <button data-runtime-debug="${runtimeUrlFor(item, true)}">Debug Resolve</button>
    </div>
  `;
}

function bindRuntimeButtons(scope = document) {
  scope.querySelectorAll("[data-runtime-debug], [data-runtime-open]").forEach((button) => button.addEventListener("click", () => {
    const url = button.dataset.runtimeDebug || button.dataset.runtimeOpen;
    if (url) window.open(url, "_blank", "noopener");
  }));
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
        <td>${source.provider_name || "Manual"}</td>
        <td>${source.account_name || "No account"}</td>
        <td>${source.priority_group || ""}</td>
        <td>${source.weight ?? ""}</td>
        <td>${badge(source.account_health_status || "Unknown", source.account_health_status || "Unknown")}</td>
        <td>${source.enabled ? "Enabled" : "Disabled"}</td>
        <td class="url-cell">${source.location_ref}</td>
      </tr>
    `);
    target.innerHTML = `${table(["Type", "Internal ID", "Provider", "Account", "Priority", "Weight", "Health", "Enabled", "Reference"], rows)}<p class="muted helper-text">Showing up to 100 records. Use the API limit and offset parameters for deeper pages.</p>`;
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
      <td>${runtimeCell(item)}</td>
      ${state.catalogTab === "live" ? `<td><button data-show-placements="${item.internal_id}">Placements</button></td>` : ""}
    </tr>
  `);
  const headers = ["Title", "Group", "TVG ID", "CUID", "Show", "Season", "Episode", "Confidence", "Runtime"];
  if (state.catalogTab === "live") headers.push("Editorial");
  target.innerHTML = `${table(headers, rows)}<p class="muted helper-text">Showing up to 100 records. Use the API limit and offset parameters for deeper pages.</p>`;
  bindRuntimeButtons(target);
  target.querySelectorAll("[data-show-placements]").forEach((button) => button.addEventListener("click", async () => {
    const item = state.catalogRows.find((row) => row.internal_id === button.dataset.showPlacements);
    const placements = await api(`/api/catalog/${button.dataset.showPlacements}/placements?limit=500&offset=0`);
    const placementRows = placements.map((placement) => `<tr><td>${placement.group_title || ""}</td><td>${placement.channel_number || ""}</td><td>${placement.display_title}</td><td>${placement.source_name}</td><td class="url-cell">${placement.source_playlist || ""}</td><td>${placement.placement_index}</td></tr>`);
    document.getElementById("catalog-placement-detail").innerHTML = `<h3>${item.title}</h3><p><strong>Canonical identity:</strong> <code>${item.internal_id}</code></p>${table(["Group", "Channel", "Display Title", "Source", "Playlist", "Order"], placementRows)}`;
  }));
}

function optionList(items, emptyLabel = "None", selectedValue = "") {
  return `<option value="" ${selectedValue ? "" : "selected"}>${emptyLabel}</option>` + items.map((item) => `<option value="${item.id}" ${item.id === selectedValue ? "selected" : ""}>${item.friendly_name}</option>`).join("");
}

function syncCatalogImportSelection() {
  const provider = state.providers.find((item) => item.id === state.catalogImportProviderId);
  if (!provider) state.catalogImportProviderId = "";
  const account = state.accounts.find((item) => item.id === state.catalogImportAccountId);
  if (!account || account.provider_id !== state.catalogImportProviderId) state.catalogImportAccountId = "";
}

function renderProviderSelectors() {
  const accountProvider = document.getElementById("account-provider-select");
  const catalogProvider = document.getElementById("catalog-provider-select");
  const catalogAccount = document.getElementById("catalog-account-select");
  const accountProviderValue = accountProvider?.value || "";
  const providerIds = new Set(state.providers.map((provider) => provider.id));
  const selectedAccountProvider = providerIds.has(accountProviderValue) ? accountProviderValue : "";
  syncCatalogImportSelection();

  if (accountProvider) accountProvider.innerHTML = optionList(state.providers, "Select provider", selectedAccountProvider);
  if (catalogProvider) catalogProvider.innerHTML = optionList(state.providers, "Select provider", state.catalogImportProviderId);
  if (catalogAccount) {
    const selectedProvider = state.catalogImportProviderId;
    const accounts = selectedProvider ? state.accounts.filter((account) => account.provider_id === selectedProvider) : [];
    catalogAccount.innerHTML = optionList(accounts, "Select account", state.catalogImportAccountId);
  }
}

function renderProviders() {
  renderProviderSelectors();
  const rows = state.providers.map((provider) => `
    <tr>
      <td><strong>${provider.friendly_name}</strong><br><code>${provider.id}</code></td>
      <td>${provider.provider_type}</td>
      <td>${badge(provider.health_status, provider.health_status)}</td>
      <td>${provider.enabled ? "Enabled" : "Disabled"}</td>
      <td>${provider.notes || ""}</td>
      <td>
        <button data-provider-toggle="${provider.id}">${provider.enabled ? "Disable" : "Enable"}</button>
        <button data-provider-delete="${provider.id}">Delete</button>
      </td>
    </tr>
  `);
  document.getElementById("providers-list").innerHTML = table(["Provider", "Type", "Health", "Enabled", "Notes", "Actions"], rows);
  document.querySelectorAll("[data-provider-toggle]").forEach((button) => button.addEventListener("click", async () => {
    const provider = state.providers.find((item) => item.id === button.dataset.providerToggle);
    await api(`/api/providers/${provider.id}`, { method: "PUT", body: JSON.stringify({ enabled: !provider.enabled }) });
    await loadProviders();
    renderProviders();
    await loadDashboard();
    renderDashboard();
  }));
  document.querySelectorAll("[data-provider-delete]").forEach((button) => button.addEventListener("click", async () => {
    await api(`/api/providers/${button.dataset.providerDelete}`, { method: "DELETE" });
    await loadProviders();
    renderProviders();
    await loadDashboard();
    renderDashboard();
  }));
}

function renderAccounts() {
  renderProviderSelectors();
  const editingAccount = state.accounts.find((account) => account.id === state.editingAccountId);
  if (state.editingAccountId && !editingAccount) clearAccountForm();
  if (editingAccount) setAccountForm(editingAccount);
  const rows = state.accounts.map((account) => `
    <tr>
      <td><strong>${account.friendly_name}</strong><br><code>${account.id}</code></td>
      <td>${account.provider_name || account.provider_id}</td>
      <td>${account.priority_group}</td>
      <td>${account.weight}</td>
      <td>${account.max_simultaneous_streams}</td>
      <td>${badge(account.health_status, account.health_status)}</td>
      <td>${account.enabled ? "Enabled" : "Disabled"}</td>
      <td>${account.has_secret ? "Stored" : "Blank"}</td>
      <td>
        <button data-account-edit="${account.id}">Edit</button>
        <button data-account-test="${account.id}">Test</button>
        <button data-account-toggle="${account.id}">${account.enabled ? "Disable" : "Enable"}</button>
        <button data-account-delete="${account.id}">Delete</button>
      </td>
    </tr>
  `);
  document.getElementById("accounts-list").innerHTML = table(["Account", "Provider", "Priority", "Weight", "Max Streams", "Health", "Enabled", "Secret", "Actions"], rows);
  document.querySelectorAll("[data-account-edit]").forEach((button) => button.addEventListener("click", () => {
    const account = state.accounts.find((item) => item.id === button.dataset.accountEdit);
    setAccountForm(account);
  }));
  document.querySelectorAll("[data-account-test]").forEach((button) => button.addEventListener("click", async () => {
    const result = await api(`/api/accounts/${button.dataset.accountTest}/test`, { method: "POST" });
    toast(result.message);
    await loadAccounts();
    renderAccounts();
    await loadDashboard();
    renderDashboard();
  }));
  document.querySelectorAll("[data-account-toggle]").forEach((button) => button.addEventListener("click", async () => {
    const account = state.accounts.find((item) => item.id === button.dataset.accountToggle);
    await api(`/api/accounts/${account.id}`, { method: "PUT", body: JSON.stringify({ enabled: !account.enabled }) });
    await loadAccounts();
    renderAccounts();
    await loadDashboard();
    renderDashboard();
  }));
  document.querySelectorAll("[data-account-delete]").forEach((button) => button.addEventListener("click", async () => {
    if (state.editingAccountId === button.dataset.accountDelete) clearAccountForm();
    await api(`/api/accounts/${button.dataset.accountDelete}`, { method: "DELETE" });
    await loadAccounts();
    renderAccounts();
    await loadDashboard();
    renderDashboard();
  }));
}

function setAccountForm(account) {
  const form = document.getElementById("account-form");
  if (!form || !account) return;
  state.editingAccountId = account.id;
  form.elements.provider_id.value = account.provider_id || "";
  form.elements.friendly_name.value = account.friendly_name || "";
  form.elements.username.value = account.username || "";
  form.elements.password.value = "";
  form.elements.base_url.value = account.base_url || "";
  form.elements.max_simultaneous_streams.value = account.max_simultaneous_streams || 1;
  form.elements.priority_group.value = account.priority_group || "Preferred";
  form.elements.weight.value = account.weight ?? 100;
  form.elements.enabled.value = String(Boolean(account.enabled));
  form.elements.notes.value = account.notes || "";
  document.getElementById("save-account").textContent = "Update Account";
  document.getElementById("cancel-account-edit").textContent = "Cancel";
  setFormError("account-form-error");
}

function clearAccountForm() {
  const form = document.getElementById("account-form");
  if (!form) return;
  state.editingAccountId = null;
  form.reset();
  form.elements.provider_id.value = "";
  form.elements.password.value = "";
  document.getElementById("save-account").textContent = "Add Account";
  document.getElementById("cancel-account-edit").textContent = "New";
  setFormError("account-form-error");
}

function resetCatalogImportForm() {
  const form = document.getElementById("catalog-import-form");
  if (!form) return;
  form.reset();
  state.catalogImportProviderId = "";
  state.catalogImportAccountId = "";
  setFormError("catalog-import-error");
  renderProviderSelectors();
}

function validateAccountValues(values) {
  if (!values.provider_id) return "Choose a provider before saving the account.";
  if (!values.friendly_name || !values.friendly_name.trim()) return "Account name is required.";
  if (Number(values.max_simultaneous_streams || 0) < 1) return "Max streams must be at least 1.";
  if (Number(values.weight || 0) < 0) return "Weight must be 0 or greater.";
  return "";
}

function validateCatalogImportValues(values) {
  if (!values.provider_id) return "Choose a provider before importing.";
  if (!values.account_id) return "Choose an account/connection before importing.";
  const account = state.accounts.find((item) => item.id === values.account_id);
  if (!account) return "Selected account/connection was not found.";
  if (account.provider_id !== values.provider_id) return "Selected account does not belong to the selected provider.";
  if (!values.playlist || !values.playlist.trim()) return "Enter a playlist file, container path, or HTTP/HTTPS URL before importing.";
  if (!["", "live", "movie", "series"].includes(values.media_type || "")) return "Choose a supported media type.";
  return "";
}

function catalogImportValues(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  values.provider_id = state.catalogImportProviderId;
  values.account_id = state.catalogImportAccountId;
  return values;
}

function brokerCatalogOptions() {
  return `<option value="">Select catalog item</option>` + state.brokerCatalogItems.map((item) => `<option value="${item.internal_id}">${item.media_type}: ${item.title}</option>`).join("");
}

function selectedBrokerCatalogItem() {
  const itemId = document.getElementById("broker-catalog-item")?.value || "";
  return state.brokerCatalogItems.find((item) => item.internal_id === itemId) || null;
}

function renderBrokerRuntimePreview() {
  const target = document.getElementById("broker-runtime-preview");
  if (!target) return;
  const item = selectedBrokerCatalogItem();
  if (!item) {
    target.innerHTML = '<p class="muted">Select a catalog item to preview its runtime URL.</p>';
    return;
  }
  const resolvedUrl = state.brokerDecision?.reservation?.catalog_item_id === item.internal_id
    ? brokerDecisionRuntimeUrl(state.brokerDecision)
    : "";
  const url = resolvedUrl || runtimeUrlFor(item);
  if (!url) {
    target.innerHTML = '<p class="muted">This catalog item does not have a Sprint 5 runtime route.</p>';
    return;
  }
  target.innerHTML = `
    <div class="runtime-preview">
      <code>${url}</code>
      <span><strong>URL type:</strong> ${runtimeUrlKind(url)}</span>
      ${resolvedUrl ? '<button data-broker-runtime-copy>Copy Runtime URL</button><button data-broker-runtime-open>Open Runtime URL</button>' : '<span class="muted">Resolve first to open a reservation-specific URL.</span>'}
    </div>
  `;
  if (resolvedUrl) bindExactRuntimeActions(target, resolvedUrl);
}

function renderBroker() {
  renderBrokerRefreshControls();
  renderBrokerLive();
  renderBrokerCatalogSelect();
  renderBrokerRuntimePreview();
  renderBrokerDecision();
}

function renderBrokerLive() {
  const status = state.brokerStatus;
  if (!status) return;
  document.getElementById("broker-active-reservations").textContent = status.active_reservations;
  document.getElementById("broker-provisional-reservations").textContent = status.provisional_reservations;
  document.getElementById("broker-consuming-reservations").textContent = status.consuming_reservations;
  document.getElementById("broker-total-reservations").textContent = status.total_reservations;
  document.getElementById("broker-released-reservations").textContent = status.released_reservations;
  document.getElementById("broker-expired-reservations").textContent = status.expired_reservations;
  document.getElementById("broker-capacity-accounts").textContent = status.accounts_at_capacity;
  document.getElementById("broker-available-accounts").textContent = status.available_accounts;
  renderBrokerRefreshStatus();
  renderBrokerAccountUsage();
  renderBrokerReservations();
  updateBrokerTtls();
}

function renderBrokerCatalogSelect() {
  const itemSelect = document.getElementById("broker-catalog-item");
  if (!itemSelect) return;
  const selectedItem = itemSelect.value;
  itemSelect.innerHTML = brokerCatalogOptions();
  itemSelect.value = state.brokerCatalogItems.some((item) => item.internal_id === selectedItem) ? selectedItem : "";
}

function renderBrokerRefreshControls() {
  const auto = document.getElementById("broker-auto-refresh");
  const interval = document.getElementById("broker-refresh-interval");
  if (auto) auto.value = String(Boolean(state.brokerAutoRefresh));
  if (interval) interval.value = String(state.brokerRefreshIntervalMs);
  renderBrokerRefreshStatus();
}

function renderBrokerRefreshStatus() {
  const updated = document.getElementById("broker-last-updated");
  const stale = document.getElementById("broker-stale-indicator");
  if (updated) updated.textContent = state.brokerLastUpdatedAt ? new Date(state.brokerLastUpdatedAt).toLocaleTimeString() : "Never";
  if (!stale) return;
  if (document.hidden && state.brokerAutoRefresh) {
    stale.innerHTML = `${badge("Paused", "warning")}`;
  } else if (!state.brokerAutoRefresh) {
    stale.innerHTML = `${badge("Auto refresh off", "disabled")}`;
  } else if (state.brokerPollFailures >= 3) {
    stale.innerHTML = `${badge("Stale", "error")}`;
  } else if (state.brokerPollInFlight) {
    stale.innerHTML = `${badge("Updating", "running")}`;
  } else {
    stale.innerHTML = `${badge("Live", "ready")}`;
  }
}

function renderBrokerDecision() {
  const target = document.getElementById("broker-decision");
  if (!state.brokerDecision) {
    target.innerHTML = '<p class="muted">No broker decision yet.</p>';
    return;
  }
  const decision = state.brokerDecision;
  if (decision.failure_code) {
    target.innerHTML = `
      <div class="broker-error">
        <strong>${decision.failure_message || "No source selected."}</strong>
        <p>${friendlyBrokerFailure(decision.failure_code)}</p>
      </div>
      ${candidateTable(decision.evaluated_candidates || [])}
    `;
    return;
  }
  const selected = decision.selected_source;
  const reservation = decision.reservation;
  const runtimeUrl = brokerDecisionRuntimeUrl(decision);
  target.innerHTML = `
    <div class="detail-grid">
      <div><span>Reservation</span><strong><code>${reservation.reservation_id}</code></strong></div>
      <div><span>Account</span><strong>${selected.account_name}</strong></div>
      <div><span>Provider</span><strong>${selected.provider_name}</strong></div>
      <div><span>Priority</span><strong>${selected.priority_group}</strong></div>
      <div><span>Weight</span><strong>${selected.weight}</strong></div>
      <div><span>Usage Before Reserve</span><strong>${selected.active_reservations} / ${selected.max_simultaneous_streams}</strong></div>
      <div><span>TTL</span><strong>${decision.reservation_ttl_seconds}s</strong></div>
      <div><span>Expires</span><strong>${formatDate(decision.expires_at)}</strong></div>
    </div>
    <p class="muted helper-text url-cell">${runtimeUrl}</p>
    <p class="helper-text"><strong>URL type:</strong> ${runtimeUrlKind(runtimeUrl)}</p>
    <p class="helper-text"><strong>Why selected:</strong> ${decision.decision_reason}</p>
    <div class="button-row">
      <button data-broker-runtime-copy>Copy Runtime URL</button>
      <button data-broker-runtime-open>Open Runtime URL</button>
      <button data-release-reservation="${reservation.reservation_id}">Release Reservation</button>
    </div>
    <div class="muted-list">${decision.decision_reasons.map((reason) => `<div>${reason}</div>`).join("")}</div>
    ${candidateTable(decision.evaluated_candidates || [])}
  `;
  bindExactRuntimeActions(target, runtimeUrl);
  bindBrokerReleaseButtons(target);
}

function friendlyBrokerFailure(code) {
  const messages = {
    no_sources: "No catalog sources available.",
    all_disabled: "All matching sources, providers, or accounts are disabled.",
    all_unhealthy: "No healthy accounts found.",
    all_at_capacity: "All matching accounts are at capacity.",
  };
  return messages[code] || "No source selected.";
}

function candidateTable(candidates) {
  if (!candidates.length) return '<section class="subsection"><h3>Evaluated Candidates</h3><p class="muted">No matching source candidates were found.</p></section>';
  const rows = candidates.map((candidate) => `
    <tr>
      <td>${candidate.selected ? badge("Selected", "active") : badge("Skipped", "disabled")}</td>
      <td>${candidate.account_name || "No account"}</td>
      <td>${candidate.provider_name || "No provider"}</td>
      <td>${candidate.priority_group || ""}</td>
      <td>${candidate.weight ?? ""}</td>
      <td>${candidate.max_simultaneous_streams ? `${candidate.active_reservations} / ${candidate.max_simultaneous_streams}` : ""}</td>
      <td>${badge(candidate.account_health_status || "Unknown", candidate.account_health_status || "Unknown")}</td>
      <td><strong>${candidate.reason}</strong><br><span class="muted">${candidate.reason_detail}</span></td>
    </tr>
  `);
  return `<section class="subsection"><h3>Evaluated Candidates</h3>${table(["Result", "Account", "Provider", "Priority", "Weight", "Usage", "Health", "Reason"], rows)}</section>`;
}

function renderBrokerAccountUsage() {
  const rows = state.brokerStatus.account_usage.map((account) => `
    <tr>
      <td><strong>${account.account_name}</strong><br><code>${account.account_id}</code></td>
      <td>${account.provider_name}</td>
      <td>${account.priority_group}</td>
      <td>${account.weight}</td>
      <td>${account.provisional_reservations} provisional<br>${account.active_reservations} active<br><strong>${account.consuming_reservations} / ${account.max_simultaneous_streams} total</strong></td>
      <td>${badge(account.health_status, account.health_status)}</td>
      <td>${account.available ? "Available" : account.at_capacity ? "At capacity" : "Unavailable"}</td>
    </tr>
  `);
  document.getElementById("broker-account-usage").innerHTML = table(["Account", "Provider", "Priority", "Weight", "Usage", "Health", "Status"], rows);
}

function renderBrokerReservations() {
  const filter = document.getElementById("broker-reservation-filter")?.value || "all";
  const reservations = state.brokerReservations.filter((reservation) => filter === "all" || (filter === "consuming" ? ["provisional", "active"].includes(reservation.lifecycle_state) : reservation.lifecycle_state === filter));
  const rows = reservations.map((reservation) => `
    <tr>
      <td><code>${reservation.reservation_id}</code></td>
      <td>${badge(reservation.lifecycle_state, reservation.lifecycle_state)}</td>
      <td><strong>${reservation.catalog_title || reservation.catalog_item_id}</strong><br><code>${reservation.catalog_item_id}</code></td>
      <td>${reservation.media_type}</td>
      <td>${reservation.account_name || reservation.account_id}</td>
      <td><span data-ttl-expires="${reservation.expires_at}" data-ttl-status="${reservation.status}">${remainingTtl(reservation.expires_at, reservation.status)}</span></td>
      <td>${formatDate(reservation.expires_at)}</td>
      <td>${reservation.client_label || ""}</td>
      <td>${reservation.identity_type || ""}<br><code>${reservation.masked_client_identity || ""}</code></td>
      <td>Created ${formatDate(reservation.created_at)}<br>Promoted ${formatDate(reservation.promoted_at)}<br>Seen ${formatDate(reservation.last_seen_at)}</td>
      <td>${reservation.request_count || 0} request(s)<br>${reservation.distinct_activity_count || 0} meaningful<br>${reservation.alias_count || 0} alias(es)</td>
      <td>${reservation.startup_coalesced ? badge("Startup coalesced", "warning") : ""}</td>
      <td>${reservation.duplicate_warning ? badge("Possible duplicate", "warning") : ""}</td>
      <td>${reservation.promotion_reason || reservation.release_reason || ""}<br>${reservation.superseded_by_reservation_id ? `By <code>${reservation.superseded_by_reservation_id}</code>` : ""}</td>
      <td>${reservation.lifecycle_state === "provisional" ? `<button data-confirm-reservation="${reservation.reservation_id}">Promote Now</button>` : ""}${["provisional","active"].includes(reservation.lifecycle_state) ? `<button data-release-reservation="${reservation.reservation_id}">Release</button><button data-expire-reservation="${reservation.reservation_id}">Expire Now</button>` : ""}</td>
    </tr>
  `);
  const target = document.getElementById("broker-reservations-list");
  target.innerHTML = table(["Reservation", "Lifecycle", "Catalog Item", "Type", "Account", "Remaining", "Expires", "Client", "Identity", "Times", "Activity", "Coalescing", "Duplicate", "Reason / Relation", "Actions"], rows);
  bindBrokerReleaseButtons(target);
  bindBrokerLifecycleButtons(target);
}

function bindBrokerLifecycleButtons(scope = document) {
  scope.querySelectorAll("[data-confirm-reservation]").forEach((button) => button.addEventListener("click", async () => { await api(`/api/broker/reservations/${button.dataset.confirmReservation}/confirm`, { method: "POST" }); await refreshBrokerLive({ force: true }); }));
  scope.querySelectorAll("[data-expire-reservation]").forEach((button) => button.addEventListener("click", async () => { await api(`/api/broker/reservations/${button.dataset.expireReservation}/expire`, { method: "POST" }); await refreshBrokerLive({ force: true }); }));
}

function updateBrokerTtls() {
  document.querySelectorAll("[data-ttl-expires]").forEach((node) => {
    node.textContent = remainingTtl(node.dataset.ttlExpires, node.dataset.ttlStatus);
  });
}

function bindBrokerReleaseButtons(scope = document) {
  scope.querySelectorAll("[data-release-reservation]").forEach((button) => button.addEventListener("click", async () => {
    const reservationId = button.dataset.releaseReservation;
    await api("/api/broker/release", { method: "POST", body: JSON.stringify({ reservation_id: reservationId }) });
    toast("Reservation released");
    await refreshBrokerLive({ force: true });
    await loadDashboard();
    renderDashboard();
  }));
}

function brokerResolveValues() {
  const form = document.getElementById("broker-resolve-form");
  const values = Object.fromEntries(new FormData(form).entries());
  return {
    catalog_item_id: values.catalog_item_id,
    media_type: values.media_type || null,
    client_label: values.client_label || null,
    client_session: values.client_session || null,
    reservation_ttl_seconds: values.reservation_ttl_seconds ? Number(values.reservation_ttl_seconds) : null,
  };
}

function setStrmSettingsForm() {
  const form = document.getElementById("strm-settings-form");
  if (!form || !state.strmSettings) return;
  Object.entries(state.strmSettings).forEach(([key, value]) => {
    if (form.elements[key]) form.elements[key].value = String(value);
  });
  const custom = form.elements.generation_mode.value === "Custom";
  form.elements.maximum_movies.disabled = !custom;
  form.elements.maximum_episodes.disabled = !custom;
  document.getElementById("strm-unlimited-warning").hidden = form.elements.generation_mode.value !== "Unlimited";
}

function strmSettingsValues() {
  const values = Object.fromEntries(new FormData(document.getElementById("strm-settings-form")).entries());
  values.overwrite_existing_files = values.overwrite_existing_files === "true";
  values.remove_orphaned_files = values.remove_orphaned_files === "true";
  values.dry_run_mode = values.dry_run_mode === "true";
  values.maximum_movies = Number(values.maximum_movies || 0);
  values.maximum_episodes = Number(values.maximum_episodes || 0);
  values.batch_size = Number(values.batch_size || 250);
  values.worker_count = Number(values.worker_count || 4);
  return values;
}

function renderStrmSummary(result = state.strmResult || (state.strmHistory[0] ? { summary: state.strmHistory[0].summary, operations: [] } : null)) {
  const target = document.getElementById("strm-summary");
  const operationsTarget = document.getElementById("strm-operations");
  if (!result) {
    target.innerHTML = '<p class="muted">Run a dry run or generate job to see STRM output changes.</p>';
    operationsTarget.innerHTML = "";
    return;
  }
  const summary = result.summary;
  target.innerHTML = `
    <div class="detail-grid">
      <div><span>Mode</span><strong>${summary.mode}</strong></div>
      <div><span>Limit</span><strong>${summary.capped ? "Capped" : "Unlimited"}</strong></div>
      <div><span>Processed</span><strong>${summary.processed_items}/${summary.total_items}</strong></div>
      <div><span>Excluded by Limits</span><strong>${summary.excluded_by_limits}</strong></div>
      <div><span>Movies</span><strong>${summary.movie_count}</strong></div>
      <div><span>Episodes</span><strong>${summary.episode_count}</strong></div>
      <div><span>Created</span><strong>${summary.created_count}</strong></div>
      <div><span>Updated</span><strong>${summary.updated_count}</strong></div>
      <div><span>Skipped</span><strong>${summary.skipped_count}</strong></div>
      <div><span>Removed</span><strong>${summary.removed_count}</strong></div>
      <div><span>Failed</span><strong>${summary.failed_count}</strong></div>
      <div><span>Throughput</span><strong>${summary.items_per_second || 0} items/s</strong></div>
      <div><span>Average</span><strong>${summary.average_ms_per_item || 0} ms/item</strong></div>
      <div><span>Workers</span><strong>${summary.worker_count || 4}</strong></div>
    </div>
  `;
  const rows = result.operations.slice(0, 200).map((operation) => `
    <tr>
      <td>${badge(operation.action, operation.action)}</td>
      <td>${operation.media_type}</td>
      <td>${operation.title || operation.catalog_item_id || ""}</td>
      <td class="url-cell">${operation.output_path}</td>
      <td class="url-cell">${operation.runtime_url || ""}</td>
      <td>${operation.reason}</td>
    </tr>
  `);
  operationsTarget.innerHTML = table(["Action", "Type", "Catalog Item", "Output Path", "Runtime URL", "Reason"], rows);
}

function renderStrmPathValidation() {
  const target = document.getElementById("strm-path-validation");
  if (!target) return;
  const generateButton = document.getElementById("generate-strm");
  if (!state.strmPathValidation) {
    target.innerHTML = '<p class="muted">Validate paths before generating STRM files.</p>';
    if (generateButton) generateButton.disabled = false;
    return;
  }
  const rows = state.strmPathValidation.paths.map((item) => `
    <tr>
      <td>${item.purpose}</td>
      <td class="url-cell">${item.path}</td>
      <td>${badge(item.status === "ok" ? (item.writable ? "Writable" : "Readable") : item.status === "warning" ? "Missing" : item.writable ? "Writable" : item.readable ? "Readable" : "Not writable", item.status)}</td>
      <td>${item.exists ? "Yes" : "No"}</td>
      <td>${item.readable ? "Yes" : "No"}</td>
      <td>${item.writable ? "Yes" : "No"}</td>
      <td>${item.can_create ? "Yes" : "No"}</td>
      <td>${item.message}</td>
    </tr>
  `);
  target.innerHTML = `<h3>Path Validation</h3>${table(["Purpose", "Path", "Status", "Exists", "Readable", "Writable", "Can Create", "Message"], rows)}`;
  if (generateButton) generateButton.disabled = !state.strmPathValidation.can_generate;
}

function setLiveM3uSettingsForm() {
  const form = document.getElementById("live-m3u-settings-form");
  if (!form || !state.liveM3uSettings) return;
  Object.entries(state.liveM3uSettings).forEach(([key, value]) => {
    if (form.elements[key]) form.elements[key].value = String(value);
  });
  const custom = form.elements.generation_mode.value === "Custom";
  form.elements.maximum_live_channels.disabled = !custom;
  document.getElementById("live-m3u-unlimited-warning").hidden = form.elements.generation_mode.value !== "Unlimited";
}

function liveM3uSettingsValues() {
  const values = Object.fromEntries(new FormData(document.getElementById("live-m3u-settings-form")).entries());
  ["include_disabled_channels", "include_logos", "include_group_title", "include_tvg_id", "include_tvg_name", "dry_run_mode"].forEach((key) => {
    values[key] = values[key] === "true";
  });
  values.maximum_live_channels = Number(values.maximum_live_channels || 0);
  return values;
}

function renderLiveM3uPathValidation() {
  const target = document.getElementById("live-m3u-path-validation");
  const generateButton = document.getElementById("generate-live-m3u");
  if (!target) return;
  if (!state.liveM3uPathValidation) {
    target.innerHTML = '<p class="muted">Validate the output file path before generating Live M3U.</p>';
    if (generateButton) generateButton.disabled = false;
    return;
  }
  const rows = state.liveM3uPathValidation.paths.map((item) => `
    <tr>
      <td>${item.purpose}</td>
      <td class="url-cell">${item.path}</td>
      <td>${badge(item.status === "ok" ? (item.writable ? "Writable" : "Ready") : item.status, item.status)}</td>
      <td>${item.exists ? "Yes" : "No"}</td>
      <td>${item.readable ? "Yes" : "No"}</td>
      <td>${item.writable ? "Yes" : "No"}</td>
      <td>${item.can_create ? "Yes" : "No"}</td>
      <td>${item.message}</td>
    </tr>
  `);
  target.innerHTML = `<h3>Live M3U Path Validation</h3>${table(["Purpose", "Path", "Status", "Exists", "Readable", "Writable", "Can Create", "Message"], rows)}`;
  if (generateButton) generateButton.disabled = !state.liveM3uPathValidation.can_generate;
}

function renderLiveM3uSummary() {
  const target = document.getElementById("live-m3u-summary");
  const preview = document.getElementById("live-m3u-preview");
  if (!state.liveM3uResult) {
    target.innerHTML = '<p class="muted">Run a dry run or preview to see generated Live M3U entries.</p>';
    preview.innerHTML = "";
    return;
  }
  const summary = state.liveM3uResult.summary;
  target.innerHTML = `
    <div class="detail-grid">
      <div><span>Total Channels</span><strong>${summary.total_live_channels}</strong></div>
      <div><span>Eligible Channels</span><strong>${summary.eligible_live_channels}</strong></div>
      <div><span>Configured Limit</span><strong>${summary.configured_limit ?? "Unlimited"}</strong></div>
      <div><span>Limit Status</span><strong>${summary.capped ? "Capped" : "Unlimited"}</strong></div>
      <div><span>Excluded by Limit</span><strong>${summary.excluded_by_limit}</strong></div>
      <div><span>Written</span><strong>${summary.written_count}</strong></div>
      <div><span>Skipped</span><strong>${summary.skipped_count}</strong></div>
      <div><span>Created</span><strong>${summary.created_count}</strong></div>
      <div><span>Updated</span><strong>${summary.updated_count}</strong></div>
      <div><span>Failed</span><strong>${summary.failed_count}</strong></div>
    </div>
  `;
  const rows = state.liveM3uResult.preview_entries.map((entry) => `
    <tr>
      <td>${entry.channel_number || ""}</td>
      <td>${entry.title}</td>
      <td>${entry.group_title || ""}</td>
      <td><code>${entry.catalog_item_id}</code></td>
      <td class="url-cell">${entry.extinf}</td>
      <td class="url-cell">${entry.runtime_url}</td>
    </tr>
  `);
  preview.innerHTML = table(["Ch", "Channel", "Group", "Catalog ID", "EXTINF", "Runtime URL"], rows);
}

function renderOutputs() {
  setStrmSettingsForm();
  setLiveM3uSettingsForm();
  document.getElementById("strm-runtime-base").textContent = publicBaseUrl();
  document.getElementById("strm-generated-count").textContent = state.strmGeneratedFiles.length;
  const catalog = state.strmCatalogSummary;
  const s = state.strmSettings;
  const estimated = !catalog || !s ? "-" : s.generation_mode === "Unlimited" ? catalog.movies + catalog.episodes : Math.min(catalog.movies, s.maximum_movies) + Math.min(catalog.episodes, s.maximum_episodes);
  document.getElementById("strm-estimated-count").textContent = String(estimated);
  const active = state.jobs.find((job) => job.kind === "strm_generate" && ["queued", "running"].includes(job.status));
  document.getElementById("strm-current-progress").textContent = active ? `${active.progress}% — ${active.message}` : "Idle";
  const lastRun = state.strmHistory[0];
  document.getElementById("strm-last-run").textContent = lastRun ? `${lastRun.status} ${formatDate(lastRun.finished_at || lastRun.started_at)}` : "Never";
  renderStrmSummary();
  renderStrmPathValidation();
  document.getElementById("live-m3u-runtime-base").textContent = state.liveM3uSettings?.runtime_client_access_url || publicBaseUrl();
  document.getElementById("live-m3u-output-file").textContent = state.liveM3uSettings?.output_file_path || "-";
  document.getElementById("live-m3u-playlist-url").textContent = "Future hosted playlist URL";
  const liveLastRun = state.liveM3uHistory[0];
  const liveLastGenerate = state.liveM3uHistory.find((run) => run.mode === "generate");
  const liveSummary = state.liveM3uResult?.summary || liveLastRun?.summary || null;
  document.getElementById("live-m3u-last-run").textContent = liveLastRun ? `${liveLastRun.status} ${formatDate(liveLastRun.finished_at || liveLastRun.started_at)}` : "Never";
  document.getElementById("live-m3u-last-generated").textContent = liveLastGenerate ? formatDate(liveLastGenerate.finished_at || liveLastGenerate.started_at) : "Never";
  document.getElementById("live-m3u-channel-count").textContent = liveSummary ? String(liveSummary.written_count) : "0";
  document.getElementById("live-m3u-skipped-count").textContent = liveSummary ? String(liveSummary.skipped_count) : "0";
  document.getElementById("live-m3u-eligible-count").textContent = String(liveSummary?.eligible_live_channels ?? state.liveM3uEstimate?.eligible_live_channels ?? "-");
  document.getElementById("live-m3u-limit-status").textContent = state.liveM3uSettings?.generation_mode === "Unlimited" ? "Unlimited" : `Capped at ${state.liveM3uSettings?.maximum_live_channels ?? 500}`;
  const activeLive = state.jobs.find((job) => job.kind === "live_m3u_generate" && ["queued", "running"].includes(job.status));
  document.getElementById("live-m3u-current-progress").textContent = activeLive ? `${activeLive.progress}% — ${activeLive.message}` : "Idle";
  renderLiveM3uPathValidation();
  renderLiveM3uSummary();
  const rows = state.strmGeneratedFiles.map((file) => `
    <tr>
      <td>${file.media_type}</td>
      <td><code>${file.catalog_item_id}</code></td>
      <td class="url-cell">${file.output_path}</td>
      <td>${badge(file.status, file.status)}</td>
      <td>${formatDate(file.last_generated_at)}</td>
    </tr>
  `);
  document.getElementById("strm-generated-files").innerHTML = table(["Type", "Catalog Item", "Output Path", "Status", "Last Generated"], rows);
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

function renderEmby() {
  if (!state.embySettings || !state.embyStatus) return;
  const form = document.getElementById("emby-settings-form");
  for (const [key, value] of Object.entries(state.embySettings)) {
    const input = form.elements.namedItem(key);
    if (input && key !== "has_api_key") input.value = String(value);
  }
  form.elements.api_key.value = "";
  form.elements.api_key.placeholder = state.embySettings.has_api_key ? "Stored — blank preserves key" : "API key required";
  const status = state.embyStatus;
  setBadge("emby-health", status.health_state, status.health_state);
  document.getElementById("emby-server").textContent = status.server_name ? `${status.server_name} ${status.server_version || ""}`.trim() : "Not connected";
  document.getElementById("emby-last-success").textContent = formatDate(status.last_successful_poll);
  document.getElementById("emby-observed").textContent = status.observed_playback_count;
  document.getElementById("emby-match-count").textContent = `${status.matched_playback_count} / ${status.unmatched_playback_count}`;
  document.getElementById("emby-active-bindings").textContent = status.active_binding_count;
  document.getElementById("emby-pending-releases").textContent = status.pending_release_count;
  document.getElementById("emby-status-error").textContent = status.last_error || "";
  const orderedSessions = [...state.embySessions].sort((left, right) =>
    Number(Boolean(left.catalog_item_id)) - Number(Boolean(right.catalog_item_id)));
  const sessionRows = orderedSessions.map((session) => `<tr>
    <td>${session.user_name || ""}</td><td>${session.device_name || ""}<br><span class="muted">${session.client_name || ""}</span></td>
    <td><strong>${session.item_name || session.emby_item_id || "Unknown"}</strong><br>${session.media_type || session.item_type || ""}</td>
    <td>${badge(session.playback_state, session.playback_state)}</td><td><code>${session.catalog_item_id || ""}</code></td>
    <td><code>${session.reservation_id || "Unmatched"}</code></td><td>${session.correlation_method || ""}<br><span class="muted">${session.correlation_confidence || ""}${session.unmatched_reason ? ` · ${session.unmatched_reason}` : ""}${session.correlation_candidate_count ? ` · ${session.correlation_candidate_count} candidates` : ""}${session.recent_runtime_observation_found ? " · recent runtime request" : ""}${session.rejected_for_client_context_count ? ` · ${session.rejected_for_client_context_count} client mismatch` : ""}</span></td>
    <td>${formatDate(session.observed_at)}</td></tr>`);
  document.getElementById("emby-sessions-list").innerHTML = table(["User", "Device / Client", "Item", "State", "Catalog", "Reservation", "Correlation", "Observed"], sessionRows);
  const bindingRows = state.embyBindings.map((binding) => `<tr>
    <td><code>${binding.emby_session_id}</code></td><td>${binding.user_name || ""}</td><td>${binding.device_name || ""}<br><span class="muted">${binding.client_name || ""}</span></td>
    <td><strong>${binding.item_name || binding.emby_item_id || ""}</strong><br>${binding.media_type}</td><td>${badge(binding.playback_state, binding.playback_state)}</td>
    <td><code>${binding.reservation_id}</code></td><td>${binding.correlation_method}<br><span class="muted">${binding.correlation_confidence}</span></td>
    <td>${binding.missing_since ? badge("Pending release", "warning") : binding.released_at ? badge("Released", "released") : badge("Bound", "ready")}</td>
    <td>${formatDate(binding.last_observed_at)}<br><span class="muted">${binding.release_reason || ""}</span></td></tr>`);
  document.getElementById("emby-bindings-list").innerHTML = table(["Session", "User", "Device / Client", "Item", "State", "Reservation", "Correlation", "Binding", "Last Seen / Reason"], bindingRows);
  const channelOptions = state.brokerCatalogItems.filter((item) => item.media_type === "channel")
    .map((item) => `<option value="${item.internal_id}">${item.title} · ${item.internal_id}</option>`).join("");
  const mappingRows = state.embyChannelMappings.map((mapping) => `<tr>
    <td>${mapping.emby_channel_name || ""}</td><td><code>${mapping.emby_item_id}</code></td>
    <td><code>${mapping.emby_media_source_id || ""}</code></td>
    <td>${mapping.catalog_item_id ? `<code>${mapping.catalog_item_id}</code>` : `<select data-emby-map-select="${mapping.emby_item_id}"><option value="">Choose channel</option>${channelOptions}</select>`}</td>
    <td>${badge(mapping.catalog_item_id ? mapping.mapping_source : "Unmapped", mapping.catalog_item_id ? "ready" : "warning")}</td>
    <td>${mapping.catalog_item_id ? "" : `<button data-emby-map-link="${mapping.emby_item_id}" data-emby-server="${mapping.emby_server_id}">Link</button>`}</td></tr>`);
  document.getElementById("emby-channel-mappings-list").innerHTML = table(["Emby Channel", "ItemId", "MediaSourceId", "Media Router Catalog", "Source", "Action"], mappingRows);
  document.getElementById("emby-channel-mapping-page-status").textContent = `${state.embyChannelMappingPage.offset + 1}-${Math.min(state.embyChannelMappingPage.offset + state.embyChannelMappings.length, state.embyChannelMappingPage.total)} of ${state.embyChannelMappingPage.total}`;
  document.querySelectorAll("[data-emby-map-link]").forEach((button) => button.addEventListener("click", async () => {
    const select = document.querySelector(`[data-emby-map-select="${button.dataset.embyMapLink}"]`);
    if (!select?.value) return toast("Choose a Media Router channel");
    await api(`/api/integrations/emby/channel-mappings/${encodeURIComponent(button.dataset.embyServer)}/${encodeURIComponent(button.dataset.embyMapLink)}`,
      { method: "PUT", body: JSON.stringify({ catalog_item_id: select.value }) });
    await loadEmby(); renderEmby(); toast("Emby channel linked");
  }));
}

function renderJobs() {
  const list = document.getElementById("jobs-list");
  if (!state.jobs.length) {
    list.innerHTML = '<p class="muted">No jobs yet.</p>';
    return;
  }
  list.innerHTML = state.jobs
    .map((job) => {
      const result = job.result || {};
      const resultText = result.eligible_live_channels !== undefined
        ? `<p>${result.included_channels ?? result.written_count ?? 0}/${result.eligible_live_channels} eligible channels included (${result.percentage_complete ?? job.progress}%), ${result.excluded_by_limit ?? 0} excluded by limit, ${result.skipped_count ?? 0} unavailable; ${Number(result.duration_seconds || 0).toFixed(1)}s</p>`
        : result.created_count !== undefined
        ? `<p>${result.processed_items ?? 0}/${result.total_items ?? 0} processed (${result.percentage_complete ?? job.progress}%), ${result.created_count} created, ${result.updated_count} updated, ${result.skipped_count} skipped, ${result.removed_count} removed, ${result.failed_count} failed; ${result.current_media_type || "-"} batch ${result.current_batch || 0}; ${Number(result.duration_seconds || 0).toFixed(1)}s${result.failure_reason ? ` — ${result.failure_reason}` : ""}</p>`
        : "";
      return `
      <article class="job ${job.status}">
        <div>
          <strong>${job.kind}</strong>
          <p>${job.message}</p>
          ${resultText}
        </div>
        <div class="progress"><span style="width:${job.progress}%"></span></div>
        ${badge(userStatus[job.status], job.status)}
        ${job.kind === "strm_generate" && ["queued", "running"].includes(job.status) ? `<button data-cancel-job="${job.id}">Cancel</button>` : ""}
      </article>
    `;
    })
    .join("");
  list.querySelectorAll("[data-cancel-job]").forEach((button) => button.addEventListener("click", async () => {
    await api(`/api/jobs/${button.dataset.cancelJob}/cancel`, { method: "POST" });
    await loadJobs(); renderJobs();
  }));
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
async function loadEmby() {
  const mappingQuery = new URLSearchParams({ limit: "100", offset: String(state.embyChannelMappingPage.offset || 0), search: state.embyChannelMappingSearch });
  [state.embySettings, state.embyStatus, state.embySessions, state.embyBindings, state.embyChannelMappingPage, state.brokerCatalogItems] = await Promise.all([
    api("/api/integrations/emby"), api("/api/integrations/emby/status"),
    api("/api/integrations/emby/sessions?limit=100&offset=0"), api("/api/integrations/emby/bindings?limit=100&offset=0"),
    api(`/api/integrations/emby/channel-mappings/page?${mappingQuery}`),
    api("/api/catalog/items?limit=500&offset=0"),
  ]);
  state.embyChannelMappings = state.embyChannelMappingPage.items;
}
async function loadProviders() { state.providers = await api("/api/providers"); }
async function loadAccounts() { state.accounts = await api("/api/accounts"); }
async function loadBroker() {
  [state.brokerStatus, state.brokerReservations, state.brokerCatalogItems, state.settings] = await Promise.all([
    api("/api/broker/status"),
    api("/api/broker/reservations"),
    api("/api/catalog/items?limit=500&offset=0"),
    api("/api/settings"),
  ]);
  state.brokerLastUpdatedAt = Date.now();
  state.brokerPollFailures = 0;
}

async function loadBrokerLive() {
  [state.brokerStatus, state.brokerReservations] = await Promise.all([
    api("/api/broker/status"),
    api("/api/broker/reservations"),
  ]);
  state.brokerLastUpdatedAt = Date.now();
  state.brokerPollFailures = 0;
}
async function loadOutputs() {
  [state.strmSettings, state.strmHistory, state.strmGeneratedFiles, state.settings, state.strmPathValidation, state.liveM3uSettings, state.liveM3uHistory, state.liveM3uPathValidation, state.strmCatalogSummary, state.jobs, state.liveM3uEstimate] = await Promise.all([
    api("/api/outputs/strm/settings"),
    api("/api/outputs/strm/history"),
    api("/api/outputs/strm/generated-files?limit=100&offset=0"),
    api("/api/settings"),
    api("/api/outputs/strm/validate-paths").catch(() => null),
    api("/api/outputs/live-m3u/settings"),
    api("/api/outputs/live-m3u/history"),
    api("/api/outputs/live-m3u/validate-paths").catch(() => null),
    api("/api/catalog/summary"),
    api("/api/jobs"),
    api("/api/outputs/live-m3u/estimate"),
  ]);
}
async function loadCatalog() {
  [state.catalogSummary, state.providers, state.accounts, state.settings] = await Promise.all([api("/api/catalog/summary"), api("/api/providers"), api("/api/accounts"), api("/api/settings")]);
  syncCatalogImportSelection();
  renderProviderSelectors();
  const endpoints = {
    live: "/api/catalog/live",
    movies: "/api/catalog/movies",
    series: "/api/catalog/series",
    episodes: "/api/catalog/episodes",
    sources: "/api/catalog/sources",
  };
  state.catalogRows = state.catalogTab === "overview" ? [] : await api(`${endpoints[state.catalogTab]}?limit=100&offset=0`);
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
    if (state.view === "broker") { await loadBroker(); renderBroker(); }
    if (state.view === "outputs") { await loadOutputs(); renderOutputs(); }
    if (state.view === "providers") { await loadProviders(); renderProviders(); }
    if (state.view === "accounts") { await Promise.all([loadProviders(), loadAccounts()]); renderAccounts(); }
    if (state.view === "integrations") { await loadEmby(); renderEmby(); }
    if (state.view === "settings") { await loadSettings(); renderSettings(); }
    if (state.view === "jobs") { await loadJobs(); renderJobs(); }
    if (state.view === "logs") { await loadLogs(); renderLogs(); }
    if (state.view === "system") { await loadSystem(); renderSystem(); }
    if (state.view === "foundation") { await loadFoundation(); renderFoundation(); }
  } catch (error) {
    toast(error.message);
  }
}

async function refreshBrokerLive({ force = false } = {}) {
  if (state.view !== "broker") return;
  if (!force && (!state.brokerAutoRefresh || document.hidden)) {
    renderBrokerRefreshStatus();
    return;
  }
  if (state.brokerPollInFlight) return;
  state.brokerPollInFlight = true;
  renderBrokerRefreshStatus();
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  try {
    await loadBrokerLive();
    if (state.view !== "broker") return;
    renderBrokerLive();
    await loadDashboard();
    renderDashboard();
    window.scrollTo(scrollX, scrollY);
  } catch (error) {
    state.brokerPollFailures += 1;
    renderBrokerRefreshStatus();
    if (force) toast(error.message);
  } finally {
    state.brokerPollInFlight = false;
    renderBrokerRefreshStatus();
  }
}

function startBrokerPolling({ immediate = false } = {}) {
  stopBrokerPolling();
  if (state.view !== "broker") return;
  state.brokerTtlTimer = setInterval(updateBrokerTtls, 1000);
  if (state.brokerAutoRefresh) {
    state.brokerPollTimer = setInterval(() => refreshBrokerLive(), state.brokerRefreshIntervalMs);
    if (immediate && !document.hidden) refreshBrokerLive({ force: true });
  }
  renderBrokerRefreshStatus();
}

function stopBrokerPolling() {
  if (state.brokerPollTimer) clearInterval(state.brokerPollTimer);
  if (state.brokerTtlTimer) clearInterval(state.brokerTtlTimer);
  state.brokerPollTimer = null;
  state.brokerTtlTimer = null;
  state.brokerPollInFlight = false;
}

function setBrokerAutoRefresh(value) {
  state.brokerAutoRefresh = value;
  localStorage.setItem("mediaRouterBrokerAutoRefresh", String(value));
  startBrokerPolling({ immediate: value });
}

function setBrokerRefreshInterval(value) {
  state.brokerRefreshIntervalMs = Number(value) || 3000;
  localStorage.setItem("mediaRouterBrokerRefreshIntervalMs", String(state.brokerRefreshIntervalMs));
  startBrokerPolling({ immediate: state.brokerAutoRefresh });
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
  document.getElementById("catalog-provider-select").addEventListener("change", (event) => {
    state.catalogImportProviderId = event.target.value;
    const selectedAccount = state.accounts.find((account) => account.id === state.catalogImportAccountId);
    if (!selectedAccount || selectedAccount.provider_id !== state.catalogImportProviderId) state.catalogImportAccountId = "";
    renderProviderSelectors();
    setFormError("catalog-import-error");
  });
  document.getElementById("catalog-account-select").addEventListener("change", (event) => {
    state.catalogImportAccountId = event.target.value;
    setFormError("catalog-import-error");
  });
  document.getElementById("import-catalog").addEventListener("click", async () => {
    const form = document.getElementById("catalog-import-form");
    const values = catalogImportValues(form);
    const error = validateCatalogImportValues(values);
    if (error) {
      setFormError("catalog-import-error", error);
      toast(error);
      return;
    }
    try {
      setFormError("catalog-import-error");
      const result = await api("/api/catalog/import", {
        method: "POST",
        body: JSON.stringify({ source_name: values.source_name || "Manual Import", playlist: values.playlist || null, media_type: values.media_type || null, provider_id: values.provider_id || null, account_id: values.account_id || null }),
      });
      toast(`Catalog import queued: ${result.job_id}`);
      state.catalogTab = "overview";
      setView("jobs");
    } catch (error) {
      setFormError("catalog-import-error", error.message);
      toast(error.message);
    }
  });
  document.getElementById("reset-catalog-import").addEventListener("click", resetCatalogImportForm);
  document.getElementById("broker-catalog-item").addEventListener("change", renderBrokerRuntimePreview);
  document.getElementById("broker-auto-refresh").addEventListener("change", (event) => {
    setBrokerAutoRefresh(event.target.value === "true");
  });
  document.getElementById("broker-refresh-interval").addEventListener("change", (event) => {
    setBrokerRefreshInterval(event.target.value);
  });
  document.getElementById("broker-reservation-filter").addEventListener("change", renderBrokerReservations);
  document.getElementById("broker-refresh-now").addEventListener("click", () => {
    refreshBrokerLive({ force: true });
  });
  document.addEventListener("visibilitychange", () => {
    if (state.view !== "broker") return;
    if (document.hidden) {
      renderBrokerRefreshStatus();
      return;
    }
    startBrokerPolling({ immediate: true });
  });
  document.getElementById("resolve-broker-source").addEventListener("click", async () => {
    const values = brokerResolveValues();
    if (!values.catalog_item_id) {
      setFormError("broker-resolve-error", "Choose a catalog item before resolving.");
      toast("Choose a catalog item before resolving.");
      return;
    }
    try {
      setFormError("broker-resolve-error");
      state.brokerDecision = await api("/api/broker/resolve", { method: "POST", body: JSON.stringify(values) });
      renderBrokerRuntimePreview();
      renderBrokerDecision();
      toast("Broker reservation created");
      await refreshBrokerLive({ force: true });
      await loadDashboard();
      renderDashboard();
    } catch (error) {
      state.brokerDecision = null;
      setFormError("broker-resolve-error", error.message);
      toast(error.message);
      await refreshBrokerLive({ force: true });
    }
  });
  document.getElementById("expire-broker-reservations").addEventListener("click", async () => {
    await api("/api/broker/expire-now", { method: "POST" });
    toast("Expired reservations updated");
    await refreshBrokerLive({ force: true });
    await loadDashboard();
    renderDashboard();
  });
  document.getElementById("repair-broker-duplicates").addEventListener("click", async () => {
    const result = await api("/api/broker/repair-duplicates", { method: "POST" });
    toast(`Released ${result.released_reservations} duplicate reservation(s)`);
    await refreshBrokerLive({ force: true });
  });
  document.getElementById("release-all-broker-reservations").addEventListener("click", async () => {
    await api("/api/broker/release-all", { method: "POST" });
    toast("Active reservations released");
    state.brokerDecision = null;
    await refreshBrokerLive({ force: true });
    await loadDashboard();
    renderDashboard();
  });
  document.getElementById("save-strm-settings").addEventListener("click", async () => {
    try {
      setFormError("strm-settings-error");
      state.strmSettings = await api("/api/outputs/strm/settings", { method: "PUT", body: JSON.stringify(strmSettingsValues()) });
      toast("STRM settings saved");
      await loadOutputs();
      renderOutputs();
    } catch (error) {
      setFormError("strm-settings-error", error.message);
      toast(error.message);
    }
  });
  document.querySelector('#strm-settings-form [name="generation_mode"]').addEventListener("change", (event) => {
    const form = document.getElementById("strm-settings-form");
    const custom = event.target.value === "Custom";
    form.elements.maximum_movies.disabled = !custom;
    form.elements.maximum_episodes.disabled = !custom;
    document.getElementById("strm-unlimited-warning").hidden = event.target.value !== "Unlimited";
  });
  document.getElementById("validate-strm-paths").addEventListener("click", async () => {
    try {
      setFormError("strm-settings-error");
      state.strmSettings = await api("/api/outputs/strm/settings", { method: "PUT", body: JSON.stringify(strmSettingsValues()) });
      state.strmPathValidation = await api("/api/outputs/strm/validate-paths");
      toast(state.strmPathValidation.can_generate ? "Output paths are writable" : "One or more output paths need attention");
      await loadOutputs();
      renderOutputs();
    } catch (error) {
      setFormError("strm-settings-error", error.message);
      toast(error.message);
    }
  });
  document.getElementById("dry-run-strm").addEventListener("click", async () => {
    try {
      setFormError("strm-settings-error");
      state.strmSettings = await api("/api/outputs/strm/settings", { method: "PUT", body: JSON.stringify(strmSettingsValues()) });
      state.strmPathValidation = await api("/api/outputs/strm/validate-paths");
      state.strmResult = await api("/api/outputs/strm/dry-run", { method: "POST" });
      toast("STRM dry run complete");
      await loadOutputs();
      renderOutputs();
    } catch (error) {
      setFormError("strm-settings-error", error.message);
      toast(error.message);
    }
  });
  document.getElementById("generate-strm").addEventListener("click", async () => {
    try {
      setFormError("strm-settings-error");
      state.strmSettings = await api("/api/outputs/strm/settings", { method: "PUT", body: JSON.stringify(strmSettingsValues()) });
      state.strmPathValidation = await api("/api/outputs/strm/validate-paths");
      if (!state.strmPathValidation.can_generate) {
        renderOutputs();
        setFormError("strm-settings-error", "One or more output paths are not writable. Validate paths before generating.");
        toast("Output paths are not ready");
        return;
      }
      const unlimited = state.strmSettings.generation_mode === "Unlimited";
      if (unlimited && !window.confirm("Unlimited STRM generation will process the entire movie and episode catalog. Continue?")) return;
      const job = await api("/api/outputs/strm/generate", { method: "POST", body: JSON.stringify({ confirm_unlimited: unlimited }) });
      toast(`STRM generation queued: ${job.id}`);
      setView("jobs");
      const timer = setInterval(async () => {
        await loadJobs();
        renderJobs();
        await loadDashboard();
        renderDashboard();
        if (!state.jobs.some((item) => ["queued", "running"].includes(item.status))) {
          clearInterval(timer);
          await loadOutputs();
          state.strmResult = null;
        }
      }, 700);
    } catch (error) {
      setFormError("strm-settings-error", error.message);
      toast(error.message);
    }
  });
  document.getElementById("save-live-m3u-settings").addEventListener("click", async () => {
    try {
      setFormError("live-m3u-settings-error");
      state.liveM3uSettings = await api("/api/outputs/live-m3u/settings", { method: "PUT", body: JSON.stringify(liveM3uSettingsValues()) });
      toast("Live M3U settings saved");
      await loadOutputs();
      renderOutputs();
    } catch (error) {
      setFormError("live-m3u-settings-error", error.message);
      toast(error.message);
    }
  });
  document.querySelector('#live-m3u-settings-form [name="generation_mode"]').addEventListener("change", (event) => {
    const form = document.getElementById("live-m3u-settings-form");
    form.elements.maximum_live_channels.disabled = event.target.value !== "Custom";
    document.getElementById("live-m3u-unlimited-warning").hidden = event.target.value !== "Unlimited";
  });
  document.getElementById("validate-live-m3u-path").addEventListener("click", async () => {
    try {
      setFormError("live-m3u-settings-error");
      state.liveM3uSettings = await api("/api/outputs/live-m3u/settings", { method: "PUT", body: JSON.stringify(liveM3uSettingsValues()) });
      state.liveM3uPathValidation = await api("/api/outputs/live-m3u/validate-paths");
      toast(state.liveM3uPathValidation.can_generate ? "Live M3U output path is writable" : "Live M3U output path needs attention");
      await loadOutputs();
      renderOutputs();
    } catch (error) {
      setFormError("live-m3u-settings-error", error.message);
      toast(error.message);
    }
  });
  document.getElementById("dry-run-live-m3u").addEventListener("click", async () => {
    try {
      setFormError("live-m3u-settings-error");
      state.liveM3uSettings = await api("/api/outputs/live-m3u/settings", { method: "PUT", body: JSON.stringify(liveM3uSettingsValues()) });
      state.liveM3uPathValidation = await api("/api/outputs/live-m3u/validate-paths");
      state.liveM3uResult = await api("/api/outputs/live-m3u/dry-run", { method: "POST" });
      toast("Live M3U dry run complete");
      await loadOutputs();
      renderOutputs();
    } catch (error) {
      setFormError("live-m3u-settings-error", error.message);
      toast(error.message);
    }
  });
  document.getElementById("generate-live-m3u").addEventListener("click", async () => {
    try {
      setFormError("live-m3u-settings-error");
      state.liveM3uSettings = await api("/api/outputs/live-m3u/settings", { method: "PUT", body: JSON.stringify(liveM3uSettingsValues()) });
      state.liveM3uPathValidation = await api("/api/outputs/live-m3u/validate-paths");
      if (!state.liveM3uPathValidation.can_generate) {
        renderOutputs();
        setFormError("live-m3u-settings-error", "The Live M3U output path is not writable. Validate the path before generating.");
        toast("Live M3U output path is not ready");
        return;
      }
      const unlimited = state.liveM3uSettings.generation_mode === "Unlimited";
      if (unlimited && !window.confirm("Unlimited Live M3U generation will include every eligible channel. Continue?")) return;
      const job = await api("/api/outputs/live-m3u/generate", { method: "POST", body: JSON.stringify({ confirm_unlimited: unlimited }) });
      toast(`Live M3U generation queued: ${job.id}`);
      setView("jobs");
      const timer = setInterval(async () => {
        await loadJobs();
        renderJobs();
        await loadDashboard();
        renderDashboard();
        if (!state.jobs.some((item) => ["queued", "running"].includes(item.status))) {
          clearInterval(timer);
          await loadOutputs();
          state.liveM3uResult = null;
        }
      }, 700);
    } catch (error) {
      setFormError("live-m3u-settings-error", error.message);
      toast(error.message);
    }
  });
  document.getElementById("save-provider").addEventListener("click", async () => {
    const values = Object.fromEntries(new FormData(document.getElementById("provider-form")).entries());
    values.enabled = values.enabled === "true";
    await api("/api/providers", { method: "POST", body: JSON.stringify(values) });
    toast("Provider added");
    setView("providers");
  });
  document.getElementById("save-account").addEventListener("click", async () => {
    const values = Object.fromEntries(new FormData(document.getElementById("account-form")).entries());
    values.enabled = values.enabled === "true";
    values.max_simultaneous_streams = Number(values.max_simultaneous_streams || 1);
    values.weight = Number(values.weight || 100);
    const validationError = validateAccountValues(values);
    if (validationError) {
      setFormError("account-form-error", validationError);
      toast(validationError);
      return;
    }
    const editing = Boolean(state.editingAccountId);
    const path = editing ? `/api/accounts/${state.editingAccountId}` : "/api/accounts";
    try {
      setFormError("account-form-error");
      await api(path, { method: editing ? "PUT" : "POST", body: JSON.stringify(values) });
      toast(editing ? "Account updated" : "Account added");
      clearAccountForm();
      await Promise.all([loadProviders(), loadAccounts()]);
      renderAccounts();
      await loadDashboard();
      renderDashboard();
    } catch (error) {
      setFormError("account-form-error", error.message);
      toast(error.message);
    }
  });
  document.getElementById("cancel-account-edit").addEventListener("click", clearAccountForm);
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
  document.getElementById("save-emby-settings").addEventListener("click", async () => {
    try {
      setFormError("emby-settings-error");
      const values = normalizeValues(document.getElementById("emby-settings-form"));
      ["poll_interval_seconds", "release_grace_seconds", "unavailable_timeout_seconds", "request_timeout_seconds"].forEach((key) => values[key] = Number(values[key]));
      state.embySettings = await api("/api/integrations/emby", { method: "PUT", body: JSON.stringify(values) });
      await loadEmby(); renderEmby(); toast("Emby settings saved");
    } catch (error) { setFormError("emby-settings-error", error.message); }
  });
  document.getElementById("test-emby-connection").addEventListener("click", async () => {
    try {
      setFormError("emby-settings-error");
      const values = normalizeValues(document.getElementById("emby-settings-form"));
      ["poll_interval_seconds", "release_grace_seconds", "unavailable_timeout_seconds", "request_timeout_seconds"].forEach((key) => values[key] = Number(values[key]));
      state.embySettings = await api("/api/integrations/emby", { method: "PUT", body: JSON.stringify(values) });
      const result = await api("/api/integrations/emby/test", { method: "POST" });
      if (!result.success) throw new ApiError(result.message);
      toast(`${result.message}${result.server_name ? ` ${result.server_name}` : ""}`);
      await loadEmby(); renderEmby();
    } catch (error) { setFormError("emby-settings-error", error.message); }
  });
  document.getElementById("preview-emby-channel-mappings").addEventListener("click", async () => {
    try {
      const result = await api("/api/integrations/emby/channel-mappings/preview", { method: "POST" });
      toast(`Preview: ${result.automatic_matches} automatic, ${result.manual_matches} manual, ${result.ambiguous} ambiguous, ${result.unmatched} unmatched, ${result.conflicts} conflicts`);
    } catch (error) { setFormError("emby-status-error", error.message); }
  });
  document.getElementById("refresh-emby-channel-mappings").addEventListener("click", async () => {
    try {
      const result = await api("/api/integrations/emby/channel-mappings/refresh", { method: "POST" });
      await loadEmby(); renderEmby();
      toast(`Emby channels refreshed: ${result.mapped} mapped, ${result.unmapped} unmapped`);
    } catch (error) { setFormError("emby-status-error", error.message); }
  });
  document.getElementById("search-emby-channel-mappings").addEventListener("change", async (event) => {
    state.embyChannelMappingSearch = event.target.value.trim(); state.embyChannelMappingPage.offset = 0;
    await loadEmby(); renderEmby();
  });
  document.getElementById("prev-emby-channel-mappings").addEventListener("click", async () => {
    state.embyChannelMappingPage.offset = Math.max(0, state.embyChannelMappingPage.offset - 100);
    await loadEmby(); renderEmby();
  });
  document.getElementById("next-emby-channel-mappings").addEventListener("click", async () => {
    if (state.embyChannelMappingPage.offset + 100 < state.embyChannelMappingPage.total) state.embyChannelMappingPage.offset += 100;
    await loadEmby(); renderEmby();
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
