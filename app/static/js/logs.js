// Элементы страницы
let logsContainer, clearLogsBtn, refreshLogsBtn, pauseLogsBtn;
let jobsTableBody, jobLogSelect, logStreamTabs, isLogsPage;

function initializeLogsPage() {
    logsContainer = document.getElementById('logsContainer');
    clearLogsBtn = document.getElementById('clearLogsBtn');
    refreshLogsBtn = document.getElementById('refreshLogsBtn');
    pauseLogsBtn = document.getElementById('pauseLogsBtn');
    jobsTableBody = document.getElementById('jobsTableBody');
    jobLogSelect = document.getElementById('jobLogSelect');
    logStreamTabs = document.getElementById('logStreamTabs');

    isLogsPage = logsContainer && clearLogsBtn && refreshLogsBtn && pauseLogsBtn && jobsTableBody;

    if (!isLogsPage) {
        return;
    }

    let logs = [];
    let isPaused = false;
    let currentStream = 'system';
    let currentJobId = '';
    window.jobLogLabels = window.jobLogLabels || {};

    function rememberJobLogLabel(jobId, label) {
        if (jobId && label) {
            window.jobLogLabels[jobId] = label;
        }
    }

    function getJobLogLabel(jobId) {
        if (!jobId || jobId === 'system') {
            return 'system';
        }
        return window.jobLogLabels[jobId] || jobId;
    }

    function setupStreamTabs() {
        if (!logStreamTabs) return;

        logStreamTabs.querySelectorAll('[data-stream]').forEach(tab => {
            tab.addEventListener('click', () => {
                const stream = tab.dataset.stream;
                logStreamTabs.querySelectorAll('.nav-link').forEach(link => {
                    link.classList.toggle('active', link === tab);
                });

                currentStream = stream;
                if (stream === 'job') {
                    if (jobLogSelect) {
                        jobLogSelect.style.display = 'block';
                    }
                    loadJobLogOptions().then(() => {
                        if (!currentJobId && jobLogSelect && jobLogSelect.options.length > 1) {
                            currentJobId = jobLogSelect.options[1].value;
                            jobLogSelect.value = currentJobId;
                        }
                        reloadLogs();
                    });
                } else {
                    currentJobId = '';
                    if (jobLogSelect) {
                        jobLogSelect.style.display = 'none';
                        jobLogSelect.value = '';
                    }
                    reloadLogs();
                }
            });
        });

        if (jobLogSelect) {
            jobLogSelect.addEventListener('change', () => {
                currentJobId = jobLogSelect.value;
                reloadLogs();
            });
        }
    }

    function loadJobLogOptions() {
        return fetch('/api/logs/jobs')
            .then(response => response.json())
            .then(jobs => {
                if (!jobLogSelect) return;

                const selected = jobLogSelect.value;
                jobLogSelect.innerHTML = '<option value="">Выберите задание...</option>';

                if (!Array.isArray(jobs)) {
                    return;
                }

                jobs.forEach(job => {
                    rememberJobLogLabel(job.id, job.label);
                    const option = document.createElement('option');
                    option.value = job.id;
                    option.textContent = job.label;
                    option.title = job.label;
                    jobLogSelect.appendChild(option);
                });

                const jobIds = jobs.map(job => job.id);
                if (selected && jobIds.includes(selected)) {
                    jobLogSelect.value = selected;
                    currentJobId = selected;
                } else if (selected) {
                    currentJobId = '';
                    jobLogSelect.value = '';
                    if (currentStream === 'job') {
                        reloadLogs();
                    }
                }
            })
            .catch(error => {
                console.error('Ошибка загрузки списка заданий для логов:', error);
            });
    }

    function getLogsApiUrl() {
        if (currentStream === 'job' && currentJobId) {
            return `/api/logs?job_id=${encodeURIComponent(currentJobId)}`;
        }
        return '/api/logs?stream=system';
    }

    function shouldAcceptLogUpdate(data) {
        const stream = data.stream || (data.job_id ? 'job' : 'system');
        if (currentStream === 'system') {
            return stream === 'system';
        }
        if (currentStream === 'job') {
            return stream === 'job' && data.job_id === currentJobId;
        }
        return true;
    }

    function setupSocketHandlers() {
        if (!window.socket) {
            setTimeout(setupSocketHandlers, 100);
            return;
        }

        window.socket.on('disconnect', () => {
            if (window.isDevelopment) {
                console.log('Отключен от сервера');
            }
        });

        window.socket.on('log_update', (data) => {
            if (!isLogsPage || isPaused || !shouldAcceptLogUpdate(data)) {
                return;
            }
            const jobId = data.job_id || data.jobId || 'system';
            addLogEntry(jobId, data.message, data.timestamp, data.stream || (data.job_id ? 'job' : 'system'));
        });

        window.socket.on('job_update', (job) => {
            if (!isLogsPage || !job || !job.id) {
                return;
            }
            rememberJobLogLabel(job.id, job.output_filename);
            if (currentStream === 'job' && job.id === currentJobId) {
                loadJobLogOptions();
            }
        });
    }

    const tzHint = document.getElementById('browserTzHint');
    if (tzHint) {
        const tz = getBrowserTimeZone();
        tzHint.textContent = tz ? `Часовой пояс: ${tz}` : '';
    }

    setupStreamTabs();
    setupSocketHandlers();
    updatePauseButton();
    reloadLogs();
    loadJobs();

    if (clearLogsBtn) {
        clearLogsBtn.addEventListener('click', () => {
            const url = currentStream === 'job' && currentJobId
                ? `/api/logs/clear?job_id=${encodeURIComponent(currentJobId)}`
                : '/api/logs/clear?stream=system';

            fetch(url, { method: 'POST' })
                .then(() => {
                    logs = [];
                    updateLogsDisplay();
                })
                .catch(error => {
                    console.error('Ошибка очистки логов:', error);
                });
        });
    }

    if (refreshLogsBtn) {
        refreshLogsBtn.addEventListener('click', () => {
            reloadLogs();
            loadJobs();
            if (currentStream === 'job') {
                loadJobLogOptions();
            }
        });
    }

    if (pauseLogsBtn) {
        pauseLogsBtn.addEventListener('click', () => {
            isPaused = !isPaused;
            updatePauseButton();
        });
    }

    function reloadLogs() {
        logs = [];
        updateLogsDisplay();
        loadExistingLogs();
    }

    function loadExistingLogs() {
        fetch(getLogsApiUrl())
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(existingLogs => {
                logs = [];
                existingLogs.forEach(logData => {
                    addLogEntry(
                        logData.jobId || logData.job_id || 'system',
                        logData.message,
                        logData.timestamp,
                        logData.stream || (logData.job_id ? 'job' : 'system'),
                        false
                    );
                });
                updateLogsDisplay();
            })
            .catch(error => {
                console.error('Ошибка загрузки существующих логов:', error);
            });
    }

    function addLogEntry(jobId, message, timestamp, stream = 'system', refresh = true) {
        const logEntry = {
            jobId: jobId || 'system',
            message: message,
            timestamp: timestamp || Date.now() / 1000,
            stream: stream,
            time: formatTime(timestamp || Date.now() / 1000)
        };

        const isProgressUpdate = /frame=\s*\d+\s+fps=/.test(message);

        if (isProgressUpdate && logs.length > 0) {
            const lastLog = logs[logs.length - 1];
            const isLastProgressUpdate = /frame=\s*\d+\s+fps=/.test(lastLog.message);

            if (isLastProgressUpdate && lastLog.jobId === jobId) {
                logs[logs.length - 1] = logEntry;
                if (refresh) {
                    updateLogsDisplay();
                }
                return;
            }
        }

        logs.push(logEntry);

        if (refresh) {
            updateLogsDisplay();
        }
    }

    function updatePauseButton() {
        if (!pauseLogsBtn) return;

        if (isPaused) {
            pauseLogsBtn.innerHTML = '<i class="bi bi-play-fill"></i> Возобновить';
            pauseLogsBtn.className = 'btn btn-outline-success btn-sm';
        } else {
            pauseLogsBtn.innerHTML = '<i class="bi bi-pause-fill"></i> Пауза';
            pauseLogsBtn.className = 'btn btn-outline-warning btn-sm';
        }
    }

    function updateLogsDisplay() {
        if (!logsContainer) {
            return;
        }

        if (currentStream === 'job' && !currentJobId) {
            logsContainer.innerHTML = `
                <div class="text-center text-muted p-3">
                    <i class="bi bi-list-task"></i><br>
                    Выберите задание для просмотра логов
                </div>
            `;
            return;
        }

        if (logs.length === 0) {
            logsContainer.innerHTML = `
                <div class="text-center text-muted p-3">
                    <i class="bi bi-hourglass-split"></i><br>
                    Ожидание логов...
                </div>
            `;
            return;
        }

        const logsHtml = logs.map(log => {
            const label = currentStream === 'job'
                ? getJobLogLabel(currentJobId)
                : getJobLogLabel(log.jobId);
            return `
            <div class="log-entry">
                <span class="text-muted">[${log.time}]</span>
                <span class="text-primary">[${escapeHtml(label)}]</span>
                <span>${escapeHtml(log.message)}</span>
            </div>
        `;
        }).join('');

        logsContainer.innerHTML = logsHtml;
        logsContainer.scrollTop = logsContainer.scrollHeight;
    }

    function loadJobs() {
        fetch('/api/jobs')
            .then(response => response.json())
            .then(jobs => {
                if (Array.isArray(jobs)) {
                    jobs.forEach(job => rememberJobLogLabel(job.id, job.output_filename));
                }
                updateJobsTable(jobs);
            })
            .catch(error => {
                console.error('Ошибка загрузки заданий:', error);
            });
    }

    function updateJobsTable(jobs) {
        if (!jobsTableBody) return;

        if (jobs.length === 0) {
            jobsTableBody.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center text-muted">
                        <i class="bi bi-inbox"></i><br>
                        Нет заданий
                    </td>
                </tr>
            `;
            return;
        }

        const rowsHtml = jobs.map(job => {
            const row = createJobRow(job);
            return row.outerHTML;
        }).join('');

        jobsTableBody.innerHTML = rowsHtml;
    }

    window.refreshJobLogOptions = function refreshJobLogOptions() {
        if (!isLogsPage) {
            return Promise.resolve();
        }
        return loadJobLogOptions();
    };
}

document.addEventListener('DOMContentLoaded', initializeLogsPage);
