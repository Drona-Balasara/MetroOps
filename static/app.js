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

// Mirrors the server lifecycle: one forward step per status (see ALLOWED_TRANSITIONS).
const NEXT_ACTION = {
  "Active": { status: "Acknowledged", label: "Acknowledge" },
  "Acknowledged": { status: "In Progress", label: "Mark in progress" },
  "In Progress": { status: "Resolved", label: "Resolve" },
};

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
    empty.textContent = "No incidents to show.";
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
    badge.className = `badge badge--status status--${inc.status.toLowerCase().replace(/\s+/g, "-")}`;
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

    if (Array.isArray(inc.history) && inc.history.length) {
      const histWrap = document.createElement("div");
      histWrap.className = "incident-row";
      const hl = document.createElement("span");
      hl.className = "incident-label";
      hl.textContent = "Status history";
      const hul = document.createElement("ul");
      hul.className = "history";
      for (const h of inc.history) {
        const li = document.createElement("li");
        const when = new Date(h.at).toLocaleString();
        li.textContent = h.from ? `${h.from} → ${h.to} (${when})` : `${h.to} (${when})`;
        hul.append(li);
      }
      histWrap.append(hl, hul);
      item.append(histWrap);
    }

    const next = NEXT_ACTION[inc.status];
    if (next) {
      const controls = document.createElement("div");
      controls.className = "incident-controls";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-sm";
      btn.textContent = next.label;
      const msg = document.createElement("span");
      msg.className = "status-msg";
      btn.addEventListener("click", () => updateStatus(inc.id, next.status, btn, msg));
      controls.append(btn, msg);
      item.append(controls);
    }

    list.append(item);
  }
}

let allIncidents = [];

// Re-renders the list honoring the current status filter (empty value = all).
function applyFilter() {
  const want = document.getElementById("status-filter").value;
  renderIncidents(want ? allIncidents.filter((i) => i.status === want) : allIncidents);
}

async function loadIncidents() {
  const list = document.getElementById("incident-list");
  try {
    const res = await fetch("/incidents");
    if (!res.ok) throw new Error("bad status");
    allIncidents = await res.json();
    applyFilter();
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

async function updateStatus(id, status, button, msg) {
  button.disabled = true;
  msg.className = "status-msg";
  msg.textContent = "";
  try {
    const res = await fetch(`/incidents/${encodeURIComponent(id)}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      msg.classList.add("status-msg--error");
      msg.textContent = typeof data.detail === "string" ? data.detail : "Status update failed.";
      button.disabled = false;
      return;
    }
    loadIncidents();  // re-render with new status, history and controls
  } catch {
    msg.classList.add("status-msg--error");
    msg.textContent = "Network error. Could not reach the local server.";
    button.disabled = false;
  }
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
document.getElementById("status-filter").addEventListener("change", applyFilter);
checkStatus();
loadIncidents();
