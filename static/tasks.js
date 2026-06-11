/**
 * @file tasks.js
 * @description Manages the Memory Feature Task List UI, including single/bulk additions,
 *              pagination, drag-and-drop reordering, and asynchronous data synchronization.
 */

let allFeatures = [];
let completedIds = new Set();
let availablePatches = [];
let currentPatchDir = '';
let editingId = null;
let editingGroupOnly = false; // Track if we are only editing the group ID for completed tasks

// Pagination state
let currentPage = 1;
const itemsPerPage = 10;

// Bulk operation buffers
let bulkRuntimeGroups = [];
let bulkBuildGroups = [];
let bulkPatches = [];

/**
 * Global initialization entry point. Sets up UI event listeners and loads initial data.
 */
document.addEventListener('DOMContentLoaded', () => {
    console.log("Setting up UI...");
    
    // 1. Setup Non-blocking UI (Buttons, Form listeners)
    setupUI();
    
    // 2. Load Data Asynchronously
    initData();
});

/**
 * Asynchronously fetches all required data from the backend to initialize the dashboard.
 * @async
 */
async function initData() {
    console.log("Loading data...");
    try {
        await loadStatus(); 
        await loadFeatures(); // This triggers first renderTable
        await fetchPatchList();
        console.log("Data loaded.");
    } catch (e) {
        console.error("Data loading failed:", e);
    }
}

/**
 * Binds event listeners to UI components including forms, pagination, and modals.
 * Handles single task submission and global keyboard shortcuts.
 */
function setupUI() {
    // Overlays
    const openSingle = document.getElementById('openSingleAddBtn');
    const openBulk = document.getElementById('openBulkAddBtn');
    const cleanBtn = document.getElementById('cleanTasksBtn');

    if (openSingle) openSingle.onclick = () => openOverlay('singleAddOverlay');
    if (openBulk) openBulk.onclick = () => openOverlay('bulkAddOverlay');
    if (cleanBtn) cleanBtn.onclick = handleCleanTasks;

    document.querySelectorAll('.close-overlay').forEach(btn => {
        btn.onclick = () => closeOverlay(btn.dataset.target);
    });

    // Single Add Form
    const singleForm = document.getElementById('addTaskForm');
    if (singleForm) {
        singleForm.onsubmit = async (e) => {
            e.preventDefault();
            const groupId = document.getElementById('groupId').value.trim();
            const buildStr = document.getElementById('buildFlags').value.trim();
            const runtimeStr = document.getElementById('runtimeFlags').value.trim();
            const patchFile = document.getElementById('patchFile').value.trim();
            allFeatures.push({
                id: getNextId(),
                group_id: groupId || null,
                build_flags: buildStr ? buildStr.split(/\s+/) : [],
                runtime_flags: runtimeStr ? runtimeStr.split(/\s+/) : [],
                patch: patchFile || null
            });
            await saveAllFeatures();
            closeOverlay('singleAddOverlay');
            singleForm.reset();
            renderTable();
        };
    }

    // Bulk Add Controls
    setupBulkUI();
    
    // Directory Browser
    setupDirectoryBrowserUI();

    // Pagination
    const prev = document.getElementById('prevPage');
    const next = document.getElementById('nextPage');
    if (prev) prev.onclick = () => { if (currentPage > 1) { currentPage--; renderTable(); } };
    if (next) next.onclick = () => { if (currentPage * itemsPerPage < allFeatures.length) { currentPage++; renderTable(); } };

    // Autocomplete focus
    setupAutocompleteUI();

    // Global Esc
    window.onkeydown = (e) => {
        if (e.key === 'Escape') ['singleAddOverlay', 'bulkAddOverlay', 'pathModal'].forEach(closeOverlay);
    };

    // Table Drag support
    setupDragAndDrop();
}

/**
 * Toggles the visibility of an overlay/modal.
 * @param {string} id - The DOM element ID of the overlay to open.
 */
function openOverlay(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'block';
}

/**
 * Hides a specific overlay or the default path modal.
 * @param {string|Object} id - The ID of the overlay or an event object (for modal closing).
 */
function closeOverlay(id) {
    if (typeof id === 'string') {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    } else {
        // Handle case where id is not a string (like from pathModal close)
        const modal = document.getElementById('pathModal');
        if (modal) modal.style.display = 'none';
    }
}

/**
 * Fetches the status of completed tasks from the API to update UI badges.
 * @async
 */
async function loadStatus() {
    try {
        const r = await fetch('/api/results');
        const data = await r.json();
        completedIds = new Set(data.map(i => i.id));
    } catch (e) { console.error(e); }
}

