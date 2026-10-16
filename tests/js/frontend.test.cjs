const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');

function element() {
    const attributes = new Map();
    const classes = new Set();
    return {
        style: {}, value: '', innerHTML: '', textContent: '', disabled: false,
        classList: {
            add: (...names) => names.forEach((name) => classes.add(name)),
            remove: (...names) => names.forEach((name) => classes.delete(name)),
            toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
            contains: (name) => classes.has(name),
        },
        setAttribute: (name, value) => attributes.set(name, String(value)),
        getAttribute: (name) => attributes.get(name),
        hasAttribute: (name) => attributes.has(name),
        removeAttribute: (name) => attributes.delete(name),
        addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    };
}

function frontend(files = ['common', 'socket', 'upload']) {
    const elements = {};
    const alerts = [];
    const domReady = [];
    const intervals = new Map();
    let nextIntervalId = 0;
    const context = vm.createContext({
        window: { socket: { on() {} }, location: { hostname: 'example.local' } },
        document: {
            addEventListener(event, handler) { if (event === 'DOMContentLoaded') domReady.push(handler); },
            getElementById: (id) => elements[id] || null,
            querySelector() { return null; }, querySelectorAll() { return []; },
            createElement: element,
        },
        console: { log() {}, warn() {}, error() {} },
        setTimeout, clearTimeout,
        setInterval: (handler, delay) => {
            const id = ++nextIntervalId;
            intervals.set(id, { handler, delay });
            return id;
        },
        clearInterval: (id) => intervals.delete(id),
        FormData, Blob, Date, Intl,
        DataTransfer: class {
            constructor() {
                this.files = [];
                this.items = { add: (file) => this.files.push(file) };
            }
        },
    });
    for (const file of files) {
        vm.runInContext(fs.readFileSync(path.join(root, `app/static/js/${file}.js`), 'utf8'), context);
    }
    context.showAlert = (type, message) => alerts.push({ type, message });
    context.refreshJobsList = () => {};
    return { context, elements, alerts, domReady, intervals };
}

test('filename escaping is safe in text and quoted HTML attributes', () => {
    const { context } = frontend(['common']);
    assert.equal(context.escapeHtml(`A " onmouseover="alert(1)'<&>.mkv`),
        'A &quot; onmouseover=&quot;alert(1)&#39;&lt;&amp;&gt;.mkv');
    const card = context.createJobElement({ id: 'safe-id', filename: '" onmouseover="bad', status: 'done' });
    assert.match(card.innerHTML, /title="&quot; onmouseover=&quot;bad"/);
});

test('partial multipart success reports accepted count and retains only failed files', async () => {
    const { context, elements, alerts } = frontend();
    const accepted = { name: 'accepted.mp4', size: 10 };
    const failed = { name: 'failed.mkv', size: 10 };
    elements.fileInput = element();
    elements.fileInput.files = [accepted, failed];
    context.uploadFilesBatch = async () => ({
        jobs: [{ id: 'one-job' }], errors: [{ filename: failed.name, error: 'Нет места' }],
    });
    await context.uploadMultipleFiles([accepted, failed], 100, new FormData());
    assert.deepEqual(elements.fileInput.files, [failed]);
    assert.equal(alerts.length, 1);
    assert.equal(alerts[0].type, 'warning');
    assert.match(alerts[0].message, /1 из 2/);
    assert.match(alerts[0].message, /failed\.mkv — Нет места/);
});

test('HTTP 207 upload response keeps accepted jobs and per-file errors', async () => {
    const { context } = frontend();
    const payload = { jobs: [{ id: 'accepted' }], errors: [{ filename: 'bad.mkv', error: 'invalid' }] };
    let applied;
    context.applyUploadJobsFromResult = (result) => applied = result;
    context.XMLHttpRequest = class {
        constructor() {
            this.handlers = {};
            this.upload = { addEventListener() {} };
            this.status = 207;
            this.responseText = JSON.stringify(payload);
        }
        addEventListener(event, callback) { this.handlers[event] = callback; }
        open() {}
        send() { this.handlers.load(); }
    };
    const result = await context.uploadFilesBatch([new Blob(['video'])], null, new FormData());
    assert.deepEqual(JSON.parse(JSON.stringify(result)), payload);
    assert.deepEqual(JSON.parse(JSON.stringify(applied)), payload);
});

