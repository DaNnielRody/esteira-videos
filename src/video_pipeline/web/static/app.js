"use strict";

const state = {
  csrf: "",
  projectId: null,
  project: null,
  ui: null,
  projects: [],
  jobs: [],
  selectedRevision: null,
  selectedSceneId: null,
  pollToken: 0,
};

const elements = {
  connection: document.querySelector("#connection-status"),
  createForm: document.querySelector("#create-form"),
  createSubmit: document.querySelector("#create-form button[type=submit]"),
  projectSelector: document.querySelector("#project-selector"),
  openProject: document.querySelector("#open-project-button"),
  projectCount: document.querySelector("#project-count"),
  jobList: document.querySelector("#job-list"),
  jobCount: document.querySelector("#job-count"),
  confirm: document.querySelector("#confirm-button"),
  render: document.querySelector("#render-button"),
  correctionForm: document.querySelector("#correction-form"),
  regenerate: document.querySelector("#regenerate-button"),
  accept: document.querySelector("#accept-button"),
  projectTitle: document.querySelector("#project-title-display"),
  projectId: document.querySelector("#project-id"),
  timelineStatus: document.querySelector("#timeline-status"),
  runId: document.querySelector("#run-id"),
  revisionList: document.querySelector("#revision-list"),
  revisionCount: document.querySelector("#revision-count"),
  sceneList: document.querySelector("#scene-list"),
  finalVideo: document.querySelector("#final-video"),
  sceneVideo: document.querySelector("#scene-video"),
  finalLabel: document.querySelector("#final-label"),
  badge: document.querySelector("#state-badge"),
  stage: document.querySelector("#job-stage"),
  progress: document.querySelector("#progress-fill"),
  log: document.querySelector("#operator-log"),
  conversation: document.querySelector("#revision-conversation"),
  actionNext: document.querySelector("#action-next"),
  diagnostic: document.querySelector("#diagnostic"),
  golden: document.querySelector("#golden-status"),
};

function setStatus(label, tone, message) {
  elements.badge.textContent = label;
  elements.badge.dataset.state = tone;
  if (message) elements.log.textContent = message;
}

function setDiagnostic(message) {
  elements.diagnostic.hidden = !message;
  elements.diagnostic.textContent = message || "";
}

async function request(path, options = {}) {
  const headers = {Accept: "application/json", ...(options.headers || {})};
  if (options.method && options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = state.csrf;
  }
  const response = await fetch(path, {...options, headers, credentials: "same-origin"});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "request failed");
  return payload;
}

function renderProjectOptions(projects) {
  elements.projectSelector.replaceChildren();
  elements.projectCount.textContent = String(projects.length);
  if (projects.length === 0) {
    elements.projectSelector.append(new Option("Nenhum projeto encontrado", ""));
    elements.projectSelector.value = "";
    elements.openProject.disabled = true;
    return;
  }
  for (const project of projects) {
    const projectId = project.id || project.project_id;
    const title = project.title || projectId;
    elements.projectSelector.append(new Option(`${title} · ${projectId}`, projectId));
  }
  elements.projectSelector.value = state.projectId || projects[0].id || projects[0].project_id;
  elements.openProject.disabled = !elements.projectSelector.value;
}

function rememberProject(project) {
  const projectData = project?.project;
  if (!projectData?.id) return;
  state.projects = [
    {
      id: projectData.id,
      project_id: projectData.id,
      title: projectData.title,
    },
    ...state.projects.filter(
      item => (item.id || item.project_id) !== projectData.id,
    ),
  ];
  renderProjectOptions(state.projects);
}

