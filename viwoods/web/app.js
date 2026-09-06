// Viwoods Companion Web App Logic

let activeNote = null;
let activePageIndex = 0;
let syncPollingTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initSettingsToggles();
  loadStatus();
  loadTree();
  loadNotes();
  loadJournals();
  bindEvents();
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

function initSettingsToggles() {
  const select = document.getElementById("cfgOcrEngine");
  const secGemini = document.getElementById("secGemini");
  const secLMStudio = document.getElementById("secLMStudio");

  function update() {
    secGemini.classList.add("hidden");
    secLMStudio.classList.add("hidden");
    if (select.value === "gemini") secGemini.classList.remove("hidden");
    if (select.value === "lmstudio") secLMStudio.classList.remove("hidden");
  }

  select.addEventListener("change", update);
  update();
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

    // Populate Settings Inputs
    document.getElementById("cfgVaultPath").value = data.vault.path || "";
    document.getElementById("cfgMirrorFolder").value = mirrorFolder;
    document.getElementById("cfgOcrEngine").value = data.ocr.engine || "ollama";
    if (data.ocr.ollama_url) document.getElementById("cfgOllamaUrl").value = data.ocr.ollama_url;
    if (data.ocr.ollama_model) document.getElementById("cfgOllamaModel").value = data.ocr.ollama_model;
    if (data.ocr.lmstudio_url) document.getElementById("cfgLMStudioUrl").value = data.ocr.lmstudio_url;
    document.getElementById("cfgOcrEngine").dispatchEvent(new Event("change"));
  } catch (err) {
    console.error("Failed to load status:", err);
  }
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
            <li class="item-row ${isFolder ? 'is-folder' : ''}" data-uuid="${it.uuid || it.resourceId || ''}" data-type="${it.resourceType}">
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
          openInStudio(uuid);
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
      btn.innerHTML = `
        <span class="note-item-title">${escapeHtml(n.name)}</span>
        <span class="note-item-sub">${n.pages_count || 1} pages • ${formatTime(n.synced_at)}</span>
      `;
      btn.addEventListener("click", () => openInStudio(uuid, btn));
      listEl.appendChild(btn);
    });
  } catch (err) {
    console.error("Failed to load notes list:", err);
  }
}

async function openInStudio(uuid, clickedBtn = null) {
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
    const res = await fetch(`/api/preview/${uuid}`);
    const data = await res.json();
    activeNote = data;
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
    const res = await fetch("/api/folder/1?resource_id=0d7e25d2-4f39-4122-affa-ea02704a5637");
    const data = await res.json();
    const items = data.items || [];

    if (items.length === 0) {
      container.innerHTML = '<div class="empty-state">No journal entries found in Journals folder.</div>';
      return;
    }

    container.innerHTML = items.slice(0, 12).map(it => {
      const dateName = escapeHtml(it.name);
      return `
        <div class="journal-card">
          <div class="journal-card-header">
            <span class="journal-date">📅 ${dateName}</span>
            <span class="journal-badge">Journals</span>
          </div>
          <div class="journal-snippet">
            Mirrored in <code>Viwoods/Paper/Journals/${dateName}.md</code> and injected into <code>10 - Journals/...</code>
          </div>
          <button class="btn btn-sm btn-outline btn-inspect-journal" data-uuid="${it.uuid || it.resourceId}">
            Inspect Handwriting & OCR
          </button>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".btn-inspect-journal").forEach(btn => {
      btn.addEventListener("click", () => {
        openInStudio(btn.dataset.uuid);
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
  const btnExportPdf = document.getElementById("btnExportPdf");
  if (btnExportPdf) {
    btnExportPdf.addEventListener("click", () => {
      if (activeNote && activeNote.uuid) {
        window.open(`/api/export/${activeNote.uuid}?format=pdf`, "_blank");
      }
    });
  }

  const btnExportHtml = document.getElementById("btnExportHtml");
  if (btnExportHtml) {
    btnExportHtml.addEventListener("click", () => {
      if (activeNote && activeNote.uuid) {
        window.open(`/api/export/${activeNote.uuid}?format=html`, "_blank");
      }
    });
  }

  const btnExportZip = document.getElementById("btnExportZip");
  if (btnExportZip) {
    btnExportZip.addEventListener("click", () => {
      if (activeNote && activeNote.uuid) {
        window.open(`/api/export/${activeNote.uuid}?format=zip`, "_blank");
      }
    });
  }

  // OCR Engine Selection Toggle
  const selEngine = document.getElementById("cfgOcrEngine");
  selEngine.addEventListener("change", () => {
    const val = selEngine.value;
    const secOllama = document.getElementById("secOllama");
    const secGemini = document.getElementById("secGemini");
    const secLMStudio = document.getElementById("secLMStudio");
    if (secOllama) secOllama.classList.toggle("hidden", val !== "ollama");
    if (secGemini) secGemini.classList.toggle("hidden", val !== "gemini");
    if (secLMStudio) secLMStudio.classList.toggle("hidden", val !== "lmstudio");
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
  const payload = {
    vault_path: document.getElementById("cfgVaultPath").value.trim(),
    vault_mirror_folder: document.getElementById("cfgMirrorFolder").value.trim(),
    daily_folder: document.getElementById("cfgDailyFolder").value.trim(),
    daily_heading: document.getElementById("cfgDailyHeading").value.trim(),
    mirror_daily: document.getElementById("cfgMirrorDaily").checked,
    ocr_engine: document.getElementById("cfgOcrEngine").value,
    gemini_api_key: document.getElementById("cfgGeminiKey").value.trim(),
    lmstudio_url: document.getElementById("cfgLMStudioUrl").value.trim(),
    lmstudio_model: document.getElementById("cfgLMStudioModel").value.trim(),
    ollama_url: document.getElementById("cfgOllamaUrl") ? document.getElementById("cfgOllamaUrl").value.trim() : "http://localhost:11434",
    ollama_model: document.getElementById("cfgOllamaModel") ? document.getElementById("cfgOllamaModel").value.trim() : "qwen3-vl:8b-instruct",
  };

  const tokenVal = document.getElementById("cfgToken").value.trim();
  if (tokenVal) payload.token = tokenVal;

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    const lbl = document.getElementById("lblConfigSaved");
    lbl.classList.remove("hidden");
    setTimeout(() => { lbl.classList.add("hidden"); }, 3000);
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
