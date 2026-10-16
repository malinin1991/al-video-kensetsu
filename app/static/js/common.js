// Общие функции для всех страниц приложения

/** IANA-зона браузера (например Asia/Novosibirsk), пустая строка при недоступности. */
function getBrowserTimeZone() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (e) {
        return '';
    }
}

/**
 * Парсит ISO UTC / unix-секунды / Date в Date для отображения в локальном TZ браузера.
 * Naive ISO без Z трактуем как UTC (сервер хранит UTC).
 */
function parseServerDate(value) {
    if (value == null || value === '') {
        return null;
    }
    if (value instanceof Date) {
        return Number.isNaN(value.getTime()) ? null : value;
    }
    if (typeof value === 'number') {
        const ms = value < 1e12 ? value * 1000 : value;
        const d = new Date(ms);
        return Number.isNaN(d.getTime()) ? null : d;
    }
    let s = String(value).trim();
    if (/^\d+(\.\d+)?$/.test(s)) {
        const num = parseFloat(s);
        const ms = num < 1e12 ? num * 1000 : num;
        const d = new Date(ms);
        return Number.isNaN(d.getTime()) ? null : d;
    }
    // ISO без смещения → как UTC
    if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(s)) {
        s += 'Z';
    }
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
}

function formatDateTime(value) {
    const d = parseServerDate(value);
    return d ? d.toLocaleString() : '—';
}

function formatTime(value) {
    const d = parseServerDate(value);
    return d ? d.toLocaleTimeString() : '—';
}

// Функция экранирования HTML
function escapeHtml(text) {
    return String(text ?? '').replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[character]);
}

const _ALERT_TYPES = new Set([
    'success', 'danger', 'warning', 'info', 'primary', 'secondary',
]);

/** Активное подтверждение: один экземпляр модалки, без накопления listener. */
let _activeConfirmFinish = null;

function getToastContainer() {
    let container = document.getElementById('appToastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'appToastContainer';
        container.className = 'app-toast-container';
        container.setAttribute('aria-live', 'polite');
        container.setAttribute('aria-atomic', 'true');
        document.body.appendChild(container);
    }
    return container;
}

