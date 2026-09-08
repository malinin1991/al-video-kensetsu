// Ждем загрузки DOM
if (window.isDevelopment) {
    console.log('upload.js загружен');
}

document.addEventListener('DOMContentLoaded', function() {
    if (window.isDevelopment) {
        console.log('DOM загружен, инициализируем загрузку файлов');
    }
    
    // Элементы формы
    const uploadForm = document.getElementById('uploadForm');
    const fileInput = document.getElementById('fileInput');
    const uploadBtn = document.getElementById('uploadBtn');
    const uploadProgress = document.getElementById('uploadProgress');
    const jobsContainer = document.getElementById('jobsContainer');

    // Проверяем, что все элементы найдены
    if (!uploadForm) {
        console.error('Форма загрузки не найдена!');
        return;
    }
    if (!fileInput) {
        console.error('Поле выбора файла не найдено!');
        return;
    }
    if (!uploadBtn) {
        console.error('Кнопка загрузки не найдена!');
        return;
    }
    if (!uploadProgress) {
        console.error('Прогресс-бар загрузки не найден!');
        return;
    }
    if (!jobsContainer) {
        console.error('Контейнер заданий не найден!');
        return;
    }
    
    if (window.isDevelopment) {
        console.log('Все элементы найдены, добавляем обработчики');
    }

    setupSourceModeToggle();

    // Обработчик отправки формы
    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (window.isDevelopment) {
            console.log('Форма отправлена!');
        }

        if (isStorageSourceMode()) {
            await submitInternalEncode();
            return;
        }
        
        const files = Array.from(fileInput.files);
        if (files.length === 0) {
            showAlert('warning', 'Выберите файл(ы) для загрузки');
            return;
        }
        
        if (window.isDevelopment) {
            console.log('Выбрано файлов:', files.length);
        }
        
        // Показываем прогресс загрузки
        uploadProgress.style.display = 'block';
        
        // Показываем общий прогресс, если файлов больше одного
        const totalUploadProgressDiv = document.getElementById('totalUploadProgress');
        if (files.length > 1) {
            totalUploadProgressDiv.style.display = 'block';
        } else {
            totalUploadProgressDiv.style.display = 'none';
        }
        
        uploadBtn.disabled = true;
        uploadBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Загрузка...';
        
        try {
            // Определяем размер чанка (4MB)
            const CHUNK_SIZE = 4 * 1024 * 1024; // 4MB
            
            // Загружаем все файлы
            await uploadMultipleFiles(files, CHUNK_SIZE);
            
        } catch (error) {
            console.error('Ошибка загрузки:', error);
            showAlert('danger', 'Ошибка загрузки файла(ов)');
        } finally {
            // Скрываем прогресс и восстанавливаем кнопку
            uploadProgress.style.display = 'none';
            totalUploadProgressDiv.style.display = 'none';
            uploadBtn.disabled = false;
            uploadBtn.innerHTML = '<i class="bi bi-upload"></i> Загрузить';
        }
    });

}); // Конец DOMContentLoaded

function isStorageSourceMode() {
    const storageRadio = document.getElementById('sourceModeStorage');
    return !!(storageRadio && storageRadio.checked);
}

function setupSourceModeToggle() {
    const deviceRadio = document.getElementById('sourceModeDevice');
    const storageRadio = document.getElementById('sourceModeStorage');
    const deviceFields = document.getElementById('deviceSourceFields');
    const storageFields = document.getElementById('storageSourceFields');
    const fileInput = document.getElementById('fileInput');
    const storagePathInput = document.getElementById('storagePathInput');
    const uploadBtn = document.getElementById('uploadBtn');

    const applyMode = () => {
        const storage = isStorageSourceMode();
        if (deviceFields) {
            deviceFields.style.display = storage ? 'none' : 'block';
        }
        if (storageFields) {
            storageFields.style.display = storage ? 'block' : 'none';
        }
        if (fileInput) {
            if (storage) {
                fileInput.removeAttribute('required');
            } else {
                fileInput.setAttribute('required', 'required');
            }
        }
        if (storagePathInput) {
            if (storage) {
                storagePathInput.setAttribute('required', 'required');
            } else {
                storagePathInput.removeAttribute('required');
            }
        }
        if (uploadBtn && !uploadBtn.disabled) {
            uploadBtn.innerHTML = storage
                ? '<i class="bi bi-hdd-network"></i> Кодировать'
                : '<i class="bi bi-upload"></i> Загрузить';
        }
    };

    if (deviceRadio) {
        deviceRadio.addEventListener('change', applyMode);
    }
    if (storageRadio) {
        storageRadio.addEventListener('change', applyMode);
    }
    applyMode();
}

