const form = document.getElementById("convert-form");
const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const dropTitle = document.getElementById("drop-title");
const dropHint = document.getElementById("drop-hint");
const meta = document.getElementById("meta");
const titleField = document.getElementById("title-field");
const authorField = document.getElementById("author-field");
const preview = document.getElementById("preview");
const previewStats = document.getElementById("preview-stats");
const previewText = document.getElementById("preview-text");
const chapterEditor = document.getElementById("chapter-editor");
const chapterList = document.getElementById("chapter-list");
const queueEl = document.getElementById("queue");
const statusEl = document.getElementById("status");
const convertBtn = document.getElementById("convert-btn");
const downloadLink = document.getElementById("download-link");
const openBooksBtn = document.getElementById("open-books-btn");
const zipLink = document.getElementById("zip-link");
const titleInput = document.getElementById("title-input");
const authorInput = document.getElementById("author-input");
const imagesInput = document.getElementById("images-input");
const progressEl = document.getElementById("progress");
const progressBar = document.getElementById("progress-bar");
const progressLabel = document.getElementById("progress-label");
const progressPct = document.getElementById("progress-pct");
const offlineBanner = document.getElementById("offline-banner");

const MAX_UPLOAD_BYTES = 80 * 1024 * 1024;
const OFFLINE_STATUS = "The converter is not running. This is not a problem with your PDF.";

