/**
 * @file script.js
 * @description Main entry point for the Dashboard UI. Handles real-time status updates,
 *              result visualization via Chart.js, and sidebar management.
 */

document.addEventListener('DOMContentLoaded', () => {
    // Sidebar toggle functionality
    const sidebar = document.getElementById('sidebar');
    const toggleBtnSidebar = document.getElementById('sidebarToggle');

    /**
     * Initializes sidebar state based on pre-rendered classes and local storage.
     */
    if (document.documentElement.classList.contains('sidebar-collapsed-init')) {
        sidebar.classList.add('collapsed');
        document.documentElement.classList.remove('sidebar-collapsed-init');
    }

    /**
     * Toggles the sidebar visibility and persists the state in local storage.
     */
    function toggleSidebar() {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
    }

    if (toggleBtnSidebar) toggleBtnSidebar.onclick = toggleSidebar;

    // DOM Element References for Dashboard functionality
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const statusDiv = document.getElementById('status');
    const resultsTableBody = document.querySelector('#resultsTable tbody');
    const memoryChartCanvas = document.getElementById('memoryChart');
    
    // Guard clause for non-dashboard pages
    if (!startBtn || !stopBtn || !statusDiv || !resultsTableBody || !memoryChartCanvas) {
        console.log("Dashboard elements not found, skipping dashboard initialization.");
        return;
    }

    const ctx = memoryChartCanvas.getContext('2d');
    
    let memoryChart;
    let lastResultsCount = -1;

    /**
     * Fetches the current execution status from the backend.
     * Updates the status text and enables/disables control buttons based on whether
     * the pipeline process is active.
     * @async
     */
    const updateStatus = async () => {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();
            if (data.is_running) {
                // Show detailed status (e.g., "Building", "Measuring URL...") next to the task ID
                statusDiv.innerText = `Status: ${data.detailed_status || 'Running'} (${data.current_task || 'initializing'})`;
                startBtn.disabled = true;
                stopBtn.disabled = false;
            } else {
                statusDiv.innerText = `Status: ${data.detailed_status || 'Idle'}`;
                startBtn.disabled = false;
                stopBtn.disabled = true;
            }
        } catch (err) {
            console.error('Failed to fetch status:', err);
        }
    };

    /**
     * Fetches all measurement results and updates the UI.
     * Includes logic to avoid unnecessary re-renders if data count hasn't changed,
     * which prevents disruptive chart animation resets.
     * @async
     */
    const fetchResults = async () => {
        try {
            const response = await fetch('/api/results');
            const results = await response.json();
            
            // Only update if data count changed to prevent chart animation jumping
            if (results.length !== lastResultsCount) {
                lastResultsCount = results.length;
                updateTable(results);
                updateChart(results);
            }
        } catch (err) {
            console.error('Failed to fetch results:', err);
        }
    };

    /**
     * Helper to calculate the median of a numeric array.
     * Used for aggregating memory peak values across multiple iterations.
     * @param {number[]} arr - Array of numbers.
     * @returns {number} The calculated median.
     */
    const calculateMedian = (arr) => {
        if (!arr.length) return 0;
        const sorted = [...arr].sort((a, b) => a - b);
        const mid = Math.floor(sorted.length / 2);
        return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    };

    /**
     * Rebuilds the results table with the latest data.
     * Aggregates iteration peaks into a single median peak value per task.
     * @param {Object[]} results - Array of result objects from the backend.
     */
    const updateTable = (results) => {
        resultsTableBody.innerHTML = '';
        results.forEach(res => {
            const row = document.createElement('tr');
            
            // For each iteration, get the peak PSS memory for each URL.
            // RSS is no longer used for display.
            const allIterationPeaks = [];
            res.memory_results.forEach(iter => {
                if (iter.urls) {
                    Object.values(iter.urls).forEach(urlData => {
                        // Use only peak_pss. If missing, show 0.
                        if (urlData && urlData.peak_pss !== undefined) {
                            allIterationPeaks.push(urlData.peak_pss);
                        } else {
                            allIterationPeaks.push(0);
                        }
                    });
                }
            });
            
            const medianPeak = calculateMedian(allIterationPeaks).toFixed(2);

            row.innerHTML = `
                <td>${res.id}</td>
                <td>${res.group_id || '-'}</td>
                <td>${res.build_time.toFixed(2)}</td>
                <td>${medianPeak}</td>
                <td>${res.build_flags.join(' ') || 'None'}</td>
                <td>${res.runtime_flags.join(' ') || 'None'}</td>
                <td>${new Date(res.timestamp * 1000).toLocaleString()}</td>
            `;
            resultsTableBody.appendChild(row);
        });
    };

    /**
     * Updates the Chart.js visualization.
     * Renders a bar chart showing median PSS memory usage indexed by Feature ID.
     * @param {Object[]} results - Array of result objects from the backend.
     */
    const updateChart = (results) => {
        const labels = results.map(r => r.id);
        const dataPoints = results.map(res => {
            const allIterationPeaks = [];
            res.memory_results.forEach(iter => {
                if (iter.urls) {
                    Object.values(iter.urls).forEach(urlData => {
                        // Use only peak_pss. If missing, show 0.
                        if (urlData && urlData.peak_pss !== undefined) {
                            allIterationPeaks.push(urlData.peak_pss);
                        } else {
                            allIterationPeaks.push(0);
                        }
                    });
                }
            });
            return calculateMedian(allIterationPeaks);
        });

        // Clear existing chart instance if it exists to avoid overlaying charts
        if (memoryChart) {
            memoryChart.destroy();
        }

        memoryChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Median Memory Usage (PSS, MB)',
                    data: dataPoints,
                    backgroundColor: 'rgba(54, 162, 235, 0.5)',
                    borderColor: 'rgba(54, 162, 235, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                animation: true, 
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Memory (MB)' }
                    },
                    x: {
                        title: { display: true, text: 'Feature ID' }
                    }
                }
            }
        });
    };

    /**
     * Start Pipeline execution.
     */
    startBtn.addEventListener('click', async () => {
        const response = await fetch('/api/start', { method: 'POST' });
        if (!response.ok) {
            const data = await response.json();
            if (data.status === 'error') {
                alert(data.message || 'Failed to start pipeline');
            }
        }
        updateStatus();
    });

    /**
     * Stop Pipeline execution and clean up processes.
     */
    stopBtn.addEventListener('click', async () => {
        // Disable both buttons immediately to prevent multiple clicks during cleanup
        startBtn.disabled = true;
        stopBtn.disabled = true;
        statusDiv.innerText = 'Status: Stopping processes... please wait.';
        
        await fetch('/api/stop', { method: 'POST' });
        updateStatus();
    });

    /**
     * Polling intervals for real-time dashboard updates (every 3 seconds).
     */
    setInterval(() => {
        updateStatus();
        fetchResults();
    }, 3000);

    // Initial load for immediate feedback
    updateStatus();
    fetchResults();

    // Initial load
    updateStatus();
    fetchResults();


    // ================================================================================================
    // --- Vitals Widget Logic ---
    // ================================================================================================
    const vitalsContainer = document.getElementById('vitalsContainer');
    const vitalsIndicator = document.getElementById('vitalsIndicator');
    const vitalDetailsText = document.getElementById('vitalDetailsText');
    const vitalsTimerText = document.getElementById('vitalsTimerText');
    const vitalsRefreshBtn = document.getElementById('vitalsRefreshBtn');

    let isVitalsExpanded = false;
    let isServerMode = false;

    let countdown = 15;
    let timerInterval = null;
    let isFetching = false;

    // 1. Toggle widget and change refresh interval on click
    if (vitalsContainer) {
        vitalsContainer.addEventListener('click', (event) => {
            vitalsContainer.classList.toggle('expanded');
            isVitalsExpanded = vitalsContainer.classList.contains('expanded');

            if (isVitalsExpanded) {
                forceRefreshVitals();
            } else {
                countdown = 15;
                updateTimerUI();
            }
        });
    }

    // 3. Manual refresh button click (Stop propagation)
    if (vitalsRefreshBtn) {
        vitalsRefreshBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            forceRefreshVitals();
        });
    }

    /**
     * Mocking
     */
    const fetchLocalMockVitals = async () => {
        return new Promise(resolve => setTimeout(() => {
            const diskTot = 500;
            const diskUsed = Math.floor(Math.random() * 480); // 0 ~ 480 used
            resolve({
                status: "connected",
                cpu_percent: Math.floor(Math.random() * 60),
                ram_curr_gb: (Math.random() * 8 + 4).toFixed(1), // 4 ~ 12
                ram_tot_gb: 16.0,
                swap_curr_gb: (Math.random() * 2).toFixed(1),
                swap_tot_gb: 4.0,
                load_avg: "0.45, 0.55, 0.61",
                disk_used_gb: diskUsed,
                disk_tot_gb: diskTot
            });
        }, 300));
    };

    const fetchServerMockVitals = async () => {
        return new Promise(resolve => setTimeout(() => {
            // Simulate poor server connection (10% probability)
            if (Math.random() < 0.1) return resolve({ status: "disconnected" });

            const diskTot = 2000;
            const diskUsed = Math.floor(Math.random() * 1960);
            resolve({
                status: "connected",
                cpu_percent: Math.floor(Math.random() * 50) + 40, // 40 ~ 90%
                ram_curr_gb: (Math.random() * 40 + 10).toFixed(1), // 10 ~ 50
                ram_tot_gb: 64.0,
                swap_curr_gb: (Math.random() * 5 + 1).toFixed(1),
                swap_tot_gb: 16.0,
                load_avg: "2.14, 1.85, 1.50",
                disk_used_gb: diskUsed,
                disk_tot_gb: diskTot
            });
        }, 300));
    };

    /**
     * Updates UI (text, color) with received data
     */
    const updateVitalsUI = (data) => {
        if (!vitalsIndicator || !vitalDetailsText) return;

        if (data.status === "disconnected") {
            vitalsIndicator.style.backgroundColor = 'var(--vital-danger)';
            vitalDetailsText.innerHTML = "<strong>Connection Error</strong>";
            return;
        }

        const ramPercent = (data.ram_curr_gb / data.ram_tot_gb) * 100;
        const diskFree = data.disk_tot_gb - data.disk_used_gb;

        let statusColor = 'var(--vital-good)';
        const isDanger = diskFree <= 45;
        const isWarning = diskFree <= 50 || data.cpu_percent >= 50 || ramPercent >= 50;

        if (isDanger) statusColor = 'var(--vital-danger)';
        else if (isWarning) statusColor = 'var(--vital-warning)';

        vitalsIndicator.style.backgroundColor = statusColor;

        vitalDetailsText.innerHTML = `
            <i class="fa-solid fa-memory vitals-icon" title="Memory(Swap)"></i> ${data.ram_curr_gb}/${data.ram_tot_gb}GB (${data.swap_curr_gb}/${data.swap_tot_gb}GB) 
            <span class="vitals-divider">|</span> 
            <i class="fa-solid fa-microchip vitals-icon" title="CPU"></i> ${data.cpu_percent}% 
            <span class="vital-sys">
                <span class="vitals-divider">|</span> 
                <i class="fa-solid fa-desktop vitals-icon" title="System Load"></i> ${data.load_avg} 
            </span>
            <span class="vital-disk">
                <span class="vitals-divider">|</span> 
                <i class="fa-solid fa-hard-drive vitals-icon" title="Disk"></i> ${data.disk_used_gb}/${data.disk_tot_gb}GB
            </span>
        `;
    };

    /**
     * Fetch actual data
     */
    const fetchAndUpdateVitals = async () => {
        if (isFetching) return;
        isFetching = true;

        try {
            const response = await fetch('/api/vitals'); 
            const data = await response.json();
            // const data = isServerMode ? await fetchServerMockVitals() : await fetchLocalMockVitals();
            updateVitalsUI(data);
        } catch (error) {
            console.error("Vitals Error:", error);
            if (vitalsIndicator) vitalsIndicator.style.backgroundColor = '#95a5a6';
            if (vitalDetailsText) vitalDetailsText.innerHTML = "Error fetching data.";
        } finally {
            isFetching = false;
        }
    };

    /**
     * Update countdown timer UI
     */
    const updateTimerUI = () => {
        if (vitalsTimerText) {
            vitalsTimerText.innerText = `Refreshing in ${countdown}s...`;
        }
    };

    /**
     * Immediate refresh and countdown reset
     */
    const forceRefreshVitals = () => {
        countdown = isVitalsExpanded ? 5 : 15;
        if (isVitalsExpanded) {
            vitalsTimerText.innerText = "Refreshing...";
        }
        fetchAndUpdateVitals();
    };

    /**
     * Main timer loop running every second
     */
    const startVitalsTimerLoop = () => {
        if (timerInterval) clearInterval(timerInterval);

        timerInterval = setInterval(() => {
            // [DEEP DIVE] Stop countdown if the browser tab is hidden (viewing another tab).
            if (document.hidden) return; 

            countdown--;

            if (countdown <= 0) {
                forceRefreshVitals();
            } else {
                if (isVitalsExpanded) updateTimerUI();
            }
        }, 1000);
    };

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            // When the user returns to this tab
            console.log("Welcome back! Refreshing vitals...");
            forceRefreshVitals();
        }
    });

    /**
     * Initialization (Start timer after syncing settings)
     */
    const syncVitalsLabel = async () => {
        const vitalsLabel = document.getElementById('vitalsLabel');
        if (!vitalsLabel) return;

        try {
            const response = await fetch('/api/settings');
            const settings = await response.json();
            isServerMode = !!settings.use_ssh;
            vitalsLabel.innerText = isServerMode ? 'Server' : 'Local';
        } catch (error) {
            vitalsLabel.innerText = 'Local (Err)';
            isServerMode = false;
        }
    };

    // Start Vitals module
    syncVitalsLabel().then(() => {
        if (vitalsContainer) {
            forceRefreshVitals(); // Initial data load
            startVitalsTimerLoop(); // Start timer
        }
    });
    // ================================================================================================
    // --- end Vitals Widget Logic ---
    // ================================================================================================
});