/**
 * Fetches the list of memory features (tasks) from the server and renders the table.
 * @async
 */
async function loadFeatures() {
    try {
        const r = await fetch('/api/features');
        allFeatures = await r.json() || [];
        renderTable();
    } catch (e) { console.error(e); }
}

/**
 * Retrieves the list of available patch files and the current patch source directory.
 * @async
 */
async function fetchPatchList() {
    try {
        const r = await fetch('/api/patches');
        const d = await r.json();
        availablePatches = d.patches || [];
        currentPatchDir = d.patch_dir;
        document.querySelectorAll('.patch-dir-display').forEach(el => el.innerText = `Source: ${currentPatchDir}`);
    } catch (e) { console.error(e); }
}

/**
 * Renders the tasks table based on the current page and pagination settings.
 * Includes visual stability measures such as empty row padding.
 */
function renderTable() {
    const tbody = document.querySelector('#featuresTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const start = (currentPage - 1) * itemsPerPage;
    const end = Math.min(start + itemsPerPage, allFeatures.length);
    const pageItems = allFeatures.slice(start, end);

    pageItems.forEach((f, idx) => {
        tbody.appendChild(createRow(f, start + idx));
    });

    // Stability: Fill empty rows to maintain table height
    for (let i = pageItems.length; i < itemsPerPage; i++) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="7" style="border-bottom:none;">&nbsp;</td>';
        tbody.appendChild(tr);
    }

    const totalPages = Math.ceil(allFeatures.length / itemsPerPage) || 1;
    const counter = document.getElementById('pageCounter');
    if (counter) counter.innerText = `${currentPage} / ${totalPages}`;

    const pBtn = document.getElementById('prevPage');
    const nBtn = document.getElementById('nextPage');
    if (pBtn) pBtn.disabled = (currentPage === 1);
    if (nBtn) nBtn.disabled = (currentPage >= totalPages);
}

/**
 * Creates a table row (TR) element for a specific feature.
 * Handles both read-only and inline-editing states.
 * @param {Object} f - The feature object containing task definitions.
 * @param {number} absIdx - The absolute index of the item in the global features array.
 * @returns {HTMLTableRowElement} The constructed table row.
 */
function createRow(f, absIdx) {
    const tr = document.createElement('tr');
    tr.dataset.index = absIdx;
    tr.draggable = (editingId === null);

    const isEditing = (editingId === f.id);
    const isDone = completedIds.has(f.id);

    // Path Logic: If contains "patches/", show only filename for brevity
    let pDisp = f.patch || '-';
    if (pDisp !== '-' && pDisp.includes('patches/')) {
        pDisp = pDisp.split('patches/').pop();
    }

    const gDisp = f.group_id || '-';

    if (isEditing) {
        tr.classList.add('editing-row');
        if (editingGroupOnly) {
            // Limited edit mode for completed tasks (only Group ID)
            const bt = (f.build_flags || []).join(' ') || '-';
            const rt = (f.runtime_flags || []).join(' ') || '-';
            tr.innerHTML = `
                <td><strong>${f.id}</strong></td>
                <td><input type="text" id="editG" value="${f.group_id || ''}" class="edit-input" placeholder="Group" autofocus></td>
                <td><small title="${bt}">${bt}</small></td>
                <td><small title="${rt}">${rt}</small></td>
                <td><code title="${f.patch || ''}">${pDisp}</code></td>
                <td><span class="badge badge-success">Completed</span></td>
                <td>
                    <div class="btn-group">
                        <button class="btn btn-success btn-sm" id="confEdit">Confirm</button>
                        <button class="btn btn-secondary btn-sm" id="cancEdit">Cancel</button>
                    </div>
                </td>
            `;
        } else {
            // Full edit mode for pending tasks
            tr.innerHTML = `
                <td><strong>${f.id}</strong></td>
                <td><input type="text" id="editG" value="${f.group_id || ''}" class="edit-input" placeholder="Group"></td>
                <td><input type="text" id="editBT" value="${(f.build_flags || []).join(' ')}" class="edit-input"></td>
                <td><input type="text" id="editRT" value="${(f.runtime_flags || []).join(' ')}" class="edit-input"></td>
                <td><input type="text" id="editP" value="${f.patch || ''}" class="edit-input"></td>
                <td>-</td>
                <td>
                    <div class="btn-group">
                        <button class="btn btn-success btn-sm" id="confEdit">Confirm</button>
                        <button class="btn btn-secondary btn-sm" id="cancEdit">Cancel</button>
                    </div>
                </td>
            `;
        }
        tr.querySelector('#confEdit').onclick = () => confirmEdit(f.id);
        tr.querySelector('#cancEdit').onclick = () => { editingId = null; editingGroupOnly = false; renderTable(); };
    } else {
        const status = isDone ? '<span class="badge badge-success">Completed</span>' : '<span class="badge badge-pending">Pending</span>';
        const bt = (f.build_flags || []).join(' ') || '-';
        const rt = (f.runtime_flags || []).join(' ') || '-';
        
        // Use different labels for completed vs pending tasks
        const modifyLabel = isDone ? 'Edit Group' : 'Modify';
        
        tr.innerHTML = `
            <td><strong>${f.id}</strong></td>
            <td><small>${gDisp}</small></td>
            <td><small title="${bt}">${bt}</small></td>
            <td><small title="${rt}">${rt}</small></td>
            <td><code title="${f.patch || ''}">${pDisp}</code></td>
            <td>${status}</td>
            <td>
                <div class="btn-group">
                    <button class="btn btn-secondary btn-sm" id="modBtn">${modifyLabel}</button>
                    <button class="btn btn-danger btn-sm" id="delBtn">Delete</button>
                </div>
            </td>
        `;
        tr.querySelector('#modBtn').onclick = () => { 
            editingId = f.id; 
            editingGroupOnly = isDone; 
            renderTable(); 
        };
        tr.querySelector('#delBtn').onclick = () => deleteFeature(f.id);
    }
    return tr;
}

