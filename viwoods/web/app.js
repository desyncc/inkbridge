// Viwoods Companion Web App Logic

let activeNote = null;
let activePageIndex = 0;
let syncPollingTimer = null;
// Last configuration loaded from the server, used to send partial updates.
let savedConfig = {};

document.addEventListener("DOMContentLoaded", async () => {
  initTabs();
  initSettingsToggles();
  bindEvents();
  // Settings first: other views label themselves with the configured folders.
  await loadSettings();
  loadStatus();
  loadTree();
  loadNotes();
  loadJournals();
});

// --- Tab Switching ---
function initTabs() {
  const tabs = document.querySelectorAll(".tab-btn");
  tabs.forEach(btn => {
    btn.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));

      btn.classList.add("active");
      const targetId = btn.dataset.tab;
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add("active");
    });
  });
}

// Single source of truth for engine-specific settings visibility.
function updateEngineSections() {
  const val = document.getElementById("cfgOcrEngine").value;
  const sections = {
    ollama: document.getElementById("secOllama"),
    gemini: document.getElementById("secGemini"),
    lmstudio: document.getElementById("secLMStudio")
  };
  Object.entries(sections).forEach(([engine, el]) => {
    if (el) el.classList.toggle("hidden", val !== engine);
  });
}

function initSettingsToggles() {
  document.getElementById("cfgOcrEngine").addEventListener("change", updateEngineSections);
  updateEngineSections();
}

// --- API Calls ---
async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    // Device Pill
    const devPill = document.getElementById("devicePill");
    const devText = document.getElementById("deviceText");
    if (data.connected) {
      devPill.className = "status-pill online";
      devText.textContent = `${data.device.model} [${data.device.sn || "Connected"}]`;
    } else {
      devPill.className = "status-pill offline";
      devText.textContent = "Offline / Token Expired";
    }

    // Vault Pill
    const vaultText = document.getElementById("vaultText");
    const mirrorFolder = data.vault.mirror_folder || "Viwoods";
    vaultText.textContent = `Obsidian: ${mirrorFolder}/ (${data.vault.notes_synced} synced)`;
    document.getElementById("lblVaultMirror").textContent = `${mirrorFolder}/`;
    document.getElementById("lblDailyFolder").textContent = `${data.vault.daily_folder}/`;
  } catch (err) {
    console.error("Failed to load status:", err);
  }
}

// Maps a settings input element id to the Config field it edits.
const SETTINGS_FIELDS = {
  cfgVaultPath: "vault_path",
  cfgMirrorFolder: "vault_mirror_folder",
  cfgDailyFolder: "daily_folder",
  cfgDailyHeading: "daily_heading",
  cfgOcrEngine: "ocr_engine",
  cfgOllamaUrl: "ollama_url",
  cfgOllamaModel: "ollama_model",
  cfgLMStudioUrl: "lmstudio_url",
  cfgLMStudioModel: "lmstudio_model"
};

// Secrets are write-only: the server never sends them back, so an empty
// field means "leave the stored value alone", not "clear it".
const SECRET_FIELDS = {
  cfgGeminiKey: "gemini_api_key",
  cfgToken: "token"
};

async function loadSettings() {
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    const cfg = data.config || {};
    savedConfig = cfg;

    Object.entries(SETTINGS_FIELDS).forEach(([elId, field]) => {
      const el = document.getElementById(elId);
      if (el && cfg[field] !== undefined && cfg[field] !== null) el.value = cfg[field];
    });

    Object.entries(SECRET_FIELDS).forEach(([elId, field]) => {
      const el = document.getElementById(elId);
      if (!el) return;
      el.value = "";
      el.placeholder = cfg[`has_${field}`]
        ? `Saved (${cfg[`${field}_masked`]}) — leave blank to keep`
        : "Not configured";
    });

    updateEngineSections();
  } catch (err) {
    console.error("Failed to load settings:", err);
  }
}

function collectSettings() {
  const values = {};
  Object.entries(SETTINGS_FIELDS).forEach(([elId, field]) => {
    const el = document.getElementById(elId);
    if (el) values[field] = el.value.trim();
  });
  return values;
}