/** Уведомление сверху экрана (не зависит от скролла страницы). */
function showAlert(type, message) {
    const safeType = _ALERT_TYPES.has(type) ? type : 'info';
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${safeType} alert-dismissible fade show`;
    alertDiv.setAttribute('role', 'alert');

    const textNode = document.createElement('span');
    textNode.textContent = message == null ? '' : String(message);
    alertDiv.appendChild(textNode);

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn-close';
    closeBtn.setAttribute('data-bs-dismiss', 'alert');
    closeBtn.setAttribute('aria-label', 'Закрыть');
    alertDiv.appendChild(closeBtn);

    getToastContainer().appendChild(alertDiv);

    if (safeType === 'danger' || safeType === 'warning') return;

    setTimeout(() => {
        if (!alertDiv.parentNode) {
            return;
        }
        alertDiv.classList.remove('show');
        setTimeout(() => alertDiv.remove(), 150);
    }, 5000);
}

/**
 * Подтверждение через Bootstrap-модалку (вместо window.confirm).
 * Не блокируется настройками браузера «не показывать диалоги».
 * Повторный вызов отменяет предыдущий Promise (false) и переиспользует модалку.
 */
function showConfirm(options) {
    const opts = typeof options === 'string'
        ? { message: options }
        : (options || {});

    const title = opts.title || 'Подтверждение';
    const message = opts.message || '';
    const confirmText = opts.confirmText || 'Подтвердить';
    const cancelText = opts.cancelText || 'Отмена';
    const confirmClass = opts.confirmClass || 'btn-danger';

    return new Promise((resolve) => {
        const modalEl = document.getElementById('appConfirmModal');
        if (!modalEl || typeof bootstrap === 'undefined' || !bootstrap.Modal) {
            console.warn('Модалка подтверждения недоступна, действие отменено');
            resolve(false);
            return;
        }

        // Снять предыдущее ожидание (без лишних listener на OK)
        if (typeof _activeConfirmFinish === 'function') {
            const prev = _activeConfirmFinish;
            _activeConfirmFinish = null;
            prev(false);
        }

        const titleEl = document.getElementById('appConfirmModalTitle');
        const bodyEl = document.getElementById('appConfirmModalBody');
        let okBtn = document.getElementById('appConfirmOkBtn');
        const cancelBtn = document.getElementById('appConfirmCancelBtn');

        // cloneNode сбрасывает все click-listener с предыдущих вызовов
        if (okBtn && okBtn.parentNode) {
            const freshOk = okBtn.cloneNode(true);
            okBtn.parentNode.replaceChild(freshOk, okBtn);
            okBtn = freshOk;
        }

        if (titleEl) titleEl.textContent = title;
        if (bodyEl) bodyEl.textContent = message;
        if (okBtn) {
            okBtn.textContent = confirmText;
            okBtn.className = `btn ${confirmClass}`;
        }
        if (cancelBtn) {
            cancelBtn.textContent = cancelText;
        }

        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        let settled = false;

        const finish = (value) => {
            if (settled) return;
            settled = true;
            if (_activeConfirmFinish === finish) {
                _activeConfirmFinish = null;
            }
            okBtn?.removeEventListener('click', onOk);
            modalEl.removeEventListener('hidden.bs.modal', onHidden);
            resolve(value);
        };

        const onOk = () => {
            finish(true);
            modal.hide();
        };

        const onHidden = () => {
            finish(false);
        };

        _activeConfirmFinish = finish;
        okBtn?.addEventListener('click', onOk);
        modalEl.addEventListener('hidden.bs.modal', onHidden, { once: true });

        document.querySelectorAll('[data-bs-original-title]').forEach((el) => {
            bootstrap.Tooltip.getInstance(el)?.hide();
        });

        modal.show();
    });
}

/** Bootstrap-тултипы сверху для кнопок действий с заданиями. */
function initActionTooltips(root) {
    const scope = root || document;
    scope.querySelectorAll('button[title]').forEach((element) => {
        if (!element.hasAttribute('aria-label')) {
            element.setAttribute('aria-label', element.getAttribute('title'));
        }
    });
    if (typeof bootstrap === 'undefined' || !bootstrap.Tooltip) {
        return;
    }
    const triggers = scope.querySelectorAll(
        '.btn-delete[title], .btn-download[title], .btn-warning[title]'
    );
    triggers.forEach((el) => {
        const existing = bootstrap.Tooltip.getInstance(el);
        if (existing) {
            existing.dispose();
        }
        const title = el.getAttribute('title') || el.getAttribute('data-bs-original-title');
        if (!title) {
            return;
        }
        new bootstrap.Tooltip(el, {
            title,
            placement: 'top',
            container: 'body',
            trigger: 'hover focus',
        });
    });
}

function disposeActionTooltips(root) {
    if (typeof bootstrap === 'undefined' || !bootstrap.Tooltip) {
        return;
    }
    const scope = root || document;
    scope.querySelectorAll('.btn-delete, .btn-download, .btn-warning').forEach((el) => {
        const tip = bootstrap.Tooltip.getInstance(el);
        if (tip) {
            tip.dispose();
        }
    });
}

// Функция получения текста статуса
function getStatusText(status) {
    const statusMap = {
        'planned': 'Запланировано',
        'in_progress': 'Выполняется',
        'done': 'Готово',
        'error': 'Ошибка',
        'cancelled': 'Отменено'
    };
    return statusMap[status] || status;
}

// Глобальное хранилище пресетов
window.presetsCache = {};

// Выбранные задания (сохраняются при перерисовке карточек/строк)
window.selectedJobIds = window.selectedJobIds || new Set();

function isJobSelected(jobId) {
    return window.selectedJobIds.has(jobId);
}

function setJobSelected(jobId, selected) {
    if (selected) {
        window.selectedJobIds.add(jobId);
    } else {
        window.selectedJobIds.delete(jobId);
    }
}

function getVisibleJobIds() {
    const ids = [];
    document.querySelectorAll('.job-card[data-job-id], tr[data-job-id]').forEach((el) => {
        const jobId = el.getAttribute('data-job-id');
        if (jobId && !ids.includes(jobId)) {
            ids.push(jobId);
        }
    });
    return ids;
}

function updateJobsSelectionUI() {
    const visibleIds = getVisibleJobIds();
    // Убираем из выбора задания, которых уже нет в DOM
    Array.from(window.selectedJobIds).forEach((jobId) => {
        if (!visibleIds.includes(jobId)) {
            window.selectedJobIds.delete(jobId);
        }
    });

    const selectedCount = window.selectedJobIds.size;
    const countEl = document.getElementById('selectedJobsCount');
    if (countEl) {
        countEl.textContent = `выбрано: ${selectedCount}`;
    }

    const downloadBtn = document.getElementById('downloadSelectedJobsBtn');
    const deleteBtn = document.getElementById('deleteSelectedJobsBtn');
    if (downloadBtn) {
        downloadBtn.disabled = !Array.from(window.selectedJobIds).some((id) =>
            isJobDownloadable(window.jobsCache && window.jobsCache[id])
        );
    }
    if (deleteBtn) {
        deleteBtn.disabled = selectedCount < 1;
    }

    const selectAll = document.getElementById('selectAllJobs');
    if (selectAll) {
        const allSelected =
            visibleIds.length > 0 &&
            visibleIds.every((id) => window.selectedJobIds.has(id));
        const someSelected = visibleIds.some((id) => window.selectedJobIds.has(id));
        selectAll.checked = allSelected;
        selectAll.indeterminate = someSelected && !allSelected;
        selectAll.disabled = visibleIds.length === 0;
    }
}

function toggleSelectAllJobs(checked) {
    getVisibleJobIds().forEach((jobId) => {
        setJobSelected(jobId, checked);
        document
            .querySelectorAll(`.job-select-checkbox[data-job-id="${jobId}"]`)
            .forEach((cb) => {
                cb.checked = checked;
            });
    });
    updateJobsSelectionUI();
}

function onJobCheckboxChange(checkbox) {
    const jobId = checkbox.getAttribute('data-job-id');
    if (!jobId) {
        return;
    }
    setJobSelected(jobId, checkbox.checked);
    document
        .querySelectorAll(`.job-select-checkbox[data-job-id="${jobId}"]`)
        .forEach((cb) => {
            if (cb !== checkbox) {
                cb.checked = checkbox.checked;
            }
        });
    updateJobsSelectionUI();
}

function jobCheckboxHtml(jobId) {
    const checked = isJobSelected(jobId) ? 'checked' : '';
    return `
        <input type="checkbox" class="form-check-input job-select-checkbox"
               data-job-id="${jobId}" ${checked}
               aria-label="Выбрать задание"
               onchange="onJobCheckboxChange(this)">
    `;
}

function parseContentDispositionFilename(header) {
    if (!header) {
        return null;
    }
    const utf8Match = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
    if (utf8Match) {
        try {
            return decodeURIComponent(utf8Match[1].trim());
        } catch (_e) {
            return utf8Match[1].trim();
        }
    }
    const plainMatch = /filename\s*=\s*"([^"]+)"/i.exec(header)
        || /filename\s*=\s*([^;]+)/i.exec(header);
    if (plainMatch) {
        return plainMatch[1].trim().replace(/^["']|["']$/g, '');
    }
    return null;
}

function removeJobFromDom(jobId) {
    document.querySelectorAll(`[data-job-id="${jobId}"]`).forEach((el) => {
        if (el.classList && (el.classList.contains('job-card') || el.tagName === 'TR')) {
            if (typeof disposeActionTooltips === 'function') {
                disposeActionTooltips(el);
            }
            el.remove();
        }
    });
    window.selectedJobIds.delete(jobId);
    if (typeof removeCachedJob === 'function') {
        removeCachedJob(jobId);
    }
}

// Функция получения названия пресета для отображения
function getPresetDisplayName(job) {
    if (job.preset_label) {
        return job.preset_label;
    }

    if (job.video_id === 'custom' || job.audio_id === 'custom') {
        let label = 'Пользовательский';
        if (job.audio_downmix === 'stereo') {
            label += ' · stereo downmix';
        }
        return label;
    }

    const videoName = (window.videosCache && window.videosCache[job.video_id]?.name)
        || (window.presetsCache && window.presetsCache[job.video_id])
        || job.video_id
        || 'video';
    const audioName = (window.audiosCache && window.audiosCache[job.audio_id]?.name)
        || (window.presetsCache && window.presetsCache[job.audio_id])
        || job.audio_id
        || 'audio';
    let label = `${videoName} / ${audioName}`;
    if (job.audio_downmix === 'stereo') {
        label += ' · stereo downmix';
    }
    return label;
}

function isJobDownloadable(job) {
    return !!job && job.status === 'done' && job.output_available !== false;
}

function getJobProgress(job) {
    const progress = Number(job && job.progress);
    return Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : 0;
}

function getJobProgressText(job) {
    return job.status === 'in_progress' && getJobProgress(job) >= 100
        ? 'Финализация…'
        : `${getJobProgress(job).toFixed(1)}%`;
}

// Функция создания элемента задания (для главной страницы)
function createJobElement(job) {
    const statusClass = `status-${job.status}`;
    const statusText = getStatusText(job.status);
    
    // Определяем название пресета для отображения
    const presetName = getPresetDisplayName(job);
    
    let progressHtml = '';
    if (job.status === 'in_progress') {
        const progressPercent = getJobProgress(job).toFixed(1);
        progressHtml = `
            <div class="job-progress">
                <div class="progress-bar${getJobProgress(job) >= 100 ? ' progress-bar-striped progress-bar-animated' : ''}" role="progressbar" aria-label="Прогресс кодирования" aria-valuenow="${progressPercent}" aria-valuemin="0" aria-valuemax="100" aria-valuetext="${getJobProgressText(job)}" style="width: ${progressPercent}%"></div>
                <div class="progress-text">${getJobProgressText(job)}</div>
            </div>
            <div class="text-muted small mt-1">
                <i class="bi bi-gear"></i> Пресет: ${escapeHtml(presetName)}
            </div>
        `;
    } else if (job.status === 'done') {
        progressHtml = `
            <div class="job-progress">
                <div class="progress-bar" style="width: 100%"></div>
                <div class="progress-text">100.0%</div>
            </div>
            <div class="text-muted small mt-1">
                <i class="bi bi-gear"></i> Пресет: ${escapeHtml(presetName)}
            </div>
            ${job.output_available === false ? '<div class="text-warning small mt-1">Готовый файл отсутствует на диске</div>' : ''}
        `;
    } else if (job.status === 'error') {
        progressHtml = '<span class="text-danger">Ошибка</span>';
    } else if (job.status === 'cancelled') {
        progressHtml = '<span class="text-warning">Отменено</span>';
    } else {
        // planned / очередь
        progressHtml = `
            <span class="text-muted">Ожидание</span>
            <div class="text-muted small mt-1">
                <i class="bi bi-gear"></i> Пресет: ${escapeHtml(presetName)}
            </div>
        `;
    }
    
    let actionsHtml = '';
    if (job.status === 'done') {
        actionsHtml = `
            <button class="btn btn-download btn-sm me-1" onclick="downloadJob('${job.id}')" ${isJobDownloadable(job) ? '' : 'disabled'} title="${isJobDownloadable(job) ? 'Скачать' : 'Готовый файл отсутствует на диске'}">
                <i class="bi bi-download"></i>
            </button>
            <button class="btn btn-delete btn-sm" onclick="deleteJob('${job.id}')" title="Удалить">
                <i class="bi bi-trash"></i>
            </button>
        `;
    } else if (job.status === 'in_progress') {
        actionsHtml = `
            <button class="btn btn-warning btn-sm me-1" onclick="stopJob('${job.id}')" title="Остановить">
                <i class="bi bi-stop-fill"></i> Остановить
            </button>
            <button class="btn btn-delete btn-sm" onclick="deleteJob('${job.id}')" title="Остановить и удалить">
                <i class="bi bi-trash"></i>
            </button>
        `;
    } else if (job.status === 'planned') {
        actionsHtml = `
            <button class="btn btn-delete btn-sm" onclick="deleteJob('${job.id}')" title="Удалить из очереди">
                <i class="bi bi-trash"></i> Удалить из очереди
            </button>
        `;
    } else if (job.status === 'error' || job.status === 'cancelled') {
        actionsHtml = `
            <button class="btn btn-delete btn-sm" onclick="deleteJob('${job.id}')" title="Удалить">
                <i class="bi bi-trash"></i>
            </button>
        `;
    }
    
    const jobDiv = document.createElement('div');
    jobDiv.className = 'card job-card';
    jobDiv.setAttribute('data-job-id', job.id);
    jobDiv.innerHTML = `
        <div class="card-body">
            <div class="d-flex justify-content-between align-items-start job-card-heading">
                <div class="d-flex align-items-start flex-grow-1 me-2" style="min-width: 0;">
                    ${jobCheckboxHtml(job.id)}
                    <div class="flex-grow-1" style="min-width: 0;">
                        <h6 class="card-title" title="${escapeHtml(job.filename)}">${escapeHtml(job.filename)}</h6>
                        <span class="badge ${statusClass}">${statusText}</span>
                        ${job.error_message ? `<div class="text-danger small mt-1">${escapeHtml(job.error_message)}</div>` : ''}
                    </div>
                </div>
                <div class="flex-shrink-0 d-flex gap-1 job-card-actions">
                    ${actionsHtml}
                </div>
            </div>
            ${progressHtml}
        </div>
    `;

    initActionTooltips(jobDiv);
    return jobDiv;
}

// Функция создания строки задания (для страницы логов)
function createJobRow(job) {
    const statusClass = `status-${job.status}`;
    const statusText = getStatusText(job.status);
    const createdDate = formatDateTime(job.created_at);
    const presetName = getPresetDisplayName(job);
    
    let progressHtml = '';
    if (job.status === 'in_progress') {
        progressHtml = `<span class="text-primary fw-bold">${getJobProgressText(job)}</span>`;
    } else if (job.status === 'done') {
        progressHtml = job.output_available === false
            ? '<span class="text-warning">Файл отсутствует</span>'
            : '<span class="text-success fw-bold">100.0%</span>';
    } else if (job.status === 'error') {
        progressHtml = '<span class="text-danger">Ошибка</span>';
    } else if (job.status === 'cancelled') {
        progressHtml = '<span class="text-warning">Отменено</span>';
    } else {
        progressHtml = `<span class="text-muted" title="Пресет: ${escapeHtml(presetName)}">Ожидание · ${escapeHtml(presetName)}</span>`;
    }
    
    let actionsHtml = '';
    if (job.status === 'done') {
        actionsHtml = `
            <button class="btn btn-download btn-sm me-1" onclick="downloadJob('${job.id}')" ${isJobDownloadable(job) ? '' : 'disabled'} title="${isJobDownloadable(job) ? 'Скачать' : 'Готовый файл отсутствует на диске'}">
                <i class="bi bi-download"></i>
            </button>
            <button class="btn btn-delete btn-sm" onclick="deleteJob('${job.id}')" title="Удалить">
                <i class="bi bi-trash"></i>
            </button>
        `;
    } else if (job.status === 'in_progress') {
        actionsHtml = `
            <button class="btn btn-warning btn-sm me-1" onclick="stopJob('${job.id}')" title="Остановить">
                <i class="bi bi-stop-fill"></i>
            </button>
            <button class="btn btn-delete btn-sm" onclick="deleteJob('${job.id}')" title="Остановить и удалить">
                <i class="bi bi-trash"></i>
            </button>
        `;
    } else if (job.status === 'planned') {
        actionsHtml = `
            <button class="btn btn-delete btn-sm" onclick="deleteJob('${job.id}')" title="Удалить из очереди">
                <i class="bi bi-trash"></i>
            </button>
        `;
    } else if (job.status === 'error' || job.status === 'cancelled') {
        actionsHtml = `
            <button class="btn btn-delete btn-sm" onclick="deleteJob('${job.id}')" title="Удалить">
                <i class="bi bi-trash"></i>
            </button>
        `;
    }
    
    const row = document.createElement('tr');
    row.setAttribute('data-job-id', job.id);
    row.innerHTML = `
        <td>${jobCheckboxHtml(job.id)}</td>
        <td><code>${job.id.substring(0, 8)}...</code></td>
        <td>${escapeHtml(job.filename)}</td>
        <td><span class="badge ${statusClass}">${statusText}</span></td>
        <td class="job-progress-cell">${progressHtml}</td>
        <td>${createdDate}</td>
        <td>${actionsHtml}</td>
    `;

    initActionTooltips(row);
    return row;
}

// Действия с заданиями
function downloadJob(jobId) {
    window.open(`/api/jobs/${jobId}/download`, '_blank');
}

async function downloadSelectedJobs() {
    const selectedIds = Array.from(window.selectedJobIds);
    if (selectedIds.length === 0) {
        return;
    }

    const skipped = [];
    const downloadIds = [];
    selectedIds.forEach((jobId) => {
        const job = window.jobsCache && window.jobsCache[jobId];
        if (isJobDownloadable(job)) {
            downloadIds.push(jobId);
        } else {
            skipped.push(jobId);
        }
    });

    if (skipped.length > 0) {
        showAlert(
            'warning',
            `Пропущено ${skipped.length} заданий (не готовы к скачиванию)`
        );
    }
    if (downloadIds.length === 0) {
        showAlert('warning', 'Нет готовых заданий среди выбранных');
        return;
    }

    try {
        const response = await fetch('/api/jobs/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_ids: downloadIds }),
        });

        if (response.status === 409) {
            const err = await response.json();
            const missing = (err.missing || [])
                .map((item) => item.filename || item.path || item.job_id)
                .join(', ');
            showAlert(
                'danger',
                `${err.error || 'Файлы отсутствуют на диске'}: ${missing}`
            );
            return;
        }

        if (!response.ok) {
            let message = 'Ошибка пакетного скачивания';
            try {
                const err = await response.json();
                if (err.error) {
                    message = err.error;
                }
            } catch (_e) {
                // ignore
            }
            showAlert('danger', message);
            return;
        }

        const blob = await response.blob();
        const filename =
            parseContentDispositionFilename(
                response.headers.get('Content-Disposition')
            ) || 'kensetsu-batch.zip';
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showAlert('success', `Скачан архив ${filename}`);
    } catch (error) {
        console.error('Ошибка пакетного скачивания:', error);
        showAlert('danger', 'Ошибка пакетного скачивания');
    }
}

async function deleteSelectedJobs() {
    const selectedIds = Array.from(window.selectedJobIds);
    if (selectedIds.length === 0) {
        return;
    }

    const confirmed = await showConfirm({
        title: 'Удаление заданий',
        message: `Удалить ${selectedIds.length} заданий? Выполняющиеся будут остановлены.`,
        confirmText: 'Удалить',
        confirmClass: 'btn-danger',
    });
    if (!confirmed) {
        return;
    }

    try {
        const response = await fetch('/api/jobs/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_ids: selectedIds }),
        });
        const result = await response.json();
        if (!response.ok && response.status !== 207) {
            throw new Error(result.error || 'Ошибка сервера');
        }

        (result.deleted || []).forEach((jobId) => {
            removeJobFromDom(jobId);
        });

        const jobsContainer = document.getElementById('jobsContainer');
        if (jobsContainer && !jobsContainer.querySelector('.job-card')) {
            jobsContainer.innerHTML = `
                <div class="text-center text-muted">
                    <i class="bi bi-inbox"></i><br>
                    Нет активных заданий
                </div>
            `;
        }
        const jobsTableBody = document.getElementById('jobsTableBody');
        if (jobsTableBody && !jobsTableBody.querySelector('tr[data-job-id]')) {
            jobsTableBody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-muted">
                        <i class="bi bi-inbox"></i><br>
                        Нет заданий
                    </td>
                </tr>
            `;
        }

        if (typeof window.refreshJobLogOptions === 'function') {
            window.refreshJobLogOptions();
        }
        updateJobsSelectionUI();

        const deletedCount = (result.deleted || []).length;
        const errorCount = (result.errors || []).length;
        if (errorCount > 0) {
            showAlert(
                'warning',
                `Удалено ${deletedCount}, ошибок: ${errorCount}`
            );
        } else {
            showAlert('success', `Удалено заданий: ${deletedCount}`);
        }
    } catch (error) {
        console.error('Ошибка пакетного удаления:', error);
        showAlert('danger', 'Ошибка пакетного удаления');
    }
}

