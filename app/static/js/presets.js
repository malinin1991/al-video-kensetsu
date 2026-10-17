/* Редактор хранит версию черновика отдельно от последнего прочитанного каталога. */
const PRESET_FIELDS = {
    video: [
        ['video_codec', 'Кодек видео', ['libx265']], ['output_format', 'Контейнер', ['matroska']],
        ['preset', 'Скорость', ['', 'ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow']],
        ['crf', 'Качество CRF', 'number', 0, 51],
        ['pixel_format', 'Формат пикселей', ['', 'yuv420p', 'yuv420p10le', 'yuv444p10le']],
        ['x265_params', 'Дополнительные параметры x265', 'text'],
        ['tune', 'Оптимизация', ['', 'animation', 'grain', 'psnr', 'ssim', 'fastdecode', 'zerolatency']],
        ['video_map', 'Карта видео', 'text'],
    ],
    audio: [
        ['codec', 'Кодек аудио', ['', 'copy', 'aac', 'libfdk_aac', 'libopus']],
        ['audio_map', 'Карта аудио', 'text'], ['bitrate', 'Битрейт, например 160k', 'text'],
        ['sample_rate', 'Частота, Hz', 'number', 8000, 192000], ['vbr', 'Переменный битрейт', ['', 'true', 'false']],
    ],
    subtitles: [['subtitle_codec', 'Кодек субтитров', ['', 'copy', 'ass', 'srt']], ['subtitle_map', 'Карта субтитров', 'text']],
    data: [['data_codec', 'Вложения', ['', 'copy']], ['data_map', 'Карта вложений', 'text']],
};
const PRESET_SOURCE_NAMES = { builtin: 'Встроенный', override: 'Ваша версия встроенного', custom: 'Пользовательский' };

function presetFieldDefinitions(layer) {
    return [['name', 'Название', 'text'], ['description', 'Описание', 'textarea'], ...PRESET_FIELDS[layer]];
}

function presetFormDefinition(form, layer) {
    const definition = {};
    for (const [key] of presetFieldDefinitions(layer)) {
        const value = form.elements.namedItem(key).value.trim();
        definition[key] = key === 'vbr' ? (value === '' ? null : value === 'true')
            : (key === 'name' || key === 'description' ? value : value || null);
    }
    return definition;
}

function presetDraftDifferences(layer, current, draft) {
    return presetFieldDefinitions(layer).filter(([key]) => JSON.stringify(current[key] ?? null) !== JSON.stringify(draft[key] ?? null))
        .map(([key, label]) => ({ label, current: current[key] ?? null, draft: draft[key] ?? null }));
}