async function loadTree() {
  const container = document.getElementById("treeContainer");
  try {
    const res = await fetch("/api/tree");
    const data = await res.json();
    if (!data.tree || data.tree.length === 0) {
      container.innerHTML = '<div class="empty-state">No folders found on Viwoods Cloud.</div>';
      return;
    }

    container.innerHTML = "";
    data.tree.forEach(cat => {
      const card = document.createElement("div");
      card.className = "tree-card";

      const icon = getCategoryIcon(cat.appType);
      let itemsHtml = "";
      if (cat.items && cat.items.length > 0) {
        itemsHtml = cat.items.map(it => {
          const isFolder = it.resourceType === 0 || it.resourceType === 5;
          const icon = isFolder ? "📁" : "📄";
          const pages = it.pageCount ? `(${it.pageCount}p)` : "";
          return `
            <li class="item-row ${isFolder ? 'is-folder' : ''}" data-uuid="${it.uuid || it.resourceId || ''}" data-type="${it.resourceType}" data-apptype="${cat.appType}">
              <span>${icon} ${escapeHtml(it.name)}</span>
              <span class="text-muted font-mono" style="font-size: 0.75rem;">${pages}</span>
            </li>
          `;
        }).join("");
      } else {
        itemsHtml = '<li class="text-muted" style="font-size: 0.82rem; padding: 6px;">Empty</li>';
      }

      card.innerHTML = `
        <div class="tree-card-header">
          <h3><span>${icon}</span> ${escapeHtml(cat.name)}</h3>
          <span class="badge">${cat.count} items</span>
        </div>
        <ul class="item-list">
          ${itemsHtml}
        </ul>
      `;

      container.appendChild(card);
    });

    // Item click listener in tree
    container.querySelectorAll(".item-row").forEach(row => {
      row.addEventListener("click", () => {
        const uuid = row.dataset.uuid;
        const isFolder = row.dataset.type === "0" || row.dataset.type === "5";
        if (uuid && !isFolder) {
          openInStudio(uuid, parseInt(row.dataset.apptype, 10) || 1);
        }
      });
    });

  } catch (err) {
    container.innerHTML = `<div class="error-msg">Failed to load tree: ${err.message}</div>`;
  }
}

function getCategoryIcon(appType) {
  switch (appType) {
    case 1: return "📝";
    case 2: return "🎙️";
    case 3: return "📚";
    case 4: return "🧠";
    case 5: return "📅";
    case 6: return "💡";
    default: return "📁";
  }
}

async function loadNotes() {
  const listEl = document.getElementById("studioNotesList");
  const countEl = document.getElementById("studioNoteCount");

  try {
    const res = await fetch("/api/notes");
    const data = await res.json();
    const notes = data.notes || {};
    const uuids = Object.keys(notes);

    countEl.textContent = uuids.length;
    if (uuids.length === 0) {
      listEl.innerHTML = '<div class="empty-state">No notes synced yet. Click "Sync All Notebooks".</div>';
      return;
    }

    listEl.innerHTML = "";
    uuids.forEach(uuid => {
      const n = notes[uuid];
      const btn = document.createElement("button");
      btn.className = "note-item-btn";
      btn.dataset.uuid = uuid;
      const synced = n.pages_count || 0;
      const total = n.total_pages || synced;
      const pagesLabel = total > synced ? `${synced} of ${total} pages` : `${synced} pages`;
      btn.dataset.apptype = n.app_type || 1;
      btn.innerHTML = `
        <span class="note-item-title">${getCategoryIcon(n.app_type || 1)} ${escapeHtml(n.name)}</span>
        <span class="note-item-sub">${pagesLabel} • ${formatTime(n.synced_at)}</span>
      `;
      btn.addEventListener("click", () => openInStudio(uuid, n.app_type || 1, btn));
      listEl.appendChild(btn);
    });
  } catch (err) {
    console.error("Failed to load notes list:", err);
  }
}

