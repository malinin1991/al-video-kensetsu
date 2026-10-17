let jobsPollingTimer = null;
let fallbackRefreshInProgress = false;

function getPollingFetchOptions() {
    const options = { cache: 'no-store' };
    if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
        options.signal = AbortSignal.timeout(10000);
    }
    return options;
}

function setConnectionStatus(state) {
    const status = document.getElementById('connectionStatus');
    if (!status) return;
    const states = {
        live: ['text-bg-success', 'Обновления подключены', 'Изменения заданий и логи поступают сразу'],
        fallback: ['text-bg-warning', 'Обновление каждые 5 с', 'Резервный режим: задания и логи загружаются через HTTP'],
        offline: ['text-bg-danger', 'Сервер недоступен', 'Показанные данные могут устареть. Проверяем соединение каждые 5 секунд'],
    };
    const [className, label, title] = states[state] || states.offline;
    status.className = `badge ${className} ms-lg-3`;
    status.textContent = label;
    status.setAttribute('title', title);
}

async function refreshFallbackJobs() {
    if (fallbackRefreshInProgress || window.socket?.connected || document.hidden) return;
    fallbackRefreshInProgress = true;
    try {
        const loaded = await loadJobsOnConnect();
        if (typeof window.refreshFallbackLogs === 'function') await window.refreshFallbackLogs();
        if (!window.socket?.connected) setConnectionStatus(loaded ? 'fallback' : 'offline');
    } catch (error) {
        console.error('Ошибка резервного обновления:', error);
        if (!window.socket?.connected) setConnectionStatus('offline');
    } finally {
        fallbackRefreshInProgress = false;
    }
}

function startJobsPolling() {
    if (jobsPollingTimer === null) {
        jobsPollingTimer = setInterval(refreshFallbackJobs, 5000);
    }
}

function stopJobsPolling() {
    if (jobsPollingTimer !== null) clearInterval(jobsPollingTimer);
    jobsPollingTimer = null;
}

// Первое чтение истории не зависит от установления Socket.IO соединения.
document.addEventListener('DOMContentLoaded', function() {
    // Проверяем, находимся ли мы в режиме разработки
    window.isDevelopment = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    
    startJobsPolling();
    refreshFallbackJobs();
    if (typeof io !== 'function') return;
    
    // Настраиваем подключение Socket.IO с правильными параметрами
    try {
        window.socket = io({
            transports: ['polling', 'websocket'],
            upgrade: true,
            rememberUpgrade: true,
            timeout: 20000,
            forceNew: false,
            autoConnect: true,
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionAttempts: 5
        });
    } catch (error) {
        console.error('Не удалось подключить обновления Socket.IO:', error);
        return;
    }

// Обработчики подключения/отключения
window.socket.on('connect', () => {
    stopJobsPolling();
    setConnectionStatus('live');
    if (typeof window.refreshPresetCatalog === 'function') window.refreshPresetCatalog();
    if (window.isDevelopment) {
        console.log('WebSocket подключен');
        console.log('Socket ID:', window.socket.id);
    }
    
    // Загружаем задания при подключении
    loadJobsOnConnect();
    if (typeof window.refreshFallbackLogs === 'function') window.refreshFallbackLogs();
});

window.socket.on('presets_updated', () => {
    if (typeof window.refreshPresetCatalog === 'function') window.refreshPresetCatalog();
});

window.socket.on('disconnect', () => {
    setConnectionStatus('fallback');
    startJobsPolling();
    refreshFallbackJobs();
    if (window.isDevelopment) {
        console.log('WebSocket отключен');
    }
});

// Обработчик ошибок
window.socket.on('connect_error', (error) => {
    startJobsPolling();
    refreshFallbackJobs();
    console.error('Ошибка подключения WebSocket:', error);
    console.error('Детали ошибки:', {
        type: error.type,
        description: error.description,
        context: error.context,
        transport: error.transport
    });
});

// Обработчик ошибок переподключения
window.socket.io.on('reconnect_error', (error) => {
    console.error('Ошибка переподключения WebSocket:', error);
});

// Обработчик успешного переподключения
window.socket.io.on('reconnect', (attemptNumber) => {
    if (window.isDevelopment) {
        console.log('WebSocket переподключен после', attemptNumber, 'попыток');
    }
});

    // Обработчик всех событий для отладки (только в режиме разработки)
    if (window.isDevelopment && window.socket && typeof window.socket.onAny === 'function') {
        window.socket.onAny((eventName, ...args) => {
            console.log('SocketIO событие:', eventName, args);
        });
    }

    // Глобальные обработчики для обновления прогресса везде
    window.socket.on('progress_update', (data) => {
        // Обновляем прогресс во всех контейнерах заданий
        updateJobProgressGlobally(data.job_id, data.progress);
    });

    window.socket.on('job_update', (job) => {
        // Обновляем задание во всех контейнерах
        updateJobGlobally(job);
    });

    window.socket.on('log_update', (data) => {
        // Обрабатываем обновления логов (если нужно)
        if (window.isDevelopment) {
            console.log('Получен log_update:', data);
        }
    });
});