document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('presetEditor');
    if (!root) return;
    const el = (id) => document.getElementById(`editor${id}`);
    const state = { catalog: null, layer: 'video', selected: null, detail: null, dirty: false,
        draftRevision: null, busy: false, preview: null, previewSequence: 0, refreshAt: 0, sequence: 0, refreshSequence: 0, selectPending: 0 };

    function status(message, type = 'secondary') {
        el('Status').className = `alert alert-${type}`;
        el('Status').textContent = message;
    }
    function markDirty() { state.dirty = true; state.sequence++; }
    function canLeave() { return !state.dirty || window.confirm('В форме есть несохранённые изменения. Отбросить их?'); }
    function conflict() {
        el('Conflict').hidden = false;
        el('AcceptRevision').hidden = true;
        status('Каталог изменился. Черновик оставлен в форме; требуется сравнение.', 'warning');
    }
    function controls(disabled) {
        state.busy = disabled;
        if (disabled) { state.sequence++; state.refreshSequence++; }
        for (const id of ['Save', 'Copy', 'Reset', 'Delete', 'New', 'SystemDefault', 'ApplyImport', 'PreviewImport', 'Refresh', 'Compare', 'AcceptRevision']) {
            el(id).disabled = disabled || !state.catalog;
        }
        el('Fields').disabled = disabled;
        el('Layer').disabled = disabled;
        el('SetDefault').disabled = disabled || state.detail?.available === false || !!state.detail?.default;
    }
    async function api(path = '', method = 'GET', body, revision = state.draftRevision || state.catalog?.catalog_revision) {
        const headers = { Accept: 'application/json' };
        if (method !== 'GET') {
            headers['Content-Type'] = 'application/json';
            headers['X-CSRF-Token'] = root.dataset.csrfToken;
            if (revision) headers['If-Match'] = `"${revision}"`;
        }
        const response = await fetch(`/api/preset-editor${path}`, {
            method, headers, cache: 'no-store', ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        const result = await response.json();
        if (!response.ok) {
            const error = new Error(result.error || `Ошибка ${response.status}`);
            error.status = response.status; error.fields = result.fields || {};
            if (response.status === 412) conflict();
            throw error;
        }
        return result;
    }
    function showError(error) {
        status(error.message, 'danger');
        for (const node of el('Fields').querySelectorAll('.invalid-feedback')) node.textContent = '';
        for (const [key, message] of Object.entries(error.fields || {})) {
            const field = el('Fields').querySelector(`[data-field-error="${key.replace(/^definition\./, '')}"]`);
            if (field) field.textContent = Array.isArray(message) ? message.join('; ') : String(message);
        }
    }
    function list() {
        el('List').replaceChildren();
        const search = el('Search').value.trim().toLocaleLowerCase();
        for (const item of state.catalog?.layers[state.layer] || []) {
            if (!`${item.name} ${item.id}`.toLocaleLowerCase().includes(search)) continue;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `list-group-item list-group-item-action text-break${state.selected === item.id ? ' active' : ''}`;
            const name = document.createElement('strong'); name.textContent = item.name;
            const info = document.createElement('small'); info.className = 'd-block';
            info.textContent = [item.id, PRESET_SOURCE_NAMES[item.source], item.default ? 'По умолчанию' : '',
                item.available === false ? 'Кодек недоступен' : '', item.builtin_changed ? 'Поставка обновилась' : '',
                item.orphaned ? 'Нет в поставке' : '', item.base_unknown ? 'База неизвестна' : ''].filter(Boolean).join(' · ');
            button.append(name, info);
            button.addEventListener('click', () => { if (!state.busy && canLeave()) select(item.id).catch(showError); });
            el('List').append(button);
        }
    }
    function renderDifferences(detail) {
        const fields = detail.differences || {};
        const entries = detail.source === 'builtin' || (detail.source === 'custom' && !detail.current_builtin)
            ? [] : Object.entries(fields);
        el('Differences').replaceChildren();
        el('DiffSection').hidden = !entries.length;
        for (const [key, values] of entries) {
            const row = document.createElement('p');
            const title = presetFieldDefinitions(state.layer).find(([name]) => name === key)?.[1] || key;
            row.textContent = `${title}: база ${JSON.stringify(values.base ?? null)} → поставка ${JSON.stringify(values.builtin ?? null)}; ваша версия ${JSON.stringify(values.user ?? null)}`;
            el('Differences').append(row);
        }
    }
    function renderForm(definition) {
        el('Fields').replaceChildren();
        for (const [key, labelText, type, min, max] of presetFieldDefinitions(state.layer)) {
            const wrapper = document.createElement('div'); wrapper.className = 'mb-3';
            const label = document.createElement('label'); label.className = 'form-label';
            label.htmlFor = `preset-${key}`; label.textContent = labelText;
            const input = document.createElement(Array.isArray(type) ? 'select' : type === 'textarea' ? 'textarea' : 'input');
            input.id = `preset-${key}`; input.name = key;
            input.className = Array.isArray(type) ? 'form-select' : 'form-control';
            if (Array.isArray(type)) {
                for (const value of type) {
                    const option = document.createElement('option'); option.value = value;
                    option.textContent = value === '' ? (key.includes('codec') || key === 'codec' ? 'Без потока' : 'Авто') : value;
                    input.append(option);
                }
            } else if (type !== 'textarea') { input.type = type; }
            if (min !== undefined) { input.min = min; input.max = max; input.step = '1'; }
            if (key === 'name') { input.required = true; input.maxLength = 120; }
            if (key === 'description') { input.maxLength = 4000; input.rows = 2; }
            if (key === 'x265_params') input.maxLength = 4096;
            input.value = definition[key] === null || definition[key] === undefined ? '' : String(definition[key]);
            const error = document.createElement('div'); error.className = 'invalid-feedback d-block';
            error.dataset.fieldError = key;
            wrapper.append(label, input, error); el('Fields').append(wrapper);
            input.addEventListener('input', markDirty);
            input.addEventListener('change', () => {
                markDirty();
                if (key === 'codec' || key.endsWith('_codec')) syncCodecFields();
            });
        }
        syncCodecFields();
    }
    function syncCodecFields() {
        const form = el('Form');
        const key = { audio: 'codec', subtitles: 'subtitle_codec', data: 'data_codec' }[state.layer];
        if (!key) return;
        const codec = form.elements.namedItem(key).value;
        const mapKey = { audio: 'audio_map', subtitles: 'subtitle_map', data: 'data_map' }[state.layer];
        const map = form.elements.namedItem(mapKey);
        if (!codec) map.value = '';
        else if (!map.value) map.value = { audio: '0:a?', subtitles: '0:s?', data: '0:t?' }[state.layer];
        map.disabled = !codec;
        if (state.layer === 'audio') {
            for (const name of ['bitrate', 'sample_rate', 'vbr']) {
                const field = form.elements.namedItem(name);
                field.disabled = !codec || codec === 'copy';
                if (field.disabled) field.value = '';
            }
        }
    }
    async function select(id) {
        const sequence = ++state.sequence;
        state.selectPending = sequence;
        try {
            const detail = await api(`/${state.layer}/${encodeURIComponent(id)}`);
            if (sequence !== state.sequence) {
                if (state.dirty && state.catalog?.catalog_revision !== state.draftRevision) conflict();
                return;
            }
            state.selected = id; state.detail = detail; state.dirty = false;
            state.draftRevision = detail.catalog_revision || state.catalog.catalog_revision;
            el('Title').textContent = detail.name || detail.definition.name;
            el('Origin').textContent = `${id} · ${PRESET_SOURCE_NAMES[detail.source]}${detail.source === 'builtin' ? '. Изменения сохранятся в вашей версии.' : ''}`;
            el('Conflict').hidden = true; el('Form').hidden = false;
            renderForm(detail.definition);
            el('SetDefault').checked = !!detail.default;
            el('SetDefault').disabled = state.busy || detail.available === false || !!detail.default;
            el('Reset').hidden = !detail.actions?.reset;
            el('Delete').hidden = !detail.actions?.delete;
            renderDifferences(detail); list();
        } catch (error) {
            if (sequence === state.sequence) throw error;
        } finally {
            if (state.selectPending === sequence) state.selectPending = 0;
        }
    }

    async function refresh({ preserve = true, locked = false } = {}) {
        if (state.busy && !locked) return;
        const sequence = ++state.refreshSequence;
        const latest = await api();
        if (sequence !== state.refreshSequence) return;
        const changed = state.catalog && state.catalog.catalog_revision !== latest.catalog_revision;
        state.catalog = latest;
        el('Warnings').replaceChildren();
        for (const warning of latest.warnings || []) {
            const paragraph = document.createElement('p'); paragraph.className = 'text-warning';
            paragraph.textContent = typeof warning === 'string' ? warning : warning.message ||
                [warning.id, warning.builtin_changed ? 'Встроенный пресет обновлён' : '', warning.orphaned ? 'Нет в поставке' : '',
                    warning.base_unknown ? 'База версии неизвестна' : '', warning.source_collision ? 'Совпадение ID с поставкой' : '',
                    warning.code === 'default_codec_unavailable' ? 'Кодек default недоступен' : ''].filter(Boolean).join(' · ');
            el('Warnings').append(paragraph);
        }
        list();
        if (!locked) controls(false);
        if (state.dirty && preserve) { if (changed) conflict(); return; }
        if (state.selectPending && !locked) return;
        if (state.selected && latest.layers[state.layer].some((item) => item.id === state.selected)) await select(state.selected);
        else {
            state.selected = null; state.detail = null;
            el('Form').hidden = true; el('Title').textContent = 'Выберите пресет';
            el('Origin').textContent = ''; el('DiffSection').hidden = true;
        }
        if (state.dirty && preserve) { if (changed) conflict(); return; }
        status('Каталог загружен. Изменения сохраняются без перезапуска.', 'secondary');
    }
    function newPreset() {
        state.sequence++; state.selected = null; state.detail = null;
        state.draftRevision = state.catalog.catalog_revision; state.dirty = true;
        const definition = { name: '', description: '', ...({
            video: { video_codec: 'libx265', output_format: 'matroska' },
            audio: { codec: 'copy', audio_map: '0:a?' },
            subtitles: { subtitle_codec: 'copy', subtitle_map: '0:s?' },
            data: { data_codec: 'copy', data_map: '0:t?' },
        }[state.layer]) };
        renderForm(definition); el('Form').hidden = false; el('Title').textContent = 'Новый пресет';
        el('Origin').textContent = 'ID будет назначен при сохранении.';
        el('SetDefault').checked = false; el('SetDefault').disabled = false;
        el('Reset').hidden = true; el('Delete').hidden = true; el('Conflict').hidden = true;
        el('DiffSection').hidden = true; list(); el('Form').elements.namedItem('name').focus();
    }
    async function save(copy = false) {
        if (state.busy || !el('Form').reportValidity()) return;
        const definition = presetFormDefinition(el('Form'), state.layer);
        const setDefault = el('SetDefault').checked && !el('SetDefault').disabled;
        controls(true);
        try {
            const isNew = copy || !state.selected;
            const result = await api(`/${state.layer}${isNew ? '' : `/${encodeURIComponent(state.selected)}`}`,
                isNew ? 'POST' : 'PUT', { definition, set_default: setDefault });
            state.dirty = false; state.selected = result.id || result.preset?.id || state.selected;
            await refresh({ preserve: false, locked: true });
            status('Пресет сохранён.', 'success');
        } catch (error) { showError(error); } finally { controls(false); }
    }
    el('Form').addEventListener('submit', (event) => { event.preventDefault(); save(); });
    el('Copy').addEventListener('click', () => save(true));
    el('SetDefault').addEventListener('change', markDirty);
    el('New').addEventListener('click', () => { if (!state.busy && canLeave()) newPreset(); });
    el('Search').addEventListener('input', list);
    el('Layer').addEventListener('change', () => {
        if (state.busy || !canLeave()) { el('Layer').value = state.layer; return; }
        state.layer = el('Layer').value; state.selected = null; state.dirty = false;
        state.sequence++; el('Form').hidden = true; el('DiffSection').hidden = true;
        el('Title').textContent = 'Выберите пресет'; el('Origin').textContent = ''; list();
    });
    el('Refresh').addEventListener('click', () => refresh().catch(showError));
    el('SystemDefault').addEventListener('click', async () => {
        if (state.busy || !canLeave()) return;
        controls(true);
        try {
            await api('/defaults', 'PUT', { defaults: { [state.layer]: null } }, state.catalog.catalog_revision);
            state.dirty = false; await refresh({ preserve: false, locked: true });
            status('Используется default текущей поставки.', 'success');
        } catch (error) { showError(error); } finally { controls(false); }
    });
    el('Reset').addEventListener('click', async () => {
        if (state.busy) return;
        if (!window.confirm('Удалить ваши изменения этого пресета и применить текущую поставляемую версию? Поля изменятся на значения из раздела сравнения.')) return;
        controls(true);
        try { await api(`/${state.layer}/${state.selected}/reset`, 'POST', {});
            state.dirty = false; await refresh({ preserve: false, locked: true }); status('Применена текущая поставляемая версия.', 'success');
        } catch (error) { showError(error); } finally { controls(false); }
    });
    el('Delete').addEventListener('click', async () => {
        if (state.busy) return;
        if (!window.confirm(`Удалить пресет «${state.detail.name || state.detail.definition.name}»? Задания сохранят прежние настройки.`)) return;
        const body = {};
        if (state.detail.default) {
            const alternatives = state.catalog.layers[state.layer].filter((item) => item.id !== state.selected && item.available !== false);
            const choices = alternatives.map((item, index) => `${index + 1}. ${item.name} (${item.id})`).join('\n');
            const choice = window.prompt(`Выберите номер пресета, который станет default:\n${choices}`);
            if (choice === null) return;
            const replacement = alternatives[Number(choice) - 1];
            if (!replacement) { status('Замена default не выбрана.', 'warning'); return; }
            body.replacement_default_id = replacement.id;
        }
        controls(true);
        try { await api(`/${state.layer}/${state.selected}`, 'DELETE', body);
            state.dirty = false; state.selected = null; await refresh({ preserve: false, locked: true }); status('Пресет удалён.', 'success');
        } catch (error) { showError(error); } finally { controls(false); }
    });
    el('Compare').addEventListener('click', async () => {
        if (state.busy) return;
        try {
            await refresh();
            let current = { definition: {}, catalog_revision: state.catalog.catalog_revision };
            el('AcceptRevision').dataset.removed = '';
            if (state.selected) {
                try { current = await api(`/${state.layer}/${state.selected}`); }
                catch (error) {
                    if (error.status !== 404) throw error;
                    el('AcceptRevision').dataset.removed = 'true';
                }
            }
            const differences = presetDraftDifferences(state.layer, current.definition, presetFormDefinition(el('Form'), state.layer));
            el('CurrentComparison').replaceChildren();
            for (const difference of differences) {
                const row = document.createElement('p');
                row.textContent = `${difference.label}: сейчас ${JSON.stringify(difference.current)}, ваш ввод ${JSON.stringify(difference.draft)}`;
                el('CurrentComparison').append(row);
            }
            if (!differences.length) el('CurrentComparison').textContent = 'Поля совпадают с текущей версией.';
            el('AcceptRevision').hidden = false;
            el('AcceptRevision').dataset.revision = current.catalog_revision || state.catalog.catalog_revision;
        } catch (error) { showError(error); }
    });
    el('AcceptRevision').addEventListener('click', () => {
        if (state.busy) return;
        if (!window.confirm('Продолжить с вашими полями поверх просмотренной версии? Сохранение выполнится только после нажатия «Сохранить».')) return;
        state.draftRevision = el('AcceptRevision').dataset.revision; el('Conflict').hidden = true;
        if (el('AcceptRevision').dataset.removed === 'true') {
            state.selected = null; state.detail = null;
            el('Title').textContent = 'Копия удалённого пресета';
            el('Origin').textContent = 'Пресет удалён из каталога. Ваши поля можно сохранить как новый.';
            el('Delete').hidden = true; el('Reset').hidden = true;
            el('SetDefault').disabled = false; el('SetDefault').checked = false;
        }
        status('Версия принята. Проверьте поля и сохраните.', 'warning');
    });
    function invalidatePreview() { state.previewSequence++; state.preview = null; el('ApplyImport').hidden = true; el('ImportPreview').textContent = ''; }
    for (const id of ['ImportFile', 'ImportMode', 'ImportDefaults']) el(id).addEventListener('change', invalidatePreview);
    el('PreviewImport').addEventListener('click', async () => {
        if (state.busy) return;
        invalidatePreview();
        const sequence = state.previewSequence;
        try {
            const file = el('ImportFile').files[0];
            if (!file) throw new Error('Выберите файл JSON.');
            if (file.size > 5 * 1024 * 1024) throw new Error('Каталог превышает 5 MiB.');
            const body = { catalog: await file.text(), mode: el('ImportMode').value };
            if (body.mode === 'merge') body.import_defaults = el('ImportDefaults').checked;
            const result = await api('/import/preview', 'POST', body, state.catalog.catalog_revision);
            if (sequence !== state.previewSequence) return;
            state.preview = { body, result };
            el('ImportPreview').replaceChildren();
            for (const [label, values] of [['Добавятся', result.additions], ['Изменятся', result.changes], ['Удалятся', result.removals], ['Конфликты', result.conflicts]]) {
                const row = document.createElement('p'); row.textContent = `${label}: ${values?.length ? values.join(', ') : 'нет'}`;
                el('ImportPreview').append(row);
            }
            for (const [layer, change] of Object.entries(result.default_changes || {})) {
                const row = document.createElement('p'); row.textContent = `Default ${layer}: ${change.old || 'поставка'} → ${change.new || 'поставка'}`;
                el('ImportPreview').append(row);
            }
            for (const warning of result.warnings || []) {
                const row = document.createElement('p'); row.className = 'text-warning';
                row.textContent = warning.message || `${warning.id || warning.layer || ''}: ${warning.code === 'default_codec_unavailable' ? 'Кодек default недоступен' : 'Сравните пользовательскую версию с текущей поставкой'}`;
                el('ImportPreview').append(row);
            }
            el('ApplyImport').hidden = !!result.conflicts?.length;
        } catch (error) { showError(error); }
    });
    el('ApplyImport').addEventListener('click', async () => {
        if (state.busy || !state.preview || !canLeave()) return;
        if (!window.confirm(state.preview.body.mode === 'replace_user_catalog'
            ? 'Заменить весь пользовательский каталог проверенными данными? Перед заменой создаётся резервная копия.'
            : 'Применить проверенное добавление пресетов?')) return;
        controls(true);
        try {
            await api('/import', 'POST', { ...state.preview.body, preview_digest: state.preview.result.preview_digest },
                state.preview.result.catalog_revision);
            state.dirty = false; invalidatePreview(); await refresh({ preserve: false, locked: true }); status('Каталог импортирован.', 'success');
        } catch (error) { showError(error); } finally { controls(false); }
    });
    async function checkUpdates() {
        if (document.hidden || state.busy || Date.now() - state.refreshAt < 5000) return;
        state.refreshAt = Date.now();
        try {
            const latest = await api();
            if (state.catalog?.catalog_revision !== latest.catalog_revision) await refresh();
        } catch (error) { showError(error); }
    }
    window.addEventListener('beforeunload', (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('focus', checkUpdates);
    document.addEventListener('visibilitychange', checkUpdates);
    window.socket?.on('presets_updated', checkUpdates);
    window.socket?.on('connect', checkUpdates);
    setInterval(checkUpdates, 5000);
    controls(true);
    state.busy = false;
    refresh().catch((error) => { showError(error); controls(false); });
});
