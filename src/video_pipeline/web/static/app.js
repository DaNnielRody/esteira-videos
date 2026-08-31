"use strict";

const state = {
  csrf: "",
  projectId: null,
  project: null,
  ui: null,
  selectedRevision: null,
  selectedSceneId: null,
  pollToken: 0,
};

const elements = {
  connection: document.querySelector("#connection-status"),
  createForm: document.querySelector("#create-form"),
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
    button.setAttribute("aria-pressed", String(revision.revision_id === ui.current_revision_id));
    button.disabled = revision.status !== "success";
    const label = document.createElement("span");
    label.textContent = `${revision.revision_id} · Restaurar`;
    const meta = document.createElement("span");
    meta.className = "selection-meta";
    meta.textContent = revision.status;
    button.append(label, meta);
    button.addEventListener("click", () => checkoutRevision(revision.revision_id));
    elements.revisionList.append(button);
  }
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

async function refreshProject(expectedToken = null) {
  if (!state.projectId) return;
  const projectId = state.projectId;
  const project = await request(`/api/projects/${projectId}`);
  if (
    projectId !== state.projectId
    || (expectedToken !== null && expectedToken !== state.pollToken)
  ) return false;
  renderProject(project);
  return true;
}

async function pollJob(jobId, token) {
  const job = await request(`/api/jobs/${jobId}`);
  if (token !== state.pollToken) return;
  elements.stage.textContent = job.stage || job.state;
  const progress = {queued: 18, running: 58, success: 100, failure: 100};
  elements.progress.style.width = `${progress[job.state] || 35}%`;
  if (job.state === "queued" || job.state === "running") {
    setStatus(job.state === "queued" ? "Na fila" : "Gerando", "loading", job.stage);
    window.setTimeout(() => pollJob(jobId, token).catch(showFailure), 80);
    return;
  }
  if (!await refreshProject(token)) return;
  if (job.state === "success" && state.ui) {
    state.ui.current_revision_id = job.revision_id;
    state.selectedRevision = selectedRevision(state.ui);
    renderRevisions(state.ui);
    elements.runId.textContent = state.selectedRevision?.run_id || "—";
  }
  if (job.state === "failure") {
    setStatus("Falha", "failure", job.error || "Falha no render.");
    setDiagnostic(job.error || "O diagnóstico foi preservado.");
    return;
  }
  setDiagnostic("");
  setStatus("Concluído", "success", `Revisão ${job.revision_id} concluída.`);
}

function beginPolling(job) {
  const token = ++state.pollToken;
  setDiagnostic("");
  setStatus("Na fila", "loading", "Job aceito na fila local.");
  elements.stage.textContent = job.stage || job.state;
  elements.progress.style.width = "12%";
  pollJob(job.job_id, token).catch(showFailure);
}

async function checkoutRevision(revisionId) {
  ++state.pollToken;
  await request(`/api/projects/${state.projectId}/checkout`, {
    method: "POST",
    body: JSON.stringify({revision_id: revisionId}),
  });
  await refreshProject();
  setStatus("Revisão restaurada", "success", `${revisionId} selecionada.`);
}

function showFailure(error) {
  setStatus("Falha", "failure", "A operação não pôde ser concluída.");
  setDiagnostic(error instanceof Error ? error.message : "Falha inesperada");
}

elements.createForm.addEventListener("submit", async event => {
  event.preventDefault();
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
    ++state.pollToken;
    renderProject(project);
  } catch (error) {
    showFailure(error);
  }
});

elements.confirm.addEventListener("click", async () => {
  try {
    renderProject(await request(`/api/projects/${state.projectId}/timeline/confirm`, {
      method: "POST",
      body: "{}",
    }));
    setStatus("Timeline confirmada", "success", "Timeline confirmada para render.");
  } catch (error) {
    showFailure(error);
  }
});

elements.render.addEventListener("click", async () => {
  try {
    beginPolling(await request(`/api/projects/${state.projectId}/render`, {
      method: "POST",
      body: "{}",
    }));
  } catch (error) {
    showFailure(error);
  }
});

elements.correctionForm.addEventListener("submit", async event => {
  event.preventDefault();
  const correction = new FormData(elements.correctionForm).get("correction");
  if (!state.selectedRevision || !state.selectedSceneId) return;
  try {
    beginPolling(await request(`/api/projects/${state.projectId}/regenerate`, {
      method: "POST",
      body: JSON.stringify({
        base_run_id: state.selectedRevision.run_id,
        scene_id: state.selectedSceneId,
        correction,
      }),
    }));
  } catch (error) {
    showFailure(error);
  }
});

elements.accept.addEventListener("click", async () => {
  if (!state.selectedRevision) return;
  try {
    await request(`/api/projects/${state.projectId}/accept`, {
      method: "POST",
      body: JSON.stringify({run_id: state.selectedRevision.run_id}),
    });
    await refreshProject();
    elements.golden.textContent = `Golden aceito · ${state.selectedRevision.run_id}`;
    elements.golden.dataset.accepted = "true";
  } catch (error) {
    showFailure(error);
  }
});

async function bootstrap() {
  try {
    const session = await request("/api/session");
    state.csrf = session.csrf_token;
    const audio = await request("/api/audio");
    const select = document.querySelector("[name=audio_asset_id]");
    select.replaceChildren(new Option("Selecione uma narração", ""));
    for (const asset of audio) select.add(new Option(asset.label, asset.id));
    elements.connection.textContent = "Sessão local pronta";
    elements.connection.dataset.tone = "ready";
  } catch (error) {
    showFailure(error);
  }
}

bootstrap();
