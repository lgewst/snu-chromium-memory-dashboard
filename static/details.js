/**
 * @file details.js
 * @description Logic for the detailed measurement results view.
 *              Uses a hierarchical tree navigation to explore nested JSON data.
 *              Features metadata display and raw JSON inspection with preserved order.
 */

document.addEventListener('DOMContentLoaded', () => {
    const treeContainer = document.getElementById('treeContainer');
    const treeSearch = document.getElementById('treeSearch');
    const detailsContent = document.getElementById('detailsContent');

    let allResults = [];

    /**
     * Initialization: Fetch all data and build the navigation tree.
     */
    const init = async () => {
        try {
            const response = await fetch('/api/results');
            allResults = await response.json();
            
            if (allResults.length === 0) {
                treeContainer.innerHTML = '<div class="status-msg">No results found.</div>';
                return;
            }

            buildTree();
        } catch (err) {
            console.error('Failed to load details:', err);
            treeContainer.innerHTML = '<div class="status-msg" style="color: red;">Error loading data.</div>';
        }
    };

    /**
     * Builds the navigable tree structure (Tasks -> Iterations -> URLs).
     */
    const buildTree = (filterText = '') => {
        treeContainer.innerHTML = '';
        const normalizedFilter = filterText.toLowerCase();

        allResults.forEach((task) => {
            if (normalizedFilter && !task.id.toLowerCase().includes(normalizedFilter)) {
                return;
            }

            // Level 0: Task
            const taskEl = createTreeItem(`Task: ${task.id}`, 0, () => renderTaskView(task));
            treeContainer.appendChild(taskEl);

            (task.memory_results || []).forEach((iter) => {
                // Level 1: Iteration
                const iterEl = createTreeItem(`Iteration ${iter.iteration}`, 1, () => renderIterationView(task, iter));
                treeContainer.appendChild(iterEl);

                if (iter.urls) {
                    Object.entries(iter.urls).forEach(([url, data]) => {
                        // Level 2: URL
                        const urlName = url.split('/').pop() || url;
                        const urlEl = createTreeItem(urlName, 2, () => renderUrlView(task, iter, url, data));
                        urlEl.title = url;
                        treeContainer.appendChild(urlEl);
                    });
                }
            });
        });
        
        if (treeContainer.innerHTML === '') {
            treeContainer.innerHTML = '<div class="status-msg">No matches found.</div>';
        }
    };

    /**
     * Helper to create a tree item element.
     */
    const createTreeItem = (label, level, onClick) => {
        const div = document.createElement('div');
        div.className = `tree-item level-${level}`;
        div.innerText = label;
        div.onclick = (e) => {
            document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
            div.classList.add('active');
            onClick();
        };
        return div;
    };

    /**
     * Renders high-level summary for a specific Task.
     */
    const renderTaskView = (task) => {
        detailsContent.innerHTML = `
            <div class="card data-view-card">
                <div class="view-header">
                    <h2>Task Details: ${task.id}</h2>
                    <button class="btn btn-sm btn-secondary" onclick="toggleRawView('task-raw-${task.id}')">Toggle Raw JSON</button>
                </div>
                <div class="view-body">
                    <div id="task-raw-${task.id}" class="raw-json" style="display:none; margin-bottom: 20px;">${JSON.stringify(task, null, 2)}</div>

                    <div class="info-grid">
                        <div class="info-item"><span class="info-label">Group ID</span><span class="info-value">${task.group_id || '-'}</span></div>
                        <div class="info-item"><span class="info-label">Build Time</span><span class="info-value">${task.build_time.toFixed(2)}s</span></div>
                        <div class="info-item"><span class="info-label">Timestamp</span><span class="info-value">${new Date(task.timestamp * 1000).toLocaleString()}</span></div>
                        <div class="info-item"><span class="info-label">Iterations</span><span class="info-value">${task.memory_results.length}</span></div>
                    </div>
                    
                    <div class="info-group">
                        <p><strong>Build Flags:</strong></p>
                        <pre style="background: #f4f4f4; padding: 10px; border-radius: 4px; font-size: 0.8rem; overflow-x: auto;">${task.build_flags.join(' ') || 'None'}</pre>
                    </div>
                    
                    <div class="info-group">
                        <p><strong>Runtime Flags:</strong></p>
                        <pre style="background: #f4f4f4; padding: 10px; border-radius: 4px; font-size: 0.8rem; overflow-x: auto;">${task.runtime_flags.join(' ') || 'None'}</pre>
                    </div>
                </div>
            </div>
        `;
    };

    /**
     * Renders view for a specific Iteration, including Metadata.
     */
    const renderIterationView = (task, iter) => {
        const metadata = iter.metadata || {};
        
        detailsContent.innerHTML = `
            <div class="card data-view-card">
                <div class="view-header">
                    <h2>Task ${task.id} - Iteration ${iter.iteration}</h2>
                    <button class="btn btn-sm btn-secondary" onclick="toggleRawView('iter-raw-${iter.iteration}')">Toggle Raw JSON</button>
                </div>
                <div class="view-body">
                    <div id="iter-raw-${iter.iteration}" class="raw-json" style="display:none; margin-bottom: 20px;">${JSON.stringify(iter, null, 2)}</div>

                    <h3>Device & Environment Metadata</h3>
                    <div class="info-grid" style="margin-bottom: 30px;">
                        ${Object.entries(metadata).map(([key, val]) => `
                            <div class="info-item">
                                <span class="info-label">${key.replace('_', ' ')}</span>
                                <span class="info-value">${val}</span>
                            </div>
                        `).join('')}
                        ${Object.keys(metadata).length === 0 ? '<p style="color:#999;">No metadata found for this iteration.</p>' : ''}
                    </div>

                    <h3>Summary of Peaks</h3>
                    <div class="samples-table-container">
                        <table class="samples-table">
                            <thead>
                                <tr>
                                    <th>URL</th>
                                    <th>Peak RSS (MB)</th>
                                    <th>Peak PSS (MB)</th>
                                    <th>Samples</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${Object.entries(iter.urls).map(([url, data]) => `
                                    <tr>
                                        <td style="text-align: left;">${url}</td>
                                        <td>${data.peak.toFixed(2)}</td>
                                        <td>${(data.peak_pss || 0).toFixed(2)}</td>
                                        <td>${data.samples.length}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;
    };

    /**
     * Renders exhaustive metric samples for a specific URL.
     */
    const renderUrlView = (task, iter, url, data) => {
        const samples = data.samples || [];
        
        detailsContent.innerHTML = `
            <div class="card data-view-card">
                <div class="view-header">
                    <div style="min-width: 0;">
                        <h2 style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">URL Data: ${url}</h2>
                        <small>Task: ${task.id} | Iteration: ${iter.iteration}</small>
                    </div>
                    <button class="btn btn-sm btn-secondary" onclick="toggleRawView('url-raw-view')">Toggle Raw JSON</button>
                </div>
                <div class="view-body">
                    <div id="url-raw-view" class="raw-json" style="display:none; margin-bottom: 20px;">${JSON.stringify(data, null, 2)}</div>

                    <div class="info-grid">
                        <div class="info-item"><span class="info-label">Peak RSS</span><span class="info-value">${data.peak.toFixed(2)} MB</span></div>
                        <div class="info-item"><span class="info-label">Peak PSS</span><span class="info-value">${(data.peak_pss || 0).toFixed(2)} MB</span></div>
                        <div class="info-item"><span class="info-label">Sample Count</span><span class="info-value">${samples.length}</span></div>
                    </div>

                    <h3>Full Metric Samples</h3>
                    <div class="samples-table-container">
                        <table class="samples-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Elapsed (s)</th>
                                    <th>RSS (MB)</th>
                                    <th>PSS (MB)</th>
                                    <th>JS Heap Used</th>
                                    <th>JS Heap Total</th>
                                    <th>Nodes</th>
                                    <th>Docs</th>
                                    <th>Layout (ms)</th>
                                    <th>Task (ms)</th>
                                    <th>Script (ms)</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${samples.map((s, idx) => `
                                    <tr>
                                        <td style="text-align: left; color: #999;">${idx + 1}</td>
                                        <td>${s.elapsed.toFixed(1)}</td>
                                        <td style="font-weight: bold;">${s.rss.toFixed(2)}</td>
                                        <td style="font-weight: bold; color: var(--primary-color);">${(s.pss || 0).toFixed(2)}</td>
                                        <td>${(s.js_heap_used || 0).toFixed(2)}</td>
                                        <td>${(s.js_heap_total || 0).toFixed(2)}</td>
                                        <td>${s.nodes || 0}</td>
                                        <td>${s.documents || 0}</td>
                                        <td>${(s.layout_duration || 0).toFixed(1)}</td>
                                        <td>${(s.task_duration || 0).toFixed(1)}</td>
                                        <td>${(s.script_duration || 0).toFixed(1)}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;
    };

    /**
     * Global function to toggle raw JSON view.
     */
    window.toggleRawView = (id) => {
        const el = document.getElementById(id);
        if (el) {
            el.style.display = el.style.display === 'none' ? 'block' : 'none';
        }
    };

    /**
     * Search functionality for the tree.
     */
    treeSearch.addEventListener('input', (e) => {
        buildTree(e.target.value);
    });

    init();
});
