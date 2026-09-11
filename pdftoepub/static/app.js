const form = document.getElementById("convert-form");
const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const dropTitle = document.getElementById("drop-title");
const dropHint = document.getElementById("drop-hint");
const meta = document.getElementById("meta");
const preview = document.getElementById("preview");
const statusEl = document.getElementById("status");
const convertBtn = document.getElementById("convert-btn");
const downloadLink = document.getElementById("download-link");
const titleInput = document.getElementById("title-input");
const authorInput = document.getElementById("author-input");
const imagesInput = document.getElementById("images-input");
const progressEl = document.getElementById("progress");
const progressBar = document.getElementById("progress-bar");
const progressLabel = document.getElementById("progress-label");
const progressPct = document.getElementById("progress-pct");

const MAX_UPLOAD_BYTES = 80 * 1024 * 1024;

let selectedFile = null;
let lastDownloadUrl = null;

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function setProgress(percent, message, visible = true, working = true) {
  progressEl.classList.toggle("hidden", !visible);
  progressEl.classList.toggle("working", visible && working);
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  progressBar.style.width = `${value}%`;
  progressEl.setAttribute("aria-valuenow", String(value));
  progressLabel.textContent = message;
  progressPct.textContent = `${value}%`;
}

function hideProgress() {
  progressEl.classList.add("hidden");
  progressEl.classList.remove("working");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function resetDownload() {
  if (lastDownloadUrl) {
    URL.revokeObjectURL(lastDownloadUrl);
    lastDownloadUrl = null;
  }
  downloadLink.classList.add("hidden");
  downloadLink.removeAttribute("href");
}

function explainError(error) {
  if (error instanceof TypeError) {
    return "The converter is not running. In Terminal, run: pdftoepub serve --open";
  }
  return error.message || "Conversion failed.";
}

async function readError(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const payload = await response.json();
    throw new Error(payload.error || "Conversion failed.");
  }
  throw new Error("Conversion failed.");
}

function renderPreview(info) {
  const chapters = (info.chapters || []).slice(0, 8)
    .map((name) => `<li>${escapeHtml(name)}</li>`)
    .join("");
  const extra = info.chapter_count > 8
    ? `<li>…and ${info.chapter_count - 8} more</li>`
    : "";
  const scanNote = info.has_text
    ? ""
    : `<p class="warning">Little text was found. This may be a scanned PDF; pages will be stored as images.</p>`;
  preview.innerHTML = `
    <h2>Detected structure</h2>
    <p>${info.page_count} pages · ${info.chapter_count} chapters · ${info.image_count} images</p>
    ${scanNote}
    <ul>${chapters}${extra}</ul>
  `;
  preview.classList.remove("hidden");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function isPdfFile(file) {
  const name = (file.name || "").toLowerCase();
  const type = (file.type || "").toLowerCase();
  return name.endsWith(".pdf") || type === "application/pdf";
}

async function useFile(file) {
  if (!file) return;
  if (!isPdfFile(file)) {
    setStatus("Please choose a PDF file.", true);
    return;
  }
  if (file.size === 0) {
    setStatus("That file is empty. Choose a PDF with content.", true);
    return;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    setStatus("PDF is larger than the 80 MB limit.", true);
    return;
  }
  selectedFile = file;
  resetDownload();
  hideProgress();
  dropTitle.textContent = file.name;
  dropHint.textContent = `${Math.max(1, Math.round(file.size / 1024))} KB — click Convert and download`;
  meta.classList.remove("hidden");
  convertBtn.disabled = false;
  convertBtn.focus();
  setStatus("Reading PDF…");
  const body = new FormData();
  body.append("file", file);
  try {
    const response = await fetch("/api/preview", { method: "POST", body });
    if (!response.ok) await readError(response);
    const info = await response.json();
    titleInput.value = info.title || "";
    authorInput.value = info.author || "";
    renderPreview(info);
    setStatus("Ready. Click Convert and download.");
  } catch (error) {
    preview.classList.add("hidden");
    setStatus(explainError(error), true);
  }
}

["dragenter", "dragover"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragover");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragover");
  });
});

dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (file) useFile(file);
});

fileInput.addEventListener("change", () => {
  useFile(fileInput.files[0]);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFile) {
    setStatus("Choose a PDF first.", true);
    fileInput.click();
    return;
  }
  resetDownload();
  convertBtn.disabled = true;
  setProgress(2, "Uploading PDF…", true, true);
  setStatus("Converting…");
  const body = new FormData();
  body.append("file", selectedFile);
  body.append("title", titleInput.value.trim());
  body.append("author", authorInput.value.trim());
  body.append("include_images", imagesInput.checked ? "true" : "false");
  try {
    const started = await fetch("/api/jobs", { method: "POST", body });
    if (!started.ok) await readError(started);
    const { id } = await started.json();
    const job = await waitForJob(id);
    const fileResponse = await fetch(`/api/jobs/${id}/file`);
    if (!fileResponse.ok) await readError(fileResponse);
    const blob = await fileResponse.blob();
    lastDownloadUrl = URL.createObjectURL(blob);
    const filename = job.filename || selectedFile.name.replace(/\.pdf$/i, ".epub");
    downloadLink.href = lastDownloadUrl;
    downloadLink.download = filename;
    downloadLink.classList.remove("hidden");
    downloadLink.click();
    setProgress(100, "Finished", true, false);
    setStatus(`Saved ${filename} to your Downloads folder.`);
  } catch (error) {
    hideProgress();
    setStatus(explainError(error), true);
  } finally {
    convertBtn.disabled = false;
  }
});

async function waitForJob(id) {
  while (true) {
    const response = await fetch(`/api/jobs/${id}`);
    if (!response.ok) await readError(response);
    const job = await response.json();
    setProgress(job.percent || 0, job.message || "Converting…", true, job.status !== "done");
    if (job.status === "done") return job;
    if (job.status === "error") throw new Error(job.error || "Conversion failed.");
    await sleep(200);
  }
}