window.jobsCache = window.jobsCache || {};

const JOB_STATUS_RANK = {
    planned: 0,
    in_progress: 1,
    done: 2,
    error: 2,
    cancelled: 2,
};

function mergeJobData(existing, incoming) {
    if (!incoming) {
        return existing ? { ...existing } : null;
    }
    if (!existing) {
        return { ...incoming };
    }

    const merged = { ...existing, ...incoming };
    const existingRank = JOB_STATUS_RANK[existing.status] ?? 0;
    const incomingRank = JOB_STATUS_RANK[incoming.status] ?? 0;

    // Не откатываем статус из-за устаревшего ответа /api/jobs
    if (existingRank > incomingRank) {
        merged.status = existing.status;
        merged.progress = Math.max(existing.progress || 0, incoming.progress || 0);
        if (existing.error_message && !incoming.error_message) {
            merged.error_message = existing.error_message;
        }
    } else if (existingRank === incomingRank) {
        merged.progress = Math.max(existing.progress || 0, incoming.progress || 0);
    }

    return merged;
}

function cacheJob(job) {
    if (!job || !job.id) {
        return job;
    }
    window.jobsCache[job.id] = mergeJobData(window.jobsCache[job.id], job);
    return window.jobsCache[job.id];
}

function removeCachedJob(jobId) {
    if (jobId && window.jobsCache[jobId]) {
        delete window.jobsCache[jobId];
    }
}

// Глобальные функции для обновления прогресса и заданий
function updateJobProgressGlobally(jobId, progress) {
    const cached = window.jobsCache[jobId];
    // Поздние progress-события не должны менять готовое/ошибочное задание.
    if (cached && !['planned', 'in_progress'].includes(cached.status)) {
        return;
    }
    const clampedProgress = Math.max(getJobProgress(cached), getJobProgress({ progress }));

    if (cached) {
        cached.progress = clampedProgress;
        if (clampedProgress > 0 && cached.status === 'planned') {
            cached.status = 'in_progress';
            updateJobGlobally(cached);
            return;
        }
    }

    // Обновляем прогресс в контейнере заданий на главной странице
    const jobElement = document.querySelector(`[data-job-id="${jobId}"]`);
    if (jobElement) {
        const progressContainer = jobElement.querySelector('.job-progress');
        if (progressContainer) {
            const progressBar = progressContainer.querySelector('.progress-bar');
            const progressText = progressContainer.querySelector('.progress-text');
            if (progressBar && progressText) {
                const progressPercent = clampedProgress.toFixed(1);
                const progressLabel = getJobProgressText({ status: 'in_progress', progress: clampedProgress });
                progressBar.style.width = `${progressPercent}%`;
                progressBar.setAttribute('aria-valuenow', progressPercent);
                progressBar.setAttribute('aria-valuetext', progressLabel);
                progressBar.classList.toggle('progress-bar-striped', clampedProgress >= 100);
                progressBar.classList.toggle('progress-bar-animated', clampedProgress >= 100);
                progressText.textContent = progressLabel;
            }
        } else if (clampedProgress > 0) {
            // Прогресс пришёл, а карточка ещё в состоянии «Ожидание» — перерисовать
            const cached = window.jobsCache[jobId] || { id: jobId, status: 'in_progress', progress: clampedProgress };
            updateJobGlobally(cacheJob(cached));
        }
    }
    
    // Обновляем прогресс в таблице на странице логов
    const jobRow = document.querySelector(`tr[data-job-id="${jobId}"]`);
    if (jobRow) {
        const progressCell = jobRow.querySelector('td.job-progress-cell');
        if (progressCell) {
            const progressLabel = getJobProgressText({ status: 'in_progress', progress: clampedProgress });
            progressCell.innerHTML = `<span class="text-primary fw-bold">${progressLabel}</span>`;
        }
    }
}