test('successful upload clears files without resetting source selection', async () => {
    const { context, elements, alerts } = frontend();
    elements.fileInput = element();
    elements.fileInput.value = 'original-file-selection';
    elements.uploadForm = { reset() { assert.fail('source form must not be reset'); } };
    context.uploadFilesBatch = async () => ({ jobs: [{ id: 'one' }, { id: 'two' }], errors: [] });
    await context.uploadMultipleFiles([{ size: 1 }, { size: 1 }], 100, new FormData());
    assert.equal(elements.fileInput.value, '');
    assert.deepEqual(alerts, [{ type: 'success', message: 'Создано заданий: 2' }]);
});

test('one encoding parameter snapshot is reused across a batch and all chunks', async () => {
    const { context } = frontend();
    const parameters = new FormData();
    parameters.set('metadata_nickname', 'Original nickname');
    const received = [];
    context.fetch = async (_url, options) => {
        received.push(options.body.get('metadata_nickname'));
        const isFinal = received.length % 2 === 0;
        return { ok: true, json: async () => isFinal ? { completed: true, job: { id: String(received.length) } } : {} };
    };
    context.applyUploadJobsFromResult = () => {};
    context.captureEncodingParameters = () => {
        assert.fail('parameters must not be reread between files/chunks');
    };
    const files = [1, 2].map((number) => Object.assign(new Blob(['abcdefgh']), { name: `${number}.mkv` }));
    await context.uploadMultipleFiles(files, 4, parameters);
    assert.deepEqual(received, Array(4).fill('Original nickname'));
});

test('unchecked Opus VBR is sent exactly once as false', () => {
    const { context, elements } = frontend();
    // Минимальная модель FormData(form), в том числе исходный битрейт в форме.
    context.FormData = class extends FormData {
        constructor(form) {
            super();
            if (form) {
                this.append('audio_bitrate', '160k');
                this.append('audio_sample_rate', '48000');
                this.append('audio_codec', 'opus');
            }
        }
    };
    elements.ffmpegForm = {};
    elements.audioSettings = element();
    elements.audioCodec = Object.assign(element(), { value: 'opus' });
    elements.audioBitrate = Object.assign(element(), { value: '160k' });
    elements.audioSampleRate = Object.assign(element(), { value: '48000' });
    elements.audioVbr = Object.assign(element(), { checked: false });
    context.syncAudioDownmixField = () => {};
    const parameters = context.captureEncodingParameters();
    assert.deepEqual(parameters.getAll('audio_vbr'), ['false']);
    assert.deepEqual(parameters.getAll('audio_bitrate'), ['160k']);
    assert.deepEqual(parameters.getAll('audio_sample_rate'), ['48000']);
});

test('source fields and action label remain consistent when upload finishes', () => {
    const { context, elements } = frontend();
    ['sourceModeDevice', 'sourceModeStorage', 'fileInput', 'storagePathInput', 'uploadBtn',
        'deviceSourceFields', 'storageSourceFields'].forEach((id) => elements[id] = element());
    elements.sourceModeStorage.checked = true;
    context.setUploadBusy(true);
    assert.equal(elements.sourceModeStorage.disabled, true);
    assert.equal(elements.fileInput.disabled, true);
    context.setUploadBusy(false);
    assert.equal(elements.sourceModeStorage.disabled, false);
    assert.equal(elements.deviceSourceFields.style.display, 'none');
    assert.equal(elements.storageSourceFields.style.display, 'block');
    assert.equal(elements.fileInput.hasAttribute('required'), false);
    assert.equal(elements.storagePathInput.hasAttribute('required'), true);
    assert.match(elements.uploadBtn.innerHTML, /Кодировать/);
});

test('progress is monotonic and late events cannot change terminal jobs', () => {
    const { context } = frontend();
    context.window.jobsCache.active = { id: 'active', status: 'in_progress', progress: 80 };
    context.updateJobProgressGlobally('active', 40);
    assert.equal(context.window.jobsCache.active.progress, 80);
    context.window.jobsCache.ready = { id: 'ready', status: 'done', progress: 100 };
    context.updateJobProgressGlobally('ready', 20);
    assert.equal(context.window.jobsCache.ready.progress, 100);
    assert.equal(context.window.jobsCache.ready.status, 'done');
    assert.equal(context.getJobProgressText({ status: 'in_progress', progress: 100 }), 'Финализация…');
    assert.equal(context.getJobProgress({ progress: 'bad' }), 0);
});

