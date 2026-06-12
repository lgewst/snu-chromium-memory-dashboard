/**
 * @file statistics.js
 * @description Advanced Statistics page logic with hierarchical filtering and Single Task Mode.
 *              Supports deep-linking via task_id and iteration-level analysis.
 */

document.addEventListener('DOMContentLoaded', () => {
    // ==========================================
    // 1. UI Elements Configuration
    // ==========================================
    const uiElements = {
        containers: {
            url: document.getElementById('urlList'),
            group: document.getElementById('groupList'),
            buildFlag: document.getElementById('buildFlagList'),
            runtimeFlag: document.getElementById('runtimeFlagList'),
            patch: document.getElementById('patchList'),
            task: document.getElementById('taskList')
        },
        controls: {
            viewMode: document.querySelectorAll('input[name="viewMode"]'),
            metric: document.querySelectorAll('input[name="metric"]')
        },
        chartCtx: document.getElementById('pssTimeSeriesChart').getContext('2d')
    };

    // ==========================================
    // 2. State Management
    // ==========================================
    const state = {
        rawPayload: [],        // Original API data
        viewMode: 'all',       // Renders average of 1st iterations vs all iterations
        activeMetric: 'pss',   // Current evaluation metric (pss, rss, jsheap)
        
        availableFilters: {
            urls: new Set(),
            groups: new Set(),
            buildFlags: new Set(),
            runtimeFlags: new Set(),
            patches: new Set()
        },
        selectedFilters: {
            urls: new Set(),
            groups: new Set(),
            buildFlags: new Set(),
            runtimeFlags: new Set(),
            patches: new Set(),
            taskIds: new Set()
        },
        memoryChartInstance: null
    };

    const NONE_GROUP = "__NONE__"; // Internal constant for tasks without a group

    // ==========================================
    // 3. Initialization & Event Bindings
    // ==========================================
    /**
     * Bootstraps the statistics dashboard, fetches payload, and starts the render pipeline.
     */
    const init = async () => {
        bindControlEvents();
        bindPopoverBulkActions();

        try {
            const response = await fetch('/api/results');
            state.rawPayload = await response.json();

            if (!state.rawPayload || state.rawPayload.length === 0) return;

            parseGlobalAttributes();

            // Set initial state: All URLs and Groups selected
            state.availableFilters.urls.forEach(url => state.selectedFilters.urls.add(url));
            state.availableFilters.groups.forEach(group => state.selectedFilters.groups.add(group));

            // Initial render
            renderPrimaryFilters();
            executeCascadeFilters();
        } catch (err) {
            console.error('Failed to initialize statistics:', err);
        }
    };

    const bindControlEvents = () => {
        uiElements.controls.viewMode.forEach(radio => {
            radio.addEventListener('change', (e) => {
                state.viewMode = e.target.value;
                renderMemoryChart(); // Redraw chart instantly on mode change
            });
        });

        uiElements.controls.metric.forEach(radio => {
            radio.addEventListener('change', (e) => {
                state.activeMetric = e.target.value;
                renderMemoryChart(); // Redraw chart instantly on metric change
            });
        });
    };

    const parseGlobalAttributes = () => {
        state.rawPayload.forEach(result => {
            state.availableFilters.groups.add(result.group_id || NONE_GROUP);
            if (result.memory_results?.[0]?.urls) {
                Object.keys(result.memory_results[0].urls).forEach(url => state.availableFilters.urls.add(url));
            }
        });
    };

    // ==========================================
    // 4. Cascade Filtering Pipeline
    // ==========================================
    /**
     * Executes the hierarchical filtering logic (Groups -> Flags/Patches -> Tasks).
     */
    const executeCascadeFilters = () => {
        // Step 1: Filter by Group
        const tasksInGroups = state.rawPayload.filter(res => 
            state.selectedFilters.groups.has(res.group_id || NONE_GROUP)
        );
        
        // Step 2: Extract available secondary filters (Flags & Patches)
        state.availableFilters.buildFlags.clear();
        state.availableFilters.runtimeFlags.clear();
        state.availableFilters.patches.clear();

        tasksInGroups.forEach(res => {
            (res.build_flags || []).forEach(f => state.availableFilters.buildFlags.add(f));
            (res.runtime_flags || []).forEach(f => state.availableFilters.runtimeFlags.add(f));
            if (res.patch) state.availableFilters.patches.add(res.patch);
        });

        // Step 3: Render Sub-filters
        renderCheckboxList(uiElements.containers.buildFlag, state.availableFilters.buildFlags, state.selectedFilters.buildFlags, executeCascadeFilters);
        renderCheckboxList(uiElements.containers.runtimeFlag, state.availableFilters.runtimeFlags, state.selectedFilters.runtimeFlags, executeCascadeFilters);
        renderCheckboxList(uiElements.containers.patch, state.availableFilters.patches, state.selectedFilters.patches, executeCascadeFilters);

        // Step 4: Final Task Filtering
        const finalValidTasks = tasksInGroups.filter(res => {
            const buildMatch = state.selectedFilters.buildFlags.size === 0 || res.build_flags.some(f => state.selectedFilters.buildFlags.has(f));
            const runtimeMatch = state.selectedFilters.runtimeFlags.size === 0 || res.runtime_flags.some(f => state.selectedFilters.runtimeFlags.has(f));
            const patchMatch = state.selectedFilters.patches.size === 0 || (res.patch && state.selectedFilters.patches.has(res.patch));
            return buildMatch && runtimeMatch && patchMatch;
        });

        // Step 5: Update Task UI List
        uiElements.containers.task.innerHTML = '';
        state.selectedFilters.taskIds.clear();

        finalValidTasks.forEach(res => {
            state.selectedFilters.taskIds.add(res.id); // Auto-select on filter
            const detailsStr = `[${res.build_flags.join(', ')}]`;
            const item = buildSecureToggleNode(res.id, detailsStr, true, (isChecked) => {
                isChecked ? state.selectedFilters.taskIds.add(res.id) : state.selectedFilters.taskIds.delete(res.id);
                renderMemoryChart(); // Task changes only require a chart redraw
            });
            uiElements.containers.task.appendChild(item);
        });

        renderMemoryChart();
    };

    // ==========================================
    // 5. Secure UI Renderers (XSS Prevented)
    // ==========================================
    const renderPrimaryFilters = () => {
        uiElements.containers.url.innerHTML = '';
        Array.from(state.availableFilters.urls).sort().forEach(url => {
            const item = buildSecureToggleNode(url, '', state.selectedFilters.urls.has(url), (isChecked) => {
                isChecked ? state.selectedFilters.urls.add(url) : state.selectedFilters.urls.delete(url);
                renderMemoryChart();
            });
            uiElements.containers.url.appendChild(item);
        });

        uiElements.containers.group.innerHTML = '';
        Array.from(state.availableFilters.groups).sort().forEach(group => {
            const label = group === NONE_GROUP ? "None" : group;
            const item = buildSecureToggleNode(label, '', state.selectedFilters.groups.has(group), (isChecked) => {
                isChecked ? state.selectedFilters.groups.add(group) : state.selectedFilters.groups.delete(group);
                executeCascadeFilters();
            });
            uiElements.containers.group.appendChild(item);
        });
    };

    const renderCheckboxList = (container, itemsSet, selectedSet, callback) => {
        container.innerHTML = '';
        Array.from(itemsSet).sort().forEach(val => {
            const item = buildSecureToggleNode(val, '', selectedSet.has(val), (isChecked) => {
                isChecked ? selectedSet.add(val) : selectedSet.delete(val);
                callback();
            });
            container.appendChild(item);
        });
    };

    /**
     * Safely constructs DOM elements using textContent.
     */
    const buildSecureToggleNode = (primaryText, secondaryText, isChecked, onChangeEvent) => {
        const wrapper = document.createElement('label');
        wrapper.className = 'group-item';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = isChecked;
        checkbox.addEventListener('change', (e) => onChangeEvent(e.target.checked));
        
        const textContainer = document.createElement('div');
        const strongTag = document.createElement('strong');
        strongTag.textContent = primaryText; 
        textContainer.appendChild(strongTag);
        
        if (secondaryText) {
            const spanTag = document.createElement('span');
            spanTag.className = 'task-details';
            spanTag.textContent = ` ${secondaryText}`;
            textContainer.appendChild(spanTag);
        }

        wrapper.appendChild(checkbox);
        wrapper.appendChild(textContainer);
        return wrapper;
    };

    // ==========================================
    // 6. Metrics & Math Extraction (Original Logic Applied)
    // ==========================================
    const resolveDataPointValue = (sample) => {
        if (state.activeMetric === 'rss') return sample.rss || 0;
        if (state.activeMetric === 'jsheap') return sample.jsheap || sample.js_heap_size || 0;
        return sample.pss || 0; // Default PSS
    };

    /**
     * Gets the server-calculated peak value averaged across selected URLs.
     */
    const calculateAveragePeak = (iterationData) => {
        let cumulativePeak = 0, validUrlCount = 0;
        state.selectedFilters.urls.forEach(url => {
            if (iterationData.urls[url]) {
                const urlData = iterationData.urls[url];
                const peakValue = state.activeMetric === 'rss' ? urlData.peak_rss : 
                                  state.activeMetric === 'jsheap' ? urlData.peak_jsheap : 
                                  urlData.peak_pss;
                cumulativePeak += peakValue || 0;
                validUrlCount++;
            }
        });
        return validUrlCount > 0 ? cumulativePeak / validUrlCount : 0;
    };

    /**
     * Generates XY coordinate points for the time-series chart.
     */
    const generateTimeSeriesData = (iterationData) => {
        const activeUrls = Array.from(state.selectedFilters.urls).filter(url => iterationData.urls[url]);
        if (activeUrls.length === 0) return [];
        
        const maxDataPoints = Math.max(...activeUrls.map(u => iterationData.urls[u].samples.length));
        const chartPoints = [];
        
        for (let i = 0; i < maxDataPoints; i++) {
            let sum = 0, count = 0, elapsed = 0;
            activeUrls.forEach(url => {
                const sample = iterationData.urls[url].samples[i];
                if (sample) { 
                    sum += resolveDataPointValue(sample); 
                    elapsed += sample.elapsed; 
                    count++; 
                }
            });
            if (count > 0) chartPoints.push({ x: elapsed / count, y: sum / count });
        }
        return chartPoints;
    };

    /**
     * Finds the index of the iteration that represents the Median peak.
     */
    const findMedianIterationIndex = (memoryResults) => {
        if (memoryResults.length === 0) return -1;
        
        // Map iterations with their original index and calculated peak
        const peakMappings = memoryResults.map((iter, idx) => ({
            originalIndex: idx,
            peakVal: calculateAveragePeak(iter)
        }));

        // Sort by peak value ascending
        peakMappings.sort((a, b) => a.peakVal - b.peakVal);
        
        // Extract median
        const medianPos = Math.floor(peakMappings.length / 2);
        return peakMappings[medianPos].originalIndex;
    };

    // ==========================================
    // 7. Visualization & Chart Routing
    // ==========================================
    const renderMemoryChart = () => {
        const activeTasks = state.rawPayload.filter(res => state.selectedFilters.taskIds.has(res.id));
        const chartDatasets = [];

        if (state.viewMode === 'all') {
            // MODE 1: Compare All (1st Iteration Average) -> Original Orange Highlight Logic
            let lowestPeak = Infinity, bestTaskIndex = -1;
            
            activeTasks.forEach((res, idx) => {
                const firstIteration = res.memory_results[0];
                const taskPeak = calculateAveragePeak(firstIteration);
                
                chartDatasets.push({
                    label: res.id,
                    data: generateTimeSeriesData(firstIteration),
                    borderColor: getCategoricalColor(idx),
                    backgroundColor: getCategoricalColor(idx),
                    borderWidth: 2, fill: false, tension: 0.1, pointRadius: 0,
                    _peakForEvaluation: taskPeak
                });
                
                // Find the task with the lowest peak
                if (taskPeak > 0 && taskPeak < lowestPeak) {
                    lowestPeak = taskPeak;
                    bestTaskIndex = idx;
                }
            });

            // Apply Orange Styling to the Best Task
            if (bestTaskIndex !== -1) {
                chartDatasets[bestTaskIndex].borderColor = 'rgba(255, 159, 64, 1)'; // Orange Series
                chartDatasets[bestTaskIndex].backgroundColor = 'rgba(255, 159, 64, 1)';
                chartDatasets[bestTaskIndex].borderWidth = 4;
                chartDatasets[bestTaskIndex].zIndex = 10;
            }

            drawChartJsInstance(chartDatasets, 'Multi Task Comparison (Average of 1st Iteration)');

        } else if (state.viewMode === 'target') {
            // MODE 2: Compare Target -> Highlighting Median Iteration
            let colorIndex = 0;
            activeTasks.forEach((res) => {
                const taskColor = getCategoricalColor(colorIndex++);
                const medianIterIdx = findMedianIterationIndex(res.memory_results);

                res.memory_results.forEach((iter, iterIdx) => {
                    const isMedian = (iterIdx === medianIterIdx);
                    
                    chartDatasets.push({
                        label: `${res.id} (Iter ${iterIdx + 1}${isMedian ? ' : Median' : ''})`,
                        data: generateTimeSeriesData(iter),
                        borderColor: taskColor,
                        backgroundColor: taskColor,
                        // Solid & Thick for Median, Dashed & Thin for others
                        borderDash: isMedian ? [] : [5, 5], 
                        borderWidth: isMedian ? 4 : 1.5,
                        fill: false, tension: 0.1, pointRadius: 0,
                        zIndex: isMedian ? 10 : 0
                    });
                });
            });

            drawChartJsInstance(chartDatasets, `Target Analysis (${activeTasks.length} Tasks, All Iterations)`);
        }
    };

    const drawChartJsInstance = (datasets, titleText) => {
        if (state.memoryChartInstance) state.memoryChartInstance.destroy();
        
        const yAxisTitle = state.activeMetric.toUpperCase() + ' (MB)';
        
        state.memoryChartInstance = new Chart(uiElements.chartCtx, {
            type: 'line',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    title: { display: true, text: titleText, font: { size: 16 } },
                },
                scales: {
                    x: { type: 'linear', title: { display: true, text: 'Seconds' } },
                    y: { title: { display: true, text: yAxisTitle } }
                }
            }
        });
    };

    const getCategoricalColor = (index) => {
        const palette = ['#4a90e2', '#2ecc71', '#9b59b6', '#e74c3c', '#34495e', '#f1c40f', '#1abc9c'];
        return palette[index % palette.length];
    };

    // ==========================================
    // 8. Popover UX Binding
    // ==========================================
    const bindPopoverBulkActions = () => {
        const attachBulkEvent = (buttonId, containerKey, isSelectAll) => {
            const btn = document.getElementById(buttonId);
            if (!btn) return;
            btn.addEventListener('click', (e) => {
                e.preventDefault(); // Keep popover open
                uiElements.containers[containerKey].querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
                    if (checkbox.checked !== isSelectAll) {
                        checkbox.checked = isSelectAll;
                        checkbox.dispatchEvent(new Event('change'));
                    }
                });
            });
        };

        attachBulkEvent('selectAllBuild', 'buildFlag', true); attachBulkEvent('deselectAllBuild', 'buildFlag', false);
        attachBulkEvent('selectAllRuntime', 'runtimeFlag', true); attachBulkEvent('deselectAllRuntime', 'runtimeFlag', false);
        attachBulkEvent('selectAllPatches', 'patch', true); attachBulkEvent('deselectAllPatches', 'patch', false);
        attachBulkEvent('selectAllTasks', 'task', true); attachBulkEvent('deselectAllTasks', 'task', false);
    };

    // run
    init();
});
    