let selectedFiles = [];
let extractId = null;
let chapterItems = [];
let lastDownloadUrl = null;
let lastJobId = null;
let zipUrl = null;
let capabilities = { open_books: false };
let queueItems = [];

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function setOffline(offline) {
  offlineBanner.classList.toggle("hidden", !offline);
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

function epubNameFromTitle(title) {
  const flattened = String(title || "").trim().replace(/[/\\]+/g, " ");
  if (!flattened) return "";
  const safe = flattened.replace(/[^\w\s-]/g, "").trim();
  return safe ? `${safe}.epub` : "";
}

function resetDownloads() {
  if (lastDownloadUrl) {
    URL.revokeObjectURL(lastDownloadUrl);
    lastDownloadUrl = null;
  }
  if (zipUrl) {
    URL.revokeObjectURL(zipUrl);
    zipUrl = null;
  }
  downloadLink.classList.add("hidden");
  downloadLink.removeAttribute("href");
  zipLink.classList.add("hidden");
  zipLink.removeAttribute("href");
  openBooksBtn.classList.add("hidden");
  lastJobId = null;
}

function explainError(error) {
  if (error instanceof TypeError) {
    setOffline(true);
    return OFFLINE_STATUS;
  }
  setOffline(false);
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

async function apiFetch(url, options) {
  const response = await fetch(url, options);
  setOffline(false);
  return response;
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

function fileSizeLabel(file) {
  return `${Math.max(1, Math.round(file.size / 1024))} KB`;
}

function keptChapters() {
  return chapterItems
    .filter((item) => !item.removed)
    .map((item) => ({ index: item.index, title: item.title }));
}

function renderTextPreview(info) {
  if (info.preview_html) {
    previewText.innerHTML = info.preview_html;
    return;
  }
  const paragraphs = Array.isArray(info.preview_paragraphs)
    ? info.preview_paragraphs.filter((text) => String(text).trim())
    : [];
  previewText.innerHTML = paragraphs
    .slice(0, 12)
    .map((text) => `<p>${escapeHtml(text)}</p>`)
    .join("");
}

function renderChapters() {
  const remaining = chapterItems.filter((item) => !item.removed);
  chapterList.innerHTML = remaining
    .map((item) => `
      <li class="chapter-row" data-index="${item.index}">
        <input type="text" value="${escapeHtml(item.title)}" aria-label="Chapter title">
        <button type="button" data-remove="${item.index}" ${remaining.length <= 1 ? "disabled" : ""}>Remove</button>
      </li>
    `)
    .join("");
}

function renderPreview(info) {
  preview.querySelectorAll("p.warning").forEach((node) => node.remove());
  const cover = info.has_cover ? " · cover" : "";
  previewStats.textContent = `${info.page_count} pages · ${info.chapter_count} chapters · ${info.image_count} images${cover}`;
  if (!info.has_text) {
    previewStats.insertAdjacentHTML(
      "afterend",
      `<p class="warning">Little text was found. This may be a scanned PDF; pages may be stored as images.</p>`,
    );
  }
  renderTextPreview(info);
  chapterItems = (info.chapter_items || []).map((item, index) => ({
    index: Number.isInteger(item.index) ? item.index : index,
    title: item.title || (info.chapters || [])[index] || `Chapter ${index + 1}`,
    removed: false,
  }));
  if (!chapterItems.length && Array.isArray(info.chapters)) {
    chapterItems = info.chapters.map((title, index) => ({
      index,
      title,
      removed: false,
    }));
  }
  renderChapters();
  chapterEditor.classList.toggle("hidden", chapterItems.length === 0);
  preview.classList.remove("hidden");
}

function renderQueue() {
  queueEl.innerHTML = queueItems
    .map((item, index) => {
      const download = item.downloadUrl
        ? `<a href="${item.downloadUrl}" download="${escapeHtml(item.filename || "book.epub")}">Download</a>`
        : "";
      const books = item.jobId && capabilities.open_books && item.status === "done"
        ? `<button type="button" data-open-books="${item.jobId}">Books</button>`
        : "";
      return `
        <li class="queue-item ${item.status === "error" ? "error" : ""}" data-index="${index}">
          <span class="queue-name">${escapeHtml(item.file.name)}</span>
          <span class="queue-status">${escapeHtml(item.message)}</span>
          <span class="queue-actions">${download}${books}</span>
        </li>
      `;
    })
    .join("");
  queueEl.classList.toggle("hidden", queueItems.length === 0);
}

function showSingleChrome() {
  titleField.classList.remove("hidden");
  authorField.classList.remove("hidden");
  queueEl.classList.add("hidden");
  convertBtn.textContent = "Convert and download";
}

function showBatchChrome(count) {
  titleField.classList.add("hidden");
  authorField.classList.add("hidden");
  preview.classList.add("hidden");
  convertBtn.textContent = `Convert ${count} PDFs`;
}

async function useFiles(fileList) {
  const incoming = Array.from(fileList || []).filter(Boolean);
  if (!incoming.length) return;

  const invalid = incoming.find((file) => !isPdfFile(file));
  if (invalid) {
    setStatus("Please choose PDF files only.", true);
    return;
  }
  const empty = incoming.find((file) => file.size === 0);
  if (empty) {
    setStatus("One of those files is empty. Choose PDFs with content.", true);
    return;
  }
  const oversized = incoming.find((file) => file.size > MAX_UPLOAD_BYTES);
  if (oversized) {
    setStatus("A PDF is larger than the 80 MB limit.", true);
    return;
  }

  selectedFiles = incoming;
  extractId = null;
  chapterItems = [];
  queueItems.forEach((item) => {
    if (item.downloadUrl) URL.revokeObjectURL(item.downloadUrl);
  });
  queueItems = [];
  resetDownloads();
  hideProgress();
  preview.classList.add("hidden");
  preview.querySelectorAll("p.warning").forEach((node) => node.remove());
  meta.classList.remove("hidden");
  convertBtn.disabled = false;
  convertBtn.focus();

  if (incoming.length === 1) {
    showSingleChrome();
    const file = incoming[0];
    dropTitle.textContent = file.name;
    dropHint.textContent = `${fileSizeLabel(file)} — edit the structure, then convert`;
    setStatus("Reading PDF…");
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await apiFetch("/api/preview", { method: "POST", body });
      if (!response.ok) await readError(response);
      const info = await response.json();
      extractId = info.extract_id || null;
      titleInput.value = info.title || "";
      authorInput.value = info.author || "";
      renderPreview(info);
      setStatus("Ready. Edit details if you like, then convert.");
    } catch (error) {
      preview.classList.add("hidden");
      setStatus(explainError(error), true);
    }
    return;
  }

  showBatchChrome(incoming.length);
  dropTitle.textContent = `${incoming.length} PDFs selected`;
  dropHint.textContent = "Each file converts in order. You can download them one by one or as a zip.";
  queueItems = incoming.map((file) => ({
    file,
    status: "queued",
    message: "Waiting",
    jobId: null,
    downloadUrl: null,
    filename: "",
  }));
  renderQueue();
  setStatus(`Ready to convert ${incoming.length} PDFs.`);
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
  const files = event.dataTransfer?.files;
  if (files?.length) useFiles(files);
});

fileInput.addEventListener("change", () => {
  useFiles(fileInput.files);
});

chapterList.addEventListener("input", (event) => {
  const row = event.target.closest(".chapter-row");
  if (!row) return;
  const index = Number(row.dataset.index);
  const item = chapterItems.find((chapter) => chapter.index === index);
  if (item) item.title = event.target.value;
});

chapterList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (!button || button.disabled) return;
  const index = Number(button.dataset.remove);
  const remaining = chapterItems.filter((item) => !item.removed);
  if (remaining.length <= 1) return;
  const item = chapterItems.find((chapter) => chapter.index === index);
  if (item) item.removed = true;
  renderChapters();
});

