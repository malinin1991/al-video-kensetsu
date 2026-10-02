// Общее подключение к WebSocket
document.addEventListener('DOMContentLoaded', function() {
    // Проверяем, находимся ли мы в режиме разработки
    window.isDevelopment = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    
    if (window.isDevelopment) {
        console.log('Инициализация SocketIO...');
    }
    
    // Настраиваем подключение Socket.IO с правильными параметрами
    window.socket = io({
        transports: ['polling', 'websocket'],
        upgrade: true,
        rememberUpgrade: true,
        timeout: 20000,
        forceNew: false,
        autoConnect: true,
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionAttempts: 5,
        maxReconnectionAttempts: 5
    });

// Обработчики подключения/отключения
window.socket.on('connect', () => {
    if (window.isDevelopment) {
        console.log('WebSocket подключен');
        console.log('Socket ID:', window.socket.id);
    }
    
    // Загружаем задания при подключении
    loadJobsOnConnect();
});

window.socket.on('disconnect', () => {
    if (window.isDevelopment) {
        console.log('WebSocket отключен');
    }
});

// Обработчик ошибок
window.socket.on('connect_error', (error) => {
    console.error('Ошибка подключения WebSocket:', error);
    console.error('Детали ошибки:', {
        type: error.type,
        description: error.description,
        context: error.context,
        transport: error.transport
    });
});

// Обработчик ошибок переподключения
window.socket.on('reconnect_error', (error) => {
    console.error('Ошибка переподключения WebSocket:', error);
});

// Обработчик успешного переподключения
window.socket.on('reconnect', (attemptNumber) => {
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
    const clampedProgress = Math.max(0, Math.min(100, progress || 0));

    if (window.jobsCache[jobId]) {
        const cached = window.jobsCache[jobId];
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
                progressBar.style.width = `${progressPercent}%`;
                progressText.textContent = `${progressPercent}%`;
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
            const progressPercent = clampedProgress.toFixed(1);
            progressCell.innerHTML = `<span class="text-primary fw-bold">${progressPercent}%</span>`;
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
    // Загружаем задания на главной странице
    const jobsContainer = document.getElementById('jobsContainer');
    if (jobsContainer) {
        loadJobsForContainer(jobsContainer);
    }
    
    // Загружаем задания на странице логов
    const jobsTableBody = document.getElementById('jobsTableBody');
    if (jobsTableBody) {
        loadJobsForTable(jobsTableBody);
    }
}

// Функция загрузки заданий для контейнера на главной странице
function loadJobsForContainer(container) {
    fetch('/api/jobs')
        .then(response => response.json())
        .then(jobs => {
            if (!Array.isArray(jobs)) {
                return;
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
        })
        .catch(error => {
            console.error('Ошибка загрузки заданий:', error);
        });
}

// Функция загрузки заданий для таблицы на странице логов
function loadJobsForTable(tableBody) {
    fetch('/api/jobs')
        .then(response => response.json())
        .then(jobs => {
            if (!Array.isArray(jobs)) {
                return;
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
        })
        .catch(error => {
            console.error('Ошибка загрузки заданий:', error);
        });
}