/**
 * Handles the bulk cleaning of pending tasks.
 * Excludes currently running tasks and already completed tasks.
 */
async function handleCleanTasks() {
    const msg = "Are you sure you want to delete all pending tasks?\n(Currently running tasks and completed tasks will be kept.)";
    if (!confirm(msg)) return;

    try {
        // 1. Get current status to see if any task is running
        const statusRes = await fetch('/api/status');
        const status = await statusRes.json();
        const runningId = status.is_running ? status.current_task : null;

        // 2. Filter allFeatures
        // Keep if: it is currently running OR it is already completed
        allFeatures = allFeatures.filter(f => {
            const isRunning = (runningId && f.id === runningId);
            const isDone = completedIds.has(f.id);
            return isRunning || isDone;
        });

        // 3. Save and refresh
        await saveAllFeatures();
        renderTable();
        alert("Clean complete.");
    } catch (e) {
        console.error("Clean failed:", e);
        alert("Failed to clean tasks.");
    }
}

/**
 * Initializes the bulk addition UI, handling group additions and combination generation.
 */
function setupBulkUI() {
    const addRG = document.getElementById('addRuntimeGroup');
    const addBG = document.getElementById('addBuildGroup');
    const addP = document.getElementById('addPatchEntry');
    const genBtn = document.getElementById('generateBulkBtn');

    if (addBG) addBG.onclick = () => {
        const v = document.getElementById('newBuildGroup').value.trim();
        if (v) { bulkBuildGroups.push(v.split(/\s+/)); document.getElementById('newBuildGroup').value = ''; renderBulk(); }
    };
    if (addRG) addRG.onclick = () => {
        const v = document.getElementById('newRuntimeGroup').value.trim();
        if (v) { bulkRuntimeGroups.push(v.split(/\s+/)); document.getElementById('newRuntimeGroup').value = ''; renderBulk(); }
    };
    if (addP) addP.onclick = () => {
        const v = document.getElementById('newPatchEntry').value.trim();
        bulkPatches.push(v || ""); document.getElementById('newPatchEntry').value = ''; renderBulk();
    };

    if (genBtn) genBtn.onclick = async () => {
        const gId = document.getElementById('bulkGroupId').value.trim();
        const fBT = document.getElementById('fixedBuildFlags').value.trim().split(/\s+/).filter(x => x);
        const fRT = document.getElementById('fixedRuntimeFlags').value.trim().split(/\s+/).filter(x => x);
        const tasks = generateCombinations(fBT, fRT, gId);
        if (tasks.length === 0) return alert('No tasks generated.');
        if (confirm(`Add ${tasks.length} tasks?`)) {
            allFeatures = [...allFeatures, ...tasks];
            await saveAllFeatures();
            closeOverlay('bulkAddOverlay');
            bulkBuildGroups = []; bulkRuntimeGroups = []; bulkPatches = [];
            document.getElementById('bulkGroupId').value = '';
            renderBulk(); renderTable();
        }
    };
}

/**
 * Updates the visual list of items added to the bulk operation buffers.
 */