queueEl.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-open-books]");
  if (!button) return;
  try {
    const response = await apiFetch(`/api/jobs/${button.dataset.openBooks}/open-books`, {
      method: "POST",
    });
    if (!response.ok) await readError(response);
    setStatus("Opened in Books.");
  } catch (error) {
    setStatus(explainError(error), true);
  }
});

openBooksBtn.addEventListener("click", async () => {
  if (!lastJobId) return;
  try {
    const response = await apiFetch(`/api/jobs/${lastJobId}/open-books`, { method: "POST" });
    if (!response.ok) await readError(response);
    setStatus("Opened in Books. The EPUB also stays in your Downloads folder.");
  } catch (error) {
    setStatus(explainError(error), true);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFiles.length) {
    setStatus("Choose a PDF first.", true);
    fileInput.click();
    return;
  }
  resetDownloads();
  convertBtn.disabled = true;
  try {
    if (selectedFiles.length === 1) {
      await convertSingle(selectedFiles[0]);
    } else {
      await convertBatch(selectedFiles);
    }
  } catch (error) {
    hideProgress();
    setStatus(explainError(error), true);
  } finally {
    convertBtn.disabled = false;
  }
});

async function convertSingle(file) {
  setProgress(2, "Starting…", true, true);
  setStatus("Converting…");
  const body = conversionForm(file, true);
  const started = await startJob(body, file);
  const { id } = await started.json();
  lastJobId = id;
  const job = await waitForJob(id, (current) => {
    setProgress(current.percent || 0, current.message || "Converting…", true, current.status !== "done");
  });
  const fileResponse = await apiFetch(`/api/jobs/${id}/file`);
  if (!fileResponse.ok) await readError(fileResponse);
  const blob = await fileResponse.blob();
  lastDownloadUrl = URL.createObjectURL(blob);
  const filename = job.filename || epubNameFromTitle(titleInput.value) || file.name.replace(/\.pdf$/i, ".epub");
  downloadLink.href = lastDownloadUrl;
  downloadLink.download = filename;
  downloadLink.classList.remove("hidden");
  downloadLink.click();
  if (capabilities.open_books) {
    openBooksBtn.classList.remove("hidden");
  }
  setProgress(100, "Finished", true, false);
  setStatus(
    capabilities.open_books
      ? `Saved ${filename} to your Downloads folder. You can also open it in Books.`
      : `Saved ${filename} to your Downloads folder.`
  );
}

