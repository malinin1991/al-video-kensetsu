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
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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
    if (typeof bootstrap === 'undefined' || !bootstrap.Tooltip) {
        return;
    }
    const scope = root || document;
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

// Функция создания элемента задания (для главной страницы)
function createJobElement(job) {
    const statusClass = `status-${job.status}`;
    const statusText = getStatusText(job.status);
    
    // Определяем название пресета для отображения
    const presetName = getPresetDisplayName(job);
    
    let progressHtml = '';
    if (job.status === 'in_progress') {
        const progressPercent = (job.progress || 0).toFixed(1);
        progressHtml = `
            <div class="job-progress">
                <div class="progress-bar" style="width: ${progressPercent}%"></div>
                <div class="progress-text">${progressPercent}%</div>
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
            <button class="btn btn-download btn-sm me-1" onclick="downloadJob('${job.id}')" title="Скачать">
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
            <div class="d-flex justify-content-between align-items-start">
                <div class="flex-grow-1 me-2" style="min-width: 0;">
                    <h6 class="card-title text-truncate" title="${escapeHtml(job.filename)}">${escapeHtml(job.filename)}</h6>
                    <span class="badge ${statusClass}">${statusText}</span>
                    ${job.error_message ? `<div class="text-danger small mt-1">${escapeHtml(job.error_message)}</div>` : ''}
                </div>
                <div class="flex-shrink-0 d-flex gap-1">
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
        const clampedProgress = Math.max(0, Math.min(100, job.progress || 0));
        const progressPercent = clampedProgress.toFixed(1);
        progressHtml = `<span class="text-primary fw-bold">${progressPercent}%</span>`;
    } else if (job.status === 'done') {
        progressHtml = '<span class="text-success fw-bold">100.0%</span>';
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
            <button class="btn btn-download btn-sm me-1" onclick="downloadJob('${job.id}')" title="Скачать">
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
        <td><code>${job.id.substring(0, 8)}...</code></td>
        <td>${escapeHtml(job.filename)}</td>
        <td><span class="badge ${statusClass}">${statusText}</span></td>
        <td>${progressHtml}</td>
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

        const jobElement = document.querySelector(`[data-job-id="${jobId}"]`);
        if (jobElement) {
            disposeActionTooltips(jobElement);
            jobElement.remove();
        }
        if (typeof removeCachedJob === 'function') {
            removeCachedJob(jobId);
        }
        if (typeof window.refreshJobLogOptions === 'function') {
            window.refreshJobLogOptions();
        }
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