test('missing output disables download in cards, rows and batch actions', () => {
    const { context } = frontend();
    const job = { id: 'missing', filename: 'file.mkv', status: 'done', output_available: false };
    assert.equal(context.isJobDownloadable(job), false);
    assert.match(context.createJobElement(job).innerHTML, /disabled title="Готовый файл отсутствует/);
    assert.match(context.createJobRow(job).innerHTML, /Файл отсутствует/);
    context.window.jobsCache[job.id] = job;
    context.window.selectedJobIds.add(job.id);
    let download = null;
    context.document.getElementById = (id) => id === 'downloadSelectedJobsBtn' ? (download = element()) : null;
    context.getVisibleJobIds = () => ['missing'];
    context.updateJobsSelectionUI();
    assert.equal(download.disabled, true);
});

test('settings reject fractions rather than silently rounding them', async () => {
    const { context, elements } = frontend(['common']);
    let submit;
    let requests = 0;
    elements.appSettingsForm = { addEventListener: (_event, handler) => submit = handler };
    elements.maxConcurrentEncodes = Object.assign(element(), {
        value: '1.5', checkValidity: () => true, focus() {},
    });
    elements.settingsStatus = element();
    elements.saveSettingsBtn = element();
    context.fetch = async () => {
        requests++;
        return { ok: true, json: async () => ({}) };
    };
    const template = fs.readFileSync(path.join(root, 'app/templates/settings.html'), 'utf8');
    const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
        .replace('{{ min_concurrent }}', '1').replace('{{ max_concurrent_limit }}', '8');
    vm.runInContext(script, context);
    await Promise.resolve();
    const before = requests;
    await submit({ preventDefault() {} });
    assert.equal(requests, before);
    assert.equal(elements.maxConcurrentEncodes.getAttribute('aria-invalid'), 'true');
    assert.match(elements.settingsStatus.textContent, /Некорректное/);
});

test('history loads and one fallback poll runs when Socket.IO is unavailable', async () => {
    const { context, elements, domReady, intervals } = frontend(['common', 'socket']);
    elements.jobsContainer = Object.assign(element(), { appendChild() {} });
    elements.connectionStatus = element();
    context.window.socket = undefined;
    let requests = 0;
    context.fetch = async () => {
        requests++;
        return { ok: true, json: async () => [] };
    };
    domReady.at(-1)();
    await new Promise(setImmediate);
    assert.equal(requests, 1);
    assert.match(elements.jobsContainer.innerHTML, /Нет активных заданий/);
    assert.equal(elements.connectionStatus.textContent, 'Обновление каждые 5 с');
    context.startJobsPolling();
    assert.equal(intervals.size, 1);
    assert.equal([...intervals.values()][0].delay, 5000);
    context.stopJobsPolling();
    assert.equal(intervals.size, 0);
});

test('fallback reports server failure and does not overlap requests', async () => {
    const { context, elements } = frontend(['common', 'socket']);
    elements.jobsContainer = element();
    elements.connectionStatus = element();
    let finish;
    let requests = 0;
    context.fetch = () => {
        requests++;
        return new Promise((resolve) => finish = () => resolve({ ok: false, status: 503 }));
    };
    const first = context.refreshFallbackJobs();
    await context.refreshFallbackJobs();
    assert.equal(requests, 1);
    finish();
    await first;
    assert.equal(elements.connectionStatus.textContent, 'Сервер недоступен');
});

test('socket reconnect stops polling and uses Manager reconnection events', async () => {
    const { context, elements, domReady, intervals } = frontend(['common', 'socket']);
    elements.connectionStatus = element();
    context.fetch = async () => ({ ok: true });
    const socketEvents = {};
    const managerEvents = {};
    context.io = () => ({
        connected: false,
        on: (event, handler) => socketEvents[event] = handler,
        io: { on: (event, handler) => managerEvents[event] = handler },
    });
    domReady.at(-1)();
    await new Promise(setImmediate);
    assert.equal(intervals.size, 1);
    assert.equal(typeof managerEvents.reconnect_error, 'function');
    assert.equal(typeof managerEvents.reconnect, 'function');
    context.window.socket.connected = true;
    socketEvents.connect();
    assert.equal(intervals.size, 0);
    assert.equal(elements.connectionStatus.textContent, 'Обновления подключены');
    context.window.socket.connected = false;
    socketEvents.disconnect();
    await new Promise(setImmediate);
    assert.equal(intervals.size, 1);
    assert.equal(elements.connectionStatus.textContent, 'Обновление каждые 5 с');
});

test('bundled Socket.IO client is valid JavaScript with version and license', () => {
    const libraryPath = path.join(root, 'app/static/js/socket.io.min.js');
    const source = fs.readFileSync(libraryPath, 'utf8');
    assert.doesNotThrow(() => new vm.Script(source));
    assert.match(source.slice(0, 150), /Socket\.IO v4\.7\.4/);
    assert.match(fs.readFileSync(path.join(root, 'app/static/js/socket.io.LICENSE'), 'utf8'), /MIT License/);
});