function renderBulk() {
    const draw = (id, arr, type) => {
        const ul = document.getElementById(id);
        if (!ul) return;
        ul.innerHTML = '';
        arr.forEach((item, idx) => {
            const li = document.createElement('li');
            const txt = Array.isArray(item) ? item.join(' ') : (item || '[No Patch]');
            li.innerHTML = `<span>${txt}</span><span class="remove-item" style="cursor:pointer; color:red;">&times;</span>`;
            li.querySelector('.remove-item').onclick = () => {
                if (type === 'BT') bulkBuildGroups.splice(idx, 1);
                if (type === 'RT') bulkRuntimeGroups.splice(idx, 1);
                if (type === 'P') bulkPatches.splice(idx, 1);
                renderBulk();
            };
            ul.appendChild(li);
        });
    };
    draw('buildGroupList', bulkBuildGroups, 'BT');
    draw('runtimeGroupList', bulkRuntimeGroups, 'RT');
    draw('patchEntryList', bulkPatches, 'P');
}

/**
 * Generates a Cartesian product of build flags, runtime flags, and patches.
 * @param {string[]} fBT - Fixed build flags to include in every generated task.
 * @param {string[]} fRT - Fixed runtime flags to include in every generated task.
 * @param {string} gId - Group ID for all generated tasks.
 * @returns {Object[]} An array of generated task objects.
 */
function generateCombinations(fBT, fRT, gId) {
    const powerSet = (arr) => arr.reduce((sub, v) => sub.concat(sub.map(s => [...s, ...v])), [[]]);
    const btSets = powerSet(bulkBuildGroups);
    const rtSets = powerSet(bulkRuntimeGroups);
    const pOpts = bulkPatches.length > 0 ? bulkPatches : [null];

    const tasks = [];
    
    // Track IDs used in this session to prevent internal batch collisions
    const usedInBatch = new Set();

    for (const patch of pOpts) {
        const p = patch || null;
        for (const bt of btSets) {
            const finalBT = [...fBT, ...bt];
            for (const rt of rtSets) {
                const finalRT = [...fRT, ...rt];
                
                // Use getNextId but also respect IDs just assigned in this loop
                let nextIdVal = parseInt(getNextId());
                while (usedInBatch.has(nextIdVal.toString())) {
                    nextIdVal++;
                }
                
                const finalId = nextIdVal.toString();
                usedInBatch.add(finalId);
                
                tasks.push({ 
                    id: finalId, 
                    group_id: gId || null,
                    build_flags: finalBT, 
                    runtime_flags: finalRT, 
                    patch: p 
                });
            }
        }
    }
    return tasks;
}

/**
 * Confirms and persists inline edits for a specific task.
 * @async
 * @param {string} id - The unique ID of the task being edited.
 */
async function confirmEdit(id) {
    const f = allFeatures.find(x => x.id === id);
    if (!f) return;
    
    // Always update group ID
    f.group_id = document.getElementById('editG').value.trim() || null;
    
    // Update other fields only if not in "group only" mode
    if (!editingGroupOnly) {
        const btInput = document.getElementById('editBT');
        const rtInput = document.getElementById('editRT');
        const pInput = document.getElementById('editP');
        
        if (btInput) f.build_flags = btInput.value.trim().split(/\s+/).filter(x => x);
        if (rtInput) f.runtime_flags = rtInput.value.trim().split(/\s+/).filter(x => x);
        if (pInput) f.patch = pInput.value.trim() || null;
    }
    
    editingId = null;
    editingGroupOnly = false;
    await saveAllFeatures();
    renderTable();
}

/**
 * Deletes a feature from the list after user confirmation.
 * @async
 * @param {string} id - The unique ID of the task to delete.
 */
async function deleteFeature(id) {
    if (!confirm('Delete?')) return;
    allFeatures = allFeatures.filter(x => x.id !== id);
    await saveAllFeatures();
    renderTable();
}

/**
 * Persists the complete `allFeatures` state to the server via a POST request.
 * @async
 */
async function saveAllFeatures() {
    try { await fetch('/api/features', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(allFeatures)}); }
    catch (e) { console.error(e); }
}

/**
 * Determines the next available numeric ID by finding the maximum existing ID 
 * in both defined tasks and completed results, then incrementing.
 * @returns {string} The next unique ID.
 */
function getNextId() {
    const existingIds = [
        ...allFeatures.map(f => parseInt(f.id) || 0),
        ...Array.from(completedIds).map(id => parseInt(id) || 0)
    ];
    return (existingIds.length > 0 ? Math.max(...existingIds) + 1 : 1).toString();
}

/**
 * Initializes and manages the autocomplete logic for patch file inputs.
 */
