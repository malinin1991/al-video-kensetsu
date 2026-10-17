/* Behavioural checks for editor drafts, asynchronous resolve and pinned batches. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const flush = () => new Promise(resolve => setImmediate(resolve));
const clean = value => JSON.parse(JSON.stringify(value));

function node(tag = 'div') {
    const handlers = {};
    const classes = new Set();
    const item = {
        tagName: tag.toUpperCase(), value: '', textContent: '', className: '', style: {}, dataset: {},
        children: [], disabled: false, checked: false, hidden: false,
        classList: { add: (...values) => values.forEach(v => classes.add(v)), remove: v => classes.delete(v),
            toggle: (v, enabled) => enabled ? classes.add(v) : classes.delete(v) },
        append(...children) { this.children.push(...children); },
        appendChild(child) { this.append(child); },
        replaceChildren(...children) { this.children = [...children]; this.textContent = ''; },
        addEventListener(type, callback) { (handlers[type] ||= []).push(callback); },
        async emit(type) {
            await Promise.all((handlers[type] || []).map(callback => callback.call(this, { type, preventDefault() {} })));
            await flush();
        },
        querySelectorAll(selector) {
            const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
            if (selector === '*') return descendants;
            if (selector.startsWith('.')) return descendants.filter(child => child.className.split(' ').includes(selector.slice(1)));
            return descendants.filter(child => selector.replaceAll(' ', '').split(',').includes(child.tagName.toLowerCase()));
        },
        querySelector(selector) {
            const key = selector.match(/data-field-error="([^"]+)"/);
            return key ? this.querySelectorAll('*').find(child => child.dataset.fieldError === key[1]) || null : null;
        },
        focus() {}, reportValidity() { return true; }, setAttribute() {}, removeAttribute() {},
    };
    Object.defineProperty(item, 'innerHTML', { set() { this.children = []; }, get() { return ''; } });
    Object.defineProperty(item, 'options', { get() { return this.children; } });
    Object.defineProperty(item, 'elements', { get() { return { namedItem: name => this.querySelectorAll('*').find(child => child.name === name) }; } });
    return item;
}

function environment() {
    const elements = {}, ready = [], alerts = [];
    const context = vm.createContext({
        window: { addEventListener() {}, confirm: () => true, socket: { on() {} }, location: { hostname: 'localhost' } },
        document: { hidden: false, addEventListener: (event, handler) => { if (event === 'DOMContentLoaded') ready.push(handler); },
            getElementById: id => elements[id] || null, createElement: node, querySelector: () => null },
        console: { log() {}, error() {}, warn() {} }, URLSearchParams, FormData, Blob, Date, Intl,
        setTimeout, clearTimeout, setInterval() {},
        DataTransfer: class { constructor() { this.files = []; this.items = { add: file => this.files.push(file) }; } },
        showAlert: (type, message) => alerts.push({ type, message }),
    });
    return { context, elements, ready, alerts, load(file) { vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context); } };
}

function mainEnvironment() {
    const env = environment();
    const ids = ['videoId', 'audioId', 'videoSelect', 'audioSelect', 'videoCodec', 'preset', 'crf', 'pixelFormat', 'x265Params', 'tune',
        'videoMap', 'outputFormat', 'audioCodec', 'audioSettings', 'audioMap', 'audioBitrate', 'audioSampleRate', 'audioVbr',
        'audioVbrContainer', 'subtitleCodec', 'subtitleMap', 'dataCodec', 'dataMap', 'presetModified', 'audioDownmix',
        'audioDownmixField', 'presetSelectionStatus', 'presetApplyLatest', 'uploadBtn', 'ffmpegForm'];
    for (const id of ids) { env.elements[id] = node(id === 'ffmpegForm' ? 'form' : 'input'); env.elements[id].id = id; }
    env.elements.ffmpegForm.append(...['crf', 'preset', 'audioCodec'].map(id => env.elements[id]));
    const source = fs.readFileSync(path.join(root, 'app/templates/index.html'), 'utf8').match(/<script>\s*([\s\S]*?)<\/script>/)[1];
    vm.runInContext(source, env.context);
    return env;
}

function resolved(crf, overrides = {}) {
    return { ok: true, json: async () => ({ catalog_revision: 'current', available: true,
        ffmpeg_params: { video_codec: 'libx265', output_format: 'matroska', crf,
            audio_codec: 'aac', audio_map: '0:a?', subtitle_codec: 'copy', subtitle_map: '0:s?',
            data_codec: 'copy', data_map: '0:t?', ...overrides } }) };
}

async function editorEnvironment() {
    const env = environment();
    const html = fs.readFileSync(path.join(root, 'app/templates/presets.html'), 'utf8');
    for (const id of html.matchAll(/id="([^"]+)"/g)) env.elements[id[1]] = node();
    env.elements.presetEditor.dataset.csrfToken = 'session-token';
    env.elements.editorForm.append(env.elements.editorFields);
    const definition = { name: 'Original', description: '', video_codec: 'libx265', output_format: 'matroska', crf: '23' };
    const detail = { id: 'hevc_rip', name: 'Original', definition, source: 'builtin', default: false,
        available: true, actions: { edit: true }, catalog_revision: 'r1' };
    const catalog = { catalog_revision: 'r1', layers: { video: [detail], audio: [], subtitles: [], data: [] }, warnings: [] };
    const calls = [];
    env.context.fetch = async (url, options = {}) => {
        calls.push({ url, ...options });
        return { ok: true, json: async () => url.endsWith('/hevc_rip') ? detail : catalog };
    };
    env.load('app/static/js/presets.js');
    env.ready.forEach(callback => callback());
    await flush();
    await env.elements.editorList.children[0].emit('click');
    return { ...env, detail, catalog, calls };
}

test('editor serializes blank optional fields as null and VBR false as boolean', () => {
    const env = environment(); env.load('app/static/js/presets.js');
    const values = { name: '  Opus CBR  ', description: '', codec: 'libopus', audio_map: '0:a?', bitrate: '', sample_rate: '', vbr: 'false' };
    const form = { elements: { namedItem: key => ({ value: values[key] }) } };
    assert.deepEqual(clean(env.context.presetFormDefinition(form, 'audio')), {
        name: 'Opus CBR', description: '', codec: 'libopus', audio_map: '0:a?', bitrate: null, sample_rate: null, vbr: false,
    });
});

test('412 and 503 keep editor draft and never retry a write automatically', async () => {
    for (const status of [412, 503]) {
        const env = await editorEnvironment();
        const name = env.elements.editorForm.elements.namedItem('name');
        name.value = 'Unsaved draft'; await name.emit('input');
        const writes = [];
        env.context.fetch = async (url, options) => {
            writes.push({ url, ...options });
            return { ok: false, status, json: async () => ({ error: 'Version or storage conflict' }) };
        };
        await env.elements.editorForm.emit('submit');
        await flush();
        assert.equal(name.value, 'Unsaved draft');
        assert.equal(writes.length, 1);
        assert.equal(writes[0].headers['If-Match'], '"r1"');
        assert.equal(writes[0].headers['X-CSRF-Token'], 'session-token');
        assert.equal(JSON.parse(writes[0].body).definition.name, 'Unsaved draft');
        assert.equal(env.elements.editorSave.disabled, false);
        if (status === 412) assert.equal(env.elements.editorConflict.hidden, false);
    }
});

test('saving captures the default choice before locking editor controls', async () => {
    const env = await editorEnvironment();
    env.elements.editorSetDefault.checked = true;
    const writes = [];
    env.context.fetch = async (url, options) => {
        writes.push({ url, ...options });
        return { ok: false, status: 503, json: async () => ({ error: 'Temporary disk failure' }) };
    };
    await env.elements.editorForm.emit('submit');
    await flush();
    assert.equal(writes.length, 1);
    assert.equal(JSON.parse(writes[0].body).set_default, true);
    assert.equal(env.elements.editorSetDefault.checked, true);
});

test('main form ignores a late response from the previous profile selection', async () => {
    const { context, elements } = mainEnvironment();
    const pending = [];
    context.fetch = () => new Promise(resolve => pending.push(resolve));
    const first = context.applyLayerSettings('first', 'aac');
    const second = context.applyLayerSettings('second', 'aac');
    pending[1](resolved('17')); await second;
    assert.equal(elements.crf.value, '17');
    pending[0](resolved('35')); await first;
    assert.equal(elements.crf.value, '17');
    assert.equal(elements.videoId.value, 'second');
    assert.equal(context.window.presetResolvePending, false);
    assert.equal(elements.uploadBtn.disabled, false);
});

test('main profile application clears null fields and false VBR from prior selection', async () => {
    const { context, elements } = mainEnvironment();
    for (const id of ['preset', 'crf', 'x265Params', 'audioBitrate', 'audioSampleRate']) elements[id].value = 'old';
    elements.audioVbr.value = 'true';
    context.fetch = async () => resolved(null, { preset: null, x265_params: null, audio_bitrate: null, audio_sample_rate: null, audio_vbr: false });
    await context.applyLayerSettings('user_1', 'user_audio');
    for (const id of ['preset', 'crf', 'x265Params', 'audioBitrate', 'audioSampleRate']) assert.equal(elements[id].value, '');
    assert.equal(elements.audioVbr.value, 'false');
});

test('a user silent audio preset applies empty subtitles/data and disables downmix', async () => {
    const { context, elements } = mainEnvironment();
    elements.audioDownmix.checked = true;
    context.fetch = async () => resolved('20', { audio_codec: null, audio_map: null, subtitle_codec: null, subtitle_map: null, data_codec: null, data_map: null });
    await context.applyLayerSettings('user_video', 'user_silent');
    assert.equal(elements.audioId.value, 'user_silent');
    for (const id of ['audioMap', 'subtitleMap', 'dataMap']) assert.equal(elements[id].value, '');
    assert.equal(elements.subtitleCodec.value, 'remove');
    assert.equal(elements.dataCodec.value, 'remove');
    assert.equal(elements.audioDownmix.disabled, true);
    assert.equal(elements.audioDownmix.checked, false);
    assert.equal(elements.audioDownmixField.value, 'none');
});

test('one captured catalog revision reaches every file and chunk despite catalog refresh', async () => {
    const env = environment(); env.load('app/static/js/upload.js');
    const parameters = new FormData(); parameters.set('catalog_revision', 'frozen-r1');
    env.context.captureEncodingParameters = () => parameters;
    env.context.applyUploadJobsFromResult = () => {};
    env.context.refreshJobsList = () => {};
    const seen = [];
    env.context.fetch = async (_url, options) => {
        seen.push(options.body.get('catalog_revision'));
        env.context.window.presetCatalogRevision = 'new-r2';
        parameters.set('unrelated', 'changed');
        return { ok: true, json: async () => seen.length % 2 === 0 ? { completed: true } : {} };
    };
    const files = ['one.mkv', 'two.mkv'].map(name => Object.assign(new Blob(['abcdefgh']), { name }));
    await env.context.uploadMultipleFiles(files, 4);
    assert.deepEqual(seen, ['frozen-r1', 'frozen-r1', 'frozen-r1', 'frozen-r1']);
});

test('chunk catalog conflict is returned once without retrying or advancing chunks', async () => {
    const env = environment(); env.load('app/static/js/upload.js');
    let requests = 0;
    env.context.fetch = async () => { requests++; return { ok: false, status: 409,
        json: async () => ({ code: 'preset_catalog_changed', error: 'Changed' }) }; };
    const file = Object.assign(new Blob(['abcdefgh']), { name: 'one.mkv' });
    await assert.rejects(env.context.uploadFileInChunks(file, 4, null, new FormData()), error => error.status === 409 && error.code === 'preset_catalog_changed');
    assert.equal(requests, 1);
    assert.equal(env.context.window.presetCatalogStale, true);
});

test('catalog conflict stops remaining files in the batch and keeps their selection', async () => {
    const env = environment(); env.load('app/static/js/upload.js');
    env.elements.fileInput = node('input');
    env.context.refreshJobsList = () => {};
    const files = ['accepted.mkv', 'conflict.mkv', 'unattempted.mkv'].map(name => ({ name, size: 8 }));
    const requested = [];
    env.context.uploadFileInChunks = async file => {
        requested.push(file.name);
        if (file === files[1]) throw Object.assign(new Error('Catalog changed'), { status: 409, code: 'preset_catalog_changed' });
    };
    await env.context.uploadMultipleFiles(files, 4, new FormData());
    assert.deepEqual(requested, ['accepted.mkv', 'conflict.mkv']);
    assert.deepEqual(env.elements.fileInput.files, [files[1], files[2]]);
    assert.ok(env.alerts.some(alert => /1 из 3/.test(alert.message)));
});

test('manual input during pending main resolve survives the late response', async () => {
    const { context, elements } = mainEnvironment();
    let respond;
    context.fetch = () => new Promise(resolve => respond = resolve);
    context.setupPresetModifiedTracking();
    const pending = context.applyLayerSettings('profile', 'aac');
    elements.crf.value = '11'; await elements.crf.emit('input');
    respond(resolved('30')); await pending;
    assert.equal(elements.crf.value, '11');
    assert.equal(elements.presetModified.value, 'true');
});

test('deleted selected preset can be compared and recovered as a new preset without losing draft', async () => {
    const env = await editorEnvironment();
    const name = env.elements.editorForm.elements.namedItem('name');
    name.value = 'Recover this draft'; await name.emit('input');
    env.catalog.catalog_revision = 'r2'; env.catalog.layers.video = [];
    const writes = [];
    let created;
    env.context.fetch = async (url, options = {}) => {
        if (options.method === 'POST') {
            writes.push({ url, ...options });
            created = { ...env.detail, id: 'user_recovered', name: name.value, source: 'custom',
                definition: JSON.parse(options.body).definition, catalog_revision: 'r3' };
            env.catalog.catalog_revision = 'r3'; env.catalog.layers.video = [created];
            return { ok: true, json: async () => created };
        }
        if (url.endsWith('/hevc_rip')) return { ok: false, status: 404, json: async () => ({ error: 'Deleted elsewhere' }) };
        return { ok: true, json: async () => url.endsWith('/user_recovered') ? created : env.catalog };
    };
    await env.elements.editorCompare.emit('click');
    assert.equal(name.value, 'Recover this draft');
    assert.equal(env.elements.editorAcceptRevision.hidden, false);
    await env.elements.editorAcceptRevision.emit('click');
    await env.elements.editorCopy.emit('click');
    await flush();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].url, '/api/preset-editor/video');
    assert.equal(writes[0].headers['If-Match'], '"r2"');
    assert.equal(JSON.parse(writes[0].body).definition.name, 'Recover this draft');
});

function catalogResponse(revision = 'current') {
    return { catalog_revision: revision,
        videos: [{ id: 'A', name: 'A' }, { id: 'B', name: 'B' }],
        audios: [{ id: 'aac', name: 'AAC', available: true }],
        defaults: { video_id: 'A', audio_id: 'aac' } };
}
const response = payload => ({ ok: true, json: async () => payload });

for (const action of ['save', 'SystemDefault', 'Reset', 'Delete', 'ApplyImport']) {
    test(`editor ${action} keeps controls locked until catalog and selected detail reload finish`, async () => {
        const env = await editorEnvironment();
        if (action === 'ApplyImport') {
            env.elements.editorImportFile.files = [{ size: 2, text: async () => '{}' }];
            env.elements.editorImportMode.value = 'merge';
            env.context.fetch = async () => response({ catalog_revision: 'r1', preview_digest: 'preview', conflicts: [] });
            await env.elements.editorPreviewImport.emit('click');
        }
        const updated = { ...env.detail, catalog_revision: 'r2' };
        const catalog = { ...env.catalog, catalog_revision: 'r2', layers: { ...env.catalog.layers, video: [updated] } };
        let finishCatalog, finishDetail;
        env.context.fetch = (url, options) => {
            if (options.method !== 'GET') return Promise.resolve(response(updated));
            if (url === '/api/preset-editor') return new Promise(resolve => finishCatalog = resolve);
            return new Promise(resolve => finishDetail = resolve);
        };
        const operation = action === 'save' ? env.elements.editorForm.emit('submit') : env.elements[`editor${action}`].emit('click');
        await flush();
        assert.equal(typeof finishCatalog, 'function');
        for (const id of ['editorFields', 'editorSave', 'editorLayer', 'editorSetDefault']) assert.equal(env.elements[id].disabled, true, id);
        finishCatalog(response(catalog)); await flush();
        if (action !== 'Delete') {
            assert.equal(typeof finishDetail, 'function');
            assert.equal(env.elements.editorFields.disabled, true);
            assert.equal(env.elements.editorSave.disabled, true);
            finishDetail(response(updated));
        }
        await operation; await flush();
        assert.equal(env.elements.editorFields.disabled, false);
        assert.equal(env.elements.editorSave.disabled, false);
    });
}

test('typing while ordinary editor refresh waits for detail preserves draft and old CAS revision', async () => {
    const env = await editorEnvironment();
    const updated = { ...env.detail, definition: { ...env.detail.definition, name: 'Changed remotely' }, catalog_revision: 'r2' };
    let finishDetail;
    const writes = [];
    env.context.fetch = (url, options) => {
        if (options.method !== 'GET') {
            writes.push(options);
            return Promise.resolve({ ok: false, status: 412, json: async () => ({ error: 'Compare first' }) });
        }
        if (url === '/api/preset-editor') return Promise.resolve(response({ ...env.catalog, catalog_revision: 'r2' }));
        return new Promise(resolve => finishDetail = resolve);
    };
    const refreshing = env.elements.editorRefresh.emit('click'); await flush();
    const name = env.elements.editorForm.elements.namedItem('name');
    name.value = 'My latest input'; await name.emit('input');
    finishDetail(response(updated)); await refreshing;
    assert.equal(env.elements.editorForm.elements.namedItem('name').value, 'My latest input');
    assert.equal(env.elements.editorConflict.hidden, false);
    await env.elements.editorForm.emit('submit');
    assert.equal(writes[0].headers['If-Match'], '"r1"');
    assert.equal(JSON.parse(writes[0].body).definition.name, 'My latest input');
});

test('ordinary editor refresh does not roll back a pending explicit preset selection', async () => {
    const env = await editorEnvironment();
    const next = { ...env.detail, id: 'other', name: 'Other', definition: { ...env.detail.definition, name: 'Other' } };
    env.catalog.layers.video.push(next);
    // Refresh once to render the second button before starting its delayed GET.
    await env.elements.editorRefresh.emit('click');
    let finishSelection;
    env.context.fetch = url => url.endsWith('/other')
        ? new Promise(resolve => finishSelection = resolve)
        : Promise.resolve(response(url === '/api/preset-editor' ? env.catalog : env.detail));
    await env.elements.editorList.children[1].emit('click');
    await env.elements.editorRefresh.emit('click');
    finishSelection(response(next)); await flush();
    assert.equal(env.elements.editorForm.elements.namedItem('name').value, 'Other');
    assert.equal(env.elements.editorTitle.textContent, 'Other');
});

for (const explicit of [false, true]) {
    test(`same-revision catalog recovery unblocks upload and preserves custom fields (explicit=${explicit})`, async () => {
        const { context, elements } = mainEnvironment();
        context.fetch = async () => resolved('23');
        await context.applyLayerSettings('A', 'aac');
        elements.videoSelect.value = 'A'; elements.audioSelect.value = 'aac';
        elements.crf.value = '11'; elements.presetModified.value = 'true';
        context.fetch = async () => { throw new Error('Offline'); };
        await context.loadPresets();
        assert.equal(context.window.presetCatalogUnavailable, true);
        assert.equal(elements.uploadBtn.disabled, true);
        assert.equal(elements.presetApplyLatest.hidden, false);
        context.fetch = async url => { assert.equal(url, '/api/presets'); return response(catalogResponse()); };
        await context.loadPresets({ explicit });
        assert.equal(context.window.presetCatalogUnavailable, false);
        assert.equal(context.window.presetCatalogStale, false);
        assert.equal(elements.uploadBtn.disabled, false);
        assert.equal(elements.presetApplyLatest.hidden, true);
        assert.equal(elements.crf.value, '11');
        assert.equal(elements.presetModified.value, 'true');
    });
}

test('successful same-revision catalog read does not erase an outstanding resolve error', async () => {
    const { context, elements } = mainEnvironment();
    context.window.presetCatalogRevision = 'current';
    context.fetch = async () => ({ ok: false, json: async () => ({ error: 'Resolve failed' }) });
    await context.applyLayerSettings('A', 'aac');
    assert.equal(context.window.presetResolveFailed, true);
    context.fetch = async () => response(catalogResponse());
    await context.loadPresets();
    assert.equal(context.window.presetCatalogStale, true);
    assert.equal(elements.uploadBtn.disabled, true);
    assert.equal(elements.presetApplyLatest.hidden, false);
});

test('catalog response keeps the profile selected while the request was pending', async () => {
    const { context, elements } = mainEnvironment();
    context.window.presetCatalogRevision = 'r1';
    context.window.presetSelectionTouched = true;
    elements.videoSelect.value = 'A'; elements.audioSelect.value = 'aac';
    let finishCatalog;
    context.fetch = () => new Promise(resolve => finishCatalog = resolve);
    const applied = [];
    context.applyLayerSettings = async (...ids) => applied.push(ids);
    const loading = context.loadPresets();
    elements.videoSelect.value = 'B';
    finishCatalog(response(catalogResponse('r2'))); await loading;
    assert.deepEqual(clean(applied), [['B', 'aac']]);
    assert.equal(elements.videoSelect.value, 'B');
});

test('an older catalog request cannot replace a newer catalog or launch another resolve', async () => {
    const { context } = mainEnvironment();
    const pending = [], applied = [];
    context.fetch = () => new Promise(resolve => pending.push(resolve));
    context.applyLayerSettings = async (...ids) => applied.push(ids);
    const first = context.loadPresets();
    const latest = context.loadPresets();
    pending[1](response(catalogResponse('r3'))); await latest;
    pending[0](response(catalogResponse('r2'))); await first;
    assert.equal(context.window.presetCatalogRevision, 'r3');
    assert.equal(applied.length, 1);
});

test('Opus VBR auto stays absent in custom upload after an unrelated CRF edit', async () => {
    const env = mainEnvironment();
    const { context, elements } = env;
    elements.audioVbr.tagName = 'SELECT';
    env.load('app/static/js/upload.js');
    context.FormData = class extends FormData {
        constructor(form) {
            super();
            if (form) {
                this.set('audio_codec', elements.audioCodec.value);
                this.set('audio_vbr', elements.audioVbr.value);
                this.set('preset_modified', elements.presetModified.value);
                this.set('crf', elements.crf.value);
            }
        }
    };
    context.fetch = async () => resolved('23', { audio_codec: 'libopus', audio_vbr: null });
    await context.applyLayerSettings('A', 'user_opus');
    context.setupPresetModifiedTracking();
    elements.crf.value = '18'; await elements.crf.emit('input');
    assert.equal(elements.audioVbr.value, '');
    const parameters = context.captureEncodingParameters();
    assert.equal(parameters.get('preset_modified'), 'true');
    assert.equal(parameters.get('crf'), '18');
    assert.equal(parameters.has('audio_vbr'), false);
    elements.audioVbr.value = 'false';
    assert.deepEqual(context.captureEncodingParameters().getAll('audio_vbr'), ['false']);
    elements.audioVbr.value = 'true';
    assert.deepEqual(context.captureEncodingParameters().getAll('audio_vbr'), ['true']);
});
