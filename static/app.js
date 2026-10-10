// Flips the header status indicator based on a local /health check.
async function checkStatus() {
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");
  try {
    const res = await fetch("/health");
    if (!res.ok) throw new Error("bad status");
    dot.className = "status-dot status-dot--ok";
    text.textContent = "Local system online";
  } catch {
    dot.className = "status-dot status-dot--down";
    text.textContent = "Local system unavailable";
  }
}

function row(label, value) {
  const li = document.createElement("div");
  li.className = "incident-row";
  const l = document.createElement("span");
  l.className = "incident-label";
  l.textContent = label;
  const v = document.createElement("span");
  v.textContent = value;
  li.append(l, v);
  return li;
}

function renderIncidents(incidents) {
  const list = document.getElementById("incident-list");
  list.textContent = "";

  if (incidents.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = "No active incidents. The system is ready.";
    list.append(empty);
    return;
  }

  // Newest first.
  for (const inc of [...incidents].reverse()) {
    const item = document.createElement("li");
    item.className = "incident";

    const head = document.createElement("div");
    head.className = "incident-head";
    const id = document.createElement("strong");
    id.textContent = inc.id;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = inc.status;
    head.append(id, badge);
    const sev = inc.analysis?.severity;
    if (sev) {
      const sevBadge = document.createElement("span");
      sevBadge.className = `badge badge--sev badge--${sev.toLowerCase()}`;
      sevBadge.textContent = `${sev} · ${inc.analysis.score}/100`;
      head.append(sevBadge);
    }
    if (inc.safety_concern) {
      const safety = document.createElement("span");
      safety.className = "badge badge--safety";
      safety.textContent = "Safety concern";
      head.append(safety);
    }

    item.append(head);
    item.append(row("Station", inc.station));
    item.append(row("Category", inc.category));
    item.append(row("Description", inc.description));
    item.append(row("Passengers affected", String(inc.passengers_affected)));
    item.append(row("Reported", new Date(inc.created_at).toLocaleString()));
    if (inc.analysis) {
      item.append(row("Why", inc.analysis.summary));
      const actionsWrap = document.createElement("div");
      actionsWrap.className = "incident-row";
      const al = document.createElement("span");
      al.className = "incident-label";
      al.textContent = "Recommended (decision support)";
      const ul = document.createElement("ul");
      ul.className = "actions";
      for (const a of inc.analysis.recommended_actions || []) {
        const li = document.createElement("li");
        li.textContent = a;
        ul.append(li);
      }
      actionsWrap.append(al, ul);
      item.append(actionsWrap);
    }
    list.append(item);
  }
}

async function loadIncidents() {
  const list = document.getElementById("incident-list");
  try {
    const res = await fetch("/incidents");
    if (!res.ok) throw new Error("bad status");
    renderIncidents(await res.json());
  } catch {
    list.textContent = "";
    const err = document.createElement("li");
    err.className = "empty-state";
    err.textContent = "Unable to load incidents.";
    list.append(err);
  }
}

function readValidationErrors(data) {
  if (Array.isArray(data?.detail)) {
    return data.detail
      .map((e) => `${e.loc?.slice(1).join(".") || "field"}: ${e.msg}`)
      .join("; ");
  }
  return data?.detail || "Submission failed.";
}

async function submitIncident(event) {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector("button[type=submit]");
  const message = document.getElementById("form-message");
  message.className = "form-message";
  message.textContent = "";

  const payload = {
    station: form.station.value,
    category: form.category.value,
    description: form.description.value,
    passengers_affected: Number(form.passengers.value),
    safety_concern: form.safety.checked,
  };

  button.disabled = true;
  try {
    const res = await fetch("/incidents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      message.classList.add("form-message--error");
      message.textContent = readValidationErrors(data);
      return;
    }
    message.classList.add("form-message--ok");
    message.textContent = `Incident ${data.id} submitted.`;
    form.reset();
    form.passengers.value = "0";
    loadIncidents();
  } catch {
    message.classList.add("form-message--error");
    message.textContent = "Network error. Could not reach the local server.";
  } finally {
    button.disabled = false;
  }
}

document.getElementById("incident-form").addEventListener("submit", submitIncident);
checkStatus();
loadIncidents();