async function deleteJob(jobId) {
    const confirmed = await showConfirm({
        title: 'Удаление задания',
        message: 'Удалить задание? Если оно выполняется — FFmpeg будет остановлен.',
        confirmText: 'Удалить',
        confirmClass: 'btn-danger',
    });
    if (!confirmed) {
        return;
    }

    try {
        const response = await fetch(`/api/jobs/${jobId}/delete`, { method: 'DELETE' });
        if (!response.ok) {
            throw new Error('Ошибка сервера');
        }
        const result = await response.json();

        removeJobFromDom(jobId);
        const jobsContainer = document.getElementById('jobsContainer');
        if (jobsContainer && !jobsContainer.querySelector('.job-card')) {
            jobsContainer.innerHTML = `
                <div class="text-center text-muted">
                    <i class="bi bi-inbox"></i><br>
                    Нет активных заданий
                </div>
            `;
        }
        const jobsTableBody = document.getElementById('jobsTableBody');
        if (jobsTableBody && !jobsTableBody.querySelector('tr[data-job-id]')) {
            jobsTableBody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-muted">
                        <i class="bi bi-inbox"></i><br>
                        Нет заданий
                    </td>
                </tr>
            `;
        }
        if (typeof window.refreshJobLogOptions === 'function') {
            window.refreshJobLogOptions();
        }
        updateJobsSelectionUI();
        const stop = result.stop || {};
        if (stop.had_process && !stop.process_dead) {
            showAlert('warning', 'Задание удалено, но процесс FFmpeg мог не завершиться');
        } else if (stop.removed_from_queue) {
            showAlert('success', 'Задание удалено из очереди');
        } else {
            showAlert('success', 'Задание удалено');
        }
    } catch (error) {
        console.error('Ошибка удаления:', error);
        showAlert('danger', 'Ошибка удаления задания');
    }
}

async function stopJob(jobId) {
    const confirmed = await showConfirm({
        title: 'Остановка кодирования',
        message: 'Остановить кодирование? Процесс FFmpeg будет завершён.',
        confirmText: 'Остановить',
        confirmClass: 'btn-warning',
    });
    if (!confirmed) {
        return;
    }

    try {
        const response = await fetch(`/api/jobs/${jobId}/stop`, { method: 'POST' });
        if (!response.ok) {
            throw new Error('Ошибка сервера');
        }
        const result = await response.json();
        const stop = result.stop || {};
        if (stop.had_process && !stop.process_dead) {
            showAlert('warning', 'Отмена запрошена, но процесс FFmpeg мог не завершиться');
        } else {
            showAlert('success', 'Задание остановлено');
        }
    } catch (error) {
        console.error('Ошибка остановки:', error);
        showAlert('danger', 'Ошибка остановки задания');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const selectAll = document.getElementById('selectAllJobs');
    if (selectAll) {
        selectAll.addEventListener('change', () => {
            toggleSelectAllJobs(selectAll.checked);
        });
    }
    const downloadBtn = document.getElementById('downloadSelectedJobsBtn');
    if (downloadBtn) {
        downloadBtn.addEventListener('click', () => {
            downloadSelectedJobs();
        });
    }
    const deleteBtn = document.getElementById('deleteSelectedJobsBtn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', () => {
            deleteSelectedJobs();
        });
    }
    updateJobsSelectionUI();
});