function setCopyingProgress(active) {
    const uploadProgress = document.getElementById('uploadProgress');
    if (!uploadProgress) {
        return;
    }
    const bar = uploadProgress.querySelector('.progress-bar');
    const text = uploadProgress.querySelector('.progress-text');
    if (active) {
        uploadProgress.style.display = 'block';
        if (bar) {
            bar.style.width = '100%';
            bar.classList.add('progress-bar-striped', 'progress-bar-animated');
        }
        if (text) {
            text.textContent = 'Копирование файла…';
        }
    } else {
        uploadProgress.style.display = 'none';
        if (bar) {
            bar.style.width = '0%';
            bar.classList.remove('progress-bar-striped', 'progress-bar-animated');
        }
        if (text) {
            text.textContent = '0.0%';
        }
    }
}

async function submitInternalEncode() {
    const storagePathInput = document.getElementById('storagePathInput');
    const uploadBtn = document.getElementById('uploadBtn');
    const path = storagePathInput ? storagePathInput.value.trim() : '';
    if (!path) {
        showAlert('danger', 'Укажите путь к файлу на хранилище');
        return;
    }

    const totalUploadProgressDiv = document.getElementById('totalUploadProgress');
    if (totalUploadProgressDiv) {
        totalUploadProgressDiv.style.display = 'none';
    }

    setCopyingProgress(true);
    if (uploadBtn) {
        uploadBtn.disabled = true;
        uploadBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Копирование файла…';
    }

    try {
        const formData = new FormData();
        formData.append('path', path);
        addFormDataToFormData(formData);

        const response = await fetch('/api/internal/encode', {
            method: 'POST',
            body: formData
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(result.error || `Ошибка сервера (${response.status})`);
        }

        if (result.job) {
            addJobToContainer(result.job);
        }
        refreshJobsList();
        showAlert('success', result.message || 'Задание создано и добавлено в очередь');
        if (storagePathInput) {
            storagePathInput.value = '';
        }
    } catch (error) {
        console.error('Ошибка внутреннего кодирования:', error);
        showAlert('danger', error.message || 'Ошибка кодирования с хранилища');
    } finally {
        setCopyingProgress(false);
        if (uploadBtn) {
            uploadBtn.disabled = false;
            uploadBtn.innerHTML = '<i class="bi bi-hdd-network"></i> Кодировать';
        }
    }
}

async function uploadMultipleFiles(files, chunkSize) {
    // Загружает несколько файлов: one-shot multipart если все ≤ chunkSize
    const totalFiles = files.length;
    let uploadedFiles = 0;
    let totalUploadedBytes = 0;
    let totalSize = 0;

    files.forEach(file => {
        totalSize += file.size;
    });

    updateTotalUploadProgress(0);

    const allDirect = files.every(file => file.size <= chunkSize);
    if (totalFiles > 1 && allDirect) {
        try {
            await uploadFilesBatch(files, (progress) => {
                updateTotalUploadProgress(progress);
            });
            uploadedFiles = totalFiles;
            updateTotalUploadProgress(100);
            showAlert('success', `Успешно загружено файлов: ${uploadedFiles}`);
            const form = document.getElementById('uploadForm');
            const input = document.getElementById('fileInput');
            if (form) {
                form.reset();
            }
            if (input) {
                input.value = '';
            }
            refreshJobsList();
        } catch (error) {
            console.error('Ошибка batch-загрузки:', error);
            showAlert('danger', error.message || 'Ошибка загрузки файлов');
        }
        return;
    }

    for (let i = 0; i < files.length; i++) {
        const file = files[i];

        if (window.isDevelopment) {
            console.log(`Загрузка файла ${i + 1}/${totalFiles}:`, file.name, 'размер:', file.size);
        }

        try {
            if (file.size > chunkSize) {
                await uploadFileInChunks(file, chunkSize, (fileProgress) => {
                    const fileCompletedBytes = (fileProgress / 100) * file.size;
                    const overallProgress = ((totalUploadedBytes + fileCompletedBytes) / totalSize) * 100;
                    updateTotalUploadProgress(overallProgress);
                });
            } else {
                await uploadFileDirectly(file, (fileProgress) => {
                    const fileCompletedBytes = (fileProgress / 100) * file.size;
                    const overallProgress = ((totalUploadedBytes + fileCompletedBytes) / totalSize) * 100;
                    updateTotalUploadProgress(overallProgress);
                });
            }

            uploadedFiles++;
            totalUploadedBytes += file.size;
            updateTotalUploadProgress((totalUploadedBytes / totalSize) * 100);

        } catch (error) {
            console.error(`Ошибка загрузки файла ${file.name}:`, error);
            showAlert('danger', `Ошибка загрузки файла: ${file.name}`);
        }
    }

    if (uploadedFiles === totalFiles) {
        showAlert('success', `Успешно загружено файлов: ${uploadedFiles}`);
        const form = document.getElementById('uploadForm');
        const input = document.getElementById('fileInput');
        if (form) {
            form.reset();
        }
        if (input) {
            input.value = '';
        }
        refreshJobsList();
    }
}

function isUploadHttpSuccess(status) {
    return status === 200 || status === 207;
}

function applyUploadJobsFromResult(result) {
    if (!result) {
        return;
    }
    if (result.jobs) {
        result.jobs.forEach(job => {
            addJobToContainer(job);
        });
    } else if (result.job) {
        addJobToContainer(result.job);
    }
    refreshJobsList();
}

async function uploadFilesBatch(files, progressCallback) {
    // One-shot multipart: несколько parts с именем files + video_id/audio_id
    if (window.isDevelopment) {
        console.log('Batch-загрузка файлов:', files.length);
    }
    const formData = new FormData();
    files.forEach(file => {
        formData.append('files', file);
    });
    addFormDataToFormData(formData);

    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();

        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                const progress = (e.loaded / e.total) * 100;
                if (progressCallback) {
                    progressCallback(progress);
                }
                updateUploadProgress(progress);
            }
        });

        xhr.addEventListener('load', () => {
            let result = {};
            try {
                result = JSON.parse(xhr.responseText);
            } catch (error) {
                if (isUploadHttpSuccess(xhr.status)) {
                    reject(new Error('Ошибка парсинга ответа сервера'));
                    return;
                }
            }

            if (isUploadHttpSuccess(xhr.status)) {
                applyUploadJobsFromResult(result);
                if (xhr.status === 207 && result.errors && result.errors.length) {
                    const names = result.errors
                        .map(item => item.filename || '?')
                        .join(', ');
                    showAlert('warning', `Часть файлов не принята: ${names}`);
                }
                resolve(result);
                return;
            }

            reject(new Error(result.error || `Ошибка загрузки: статус ${xhr.status}`));
        });

        xhr.addEventListener('error', () => {
            reject(new Error('Ошибка сети при загрузке файла'));
        });

        xhr.open('POST', '/api/upload');
        xhr.send(formData);
    });
}

