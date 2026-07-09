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
  editingAccountId: null,
  catalogImportProviderId: "",
  catalogImportAccountId: "",
  brokerStatus: null,
  brokerReservations: [],
  brokerCatalogItems: [],
  brokerDecision: null,
  jobs: [],
  logs: [],
  system: null,
};

const titles = {
  dashboard: "Dashboard",
  wizard: "Wizard",
  catalog: "Catalog",
  broker: "Broker",
  providers: "Providers",
  accounts: "Accounts",
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
    </tr>
  `);
  target.innerHTML = `${table(["Title", "Group", "TVG ID", "CUID", "Show", "Season", "Episode", "Confidence"], rows)}<p class="muted helper-text">Showing up to 100 records. Use the API limit and offset parameters for deeper pages.</p>`;
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

function renderBroker() {
  const status = state.brokerStatus;
  document.getElementById("broker-active-reservations").textContent = status.active_reservations;
  document.getElementById("broker-total-reservations").textContent = status.total_reservations;
  document.getElementById("broker-released-reservations").textContent = status.released_reservations;
  document.getElementById("broker-expired-reservations").textContent = status.expired_reservations;
  document.getElementById("broker-capacity-accounts").textContent = status.accounts_at_capacity;
  document.getElementById("broker-available-accounts").textContent = status.available_accounts;
  const itemSelect = document.getElementById("broker-catalog-item");
  const selectedItem = itemSelect.value;
  itemSelect.innerHTML = brokerCatalogOptions();
  itemSelect.value = state.brokerCatalogItems.some((item) => item.internal_id === selectedItem) ? selectedItem : "";
  renderBrokerDecision();
  renderBrokerAccountUsage();
  renderBrokerReservations();
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
    <p class="muted helper-text url-cell">${decision.stream_url}</p>
    <p class="helper-text"><strong>Why selected:</strong> ${decision.decision_reason}</p>
    <div class="button-row">
      <button data-release-reservation="${reservation.reservation_id}">Release Reservation</button>
    </div>
    <div class="muted-list">${decision.decision_reasons.map((reason) => `<div>${reason}</div>`).join("")}</div>
    ${candidateTable(decision.evaluated_candidates || [])}
  `;
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
      <td>${account.active_reservations} / ${account.max_simultaneous_streams}</td>
      <td>${badge(account.health_status, account.health_status)}</td>
      <td>${account.available ? "Available" : account.at_capacity ? "At capacity" : "Unavailable"}</td>
    </tr>
  `);
  document.getElementById("broker-account-usage").innerHTML = table(["Account", "Provider", "Priority", "Weight", "Usage", "Health", "Status"], rows);
}

function renderBrokerReservations() {
  const rows = state.brokerReservations.map((reservation) => `
    <tr>
      <td><code>${reservation.reservation_id}</code></td>
      <td>${badge(reservation.status, reservation.status)}</td>
      <td><strong>${reservation.catalog_title || reservation.catalog_item_id}</strong><br><code>${reservation.catalog_item_id}</code></td>
      <td>${reservation.media_type}</td>
      <td>${reservation.account_name || reservation.account_id}</td>
      <td>${remainingTtl(reservation.expires_at, reservation.status)}</td>
      <td>${formatDate(reservation.expires_at)}</td>
      <td>${reservation.client_label || ""}</td>
      <td>${reservation.status === "active" ? `<button data-release-reservation="${reservation.reservation_id}">Release</button>` : ""}</td>
    </tr>
  `);
  const target = document.getElementById("broker-reservations-list");
  target.innerHTML = table(["Reservation", "Status", "Catalog Item", "Type", "Account", "Remaining", "Expires", "Client", "Actions"], rows);
  bindBrokerReleaseButtons(target);
}

function bindBrokerReleaseButtons(scope = document) {
  scope.querySelectorAll("[data-release-reservation]").forEach((button) => button.addEventListener("click", async () => {
    const reservationId = button.dataset.releaseReservation;
    await api("/api/broker/release", { method: "POST", body: JSON.stringify({ reservation_id: reservationId }) });
    toast("Reservation released");
    await loadBroker();
    renderBroker();
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
    reservation_ttl_seconds: values.reservation_ttl_seconds ? Number(values.reservation_ttl_seconds) : null,
  };
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
async function loadProviders() { state.providers = await api("/api/providers"); }
async function loadAccounts() { state.accounts = await api("/api/accounts"); }
async function loadBroker() {
  [state.brokerStatus, state.brokerReservations, state.brokerCatalogItems] = await Promise.all([
    api("/api/broker/status"),
    api("/api/broker/reservations"),
    api("/api/catalog/items?limit=500&offset=0"),
  ]);
}
async function loadCatalog() {
  [state.catalogSummary, state.providers, state.accounts] = await Promise.all([api("/api/catalog/summary"), api("/api/providers"), api("/api/accounts")]);
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
    if (state.view === "providers") { await loadProviders(); renderProviders(); }
    if (state.view === "accounts") { await Promise.all([loadProviders(), loadAccounts()]); renderAccounts(); }
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
      toast("Broker reservation created");
      await loadBroker();
      renderBroker();
      await loadDashboard();
      renderDashboard();
    } catch (error) {
      state.brokerDecision = null;
      setFormError("broker-resolve-error", error.message);
      toast(error.message);
      await loadBroker();
      renderBroker();
    }
  });
  document.getElementById("expire-broker-reservations").addEventListener("click", async () => {
    await api("/api/broker/expire-now", { method: "POST" });
    toast("Expired reservations updated");
    await loadBroker();
    renderBroker();
    await loadDashboard();
    renderDashboard();
  });
  document.getElementById("release-all-broker-reservations").addEventListener("click", async () => {
    await api("/api/broker/release-all", { method: "POST" });
    toast("Active reservations released");
    state.brokerDecision = null;
    await loadBroker();
    renderBroker();
    await loadDashboard();
    renderDashboard();
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