function renderJobs(jobs) {
  elements.jobList.replaceChildren();
  elements.jobCount.textContent = String(jobs.length);
  if (jobs.length === 0) {
    const empty = document.createElement("p");
    empty.className = "selection-empty";
    empty.textContent = "Nenhum job recuperável.";
    elements.jobList.append(empty);
    return;
  }
  for (const job of jobs) {
    const item = document.createElement("div");
    item.className = "job-item";
    const heading = document.createElement("div");
    heading.className = "section-heading";
    const label = document.createElement("strong");
    label.textContent = `${job.job_id} · ${job.state}`;
    const meta = document.createElement("span");
    meta.className = "selection-meta";
    meta.textContent = `${job.project_id} · ${job.stage || job.state}`;
    heading.append(label, meta);
    item.append(heading);
    const diagnostics = Array.isArray(job.diagnostics)
      ? job.diagnostics
      : job.error
      ? [job.error]
      : [];
    if (diagnostics.length > 0) {
      const diagnostic = document.createElement("p");
      diagnostic.className = "job-diagnostic";
      diagnostic.textContent = diagnostics.join(" · ");
      item.append(diagnostic);
    }
    if (job.state === "interrupted" || job.state === "failure") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "button button-primary job-retry";
      retry.textContent = "Tentar novamente";
      retry.setAttribute("aria-label", `Tentar novamente ${job.job_id}`);
      retry.addEventListener("click", async () => {
        const projectId = job.project_id;
        let token = null;
        try {
          if (!await openProject(projectId)) return;
          token = state.pollToken;
          const retried = await request(`/api/jobs/${job.job_id}/retry`, {
            method: "POST",
            body: "{}",
          });
          if (!isCurrentOperation(projectId, token)) return;
          beginPolling(retried, projectId);
        } catch (error) {
          if (token !== null) showFailureIfCurrent(error, projectId, token);
        }
      });
      item.append(retry);
    }
    elements.jobList.append(item);
  }
}

async function refreshProjects(expectedToken = null) {
  const payload = await request("/api/projects");
  if (expectedToken !== null && expectedToken !== state.pollToken) return false;
  let projects = Array.isArray(payload) ? payload : [];
  const currentProject = state.project?.project;
  if (
    currentProject
    && state.projectId
    && !projects.some(project => (project.id || project.project_id) === state.projectId)
  ) {
    projects = [
      {id: state.projectId, project_id: state.projectId, title: currentProject.title},
      ...projects,
    ];
  }
  state.projects = projects;
  renderProjectOptions(state.projects);
  return state.projects;
}

async function refreshJobs(expectedToken = null) {
  const jobs = await request("/api/jobs");
  if (expectedToken !== null && expectedToken !== state.pollToken) return false;
  state.jobs = Array.isArray(jobs) ? jobs : [];
  renderJobs(state.jobs);
  return state.jobs;
}

function selectedRevision(ui) {
  if (!ui || !Array.isArray(ui.revisions)) return null;
  return ui.revisions.find(item => item.revision_id === ui.current_revision_id) || null;
}

function renderRevisions(ui) {
  elements.revisionList.replaceChildren();
  const revisions = ui?.revisions || [];
  elements.revisionCount.textContent = String(revisions.length);
  for (const revision of revisions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "selection-item";
    button.setAttribute(
      "aria-pressed",
      String(revision.revision_id === state.selectedRevision?.revision_id),
    );
    const label = document.createElement("span");
    label.textContent = revision.status === "success"
      ? `${revision.revision_id} · Restaurar`
      : `${revision.revision_id} · Inspecionar falha`;
    const meta = document.createElement("span");
    meta.className = "selection-meta";
    meta.textContent = revision.status;
    button.append(label, meta);
    button.addEventListener("click", () => selectRevision(revision));
    elements.revisionList.append(button);
  }
}

function isLoadedProject(projectId) {
  return Boolean(
    projectId
    && state.projectId === projectId
    && state.project?.project?.id === projectId
  );
}

function disableProjectActions() {
  elements.confirm.disabled = true;
  elements.render.disabled = true;
  elements.regenerate.disabled = true;
  elements.accept.disabled = true;
  for (const button of elements.revisionList.querySelectorAll("button")) {
    button.disabled = true;
  }
  for (const button of elements.sceneList.querySelectorAll("button")) {
    button.disabled = true;
  }
}