async function openInStudio(uuid, appType = 1, clickedBtn = null) {
  // Switch to studio tab
  document.querySelector('[data-tab="tabStudio"]').click();

  if (clickedBtn) {
    document.querySelectorAll(".note-item-btn").forEach(b => b.classList.remove("active"));
    clickedBtn.classList.add("active");
  }

  const emptyPrompt = document.getElementById("studioEmptyPrompt");
  const splitView = document.getElementById("studioSplitView");
  const header = document.getElementById("studioActiveHeader");
  const title = document.getElementById("studioActiveTitle");
  const meta = document.getElementById("studioActiveMeta");

  emptyPrompt.innerHTML = '<div class="loading-spinner">Loading notebook page & transcription...</div>';
  emptyPrompt.classList.remove("hidden");
  splitView.classList.add("hidden");

  try {
    const res = await fetch(`/api/preview/${uuid}?app_type=${appType}`);
    const data = await res.json();
    activeNote = data;
    activeNote.appType = data.appType || appType;
    activePageIndex = 0;

    emptyPrompt.classList.add("hidden");
    splitView.classList.remove("hidden");
    header.classList.remove("hidden");

    title.textContent = data.name || "Untitled Note";
    meta.textContent = `${data.pages.length} pages • Mirrored in Obsidian`;

    renderActivePage();
  } catch (err) {
    emptyPrompt.innerHTML = `<div class="error-msg">Failed to load preview: ${err.message}</div>`;
  }
}

function renderActivePage() {
  if (!activeNote || !activeNote.pages || activeNote.pages.length === 0) return;

  const page = activeNote.pages[activePageIndex];
  const imgEl = document.getElementById("imgHandwriting");
  const txtEl = document.getElementById("txtTranscript");
  const pageLabel = document.getElementById("lblCurrentPage");
  const lnkOriginal = document.getElementById("lnkOpenOriginal");

  pageLabel.textContent = `Page ${page.pageNo || (activePageIndex + 1)} of ${activeNote.pages.length}`;
  imgEl.src = page.imageUrl || "";
  lnkOriginal.href = page.imageUrl || "#";
  txtEl.value = page.transcript || "(No transcription available yet)";
}

