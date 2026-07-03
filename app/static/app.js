const state = {
  view: "dashboard",
  foundation: null,
  dashboard: null,
  settings: null,
  wizardSteps: [],
  wizardState: null,
  jobs: [],
};

const titles = {
  dashboard: "Dashboard",
  wizard: "Wizard",
  settings: "Settings",
  jobs: "Jobs",
  foundation: "Foundation",
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

function setView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === view));
  document.querySelectorAll("nav button").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
  document.getElementById("view-title").textContent = titles[view];
  refresh();
}

function renderDashboard() {
  const data = state.dashboard;
  document.getElementById("health").textContent = data.health;
  document.getElementById("wizard-progress").textContent = `${data.wizard_progress}%`;
  document.getElementById("jobs-count").textContent = `${data.jobs_total}`;
  document.getElementById("settings-count").textContent = `${data.settings_count}`;
  document.getElementById("sprint-scope").innerHTML = data.sprint_scope.map((item) => `<div>☑ ${item}</div>`).join("");
  document.getElementById("deferred-scope").innerHTML = data.deferred_scope.map((item) => `<div>☐ ${item}</div>`).join("");
}

function renderWizard() {
  const completed = new Set(state.wizardState.completed_steps);
  document.getElementById("wizard-state").textContent = state.wizardState.setup_complete ? "Complete" : "In progress";
  document.getElementById("wizard-steps").innerHTML = state.wizardSteps
    .map((step) => {
      const isDone = completed.has(step.id);
      const isCurrent = state.wizardState.current_step === step.id;
      return `
        <article class="step ${isCurrent ? "current" : ""}">
          <div>
            <strong>${isDone ? "☑" : "☐"} ${step.label}</strong>
            <p>${step.description}</p>
          </div>
          <button data-complete-step="${step.id}" ${isDone ? "disabled" : ""}>Complete</button>
        </article>
      `;
    })
    .join("");
  document.querySelectorAll("[data-complete-step]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.wizardState = await api(`/api/wizard/steps/${button.dataset.completeStep}/complete`, { method: "POST" });
      await loadDashboard();
      renderWizard();
      renderDashboard();
    });
  });
}

function renderSettings() {
  const form = document.getElementById("settings-form");
  for (const [key, value] of Object.entries(state.settings)) {
    const input = form.elements[key];
    if (input) input.value = value;
  }
}

function renderJobs() {
  const list = document.getElementById("jobs-list");
  if (!state.jobs.length) {
    list.innerHTML = '<p class="muted">No jobs yet.</p>';
    return;
  }
  list.innerHTML = state.jobs
    .map(
      (job) => `
        <article class="job">
          <div>
            <strong>${job.kind}</strong>
            <p>${job.message}</p>
          </div>
          <div class="progress"><span style="width:${job.progress}%"></span></div>
          <span class="badge">${job.status}</span>
        </article>
      `,
    )
    .join("");
}

function renderFoundation() {
  document.getElementById("principles").innerHTML = state.foundation.principles
    .map((principle) => `<article class="principle"><strong>${principle.number}. ${principle.title}</strong><p>${principle.rule}</p></article>`)
    .join("");
  document.getElementById("modules").innerHTML = state.foundation.modules
    .map((module) => `<article class="module"><span class="badge">${module.status}</span><h2>${module.label}</h2><p>${module.responsibility}</p></article>`)
    .join("");
}

async function loadDashboard() {
  state.dashboard = await api("/api/dashboard");
}

async function loadWizard() {
  [state.wizardSteps, state.wizardState] = await Promise.all([api("/api/wizard/steps"), api("/api/wizard/state")]);
}

async function loadSettings() {
  state.settings = await api("/api/settings");
}

async function loadJobs() {
  state.jobs = await api("/api/jobs");
}

async function loadFoundation() {
  state.foundation = await api("/api/foundation");
}

async function refresh() {
  try {
    await loadDashboard();
    renderDashboard();
    if (state.view === "wizard") {
      await loadWizard();
      renderWizard();
    }
    if (state.view === "settings") {
      await loadSettings();
      renderSettings();
    }
    if (state.view === "jobs") {
      await loadJobs();
      renderJobs();
    }
    if (state.view === "foundation") {
      await loadFoundation();
      renderFoundation();
    }
  } catch (error) {
    toast(error.message);
  }
}

function bind() {
  document.querySelectorAll("nav button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  document.getElementById("refresh").addEventListener("click", refresh);
  document.getElementById("save-settings").addEventListener("click", async () => {
    const values = Object.fromEntries(new FormData(document.getElementById("settings-form")).entries());
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(values) });
    toast("Settings saved");
    renderSettings();
    await loadDashboard();
    renderDashboard();
  });
  document.getElementById("start-job").addEventListener("click", async () => {
    await api("/api/jobs", { method: "POST", body: JSON.stringify({ kind: "foundation_check" }) });
    toast("Job started");
    setView("jobs");
    const timer = setInterval(async () => {
      await loadJobs();
      renderJobs();
      await loadDashboard();
      renderDashboard();
      if (!state.jobs.some((job) => ["queued", "running"].includes(job.status))) clearInterval(timer);
    }, 700);
  });
}

bind();
refresh();
