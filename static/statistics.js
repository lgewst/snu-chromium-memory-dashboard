/**
 * @file statistics.js
 * @description Advanced Statistics page logic with hierarchical filtering and Single Task Mode.
 *              Supports deep-linking via task_id and iteration-level analysis.
 */

document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const urlListContainer = document.getElementById('urlList');
    const groupListContainer = document.getElementById('groupList');
    const buildFlagListContainer = document.getElementById('buildFlagList');
    const runtimeFlagListContainer = document.getElementById('runtimeFlagList');
    const patchListContainer = document.getElementById('patchList');
    const taskListContainer = document.getElementById('taskList');
    const iterationFilterSection = document.getElementById('iterationFilterSection');
    const iterationListContainer = document.getElementById('iterationList');
    const ctx = document.getElementById('pssTimeSeriesChart').getContext('2d');

    // State Management
    let allResults = [];
    let pssChart = null;

    let selectedUrls = new Set();
    let selectedGroups = new Set();
    let selectedBuildFlags = new Set();
    let selectedRuntimeFlags = new Set();
    let selectedPatches = new Set();
    let selectedTaskIds = new Set();
    let selectedIterations = new Set(); // For Single Task Mode

    let availableUrls = new Set();
    let availableGroups = new Set();

    /**
     * Initializes the statistics page.
     */
    const init = async () => {
        try {
            const response = await fetch('/api/results');
            allResults = await response.json();
            
            if (allResults.length === 0) {
                document.querySelectorAll('.group-list, .task-filter-list').forEach(el => el.innerHTML = '<span>No results available.</span>');
                return;
            }

            // Extract global uniques
            allResults.forEach(res => {
                if (res.group_id) availableGroups.add(res.group_id);
                if (res.memory_results && res.memory_results[0]) {
                    Object.keys(res.memory_results[0].urls).forEach(url => availableUrls.add(url));
                }
            });

            // Default selections
            availableUrls.forEach(url => selectedUrls.add(url));
            
            // Check for deep link
            const urlParams = new URLSearchParams(window.location.search);
            const targetTaskId = urlParams.get('task_id');

            if (targetTaskId) {
                // If deep linked: select all groups/flags to ensure target task is visible,
                // but only select the target task in Filter C.
                availableGroups.forEach(group => selectedGroups.add(group));
                renderFilterA();
                renderGroupFilters();
                updateFilterB(targetTaskId); // Pass target to specifically select it in C
            } else {
                availableGroups.forEach(group => selectedGroups.add(group));
                renderFilterA();
                renderGroupFilters();
                updateFilterB();
            }
        } catch (err) {
            console.error('Failed to initialize statistics:', err);
        }
    };

    /**
     * URLs list (A)
     */
    const renderFilterA = () => {
        urlListContainer.innerHTML = '';
        Array.from(availableUrls).sort().forEach(url => {
            urlListContainer.appendChild(createCheckboxItem(url, selectedUrls.has(url), (checked) => {
                checked ? selectedUrls.add(url) : selectedUrls.delete(url);
                updateChart();
            }));
        });
    };

    /**
     * Group Filter
     */
    const renderGroupFilters = () => {
        groupListContainer.innerHTML = '';
        Array.from(availableGroups).sort().forEach(group => {
            groupListContainer.appendChild(createCheckboxItem(group, selectedGroups.has(group), (checked) => {
                checked ? selectedGroups.add(group) : selectedGroups.delete(group);
                updateFilterB();
            }));
        });
    };

    /**
     * Filter B: Flags & Patches
     * Cascades from Group Filter. Resets to unchecked.
     */
    const updateFilterB = (targetTaskId = null) => {
        const groupFiltered = allResults.filter(res => !res.group_id || selectedGroups.has(res.group_id));
        
        const bBuildFlags = new Set();
        const bRuntimeFlags = new Set();
        const bPatches = new Set();

        groupFiltered.forEach(res => {
            (res.build_flags || []).forEach(f => bBuildFlags.add(f));
            (res.runtime_flags || []).forEach(f => bRuntimeFlags.add(f));
            if (res.patch) bPatches.add(res.patch);
        });

        selectedBuildFlags.clear();
        selectedRuntimeFlags.clear();
        selectedPatches.clear();

        renderSubFilter(buildFlagListContainer, bBuildFlags, selectedBuildFlags, updateFilterC);
        renderSubFilter(runtimeFlagListContainer, bRuntimeFlags, selectedRuntimeFlags, updateFilterC);
        renderSubFilter(patchListContainer, bPatches, selectedPatches, updateFilterC);

        updateFilterC(targetTaskId);
    };

    /**
     * Filter C: Individual Tasks
     * Cascades from Filter B. Resets to all-checked (unless targetTaskId provided).
     */
    const updateFilterC = (targetTaskId = null) => {
        const bFiltered = allResults.filter(res => {
            const groupMatch = !res.group_id || selectedGroups.has(res.group_id);
            if (!groupMatch) return false;
            const buildMatch = selectedBuildFlags.size === 0 || res.build_flags.some(f => selectedBuildFlags.has(f));
            const runtimeMatch = selectedRuntimeFlags.size === 0 || res.runtime_flags.some(f => selectedRuntimeFlags.has(f));
            const patchMatch = selectedPatches.size === 0 || (res.patch && selectedPatches.has(res.patch));
            return buildMatch && runtimeMatch && patchMatch;
        });

        taskListContainer.innerHTML = '';
        selectedTaskIds.clear();

        bFiltered.forEach(res => {
            // If targetTaskId provided, only check that one. Otherwise check all.
            const isSelected = targetTaskId ? (res.id === targetTaskId) : true;
            if (isSelected) selectedTaskIds.add(res.id);

            const item = document.createElement('label');
            item.className = 'task-item';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = isSelected;
            cb.addEventListener('change', (e) => {
                e.target.checked ? selectedTaskIds.add(res.id) : selectedTaskIds.delete(res.id);
                updateChart();
            });
            const content = document.createElement('div');
            content.innerHTML = `<strong>${res.id}</strong> <span class="task-details">Flags: [${res.build_flags.join(', ')} | ${res.runtime_flags.join(', ')}] Patch: ${res.patch || 'None'}</span>`;
            item.appendChild(cb);
            item.appendChild(content);
            taskListContainer.appendChild(item);
        });

        updateChart();
    };

    /**
     * Chart Update - Includes Single Task Mode (Iterations)
     */
    const updateChart = () => {
        const finalResults = allResults.filter(res => selectedTaskIds.has(res.id));
        
        if (finalResults.length === 1) {
            renderSingleTaskMode(finalResults[0]);
        } else {
            renderMultiTaskMode(finalResults);
        }
    };

    /**
     * Single Task Mode: Shows iterations 1..N
     */
    const renderSingleTaskMode = (res) => {
        iterationFilterSection.style.display = 'block';
        
        // Initialize iteration selections if needed
        const iterationCount = res.memory_results.length;
        if (selectedIterations.size === 0 || Array.from(selectedIterations).some(i => i > iterationCount)) {
            selectedIterations.clear();
            for (let i = 1; i <= iterationCount; i++) selectedIterations.add(i);
        }

        // Render iteration filters
        iterationListContainer.innerHTML = '';
        for (let i = 1; i <= iterationCount; i++) {
            iterationListContainer.appendChild(createCheckboxItem(`Iteration ${i}`, selectedIterations.has(i), (checked) => {
                checked ? selectedIterations.add(i) : selectedIterations.delete(i);
                updateChart();
            }));
        }

        const datasets = [];
        res.memory_results.forEach((iter, idx) => {
            const iterNum = idx + 1;
            if (!selectedIterations.has(iterNum)) return;

            datasets.push({
                label: `Iteration ${iterNum}`,
                data: calculateIterationPoints(iter),
                borderColor: getSeriesColor(idx, false),
                backgroundColor: getSeriesColor(idx, false),
                borderWidth: 2,
                fill: false,
                tension: 0.1,
                pointRadius: 0
            });
        });

        // Highlight the best iteration
        let minPeak = Infinity, minIdx = -1;
        datasets.forEach((ds, idx) => {
            const iterNum = parseInt(ds.label.split(' ')[1]);
            const iterData = res.memory_results[iterNum - 1];
            let peakSum = 0, count = 0;
            selectedUrls.forEach(url => {
                if (iterData.urls[url]) {
                    peakSum += iterData.urls[url].peak_pss || 0;
                    count++;
                }
            });
            const avg = count > 0 ? peakSum / count : 0;
            if (avg > 0 && avg < minPeak) { minPeak = avg; minIdx = idx; }
        });

        if (minIdx !== -1) {
            datasets[minIdx].borderColor = 'rgba(255, 159, 64, 1)';
            datasets[minIdx].borderWidth = 4;
            datasets[minIdx].zIndex = 10;
        }

        drawChart(datasets, `Single Task Analysis: ${res.id} (Iterations)`);
    };

    /**
     * Multi Task Mode: Shows median of 1st Iteration for each Task ID
     */
    const renderMultiTaskMode = (results) => {
        iterationFilterSection.style.display = 'none';
        selectedIterations.clear(); // Reset

        const datasets = results.map((res, idx) => {
            return {
                label: res.id,
                data: calculateAvgPoints(res.memory_results[0]),
                borderWidth: 2,
                fill: false,
                tension: 0.1,
                pointRadius: 0,
                _originalResult: res
            };
        });

        let minPeak = Infinity, minIdx = -1;
        datasets.forEach((ds, idx) => {
            const res = ds._originalResult;
            const iteration = res.memory_results[0];
            let peakSum = 0, count = 0;
            selectedUrls.forEach(url => {
                if (iteration.urls[url]) {
                    peakSum += iteration.urls[url].peak_pss || 0;
                    count++;
                }
            });
            const avg = count > 0 ? peakSum / count : 0;
            if (avg > 0 && avg < minPeak) { minPeak = avg; minIdx = idx; }
        });

        datasets.forEach((ds, idx) => {
            const isBest = (idx === minIdx);
            const color = isBest ? 'rgba(255, 159, 64, 1)' : getSeriesColor(idx, false);
            ds.borderColor = color;
            ds.backgroundColor = color;
            if (isBest) { ds.borderWidth = 4; ds.zIndex = 10; }
        });

        drawChart(datasets, 'Multi Task Comparison (Average of 1st Iteration)');
    };

    /**
     * Points for a single iteration averaged across selected URLs
     */
    const calculateIterationPoints = (iter) => {
        const activeUrls = Array.from(selectedUrls).filter(url => iter.urls[url]);
        if (activeUrls.length === 0) return [];
        const maxSamples = Math.max(...activeUrls.map(u => iter.urls[u].samples.length));
        const pts = [];
        for (let i = 0; i < maxSamples; i++) {
            let sum = 0, count = 0, elapsed = 0;
            activeUrls.forEach(url => {
                const s = iter.urls[url].samples[i];
                if (s) { sum += s.pss; elapsed += s.elapsed; count++; }
            });
            if (count > 0) pts.push({ x: elapsed / count, y: sum / count });
        }
        return pts;
    };

    /**
     * Points for 1st iteration (same as calculateIterationPoints basically)
     */
    const calculateAvgPoints = (iter) => calculateIterationPoints(iter);

    const drawChart = (datasets, title) => {
        if (pssChart) pssChart.destroy();
        pssChart = new Chart(ctx, {
            type: 'line',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    title: { display: true, text: title },
                    tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} MB` } }
                },
                scales: {
                    x: { type: 'linear', title: { display: true, text: 'Seconds' } },
                    y: { title: { display: true, text: 'PSS (MB)' } }
                }
            }
        });
    };

    /**
     * Generic sub-filter renderer
     */
    const renderSubFilter = (container, itemsSet, selectedSet, callback) => {
        container.innerHTML = '';
        Array.from(itemsSet).sort().forEach(val => {
            container.appendChild(createCheckboxItem(val, selectedSet.has(val), (checked) => {
                checked ? selectedSet.add(val) : selectedSet.delete(val);
                callback();
            }));
        });
    };

    const createCheckboxItem = (label, checked, onChange) => {
        const wrapper = document.createElement('label');
        wrapper.className = 'group-item';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = checked;
        cb.addEventListener('change', (e) => onChange(e.target.checked));
        wrapper.appendChild(cb);
        wrapper.appendChild(document.createTextNode(label));
        return wrapper;
    };

    const getSeriesColor = (index, isHighlight) => {
        if (isHighlight) return 'rgba(255, 159, 64, 1)';
        const palette = ['#4a90e2', '#2ecc71', '#9b59b6', '#e74c3c', '#34495e', '#f1c40f', '#1abc9c'];
        return palette[index % palette.length];
    };

    // Bulk actions
    const setupBulk = (btnId, containerId, select) => {
        document.getElementById(btnId).onclick = () => {
            document.getElementById(containerId).querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (cb.checked !== select) { cb.checked = select; cb.dispatchEvent(new Event('change')); }
            });
        };
    };

    setupBulk('selectAllUrls', 'urlList', true);
    setupBulk('deselectAllUrls', 'urlList', false);
    setupBulk('selectAllGroups', 'groupList', true);
    setupBulk('deselectAllGroups', 'groupList', false);
    setupBulk('selectAllIters', 'iterationList', true);
    setupBulk('deselectAllIters', 'iterationList', false);
    setupBulk('selectAllTasks', 'taskList', true);
    setupBulk('deselectAllTasks', 'taskList', false);

    document.getElementById('selectAllB').onclick = () => {
        ['buildFlagList', 'runtimeFlagList', 'patchList'].forEach(id => {
            document.getElementById(id).querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (!cb.checked) { cb.checked = true; cb.dispatchEvent(new Event('change')); }
            });
        });
    };
    document.getElementById('deselectAllB').onclick = () => {
        ['buildFlagList', 'runtimeFlagList', 'patchList'].forEach(id => {
            document.getElementById(id).querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (cb.checked) { cb.checked = false; cb.dispatchEvent(new Event('change')); }
            });
        });
    };

    init();
});