async function uploadFileDirectly(file, progressCallback) {
    // Загружает файл напрямую (для маленьких файлов)
    if (window.isDevelopment) {
        console.log('Загружаем файл напрямую:', file.name);
    }
    const formData = new FormData();
    formData.append('file', file);

    // Добавляем параметры FFmpeg
    addFormDataToFormData(formData);

    if (window.isDevelopment) {
        console.log('Отправляем запрос на /api/upload');
    }

    // Создаем XMLHttpRequest для отслеживания прогресса
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();

        // Отслеживаем прогресс загрузки
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                const progress = (e.loaded / e.total) * 100;
                if (progressCallback) {
                    progressCallback(progress);
                }
                updateUploadProgress(progress);
            }
        });

        xhr.addEventListener('load', () => {
            if (isUploadHttpSuccess(xhr.status)) {
                try {
                    const result = JSON.parse(xhr.responseText);
                    applyUploadJobsFromResult(result);
                    resolve(result);
                } catch (error) {
                    reject(new Error('Ошибка парсинга ответа сервера'));
                }
            } else {
                try {
                    const result = JSON.parse(xhr.responseText);
                    reject(new Error(result.error || 'Ошибка загрузки файла'));
                } catch (error) {
                    reject(new Error(`Ошибка загрузки: статус ${xhr.status}`));
                }
            }
        });

        xhr.addEventListener('error', () => {
            reject(new Error('Ошибка сети при загрузке файла'));
        });

        xhr.open('POST', '/api/upload');
        xhr.send(formData);
    });
}

