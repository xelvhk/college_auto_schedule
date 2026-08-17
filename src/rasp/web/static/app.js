const state = { file: null, previewValid: false, solverReady: false };

const elements = {
  form: document.querySelector("#import-form"),
  fileInput: document.querySelector("#file-input"),
  uploadZone: document.querySelector("#upload-zone"),
  selectedFile: document.querySelector("#selected-file"),
  selectedFileName: document.querySelector("#selected-file-name"),
  selectedFileSize: document.querySelector("#selected-file-size"),
  clearFile: document.querySelector("#clear-file"),
  previewButton: document.querySelector("#preview-button"),
  activateButton: document.querySelector("#activate-button"),
  preview: document.querySelector("#preview"),
  message: document.querySelector("#message"),
  liveRegion: document.querySelector("#live-region"),
  teacherCount: document.querySelector("#teacher-count"),
  groupCount: document.querySelector("#group-count"),
  workloadCount: document.querySelector("#workload-count"),
  specialtyCount: document.querySelector("#specialty-count"),
  curriculumCount: document.querySelector("#curriculum-count"),
  disciplineCount: document.querySelector("#discipline-count"),
  studentCount: document.querySelector("#student-count"),
  studentCreated: document.querySelector("#student-created"),
  studentUpdated: document.querySelector("#student-updated"),
  studentDeactivated: document.querySelector("#student-deactivated"),
  buildingCount: document.querySelector("#building-count"),
  roomCount: document.querySelector("#room-count"),
  academicYearCount: document.querySelector("#academic-year-count"),
  academicCycleCount: document.querySelector("#academic-cycle-count"),
  calendarPeriodCount: document.querySelector("#calendar-period-count"),
  bellSlotCount: document.querySelector("#bell-slot-count"),
  calendarExceptionCount: document.querySelector("#calendar-exception-count"),
  resourceUnavailabilityCount: document.querySelector("#resource-unavailability-count"),
  readinessSummary: document.querySelector("#readiness-summary"),
  readinessStatus: document.querySelector("#readiness-status"),
  readinessCounts: document.querySelector("#readiness-counts"),
  readinessIssues: document.querySelector("#readiness-issues"),
  roomDeficitSummary: document.querySelector("#room-deficit-summary"),
  roomDeficitText: document.querySelector("#room-deficit-text"),
  teacherPreview: document.querySelector("#teacher-preview"),
  systemState: document.querySelector("#system-state"),
  activeSummary: document.querySelector("#active-summary"),
  versionList: document.querySelector("#version-list"),
  emptyHistory: document.querySelector("#empty-history"),
  refreshStatus: document.querySelector("#refresh-status"),
  stepFile: document.querySelector("#step-file"),
  stepCheck: document.querySelector("#step-check"),
  stepActivate: document.querySelector("#step-activate"),
  solveButton: document.querySelector("#solve-button"),
  solverReadiness: document.querySelector("#solver-readiness"),
  solverDetail: document.querySelector("#solver-detail"),
  solverFeedback: document.querySelector("#solver-feedback"),
  scheduleResult: document.querySelector("#schedule-result"),
  assignmentCount: document.querySelector("#assignment-count"),
  scheduleRows: document.querySelector("#schedule-rows"),
};

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КиБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МиБ`;
}

function formatDate(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function setMessage(kind, content, issues = []) {
  elements.message.className = `message is-${kind}`;
  elements.message.replaceChildren();
  const heading = document.createElement("strong");
  heading.textContent = content;
  elements.message.append(heading);
  if (issues.length) {
    const list = document.createElement("ul");
    issues.slice(0, 20).forEach((issue) => {
      const item = document.createElement("li");
      const location = issue.row ? `строка ${issue.row}` : issue.section;
      item.textContent = `${location}: ${issue.message}`;
      list.append(item);
    });
    elements.message.append(list);
  }
  elements.message.hidden = false;
  elements.liveRegion.textContent = content;
}

function clearMessage() {
  elements.message.hidden = true;
  elements.message.replaceChildren();
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.classList.toggle("is-loading", busy);
  button.setAttribute("aria-busy", String(busy));
}

function setFile(file) {
  state.file = file;
  state.previewValid = false;
  elements.activateButton.disabled = true;
  elements.preview.hidden = true;
  elements.stepFile.className = "is-current";
  elements.stepCheck.className = "";
  elements.stepActivate.className = "";
  clearMessage();
  if (!file) {
    elements.fileInput.value = "";
    elements.selectedFile.hidden = true;
    elements.uploadZone.querySelector("label").hidden = false;
    return;
  }
  elements.selectedFileName.textContent = file.name;
  elements.selectedFileSize.textContent = formatSize(file.size);
  elements.uploadZone.querySelector("label").hidden = true;
  elements.selectedFile.hidden = false;
}

function makeFormData() {
  const formData = new FormData();
  formData.append("file", state.file, state.file.name);
  return formData;
}

async function parseResponse(response) {
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error?.message || "Запрос не выполнен");
    error.payload = payload;
    throw error;
  }
  return payload;
}

function formatLessonDate(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    weekday: "short",
  }).format(new Date(`${value}T00:00:00`));
}

function setSolverFeedback(kind, content) {
  elements.solverFeedback.className = `solver-feedback is-${kind}`;
  elements.solverFeedback.textContent = content;
  elements.solverFeedback.hidden = false;
  elements.liveRegion.textContent = content;
}

function clearSolverResult() {
  elements.solverFeedback.hidden = true;
  elements.solverFeedback.textContent = "";
  elements.scheduleResult.hidden = true;
  elements.scheduleRows.replaceChildren();
}

function renderSchedule(payload) {
  const assignments = [...payload.assignments].sort((left, right) =>
    left.lessonDate.localeCompare(right.lessonDate) ||
    left.slotCode.localeCompare(right.slotCode) ||
    left.groupCode.localeCompare(right.groupCode) ||
    left.demandCode.localeCompare(right.demandCode)
  );
  elements.scheduleRows.replaceChildren();
  assignments.forEach((assignment) => {
    const row = document.createElement("tr");
    [
      formatLessonDate(assignment.lessonDate),
      assignment.occupiedSlotCodes.join(" · "),
      assignment.groupCode,
      assignment.disciplineCode,
      assignment.teacherCode,
      assignment.roomCode,
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    elements.scheduleRows.append(row);
  });
  elements.assignmentCount.textContent = `${payload.assignmentCount} занятий`;
  elements.scheduleResult.hidden = false;
  setSolverFeedback("success", "Черновой вариант рассчитан без конфликтов ресурсов.");
  elements.scheduleResult.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadSolverProblem(activeVersionId) {
  clearSolverResult();
  state.solverReady = false;
  elements.solveButton.disabled = true;
  if (!activeVersionId) {
    elements.solverReadiness.textContent = "Активные данные не выбраны";
    elements.solverDetail.textContent = "Сначала активируйте проверенный Excel-файл.";
    return;
  }
  elements.solverReadiness.textContent = "Проверяем готовность к расчёту…";
  elements.solverDetail.textContent = `Активная версия №${activeVersionId}`;
  try {
    const response = await fetch("/api/solver/problem");
    const problem = await parseResponse(response);
    state.solverReady = problem.isReady;
    elements.solveButton.disabled = !problem.isReady;
    elements.solverReadiness.textContent = problem.isReady
      ? "Данные готовы к расчёту"
      : "Расчёт заблокирован ошибками данных";
    elements.solverDetail.textContent = problem.isReady
      ? `${problem.lessonDemandCount} занятий · ${problem.placementOptionCount} допустимых размещений`
      : `Ошибок: ${problem.errorCount} · исправьте активную версию Excel`;
  } catch (error) {
    elements.solverReadiness.textContent = "Не удалось проверить готовность";
    elements.solverDetail.textContent = error.message;
  }
}

async function runSolver() {
  if (!state.solverReady) return;
  clearSolverResult();
  setBusy(elements.solveButton, true);
  elements.solverReadiness.textContent = "Идёт расчёт расписания…";
  elements.solverDetail.textContent = "Подбираем занятия без пересечений ресурсов.";
  try {
    const response = await fetch("/api/solver/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "complete", seed: 0, timeLimitSeconds: 30 }),
    });
    const payload = await parseResponse(response);
    if (payload.status === "feasible") {
      renderSchedule(payload);
      elements.solverReadiness.textContent = "Расчёт завершён";
      elements.solverDetail.textContent = `Получено назначений: ${payload.assignmentCount}`;
      return;
    }
    const detail = payload.diagnostics[0]?.message || "Допустимый вариант не найден.";
    setSolverFeedback("error", detail);
    elements.solverReadiness.textContent = "Расписание не построено";
    elements.solverDetail.textContent = "Проверьте ограничения активных данных.";
  } catch (error) {
    setSolverFeedback("error", error.message);
    elements.solverReadiness.textContent = "Ошибка расчёта";
    elements.solverDetail.textContent = "Повторите запуск после проверки данных.";
  } finally {
    setBusy(elements.solveButton, false);
    elements.solveButton.disabled = !state.solverReady;
  }
}

function renderTeacherRows(teachers) {
  elements.teacherPreview.replaceChildren();
  teachers.forEach((teacher) => {
    const row = document.createElement("tr");
    [teacher.teacherCode, teacher.fullName, teacher.department || "—"].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    elements.teacherPreview.append(row);
  });
}

function renderReadiness(readiness) {
  elements.readinessSummary.classList.toggle("is-blocked", !readiness.isReady);
  elements.readinessStatus.textContent = readiness.isReady
    ? "Можно запускать расчёт"
    : "Расчёт пока заблокирован";
  elements.readinessCounts.textContent =
    `Ошибки: ${readiness.errorCount} · Предупреждения: ${readiness.warningCount}`;
  elements.readinessIssues.replaceChildren();
  readiness.issues.forEach((issue) => {
    const item = document.createElement("li");
    item.textContent = `${issue.message}. ${issue.remediation || "Проверьте исходные данные."}`;
    elements.readinessIssues.append(item);
  });
}

async function previewFile() {
  if (!state.file) {
    setMessage("error", "Сначала выберите файл .xlsx");
    elements.fileInput.focus();
    return;
  }
  clearMessage();
  setBusy(elements.previewButton, true);
  try {
    const response = await fetch("/api/imports/preview", {
      method: "POST",
      body: makeFormData(),
    });
    const payload = await parseResponse(response);
    state.previewValid = true;
    elements.teacherCount.textContent = payload.counts.teachers;
    elements.groupCount.textContent = payload.counts.groups;
    elements.workloadCount.textContent = payload.counts.workloads;
    elements.specialtyCount.textContent = payload.counts.specialties;
    elements.curriculumCount.textContent = payload.counts.curricula;
    elements.disciplineCount.textContent = payload.counts.disciplines;
    elements.studentCount.textContent = payload.counts.students;
    elements.studentCreated.textContent = payload.studentChanges.created;
    elements.studentUpdated.textContent = payload.studentChanges.updated;
    elements.studentDeactivated.textContent = payload.studentChanges.deactivated;
    elements.buildingCount.textContent = payload.counts.buildings;
    elements.roomCount.textContent = payload.counts.rooms;
    elements.academicYearCount.textContent = payload.counts.academicYears;
    elements.academicCycleCount.textContent = payload.counts.academicCycles;
    elements.calendarPeriodCount.textContent = payload.counts.calendarPeriods;
    elements.bellSlotCount.textContent = payload.counts.bellSlots;
    elements.calendarExceptionCount.textContent = payload.counts.calendarExceptions;
    elements.resourceUnavailabilityCount.textContent = payload.counts.resourceUnavailability;
    renderReadiness(payload.readiness);
    const deficitCount = payload.roomDeficits.length;
    elements.roomDeficitSummary.hidden = deficitCount === 0;
    elements.roomDeficitText.textContent = deficitCount
      ? `${deficitCount} строк нагрузки не имеют подходящей аудитории. Импорт разрешён, расчёт расписания потребует исправления.`
      : "";
    renderTeacherRows(payload.samples.teachers);
    elements.preview.hidden = false;
    elements.activateButton.disabled = false;
    elements.stepFile.className = "is-complete";
    elements.stepCheck.className = "is-current";
    elements.preview.scrollIntoView({ behavior: "smooth", block: "start" });
    if (!payload.readiness.isReady) {
      setMessage(
        "warning",
        `Файл можно активировать, но расчёт блокируют ошибки: ${payload.readiness.errorCount}`,
      );
    } else if (payload.readiness.warningCount) {
      setMessage(
        "warning",
        `Файл проверен: предупреждений ${payload.readiness.warningCount}`,
        payload.readiness.issues.map((warning) => ({
          section: warning.section || "готовность",
          row: 0,
          message: `${warning.message}${warning.differenceHours ? ` (${warning.differenceHours} ч.)` : ""}`,
        })),
      );
    } else {
      elements.liveRegion.textContent = "Файл проверен. Можно активировать новую версию.";
    }
  } catch (error) {
    state.previewValid = false;
    elements.activateButton.disabled = true;
    setMessage(
      "error",
      error.message,
      error.payload?.issues || [],
    );
  } finally {
    setBusy(elements.previewButton, false);
  }
}

async function activateFile() {
  if (!state.file || !state.previewValid) return;
  clearMessage();
  setBusy(elements.activateButton, true);
  try {
    const response = await fetch("/api/imports/activate", {
      method: "POST",
      body: makeFormData(),
    });
    const payload = await parseResponse(response);
    elements.stepCheck.className = "is-complete";
    elements.stepActivate.className = "is-current";
    setMessage("success", `Версия №${payload.versionId} активирована`);
    await loadStatus();
  } catch (error) {
    setMessage("error", error.message, error.payload?.issues || []);
  } finally {
    setBusy(elements.activateButton, false);
  }
}

function renderStatus(payload) {
  elements.systemState.className = "system-state is-ready";
  elements.systemState.querySelector("span:last-child").textContent = payload.activeVersionId
    ? `Активна версия №${payload.activeVersionId}`
    : "Хранилище готово";
  if (payload.activeVersionId) {
    elements.activeSummary.replaceChildren();
    const label = document.createElement("span");
    label.textContent = "Активная версия";
    const value = document.createElement("strong");
    value.textContent = `№${payload.activeVersionId}`;
    const counts = document.createElement("small");
    counts.textContent = `${payload.counts.teachers} преподавателей · ${payload.counts.groups} групп · ${payload.counts.students} студентов · ${payload.counts.rooms} аудиторий`;
    elements.activeSummary.append(label, value, counts);
  }

  elements.versionList.replaceChildren();
  elements.emptyHistory.hidden = payload.versions.length > 0;
  payload.versions.forEach((version) => {
    const item = document.createElement("li");
    const number = document.createElement("span");
    number.textContent = String(version.versionId).padStart(2, "0");
    const details = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = version.sourceName;
    const date = document.createElement("small");
    date.textContent = `${formatDate(version.createdAt)}${version.isActive ? " · активна" : ""}`;
    details.append(name, date);
    if (!version.isActive) {
      const button = document.createElement("button");
      button.className = "version-action";
      button.type = "button";
      button.textContent = "Сделать активной";
      button.addEventListener("click", () => activateVersion(version.versionId, button));
      details.append(button);
    }
    item.append(number, details);
    elements.versionList.append(item);
  });
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    const payload = await parseResponse(response);
    renderStatus(payload);
    await loadSolverProblem(payload.activeVersionId);
  } catch (error) {
    elements.systemState.className = "system-state is-error";
    elements.systemState.querySelector("span:last-child").textContent = "Хранилище недоступно";
    setMessage("error", error.message);
  }
}

async function activateVersion(versionId, button) {
  setBusy(button, true);
  try {
    const response = await fetch(`/api/versions/${versionId}/activate`, { method: "POST" });
    await parseResponse(response);
    setMessage("success", `Версия №${versionId} снова активна`);
    await loadStatus();
  } catch (error) {
    setMessage("error", error.message);
  } finally {
    setBusy(button, false);
  }
}

elements.fileInput.addEventListener("change", () => setFile(elements.fileInput.files[0] || null));
elements.clearFile.addEventListener("click", () => setFile(null));
elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  previewFile();
});
elements.activateButton.addEventListener("click", activateFile);
elements.refreshStatus.addEventListener("click", loadStatus);
elements.solveButton.addEventListener("click", runSolver);

["dragenter", "dragover"].forEach((eventName) => {
  elements.uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.uploadZone.classList.add("is-dragging");
  });
});
["dragleave", "drop"].forEach((eventName) => {
  elements.uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.uploadZone.classList.remove("is-dragging");
  });
});
elements.uploadZone.addEventListener("drop", (event) => {
  const file = event.dataTransfer.files[0];
  if (file) setFile(file);
});

loadStatus();
