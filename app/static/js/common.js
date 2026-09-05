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

// Функция показа уведомлений
function showAlert(type, message) {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    const container = document.querySelector('main .container');
    if (container) {
        container.insertBefore(alertDiv, container.firstChild);
    } else {
        // Fallback: добавляем в body
        document.body.appendChild(alertDiv);
    }
    
    // Автоматически скрываем через 5 секунд
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.remove();
        }
    }, 5000);
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
    // Если нет preset_id или он равен custom, возвращаем "Пользовательский"
    if (!job.preset_id || job.preset_id === 'custom') {
        return 'Пользовательский';
    }
    
    // Пытаемся получить название из кеша пресетов
    if (window.presetsCache[job.preset_id]) {
        return window.presetsCache[job.preset_id];
    }
    
    // Словарь известных пресетов (на случай, если кеш еще не загружен)
    const knownPresets = {
        'default': 'rip / По умолчанию',
        'aac_audio': 'rip / HEVC + AAC',
        'aac_fdk_audio': 'rip / HEVC + AAC FDK',
        'opus_audio': 'rip / HEVC + Opus',
        'video_only': 'rip / Только видео',
        'bd_default': 'remux / По умолчанию',
        'bd_opus_audio': 'remux / HEVC + Opus',
        'bd_aac_audio': 'remux / HEVC + AAC',
        'bd_aac_fdk_audio': 'remux / HEVC + AAC FDK',
        'bd_video_only': 'remux / Только видео'
    };
    
    return knownPresets[job.preset_id] || job.preset_id || 'По умолчанию';
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
        progressHtml = '<span class="text-muted">Ожидание</span>';
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
    
    return jobDiv;
}

// Функция создания строки задания (для страницы логов)
function createJobRow(job) {
    const statusClass = `status-${job.status}`;
    const statusText = getStatusText(job.status);
    const createdDate = formatDateTime(job.created_at);
    
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
        progressHtml = '<span class="text-muted">Ожидание</span>';
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
    
    return row;
}

// Действия с заданиями
function downloadJob(jobId) {
    window.open(`/api/jobs/${jobId}/download`, '_blank');
}

function deleteJob(jobId) {
    if (!confirm('Удалить задание? Если оно выполняется — FFmpeg будет остановлен.')) {
        return;
    }
    fetch(`/api/jobs/${jobId}/delete`, { method: 'DELETE' })
        .then(response => {
            if (!response.ok) {
                throw new Error('Ошибка сервера');
            }
            return response.json();
        })
        .then(result => {
            const jobElement = document.querySelector(`[data-job-id="${jobId}"]`);
            if (jobElement) {
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
        })
        .catch(error => {
            console.error('Ошибка удаления:', error);
            showAlert('danger', 'Ошибка удаления задания');
        });
}

function stopJob(jobId) {
    if (!confirm('Остановить кодирование? Процесс FFmpeg будет завершён.')) {
        return;
    }
    fetch(`/api/jobs/${jobId}/stop`, { method: 'POST' })
        .then(response => {
            if (!response.ok) {
                throw new Error('Ошибка сервера');
            }
            return response.json();
        })
        .then(result => {
            const stop = result.stop || {};
            if (stop.had_process && !stop.process_dead) {
                showAlert('warning', 'Отмена запрошена, но процесс FFmpeg мог не завершиться');
            } else {
                showAlert('success', 'Задание остановлено');
            }
        })
        .catch(error => {
            console.error('Ошибка остановки:', error);
            showAlert('danger', 'Ошибка остановки задания');
        });
}