function setupAutocompleteUI() {
    const attach = (inId, listId) => {
        const input = document.getElementById(inId);
        const list = document.getElementById(listId);
        if (!input || !list) return;
        const show = () => {
            const v = input.value.toLowerCase();
            list.innerHTML = '';
            const filtered = v ? availablePatches.filter(p => p.name.toLowerCase().includes(v)) : availablePatches;
            filtered.forEach(p => {
                const d = document.createElement('div');
                d.className = 'autocomplete-item';
                d.innerText = p.name;
                d.onclick = () => { input.value = p.is_absolute ? p.full_path : p.name; list.style.display = 'none'; };
                list.appendChild(d);
            });
            list.style.display = filtered.length > 0 ? 'block' : 'none';
        };
        input.oninput = show; input.onfocus = show;
    };
    attach('patchFile', 'patchAutocomplete');
    attach('newPatchEntry', 'bulkPatchAutocomplete');
    document.addEventListener('click', (e) => {
        ['patchAutocomplete', 'bulkPatchAutocomplete'].forEach(id => {
            const el = document.getElementById(id);
            if (el && !el.contains(e.target) && !['patchFile', 'newPatchEntry'].includes(e.target.id)) el.style.display = 'none';
        });
    });
}

/**
 * Sets up the directory browser modal, allowing users to navigate and select custom patch folders.
 */
function setupDirectoryBrowserUI() {
    const modal = document.getElementById('pathModal');
    if (!modal) return;
    document.querySelectorAll('.browse-btn').forEach(btn => {
        btn.onclick = () => { openOverlay('pathModal'); updateBrowser(currentPatchDir); };
    });
    const cls = document.getElementById('closeModal');
    if (cls) cls.onclick = () => closeOverlay('pathModal');
    
    /**
     * Refreshes the directory browser view with contents from the specified path.
     * @async
     * @param {string} path - The filesystem path to browse.
     */
    async function updateBrowser(path) {
        const r = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
        const d = await r.json();
        const cur = document.getElementById('currentPath');
        if (cur) cur.innerText = d.current_path;
        const ul = document.getElementById('fileList');
        if (!ul) return;
        ul.innerHTML = '';
        d.items.forEach(i => {
            const li = document.createElement('li');
            li.innerText = (i.is_dir ? "📁 " : "📄 ") + i.name;
            if (i.is_dir) li.onclick = () => updateBrowser(i.path);
            ul.appendChild(li);
        });
    }

    const sel = document.getElementById('selectCurrentDir');
    if (sel) sel.onclick = async () => {
        const p = document.getElementById('currentPath').innerText;
        await fetch('/api/settings', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({custom_patch_dir: p})});
        closeOverlay('pathModal');
        await fetchPatchList();
    };
}

/**
 * Initializes native HTML drag-and-drop support for reordering rows in the features table.
 */
function setupDragAndDrop() {
    const b = document.querySelector('#featuresTable tbody');
    if (!b) return;
    let drg = null;
    b.ondragstart = (e) => { if (editingId) return e.preventDefault(); drg = e.target.closest('tr'); drg.classList.add('dragging'); };
    b.ondragend = () => { if (drg) drg.classList.remove('dragging'); updateOrder(); };
    b.ondragover = (e) => {
        e.preventDefault();
        const after = getAft(b, e.clientY);
        if (!after) b.appendChild(drg); else b.insertBefore(drg, after);
    };
}

/**
 * Utility to find the target row for insertion during drag operations.
 * @param {HTMLElement} c - The table body container.
 * @param {number} y - The vertical coordinate of the mouse.
 * @returns {HTMLElement|null} The element to insert before.
 */
function getAft(c, y) {
    const els = [...c.querySelectorAll('tr:not(.dragging):not(.editing-row)')];
    return els.reduce((cls, child) => {
        const box = child.getBoundingClientRect();
        const off = y - box.top - box.height / 2;
        if (off < 0 && off > cls.offset) return { offset: off, element: child };
        else return cls;
    }, { offset: Number.NEGATIVE_INFINITY }).element;
}

/**
 * Reorders the global `allFeatures` array based on the new visual order in the table.
 * @async
 */
async function updateOrder() {
    const rows = [...document.querySelectorAll('#featuresTable tbody tr')].filter(r => r.dataset.index !== undefined);
    const startIdx = (currentPage - 1) * itemsPerPage;
    const newPageOrder = rows.map(r => allFeatures[parseInt(r.dataset.index)]);
    allFeatures.splice(startIdx, itemsPerPage, ...newPageOrder);
    await saveAllFeatures();
    loadFeatures();
}