function updateJobGlobally(job) {
    job = cacheJob(job);
    if (!job) {
        return;
    }
    // Обновляем задание в контейнере на главной странице (только если есть jobsContainer)
    const jobsContainer = document.getElementById('jobsContainer');
    if (jobsContainer) {
        const jobElement = jobsContainer.querySelector(`[data-job-id="${job.id}"]`);
        
        if (jobElement) {
            // Создаем новый элемент и заменяем старый
            if (typeof disposeActionTooltips === 'function') {
                disposeActionTooltips(jobElement);
            }
            const newJobElement = createJobElement(job);
            jobElement.replaceWith(newJobElement);
        } else {
            // Сначала убираем пустое состояние, затем добавляем карточку
            const noJobsMessage = jobsContainer.querySelector(':scope > .text-center.text-muted');
            if (noJobsMessage) {
                noJobsMessage.remove();
            }
            const newJobElement = createJobElement(job);
            jobsContainer.appendChild(newJobElement);
        }
        if (typeof updateJobsSelectionUI === 'function') {
            updateJobsSelectionUI();
        }
    }
    
    // Обновляем задание в таблице на странице логов (только если есть jobsTableBody)
    const jobsTableBody = document.getElementById('jobsTableBody');
    if (jobsTableBody) {
        const jobRow = jobsTableBody.querySelector(`tr[data-job-id="${job.id}"]`);
        
        if (jobRow) {
            if (typeof disposeActionTooltips === 'function') {
                disposeActionTooltips(jobRow);
            }
            const newJobRow = createJobRow(job);
            jobRow.replaceWith(newJobRow);
        } else {
            // Если строки нет - добавляем новую строку
            const newJobRow = createJobRow(job);
            jobsTableBody.appendChild(newJobRow);
            
            // Если таблица показывала "Нет заданий", очищаем её
            const noJobsRow = jobsTableBody.querySelector('tr td[colspan]');
            if (noJobsRow) {
                noJobsRow.parentElement.remove();
            }
        }
        if (typeof updateJobsSelectionUI === 'function') {
            updateJobsSelectionUI();
        }
    }
}

// Функции createJobElement, createJobRow, getStatusText, escapeHtml, downloadJob, deleteJob, stopJob
// теперь находятся в common.js

// Функция для загрузки заданий при подключении WebSocket
function loadJobsOnConnect() {
    const requests = [];
    // Загружаем задания на главной странице
    const jobsContainer = document.getElementById('jobsContainer');
    if (jobsContainer) {
        requests.push(loadJobsForContainer(jobsContainer));
    }
    
    // Загружаем задания на странице логов
    const jobsTableBody = document.getElementById('jobsTableBody');
    if (jobsTableBody) {
        requests.push(loadJobsForTable(jobsTableBody));
    }
    // Страницы настроек/системы также должны показывать достоверный статус сервера.
    if (requests.length === 0) {
        requests.push(fetch('/health', getPollingFetchOptions()).then((response) => response.ok).catch(() => false));
    }
    return Promise.all(requests).then((results) => results.every(Boolean));
}

// Функция загрузки заданий для контейнера на главной странице
function loadJobsForContainer(container) {
    return fetch('/api/jobs', getPollingFetchOptions())
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(jobs => {
            if (!Array.isArray(jobs)) {
                return false;
            }

            const apiJobIds = new Set(jobs.map(job => job.id));
            Object.keys(window.jobsCache).forEach(jobId => {
                if (!apiJobIds.has(jobId)) {
                    removeCachedJob(jobId);
                }
            });

            const mergedJobs = jobs.map(job => cacheJob(job));

            if (typeof disposeActionTooltips === 'function') {
                disposeActionTooltips(container);
            }
            container.innerHTML = '';
            if (mergedJobs.length === 0) {
                container.innerHTML = `
                    <div class="text-center text-muted">
                        <i class="bi bi-inbox"></i><br>
                        Нет активных заданий
                    </div>
                `;
            } else {
                mergedJobs.forEach(job => {
                    const jobElement = createJobElement(job);
                    container.appendChild(jobElement);
                });
            }
            if (typeof updateJobsSelectionUI === 'function') {
                updateJobsSelectionUI();
            }
            return true;
        })
        .catch(error => {
            console.error('Ошибка загрузки заданий:', error);
            return false;
        });
}

// Функция загрузки заданий для таблицы на странице логов
function loadJobsForTable(tableBody) {
    return fetch('/api/jobs', getPollingFetchOptions())
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(jobs => {
            if (!Array.isArray(jobs)) {
                return false;
            }

            const apiJobIds = new Set(jobs.map(job => job.id));
            Object.keys(window.jobsCache).forEach(jobId => {
                if (!apiJobIds.has(jobId)) {
                    removeCachedJob(jobId);
                }
            });

            const mergedJobs = jobs.map(job => cacheJob(job));

            if (typeof disposeActionTooltips === 'function') {
                disposeActionTooltips(tableBody);
            }
            if (mergedJobs.length === 0) {
                tableBody.innerHTML = `
                    <tr>
                        <td colspan="7" class="text-center text-muted">
                            <i class="bi bi-inbox"></i><br>
                            Нет заданий
                        </td>
                    </tr>
                `;
            } else {
                tableBody.innerHTML = '';
                mergedJobs.forEach(job => {
                    const jobRow = createJobRow(job);
                    tableBody.appendChild(jobRow);
                });
            }
            if (typeof updateJobsSelectionUI === 'function') {
                updateJobsSelectionUI();
            }
            return true;
        })
        .catch(error => {
            console.error('Ошибка загрузки заданий:', error);
            return false;
        });
}