async function loadJournals() {
  const container = document.getElementById("journalsContainer");
  try {
    const res = await fetch("/api/journals");
    const data = await res.json();
    const items = data.items || [];

    if (!data.folder) {
      container.innerHTML = '<div class="empty-state">No <code>Journals</code> folder found in Paper on Viwoods Cloud.</div>';
      return;
    }
    if (items.length === 0) {
      container.innerHTML = `<div class="empty-state">No entries in the <code>${escapeHtml(data.folder.name)}</code> folder yet.</div>`;
      return;
    }

    const mirrorRoot = savedConfig.vault_mirror_folder || "Viwoods";
    const dailyFolder = savedConfig.daily_folder || "10 - Journals";
    const folderName = escapeHtml(data.folder.name || "Journals");

    container.innerHTML = items.slice(0, 12).map(it => {
      const dateName = escapeHtml(it.name);
      return `
        <div class="journal-card">
          <div class="journal-card-header">
            <span class="journal-date">📅 ${dateName}</span>
            <span class="journal-badge">${folderName}</span>
          </div>
          <div class="journal-snippet">
            Mirrored in <code>${escapeHtml(mirrorRoot)}/Paper/${folderName}/${dateName}.md</code> and injected into <code>${escapeHtml(dailyFolder)}/...</code>
          </div>
          <button class="btn btn-sm btn-outline btn-inspect-journal" data-uuid="${it.uuid || it.resourceId}">
            Inspect Handwriting & OCR
          </button>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".btn-inspect-journal").forEach(btn => {
      btn.addEventListener("click", () => {
        openInStudio(btn.dataset.uuid, 1);
      });
    });
  } catch (err) {
    container.innerHTML = `<div class="error-msg">Failed to load journals: ${err.message}</div>`;
  }
}

// --- Sync Controls ---
function bindEvents() {
  document.getElementById("btnSyncAll").addEventListener("click", () => startSync("all"));
  document.getElementById("btnSyncJournals").addEventListener("click", () => startSync("journals"));
  document.getElementById("btnRefreshTree").addEventListener("click", () => loadTree());

  // Page Controls in Studio
  document.getElementById("btnPrevPage").addEventListener("click", () => {
    if (activeNote && activePageIndex > 0) {
      activePageIndex--;
      renderActivePage();
    }
  });

  document.getElementById("btnNextPage").addEventListener("click", () => {
    if (activeNote && activePageIndex < activeNote.pages.length - 1) {
      activePageIndex++;
      renderActivePage();
    }
  });

  document.getElementById("btnCopyTranscript").addEventListener("click", () => {
    const txt = document.getElementById("txtTranscript").value;
    navigator.clipboard.writeText(txt);
    const btn = document.getElementById("btnCopyTranscript");
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = "Copy Text"; }, 2000);
  });

  // Export Note Actions (PDF, HTML, ZIP)
  [["btnExportPdf", "pdf"], ["btnExportHtml", "html"], ["btnExportZip", "zip"]].forEach(([btnId, fmt]) => {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.addEventListener("click", () => {
      if (!activeNote || !activeNote.uuid) return;
      const appType = activeNote.appType || 1;
      window.open(`/api/export/${activeNote.uuid}?format=${fmt}&app_type=${appType}`, "_blank");
    });
  });

  // Settings Save
  document.getElementById("btnSaveConfig").addEventListener("click", saveSettings);
}

async function startSync(scope) {
  const banner = document.getElementById("syncProgressBanner");
  const msg = document.getElementById("syncProgressMessage");
  const pct = document.getElementById("syncProgressPercent");
  const bar = document.getElementById("syncProgressBar");
  const syncIcon = document.getElementById("syncIcon");

  banner.classList.remove("hidden");
  syncIcon.classList.add("spin");

  try {
    const res = await fetch("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: scope, force: false })
    });
    const data = await res.json();
    pollSyncProgress();
  } catch (err) {
    alert("Failed to start sync: " + err.message);
    syncIcon.classList.remove("spin");
  }
}

function pollSyncProgress() {
  if (syncPollingTimer) clearInterval(syncPollingTimer);

  const banner = document.getElementById("syncProgressBanner");
  const msg = document.getElementById("syncProgressMessage");
  const pct = document.getElementById("syncProgressPercent");
  const bar = document.getElementById("syncProgressBar");
  const syncIcon = document.getElementById("syncIcon");

  syncPollingTimer = setInterval(async () => {
    try {
      const res = await fetch("/api/sync/status");
      const data = await res.json();
      const status = data.status || {};

      msg.textContent = status.message || "Working...";
      const percentInt = Math.round((status.progress || 0) * 100);
      pct.textContent = `${percentInt}%`;
      bar.style.width = `${percentInt}%`;

      if (!status.is_running) {
        clearInterval(syncPollingTimer);
        syncPollingTimer = null;
        syncIcon.classList.remove("spin");
        setTimeout(() => { banner.classList.add("hidden"); }, 3500);

        // Refresh views
        loadStatus();
        loadNotes();
      }
    } catch (err) {
      clearInterval(syncPollingTimer);
      syncPollingTimer = null;
      syncIcon.classList.remove("spin");
    }
  }, 1000);
}

async function saveSettings() {
  // Only send what the user actually changed, so an untouched (and
  // unreadable) field can never blank out a stored value.
  const payload = {};
  Object.entries(collectSettings()).forEach(([field, value]) => {
    if (savedConfig[field] !== value) payload[field] = value;
  });

  Object.entries(SECRET_FIELDS).forEach(([elId, field]) => {
    const el = document.getElementById(elId);
    const value = el ? el.value.trim() : "";
    if (value) payload[field] = value;
  });

  const lbl = document.getElementById("lblConfigSaved");
  if (Object.keys(payload).length === 0) {
    lbl.textContent = "No changes";
    lbl.classList.remove("hidden");
    setTimeout(() => { lbl.classList.add("hidden"); lbl.textContent = "✓ Settings saved"; }, 2000);
    return;
  }

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    lbl.classList.remove("hidden");
    setTimeout(() => { lbl.classList.add("hidden"); }, 3000);
    await loadSettings();
    loadStatus();
  } catch (err) {
    alert("Failed to save config: " + err.message);
  }
}

// Helpers
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatTime(isoStr) {
  if (!isoStr) return "";
  try {
    const dt = new Date(isoStr);
    return dt.toLocaleDateString() + " " + dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return isoStr;
  }
}