async function uploadFileInChunks(file, chunkSize, progressCallback) {
    // Загружает файл по чанкам с догрузкой при ошибке
    const totalChunks = Math.ceil(file.size / chunkSize);
    const fileId = generateFileId();
    
    // Добавляем параметры FFmpeg
    const formData = new FormData();
    addFormDataToFormData(formData);
    formData.append('filename', file.name);
    formData.append('file_id', fileId);
    formData.append('total_chunks', totalChunks);
    
    let uploadedChunks = 0;
    const maxRetries = 3;
    
    for (let chunkNumber = 0; chunkNumber < totalChunks; chunkNumber++) {
        const start = chunkNumber * chunkSize;
        const end = Math.min(start + chunkSize, file.size);
        const chunk = file.slice(start, end);
        
        // Создаем FormData для чанка
        const chunkFormData = new FormData();
        chunkFormData.append('file', chunk, `chunk_${chunkNumber}`);
        chunkFormData.append('chunk_number', chunkNumber);
        chunkFormData.append('total_chunks', totalChunks);
        chunkFormData.append('file_id', fileId);
        chunkFormData.append('filename', file.name);
        
        // Добавляем параметры FFmpeg
        addFormDataToFormData(chunkFormData);
        
        let success = false;
        let attempts = 0;
        
        while (!success && attempts < maxRetries) {
            try {
                const response = await fetch('/api/upload', {
                    method: 'POST',
                    body: chunkFormData
                });
                
                const result = await response.json();
                
                if (response.ok) {
                    uploadedChunks++;
                    const progress = (uploadedChunks / totalChunks) * 100;
                    if (window.isDevelopment) {
                        console.log(`Загружен чанк ${chunkNumber + 1}/${totalChunks}, прогресс: ${progress.toFixed(1)}%`);
                    }
                    updateUploadProgress(progress);
                    
                    // Обновляем общий прогресс через callback
                    if (progressCallback) {
                        progressCallback(progress);
                    }
                    
                    if (result.completed || result.job) {
                        // Файл полностью загружен
                        if (window.isDevelopment) {
                            console.log('Файл полностью загружен');
                        }
                        
                        applyUploadJobsFromResult(result);
                        
                        // Обновляем общий прогресс до 100%
                        if (progressCallback) {
                            progressCallback(100);
                        }
                        
                        return result;
                    }
                    
                    success = true;
                } else {
                    throw new Error(result.error || 'Ошибка загрузки чанка');
                }
            } catch (error) {
                attempts++;
                console.error(`Ошибка загрузки чанка ${chunkNumber} (попытка ${attempts}):`, error);
                
                if (attempts < maxRetries) {
                    // Ждем перед повтором
                    await new Promise(resolve => setTimeout(resolve, 1000 * attempts));
                } else {
                    throw new Error(`Не удалось загрузить чанк ${chunkNumber} после ${maxRetries} попыток`);
                }
            }
        }
    }
}