function selectRevision(revision) {
  if (!isLoadedProject(state.projectId)) return;
  if (revision.status === "success") {
    checkoutRevision(revision.revision_id);
    return;
  }
  state.pollToken += 1;
  state.selectedRevision = revision;
  renderRevisions(state.ui);
  renderConversation(revision);
  elements.runId.textContent = revision.run_id || "—";
  if (revision.status === "failure") {
    const message = revision.messages?.at(-1) || "Falha no render.";
    elements.finalVideo.src = "";
    elements.sceneVideo.src = "";
    elements.finalLabel.textContent = "Sem composição";
    elements.regenerate.disabled = true;
    elements.accept.disabled = true;
    if (state.project) renderScenes(state.project, null);
    setDiagnostic(message);
    setStatus("Falha", "failure", message);
    return;
  }
  setDiagnostic("");
  setStatus("Conteúdo pronto", "success", `Revisão ${revision.revision_id} selecionada.`);
}

function renderScenes(project, ui) {
  elements.sceneList.replaceChildren();
  const media = ui?.media?.scenes || [];
  const segments = project.timeline?.segments || [];
  if (!state.selectedSceneId || !media.some(item => item.scene_id === state.selectedSceneId)) {
    state.selectedSceneId = media[0]?.scene_id || null;
  }
  for (const segment of segments) {
    const sceneMedia = media.find(item => item.scene_id === segment.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "selection-item";
    button.setAttribute("aria-pressed", String(segment.id === state.selectedSceneId));
    button.disabled = !sceneMedia;
    const label = document.createElement("span");
    label.textContent = segment.id;
    const meta = document.createElement("span");
    meta.className = "selection-meta";
    meta.textContent = sceneMedia ? "MP4 pronto" : "Sem mídia";
    button.append(label, meta);
    button.addEventListener("click", () => {
      state.selectedSceneId = segment.id;
      renderScenes(project, ui);
    });
    elements.sceneList.append(button);
  }
  const selected = media.find(item => item.scene_id === state.selectedSceneId);
  elements.sceneVideo.src = selected ? `/api/assets/${selected.normalized_asset_id}` : "";
}

function renderConversation(revision) {
  elements.conversation.replaceChildren();
  const entries = [];
  if (revision?.correction) {
    entries.push({speaker: "operator", text: revision.correction});
  }
  for (const message of revision?.messages || []) {
    entries.push({speaker: "pipeline", text: message});
  }
  if (entries.length === 0) {
    const empty = document.createElement("p");
    empty.className = "conversation-empty";
    empty.textContent = "Nenhuma mensagem nesta revisão.";
    elements.conversation.append(empty);
    return;
  }
  for (const entry of entries) {
    const message = document.createElement("p");
    message.className = "conversation-entry";
    message.dataset.speaker = entry.speaker;
    message.textContent = `${entry.speaker === "operator" ? "Operador" : "Pipeline"}: ${entry.text}`;
    elements.conversation.append(message);
  }
}

function renderProject(project) {
  state.project = project;
  state.projectId = project.project.id;
  state.ui = project.ui || null;
  state.selectedRevision = selectedRevision(state.ui);
  elements.projectTitle.textContent = project.project.title;
  elements.projectId.textContent = project.project.id;
  elements.timelineStatus.textContent = `Timeline ${project.timeline.status}`;
  elements.runId.textContent = state.selectedRevision?.run_id || project.project.current_run || "—";
  elements.confirm.disabled = project.timeline.status !== "candidate";
  elements.render.disabled = project.timeline.status !== "confirmed";
  elements.regenerate.disabled = !state.selectedRevision || state.selectedRevision.status !== "success";
  elements.accept.disabled = !(
    state.selectedRevision
    && state.selectedRevision.status === "success"
    && state.selectedRevision.run_id === project.project.current_run
    && project.project.status === "ready"
  );
  elements.actionNext.textContent = project.latest_run?.action_next || "Revise o estado e prossiga.";
  elements.golden.textContent = project.project.accepted_run
    ? `Golden aceito · ${project.project.accepted_run}`
    : "Golden ainda não publicado";
  elements.golden.dataset.accepted = String(Boolean(project.project.accepted_run));
  renderRevisions(state.ui);
  renderScenes(project, state.ui);
  renderConversation(state.selectedRevision);
  const finalId = state.ui?.media?.final_asset_id;
  elements.finalVideo.src = finalId ? `/api/assets/${finalId}` : "";
  elements.finalLabel.textContent = finalId ? "MP4 validado" : "Sem composição";
  if (state.selectedRevision?.status === "failure") {
    const message = state.selectedRevision.messages?.at(-1) || "Falha no render.";
    setDiagnostic(message);
    setStatus("Falha", "failure", message);
  } else if (state.selectedRevision) {
    setDiagnostic("");
    setStatus("Conteúdo pronto", "success", "Revisão carregada.");
  } else {
    setDiagnostic("");
    setStatus("Projeto aberto", "empty", "Timeline pronta para revisão.");
  }
}

async function openProject(projectId) {
  if (!projectId) return false;
  const token = ++state.pollToken;
  state.projectId = projectId;
  disableProjectActions();
  try {
    const project = await request(`/api/projects/${projectId}`);
    if (!isCurrentOperation(projectId, token)) return false;
    history.replaceState(null, "", `#project=${encodeURIComponent(projectId)}`);
    renderProject(project);
    return true;
  } catch (error) {
    showFailureIfCurrent(error, projectId, token);
    return false;
  }
}

async function refreshProject(expectedToken = null) {
  if (!state.projectId) return false;
  const projectId = state.projectId;
  const token = expectedToken ?? state.pollToken;
  const project = await request(`/api/projects/${projectId}`);
  if (!isCurrentOperation(projectId, token)) return false;
  renderProject(project);
  if (!await refreshJobs(token)) return false;
  return isCurrentOperation(projectId, token);
}

async function pollJob(jobId, projectId, token) {
  const job = await request(`/api/jobs/${jobId}`);
  if (!isCurrentOperation(projectId, token)) return;
  elements.stage.textContent = job.stage || job.state;
  const progress = {queued: 18, running: 58, success: 100, failure: 100};
  elements.progress.style.width = `${progress[job.state] || 35}%`;
  if (job.state === "queued" || job.state === "running") {
    setStatus(job.state === "queued" ? "Na fila" : "Gerando", "loading", job.stage);
    window.setTimeout(
      () => pollJob(jobId, projectId, token).catch(
        error => showFailureIfCurrent(error, projectId, token)
      ),
      80,
    );
    return;
  }
  if (!await refreshProject(token)) return;
  if (!isCurrentOperation(projectId, token)) return;
  if (job.state === "success" && state.ui) {
    state.ui.current_revision_id = job.revision_id;
    state.selectedRevision = selectedRevision(state.ui);
    renderRevisions(state.ui);
    elements.runId.textContent = state.selectedRevision?.run_id || "—";
  }
  if (job.state === "failure") {
    setStatus("Falha", "failure", job.error || "Falha no render.");
    setDiagnostic(job.error || "O diagnóstico foi preservado.");
    if (!await refreshJobs(token)) return;
    if (!isCurrentOperation(projectId, token)) return;
    return;
  }
  if (!await refreshJobs(token)) return;
  if (!isCurrentOperation(projectId, token)) return;
  setDiagnostic("");
  setStatus("Concluído", "success", `Revisão ${job.revision_id} concluída.`);
}

function beginPolling(job, projectId) {
  const token = ++state.pollToken;
  if (!isCurrentOperation(projectId, token)) return;
  refreshJobs(token).catch(
    error => showFailureIfCurrent(error, projectId, token)
  );
  setDiagnostic("");
  setStatus("Na fila", "loading", "Job aceito na fila local.");
  elements.stage.textContent = job.stage || job.state;
  elements.progress.style.width = "12%";
  pollJob(job.job_id, projectId, token).catch(
    error => showFailureIfCurrent(error, projectId, token)
  );
}

async function checkoutRevision(revisionId) {
  const projectId = state.projectId;
  if (!isLoadedProject(projectId)) return false;
  const token = ++state.pollToken;
  try {
    await request(`/api/projects/${projectId}/checkout`, {
      method: "POST",
      body: JSON.stringify({revision_id: revisionId}),
    });
    if (!isCurrentOperation(projectId, token)) return;
    if (!await refreshProject(token)) return;
    if (!isCurrentOperation(projectId, token)) return;
    setStatus("Revisão restaurada", "success", `${revisionId} selecionada.`);
  } catch (error) {
    showFailureIfCurrent(error, projectId, token);
  }
}

function showFailure(error) {
  setStatus("Falha", "failure", "A operação não pôde ser concluída.");
  setDiagnostic(error instanceof Error ? error.message : "Falha inesperada");
}

function isCurrentOperation(projectId, token) {
  return projectId === state.projectId && isCurrentToken(token);
}

function isCurrentToken(token) {
  return token === state.pollToken;
}

function showFailureIfCurrent(error, projectId, token) {
  if (isCurrentOperation(projectId, token)) showFailure(error);
}

function showFailureIfCurrentToken(error, token) {
  if (isCurrentToken(token)) showFailure(error);
}

function setConnectionReady() {
  elements.connection.textContent = "Sessão local pronta";
  elements.connection.dataset.tone = "ready";
}

elements.projectSelector.addEventListener("change", () => {
  elements.openProject.disabled = !elements.projectSelector.value;
});

elements.openProject.addEventListener("click", () => {
  void openProject(elements.projectSelector.value);
});

elements.createForm.addEventListener("submit", async event => {
  event.preventDefault();
  const token = ++state.pollToken;
  const data = new FormData(elements.createForm);
  try {
    const project = await request("/api/projects", {
      method: "POST",
      body: JSON.stringify({
        title: data.get("title"),
        script: data.get("script"),
        audio_asset_id: data.get("audio_asset_id"),
      }),
    });
    if (!isCurrentToken(token)) return;
    renderProject(project);
    rememberProject(project);
    if (!await refreshProjects(token)) return;
    await refreshJobs(token);
  } catch (error) {
    showFailureIfCurrentToken(error, token);
  }
});

elements.confirm.addEventListener("click", async () => {
  const projectId = state.projectId;
  if (!isLoadedProject(projectId)) return;
  const token = ++state.pollToken;
  try {
    const project = await request(`/api/projects/${projectId}/timeline/confirm`, {
      method: "POST",
      body: "{}",
    });
    if (!isCurrentOperation(projectId, token)) return;
    renderProject(project);
    setStatus("Timeline confirmada", "success", "Timeline confirmada para render.");
  } catch (error) {
    showFailureIfCurrent(error, projectId, token);
  }
});

elements.render.addEventListener("click", async () => {
  const projectId = state.projectId;
  if (!isLoadedProject(projectId)) return;
  const token = ++state.pollToken;
  try {
    const job = await request(`/api/projects/${projectId}/render`, {
      method: "POST",
      body: "{}",
    });
    if (!isCurrentOperation(projectId, token)) return;
    beginPolling(job, projectId);
  } catch (error) {
    showFailureIfCurrent(error, projectId, token);
  }
});

elements.correctionForm.addEventListener("submit", async event => {
  event.preventDefault();
  const correction = new FormData(elements.correctionForm).get("correction");
  const projectId = state.projectId;
  if (!isLoadedProject(projectId) || !state.selectedRevision || !state.selectedSceneId) return;
  const runId = state.selectedRevision.run_id;
  const sceneId = state.selectedSceneId;
  const token = ++state.pollToken;
  try {
    const job = await request(`/api/projects/${projectId}/regenerate`, {
      method: "POST",
      body: JSON.stringify({
        base_run_id: runId,
        scene_id: sceneId,
        correction,
      }),
    });
    if (!isCurrentOperation(projectId, token)) return;
    beginPolling(job, projectId);
  } catch (error) {
    showFailureIfCurrent(error, projectId, token);
  }
});

elements.accept.addEventListener("click", async () => {
  const projectId = state.projectId;
  if (!isLoadedProject(projectId) || !state.selectedRevision) return;
  const runId = state.selectedRevision.run_id;
  const token = ++state.pollToken;
  try {
    await request(`/api/projects/${projectId}/accept`, {
      method: "POST",
      body: JSON.stringify({run_id: runId}),
    });
    if (!isCurrentOperation(projectId, token)) return;
    if (!await refreshProject(token)) return;
    if (!isCurrentOperation(projectId, token)) return;
    elements.golden.textContent = `Golden aceito · ${runId}`;
    elements.golden.dataset.accepted = "true";
  } catch (error) {
    showFailureIfCurrent(error, projectId, token);
  }
});

async function bootstrap() {
  const bootstrapToken = state.pollToken;
  try {
    const session = await request("/api/session");
    state.csrf = session.csrf_token;
    const audioPayload = await request("/api/audio");
    const audio = Array.isArray(audioPayload) ? audioPayload : audioPayload.assets || [];
    const audioDiagnostic = Array.isArray(audioPayload) ? "" : audioPayload.diagnostic || "";
    const select = document.querySelector("[name=audio_asset_id]");
    select.replaceChildren(new Option("Selecione uma narração", ""));
    for (const asset of audio) select.add(new Option(asset.label, asset.id));
    if (audio.length === 0) {
      const message = audioDiagnostic || (
        "Nenhuma narração configurada. Adicione pelo menos um arquivo de áudio " +
        "antes de criar ou renderizar um vídeo real."
      );
      select.replaceChildren(new Option("Narração necessária", ""));
      select.disabled = true;
      elements.createSubmit.disabled = true;
      state.audioDiagnostic = message;
    } else {
      select.disabled = false;
      elements.createSubmit.disabled = false;
      state.audioDiagnostic = "";
    }
    const projects = await refreshProjects(bootstrapToken);
    if (projects === false || !isCurrentToken(bootstrapToken)) {
      setConnectionReady();
      return;
    }
    const jobs = await refreshJobs(bootstrapToken);
    if (jobs === false || !isCurrentToken(bootstrapToken)) {
      setConnectionReady();
      return;
    }
    const hashProject = new URLSearchParams(location.hash.replace(/^#/, "")).get("project");
    const preferred = projects.find(project => (project.id || project.project_id) === hashProject);
    const first = preferred || projects[0];
    let opened = false;
    if (first) {
      opened = await openProject(first.id || first.project_id);
      if (!opened) {
        if (state.projectId !== (first.id || first.project_id)) setConnectionReady();
        return;
      }
    } else {
      state.projectId = null;
      setStatus("Sem projetos", "empty", "Crie um projeto local para começar.");
    }
    if (opened) {
      const activeJob = jobs.find(
        job => job.project_id === state.projectId
          && (job.state === "queued" || job.state === "running")
      );
      if (activeJob) beginPolling(activeJob, state.projectId);
    }
    if (state.audioDiagnostic) {
      setDiagnostic(state.audioDiagnostic);
      setStatus("Áudio necessário", "empty", state.audioDiagnostic);
    }
    setConnectionReady();
  } catch (error) {
    showFailureIfCurrentToken(error, bootstrapToken);
  }
}

bootstrap();