async function convertBatch(files) {
  const total = files.length;
  let finished = 0;
  setProgress(1, `Uploading 1 of ${total}…`, true, true);
  setStatus(`Converting ${total} PDFs…`);

  for (let index = 0; index < files.length; index += 1) {
    const item = queueItems[index];
    item.status = "running";
    item.message = "Converting…";
    renderQueue();
    try {
      const body = conversionForm(item.file, false);
      const started = await startJob(body, item.file);
      const { id } = await started.json();
      item.jobId = id;
      const job = await waitForJob(id, (current) => {
        const overall = ((finished + (current.percent || 0) / 100) / total) * 100;
        setProgress(overall, `${index + 1}/${total} ${current.message || "Converting…"}`, true, true);
        item.message = current.message || "Converting…";
        renderQueue();
      });
      const fileResponse = await apiFetch(`/api/jobs/${id}/file`);
      if (!fileResponse.ok) await readError(fileResponse);
      const blob = await fileResponse.blob();
      item.downloadUrl = URL.createObjectURL(blob);
      item.filename = job.filename || item.file.name.replace(/\.pdf$/i, ".epub");
      item.status = "done";
      item.message = "Ready";
      finished += 1;
    } catch (error) {
      item.status = "error";
      item.message = explainError(error);
      if (error instanceof TypeError) throw error;
    }
    renderQueue();
  }

  const succeeded = queueItems.filter((item) => item.status === "done");
  const failed = queueItems.filter((item) => item.status === "error").length;
  if (succeeded.length >= 2) {
    await prepareZip(succeeded.map((item) => item.jobId).filter(Boolean));
  }
  setProgress(100, "Finished", true, false);
  if (!succeeded.length) {
    setStatus(failed ? "None of the PDFs converted." : "Conversion failed.", true);
    return;
  }
  setStatus(
    failed
      ? `Saved ${succeeded.length} EPUB${succeeded.length === 1 ? "" : "s"}. ${failed} failed.`
      : `Saved ${succeeded.length} EPUB${succeeded.length === 1 ? "" : "s"}. Download each one, or the zip.`
  );
}

function conversionForm(file, includeStructure) {
  const body = new FormData();
  body.append("title", includeStructure ? titleInput.value.trim() : "");
  body.append("author", includeStructure ? authorInput.value.trim() : "");
  body.append("include_images", imagesInput.checked ? "true" : "false");
  if (includeStructure) {
    const chapters = keptChapters();
    if (chapters.length) {
      body.append("chapters", JSON.stringify(chapters));
    }
    if (extractId) {
      body.append("extract_id", extractId);
      return body;
    }
  }
  body.append("file", file);
  return body;
}

async function startJob(body, fileForRetry) {
  let started = await apiFetch("/api/jobs", { method: "POST", body });
  if (!started.ok && extractId && fileForRetry && started.status === 400) {
    let message = "";
    try {
      message = (await started.clone().json()).error || "";
    } catch {
      message = "";
    }
    if (/preview|extract|expired|not found/i.test(message)) {
      const retry = conversionForm(fileForRetry, true);
      retry.delete("extract_id");
      retry.append("file", fileForRetry);
      started = await apiFetch("/api/jobs", { method: "POST", body: retry });
    }
  }
  if (!started.ok) await readError(started);
  return started;
}

async function waitForJob(id, onUpdate) {
  while (true) {
    const response = await apiFetch(`/api/jobs/${id}`);
    if (!response.ok) await readError(response);
    const job = await response.json();
    if (onUpdate) onUpdate(job);
    if (job.status === "done") return job;
    if (job.status === "error") throw new Error(job.error || "Conversion failed.");
    await sleep(200);
  }
}

async function prepareZip(jobIds) {
  const response = await apiFetch("/api/archive", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_ids: jobIds }),
  });
  if (!response.ok) return;
  const blob = await response.blob();
  zipUrl = URL.createObjectURL(blob);
  zipLink.href = zipUrl;
  zipLink.download = "pdftoepub-epubs.zip";
  zipLink.classList.remove("hidden");
}

async function pingServer() {
  try {
    const response = await apiFetch("/api");
    if (!response.ok) return;
    const info = await response.json();
    capabilities.open_books = !!info.open_books;
  } catch (error) {
    if (error instanceof TypeError) {
      setOffline(true);
      setStatus(OFFLINE_STATUS, true);
    }
  }
}

pingServer();