function addFormDataToFormData(formData) {
    // Добавляет данные формы в FormData
    // Добавляем параметры FFmpeg
    const ffmpegForm = document.getElementById('ffmpegForm');
    const ffmpegData = new FormData(ffmpegForm);
    for (let [key, value] of ffmpegData.entries()) {
        formData.append(key, value);
    }

    // audio_downmix живёт вне #ffmpegForm (в шапке карточки);
    // video_id/audio_id — hidden в форме + selects в шапке
    syncAudioDownmixField();
    const videoSelect = document.getElementById('videoSelect');
    const audioSelect = document.getElementById('audioSelect');
    if (videoSelect && videoSelect.value) {
        formData.set('video_id', videoSelect.value);
    }
    if (audioSelect && audioSelect.value) {
        formData.set('audio_id', audioSelect.value);
    }
    const audioDownmixField = document.getElementById('audioDownmixField');
    formData.append(
        'audio_downmix',
        audioDownmixField && audioDownmixField.value ? audioDownmixField.value : 'none'
    );
    
    // Добавляем настройки производительности (id в HTML — camelCase, name — snake_case)
    const performanceFields = [
        ['cpu_threads', 'cpuThreads'],
        ['thread_queue_size', 'threadQueueSize'],
        ['x265_pools', 'x265Pools'],
    ];

    performanceFields.forEach(([name, elementId]) => {
        const element = document.getElementById(elementId)
            || document.querySelector(`[name="${name}"]`);
        if (element) {
            formData.append(name, element.value);
        }
    });

    const updateMetadata = document.getElementById('updateMetadata');
    // Поля метаданных вне #ffmpegForm — передаём явно (см. блок «Обновление метаданных»)
    formData.append(
        'update_metadata',
        updateMetadata && updateMetadata.checked ? 'true' : 'false'
    );

    const metadataNickname = document.getElementById('metadataNickname');
    if (metadataNickname) {
        formData.append('metadata_nickname', metadataNickname.value.trim());
    }

    const metadataVideoTitle = document.getElementById('metadataVideoTitle');
    if (metadataVideoTitle) {
        formData.append('metadata_video_title', metadataVideoTitle.value.trim());
    }
    
    // Добавляем дополнительные настройки аудио если они видимы
    const audioSettings = document.getElementById('audioSettings');
    if (audioSettings && audioSettings.style.display !== 'none') {
        const audioBitrate = document.getElementById('audioBitrate');
        const audioSampleRate = document.getElementById('audioSampleRate');
        const audioVbr = document.getElementById('audioVbr');
        const audioCodec = document.getElementById('audioCodec');
        
        if (audioBitrate) {
            formData.append('audio_bitrate', audioBitrate.value);
        }
        if (audioSampleRate) {
            formData.append('audio_sample_rate', audioSampleRate.value);
        }
        const isOpus = audioCodec && (audioCodec.value === 'opus' || audioCodec.value === 'libopus');
        if (isOpus) {
            formData.append('audio_vbr', audioVbr && audioVbr.checked ? 'true' : 'false');
        } else if (audioVbr && audioVbr.checked) {
            formData.append('audio_vbr', 'true');
        }
    }
}

function updateUploadProgress(progress) {
    // Обновляет прогресс загрузки текущего файла
    const uploadProgress = document.getElementById('uploadProgress');
    if (uploadProgress) {
        const progressBar = uploadProgress.querySelector('.progress-bar');
        const progressText = uploadProgress.querySelector('.progress-text');
        
        if (progressBar && progressText) {
            // Ограничиваем прогресс от 0 до 100
            const clampedProgress = Math.max(0, Math.min(100, progress));
            const progressPercent = clampedProgress.toFixed(1);
            
            // Плавное обновление прогресса
            progressBar.style.width = `${progressPercent}%`;
            progressText.textContent = `${progressPercent}%`;
            progressBar.setAttribute('aria-valuenow', clampedProgress);
            progressBar.setAttribute('aria-valuemin', 0);
            progressBar.setAttribute('aria-valuemax', 100);
            
            if (window.isDevelopment) {
                console.log(`Обновление прогресса загрузки: ${progress}% -> ${progressPercent}%`);
            }
        } else {
            console.error('Прогресс-бар или текст не найден в контейнере');
        }
    } else {
        console.error('Контейнер прогресса не найден');
    }
}

function updateTotalUploadProgress(progress) {
    // Обновляет общий прогресс загрузки для нескольких файлов
    const totalUploadProgressBar = document.getElementById('totalUploadProgressBar');
    const totalUploadProgressText = document.getElementById('totalUploadProgressText');
    
    if (totalUploadProgressBar && totalUploadProgressText) {
        // Ограничиваем прогресс от 0 до 100
        const clampedProgress = Math.max(0, Math.min(100, progress));
        const progressPercent = clampedProgress.toFixed(1);
        
        // Обновляем прогресс-бар
        totalUploadProgressBar.style.width = `${progressPercent}%`;
        totalUploadProgressBar.setAttribute('aria-valuenow', clampedProgress);
        totalUploadProgressText.textContent = `${progressPercent}%`;
        
        if (window.isDevelopment) {
            console.log(`Обновление общего прогресса загрузки: ${progress}% -> ${progressPercent}%`);
        }
    }
}

function generateFileId() {
    // Генерирует уникальный ID для файла
    return 'file_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

// WebSocket обработчики
function setupUploadSocketHandlers() {
    if (!window.socket) {
        if (window.isDevelopment) {
            console.log('SocketIO не инициализирован для upload, ждем...');
        }
        setTimeout(setupUploadSocketHandlers, 100);
        return;
    }
    
    // Обработчик connect теперь глобальный в socket.js
    // Удаляем дублирующий обработчик

    window.socket.on('disconnect', () => {
        if (window.isDevelopment) {
            console.log('Отключен от сервера');
        }
    });

    // Обработчики событий теперь глобальные в socket.js
    // Удаляем дублирующие обработчики

    window.socket.on('log_update', (data) => {
        // Логи обрабатываются в logs.js
        if (window.isDevelopment) {
            console.log(`Лог для задания ${data.job_id}:`, data.message);
        }
    });
}

// Запускаем настройку обработчиков
setupUploadSocketHandlers();

// Функции для работы с заданиями
function refreshJobsList() {
    const container = document.getElementById('jobsContainer');
    if (!container) return;

    if (typeof loadJobsForContainer === 'function') {
        loadJobsForContainer(container);
        return;
    }
    loadJobs();
}

function loadJobs() {
    const container = document.getElementById('jobsContainer');
    if (!container) return;
    
    fetch('/api/jobs')
        .then(response => response.json())
        .then(jobs => {
            container.innerHTML = '';
            if (!Array.isArray(jobs) || jobs.length === 0) {
                container.innerHTML = `
                    <div class="text-center text-muted">
                        <i class="bi bi-inbox"></i><br>
                        Нет активных заданий
                    </div>
                `;
            } else {
                jobs.forEach(job => {
                    addJobToContainer(job);
                });
            }
        })
        .catch(error => {
            console.error('Ошибка загрузки заданий:', error);
        });
}

function addJobToContainer(job) {
    // Глобальный апдейт корректно убирает «Нет активных заданий»
    if (typeof updateJobGlobally === 'function') {
        updateJobGlobally(job);
        return;
    }

    const container = document.getElementById('jobsContainer');
    if (!container) return;

    const emptyState = container.querySelector('.text-center.text-muted');
    if (emptyState) {
        container.innerHTML = '';
    }

    const existingJob = container.querySelector(`[data-job-id="${job.id}"]`);
    const jobElement = createJobElement(job);
    if (existingJob) {
        if (typeof disposeActionTooltips === 'function') {
            disposeActionTooltips(existingJob);
        }
        existingJob.replaceWith(jobElement);
    } else {
        container.appendChild(jobElement);
    }
}

function updateJobInContainer(job) {
    const container = document.getElementById('jobsContainer');
    if (!container) return;
    
    const existingJob = document.querySelector(`[data-job-id="${job.id}"]`);
    if (existingJob) {
        if (typeof disposeActionTooltips === 'function') {
            disposeActionTooltips(existingJob);
        }
        // Используем глобальную функцию createJobElement
        existingJob.replaceWith(createJobElement(job));
    } else {
        addJobToContainer(job);
    }
}

// Функции createJobElement и getStatusText теперь глобальные в socket.js

// Функция updateJobProgress теперь глобальная в socket.js

// Функции downloadJob, deleteJob, stopJob теперь находятся в common.js

// Функции escapeHtml и showAlert теперь находятся в common.js
